"""Support for MyHome audio zones with Dynamic Proxy for streaming services.

Architecture
------------
The MyHOME BTicino F441M (and similar) is a **hardware-only analog matrix** — it cannot
decode IP streams directly.  This module bridges Music Assistant, Spotify Connect,
and other sources to the matrix by implementing a *Dynamic Proxy* pattern:

**Recommended model — "Hardware Routing First"**

1. Physically wire your network decoder(s) (squeezelite, Cambridge Audio, etc.)
   to the desired F441M source input(s) (Source 1–4).
2. Configure each decoder's physical source number in the integration Options.
3. Use physical wall panels (or a gateway power-on scenario) to route zones
   to the streaming source input. This is the cleanest, hiss-free approach.
4. When Music Assistant calls ``play_media`` on a zone, the proxy:
   a. Claims an idle backend decoder from the shared :class:`~.decoder_pool.DecoderPool`.
   b. Wakes the decoder if it is in standby.
   c. Activates the BTicino zone amplifier with a simple OFF → ON sequence.
      Once the matrix is described in the options (a source name or an
      environment default), the zone's environment is also routed to the
      decoder's input.  Without that the routing set at the wall panels is
      trusted, as in earlier releases.
   d. Forwards the stream URL to the backend decoder via the HA service bus.
5. State, metadata (title, artist, album art), and volume are mirrored from
   the backend decoder back to the BTicino zone entity.
6. Volume changes on the zone apply **gain staging** (decoder volume =
   zone_volume + pre_gain) to keep the analog signal level high and reduce bus noise.
7. When the zone is turned off, the decoder is released back to the pool.

Source selection
----------------
Selecting a source sends the same two frames a wall panel puts on the bus:
``*16*3*10S##`` activates source ``S`` and ``*16*3*1ES##`` routes environment
``E`` to it.  The routing address carries the *environment* digit of the
amplifier address, not the amplifier digit: zone ``23`` lives in environment
``2``, so source 1 is ``121`` and source 2 is ``122``.  The F441M switches per
output and an output serves a whole environment, so every amplifier in that
environment follows the switch; that is matrix hardware behaviour.

Two consequences are enforced here rather than left to chance:

- One environment carries one stream.  A zone cannot claim a decoder while
  another zone of its environment holds one, and a default source is not
  applied over an environment that is streaming.
- Environment 0 (amplifiers ``01``-``09``) has no routing address: ``10S`` is
  the source device itself.  Selecting a source there is refused.

Earlier versions refused to send these frames, believing they caused relay
hiss on MH200-class gateways.  Bus captures on an MH200 show clean switching;
the real problem was a routing address built from the wrong digit, which
addressed an environment that does not exist.

Unconfigured sources
--------------------
A wall panel can route a room to a matrix input that has nothing wired to it,
which sounds like silence or amplifier noise.  When the user has named their
sources in the options, the entity labels such a zone as unconfigured and logs
it once, but never overrides the choice: silently re-routing a room the user
just switched by hand would be its own kind of surprise.

Backward compatibility
----------------------
If no decoders are configured in Options Flow the entity behaves exactly as
before — it controls the BTicino amplifier zone via WHO=16 commands only.
``PLAY_MEDIA`` is not advertised and Music Assistant will not try to use it.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import Platform
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from OWNd.message import OWNSoundCommand, OWNSoundEvent

from .const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    DOMAIN,
    LOGGER,
    SOURCE_UNCONFIGURED_SUFFIX,
)
from .data import MyHOMEConfigEntry, MyHOMERuntimeData, get_runtime_data
from .decoder_pool import DecoderPool, EnvironmentBusyError
from .discovery import Address, DeviceContext, KnownDevices, PlatformDiscovery
from .myhome_device import MyHOMEEntity
from .repairs import (
    async_create_incompatible_decoder_issue,
    async_delete_incompatible_decoder_issue,
    async_prune_incompatible_decoder_issues,
)

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

PARALLEL_UPDATES = 0

# The amplifier wake sequence starts with an OFF frame, and the gateway reports
# that frame back on the event session like any other bus traffic. An OFF that
# arrives this soon after a wake is our own and must not tear the zone down.
_WAKE_ECHO_WINDOW = 3.0  # seconds

# Integrations that cannot play a stream URL, and the media types they do take.
# ``cambridge_audio`` (StreamMagic) accepts presets, Airable and internet radio
# only; a Music Assistant stream is refused with ``unsupported_media_type``.
_STREAM_INCOMPATIBLE_PLATFORMS: dict[str, frozenset[str]] = {
    "cambridge_audio": frozenset({"preset", "airable", "internet_radio"}),
}


def _build_pool(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> DecoderPool:
    """Build a :class:`DecoderPool` from the current options entry.

    Called both from :func:`async_setup_entry` and from the pool-rebuild
    listener registered in ``__init__.py``.

    Args:
        hass: Home Assistant instance.
        config_entry: The active config entry for this MyHOME gateway.

    Returns:
        A fully configured :class:`DecoderPool` (may have zero decoders if
        nothing is configured yet).
    """
    options = config_entry.options
    decoder_map: dict[str, int] = {}
    pre_gain_map: dict[str, int] = {}
    stream_incompatible: set[str] = set()
    ent_reg = er.async_get(hass)

    for i in range(1, CONF_DECODER_SLOTS + 1):
        entity_id = options.get(CONF_DECODER_ENTITY.format(i), "").strip()
        source_num = options.get(CONF_DECODER_SOURCE.format(i), i)      # int
        pre_gain = options.get(CONF_DECODER_PRE_GAIN.format(i), 0)      # int

        if entity_id and entity_id.startswith("media_player."):
            decoder_map[entity_id] = int(source_num)   # always int — never f"Source N"
            pre_gain_map[entity_id] = int(pre_gain)
            reg_entry = ent_reg.async_get(entity_id)
            if reg_entry and reg_entry.platform in _STREAM_INCOMPATIBLE_PLATFORMS:
                stream_incompatible.add(entity_id)
                async_create_incompatible_decoder_issue(
                    hass, config_entry.entry_id, entity_id, reg_entry.platform
                )
            else:
                async_delete_incompatible_decoder_issue(
                    hass, config_entry.entry_id, entity_id
                )

    # Clean up any previously flagged decoder issues that are no longer configured
    async_prune_incompatible_decoder_issues(hass, config_entry.entry_id, decoder_map)

    return DecoderPool(hass, decoder_map, pre_gain_map, stream_incompatible)



async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the MyHOME media player platform and initialise the decoder pool."""
    runtime = config_entry.runtime_data

    # ── Build and store the decoder pool ─────────────────────────────────────
    pool = _build_pool(hass, config_entry)
    runtime.decoder_pool = pool

    LOGGER.info(
        "MyHOME media player: decoder pool initialised with %d decoder(s)",
        len(pool.decoder_entity_ids),
    )

    def build(ctx: DeviceContext) -> MyHOMEMediaPlayer:
        zone = ctx.address.where
        return MyHOMEMediaPlayer(
            hass=hass,
            name=f"Audio Zone {zone}",
            entity_name=None,
            device_id=ctx.key,
            who=ctx.who,
            where=zone,
            manufacturer="BTicino",
            model="Audio System",
            gateway=runtime.gateway,
        )

    discovery = PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=Platform.MEDIA_PLAYER, who="16", event_type=OWNSoundEvent, build=build,
        address=_zone_address, pre_message=_route_pseudo_zones(runtime.router),
        key_suffix="#16",
    )
    # Audio zones are keyed "<zone>#16" in unique ids; the registry restore reads that key back.
    discovery.start()


def _zone_address(message: Any) -> Address | None:
    """Sound-system frames address a zone (amplifier); sources are never devices.

    Reads ``where``, not ``zone``: ``where`` is the frame's address in every
    OWNd version, whereas ``zone`` is OWNd's reading of it, and a routing frame
    (``1ES``) must reach ``_route_pseudo_zones`` whatever OWNd calls it.
    """
    zone = getattr(message, "where", None)
    if not zone or getattr(message, "is_source_event", False):
        return None
    return Address(str(zone), key_suffix="#16")


def _zone_environment(zone: str) -> str | None:
    """Return the environment (room) an amplifier address belongs to.

    Amplifier addresses are ``EA`` — environment digit followed by the
    amplifier number within that environment (``23`` = environment 2,
    amplifier 3).  The F441M ties environments to its outputs one to one
    (OUT n serves environment n), which is why matrix routing is announced
    per environment, not per amplifier.

    The WHO=16 WHERE table only knows two-digit amplifiers (``01``-``99``),
    and OWNd hands the address through unpadded.  Anything else — the general
    address ``0``, an environment command ``#E`` or a hand-written ``1`` — has
    no environment we can be sure of, so ``None`` is returned rather than a
    guess that could switch the wrong room.
    """
    if len(zone) == 2 and zone.isdigit():
        return zone[0]
    return None


def _routing_address(zone: str, source: int) -> str | None:
    """Return the pseudo address that routes ``zone``'s environment to ``source``.

    The address is ``1`` + environment + source: zone ``23`` (environment 2)
    on source 2 gives ``122``, and on source 1 ``121`` — exactly what a wall
    panel puts on the bus when it switches that room's source.

    Returns ``None`` when the zone has no routing address: not a two-digit
    amplifier (see :func:`_zone_environment`), or environment 0 (amplifiers
    ``01``-``09``), where ``10S`` is the source device address itself.
    """
    environment = _zone_environment(zone)
    if environment is None or environment == "0":
        return None
    return f"1{environment}{source}"


def _parse_routing_address(pseudo: str) -> tuple[int, str] | None:
    """Split a ``1ES`` matrix routing address into ``(source, environment)``.

    ``10S`` is not a routing address but a source device (``101``-``109``),
    so environment 0 is excluded.  The source digit is returned as sent, even
    outside S1-S4: the frame is still a routing frame and must not fall
    through to zone discovery as a phantom amplifier ``1ES``.

    Returns ``None`` when ``pseudo`` is not a routing address.
    """
    if len(pseudo) == 3 and pseudo[0] == "1" and pseudo.isdigit() and pseudo[1] != "0":
        return int(pseudo[2]), pseudo[1]
    return None


def _route_pseudo_zones(
    router: Any
) -> Callable[[Any, Address, KnownDevices], bool]:
    """Stereo-module pseudo zones (10x-14x) select the source for an environment."""

    @callback
    def handler(message: Any, address: Address, known: KnownDevices) -> bool:
        parsed = _parse_routing_address(address.where)
        if parsed is None:
            return False
        _source, environment = parsed
        zones = [
            player_id
            for player_id in known
            if _zone_environment(player_id.split("#")[0]) == environment
        ]
        if zones:
            router.publish("16", zones, message)
        return True

    return handler


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    """Unload media player platform."""
    return True


def _get_group_members(runtime: MyHOMERuntimeData | None, entity_id: str) -> list[str] | None:
    """Return group members for entity_id (leader first), or None if not grouped."""
    if runtime is None or runtime.decoder_pool is None:
        return None
    return runtime.decoder_pool.get_group_members(entity_id)


class MyHOMEMediaPlayer(MyHOMEEntity, MediaPlayerEntity):
    """MyHome media player with optional Dynamic Proxy for streaming services.

    When decoders are configured via Options Flow this entity acts as a proxy:
    it intercepts ``play_media`` calls from Music Assistant / Spotify, claims
    an idle backend decoder, routes the BTicino analog matrix, and mirrors
    playback state back to the zone UI.

    Without decoders configured it behaves exactly like the original entity —
    full WHO=16 hardware control with no streaming features advertised.
    """

    # Audio zones are amplified speaker outputs of the SCS sound system.
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
        """Initialise the MyHOME media player entity."""
        super().__init__(
            hass=hass,
            name=name,
            platform=Platform.MEDIA_PLAYER,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            entity_name=entity_name,
        )

        # ── Base hardware state ────────────────────────────────────────────
        self._attr_state: MediaPlayerState | None = MediaPlayerState.OFF
        self._attr_source: str | None = None
        self._warned_sources: set[int] = set()
        self._attr_volume_level: float | None = None
        self._attr_is_volume_muted: bool = False

        # ── Proxy state ────────────────────────────────────────────────────
        self._active_decoder: str | None = None  # entity_id of the claimed decoder
        self._syncing_volume: bool = False        # guard flag — prevents volume feedback loop
        self._pre_mute_volume: float | None = None  # volume to restore on unmute
        self._turning_off: bool = False          # guard flag — dampens bus-OFF echo loops
        self._wake_off_sent_at: float | None = None  # monotonic time of the wake sequence's OFF
        self._unsub_decoders: Callable[[], None] | None = None  # decoder state watch

        # ── Base hardware features (always available) ──────────────────────
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.GROUPING
        )

    @property
    def active_decoder(self) -> str | None:
        """Return the active decoder entity ID claimed by this zone, if any."""
        return self._active_decoder

    @property
    def where(self) -> str:
        """Return the zone OpenWebNet address."""
        return self._where

    @property
    def group_members(self) -> list[str] | None:
        """Return a list of entity ids belonging to this entity's group, leader first."""
        return _get_group_members(self._runtime_data, self.entity_id)

    @property
    def _runtime_data(self) -> MyHOMERuntimeData | None:
        """Return the runtime data for this gateway entry."""
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        return get_runtime_data(entry) if entry is not None else None

    @property
    def _effective_decoder(self) -> str | None:
        """Return the active decoder, or the decoder associated with the current source."""
        if self._active_decoder:
            return self._active_decoder
        pool = self._get_pool()
        if pool:
            assigned = pool.get_assignment(self.entity_id)
            # A group member listens to the leader's decoder only while its
            # environment is routed there. Without automatic routing it may
            # still be on another input, and an input never reported on the
            # bus is not evidence either way, so only a known match mirrors.
            current = self._source_number(self._attr_source) if self._attr_source else None
            if assigned and current is not None and current == pool.decoder_source(assigned):
                return assigned
        if self._attr_state == MediaPlayerState.ON and self._attr_source:
            source_num = self._source_number(self._attr_source)
            if source_num is not None:
                if pool:
                    return pool.get_decoder_for_source(source_num)
        return None


    # ── Source configuration ──────────────────────────────────────────────────

    def _options(self) -> dict[str, Any]:
        """Return the config entry options, or an empty mapping when unavailable."""
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        return dict(getattr(entry, "options", None) or {})

    def _source_names(self) -> dict[int, str]:
        """Return ``{source_number: name}`` for every source the user configured.

        An empty mapping means the installation has not been described yet; the
        entity then falls back to the legacy ``Source N`` labels and assumes
        nothing about which matrix inputs are wired.
        """
        options = self._options()
        names: dict[int, str] = {}
        for i in range(1, CONF_SOURCE_SLOTS + 1):
            name = str(options.get(CONF_SOURCE_NAME.format(i), "") or "").strip()
            if name:
                names[i] = name
        return names

    def _source_label(self, source_num: int) -> str:
        """Return the label to show for ``source_num``.

        Once the user has named their sources, a zone routed to an input that
        was left blank is labelled as unconfigured rather than as a plausible
        looking "Source N" — a wall panel can route a room to an input that has
        nothing wired to it, and the resulting silence or hiss should be
        visible in Home Assistant instead of unexplained.
        """
        names = self._source_names()
        if source_num in names:
            return names[source_num]
        if names:
            return f"Source {source_num}{SOURCE_UNCONFIGURED_SUFFIX}"
        return f"Source {source_num}"

    def _warn_unconfigured_source(self, source_num: int) -> None:
        """Log once when this zone is routed to an input that has no source.

        A wall panel can route a room to a matrix input that nothing is wired
        to; the room then plays silence or amplified noise with no indication
        of why.  The integration deliberately does not "fix" this — the user
        made that choice at the panel — but it does say so, once per source,
        so the cause is findable.
        """
        names = self._source_names()
        if not names or source_num in names:
            return
        if source_num in self._warned_sources:
            return
        self._warned_sources.add(source_num)
        LOGGER.warning(
            "%s: routed to matrix source %d, which is not configured in the "
            "MyHOME options. If nothing is wired to that input the zone will "
            "play silence or noise. Select a configured source, or name this "
            "input in the integration options if it does exist.",
            self.entity_id,
            source_num,
        )

    def _source_number(self, source: str) -> int | None:
        """Resolve a source label back to its BTicino source number.

        Once sources are named only those names resolve, so an input left
        blank — nothing wired to it — cannot be selected under its legacy
        ``Source N`` label either.
        """
        names = self._source_names()
        if names:
            for number, name in names.items():
                if name == source:
                    return number
            return None
        prefix = "Source "
        if source.startswith(prefix):
            candidate = source[len(prefix):]
            if candidate.isdigit() and 1 <= int(candidate) <= CONF_SOURCE_SLOTS:
                return int(candidate)
        return None

    def _default_source(self) -> int | None:
        """Return the source this zone's environment should default to.

        Configured per environment rather than per zone: the matrix routes per
        output and an output serves a whole environment, so two amplifiers in
        the same room cannot sit on different inputs. ``None`` means "leave the
        routing alone", which is the default.
        """
        defaults = self._options().get(CONF_SOURCE_DEFAULTS) or {}
        environment = _zone_environment(self._where)
        if not isinstance(defaults, dict) or environment is None:
            return None
        value = defaults.get(environment)
        try:
            source = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        return source if 1 <= source <= CONF_SOURCE_SLOTS else None

    def _routing_configured(self) -> bool:
        """Return ``True`` once the user has described the matrix in the options.

        Naming a source or setting an environment default is the opt-in for
        automatic routing.  Until then the integration keeps its original
        behaviour and trusts the routing set at the wall panels, so upgrading
        does not start switching rooms on decoder slot numbers nobody checked.
        """
        return bool(self._source_names()) or self._default_source() is not None

    def _routing_frames(self, source_num: int) -> list[str] | None:
        """Return the activate + route frames for ``source_num``.

        ``None`` when no valid pair exists: the source is outside S1-S4 (for
        instance a decoder slot saved as ``0`` by an older options form), or
        the zone has no routing address (see :func:`_routing_address`).
        """
        if not 1 <= source_num <= CONF_SOURCE_SLOTS:
            return None
        route = _routing_address(self._where, source_num)
        if route is None:
            return None
        return [f"*16*3*{100 + source_num}##", f"*16*3*{route}##"]

    async def _route_to(self, source_num: int) -> bool:
        """Send the routing frames for ``source_num``; ``False`` if impossible."""
        frames = self._routing_frames(source_num)
        if frames is None:
            LOGGER.warning(
                "%s: cannot route amplifier %s to matrix source %s; "
                "leaving the routing unchanged",
                self.entity_id,
                self._where,
                source_num,
            )
            return False
        for frame in frames:
            await self._gateway_handler.send(OWNSoundCommand(frame))
        self._attr_source = self._source_label(source_num)
        return True

    def _environment_streamer(self) -> str | None:
        """Return another zone that streams from a decoder in this environment."""
        pool = self._get_pool()
        environment = _zone_environment(self._where)
        if pool is None or environment is None:
            return None
        return pool.environment_owner(environment, exclude=self.entity_id)

    async def _apply_default_source(self) -> None:
        """Route this zone's environment to its default source, if one is set.

        Only called when the zone is switched on from Home Assistant. Routing
        announced by a wall panel is left untouched — see
        :func:`_warn_unconfigured_source` — and so is an environment where
        another zone is streaming: the route is shared, and switching it would
        take that zone off its stream.
        """
        target = self._default_source()
        if target is None:
            return
        streamer = self._environment_streamer()
        if streamer is not None:
            LOGGER.info(
                "%s: not applying default source %d, %s is streaming in the same environment",
                self.entity_id,
                target,
                streamer,
            )
            return
        await self._route_to(target)

    # ── Pool helpers ──────────────────────────────────────────────────────────

    def _get_pool(self) -> DecoderPool | None:
        """Return the shared :class:`DecoderPool` from the entry's runtime data.

        Returns ``None`` if the pool has not yet been initialised (e.g.
        during early startup) or if no decoders are configured.
        """
        entry = getattr(getattr(self, "platform", None), "config_entry", None)
        runtime = get_runtime_data(entry) if entry is not None else None
        return runtime.decoder_pool if runtime is not None else None

    def _decoder_platform(self, decoder_id: str) -> str | None:
        """Return the integration providing ``decoder_id``, from the entity registry."""
        reg_entry = er.async_get(self.hass).async_get(decoder_id)
        return reg_entry.platform if reg_entry else None

    def _decoders_refusing(self, pool: DecoderPool, media_type: str) -> set[str]:
        """Return the decoders whose integration cannot play ``media_type``."""
        refusing: set[str] = set()
        for decoder_id in pool.stream_incompatible:
            accepted = _STREAM_INCOMPATIBLE_PLATFORMS.get(
                self._decoder_platform(decoder_id) or "", frozenset()
            )
            if media_type not in accepted:
                refusing.add(decoder_id)
        return refusing

    def _write_zone_state(self, entity_id: str | None) -> None:
        """Republish another zone of this gateway, e.g. after its group changed."""
        runtime = self._runtime_data
        zone = runtime.media_players.get(entity_id) if runtime and entity_id else None
        if zone is not None and zone is not self:
            zone.async_write_ha_state()

    async def _async_power_off_zone(self, zone: MyHOMEMediaPlayer) -> None:
        """Switch ``zone``'s amplifier off because its group no longer includes it."""
        await zone._gateway_handler.send(OWNSoundCommand.turn_off(zone._where))
        zone._attr_state = MediaPlayerState.OFF
        zone.async_write_ha_state()

    # ── Dynamic feature flags ─────────────────────────────────────────────────

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Return supported features, adding streaming controls when decoders are configured.

        Music Assistant inspects ``supported_features`` to decide whether this
        entity is a valid playback target.  Streaming features are only
        advertised when at least one decoder is configured, which keeps the
        entity backward-compatible for users without a streaming setup.
        """
        features = self._attr_supported_features
        if _zone_environment(self._where) in (None, "0"):
            features &= ~MediaPlayerEntityFeature.GROUPING
        pool = self._get_pool()
        if pool and pool.is_configured:
            features |= (
                MediaPlayerEntityFeature.PLAY_MEDIA
                | MediaPlayerEntityFeature.PAUSE
                | MediaPlayerEntityFeature.PLAY
                | MediaPlayerEntityFeature.STOP
                | MediaPlayerEntityFeature.NEXT_TRACK
                | MediaPlayerEntityFeature.PREVIOUS_TRACK
            )
        return features

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        """Register listeners when entity is added to Home Assistant."""
        self._register_availability_listener()
        runtime = self._runtime_data
        if runtime is not None:
            runtime.media_players[self.entity_id] = self

        # ── Decoder state listener ────────────────────────────────────────
        self._track_decoders()
        self.async_on_remove(self._untrack_decoders)

        # ── Pool rebuild listener (Options Flow saved) ────────────────────
        # When the user configures decoders via the UI, supported_features
        # changes.  We must fire a state update so Music Assistant re-reads
        # our features and discovers the new PLAY_MEDIA capability.
        @callback
        def _pool_updated(*args: Any) -> None:
            """Follow the rebuilt pool and re-publish state."""
            LOGGER.debug("%s: decoder pool updated — re-publishing features", self.entity_id)
            # Saving options rebuilds the pool without reloading the entry:
            # the decoders to watch may have changed, and the new pool holds
            # no claims, so a decoder remembered from the old one is not ours.
            self._track_decoders()
            self._active_decoder = None
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_pool_updated_{self._gateway_handler.mac}",
                _pool_updated,
            )
        )

    @callback
    def _track_decoders(self) -> None:
        """Watch the state of the current pool's decoders, replacing any earlier watch."""
        self._untrack_decoders()
        pool = self._get_pool()
        if pool and pool.is_configured:
            self._unsub_decoders = async_track_state_change_event(
                self.hass,
                pool.decoder_entity_ids,
                self._async_decoder_state_changed,
            )

    @callback
    def _untrack_decoders(self) -> None:
        """Stop watching decoder state."""
        if self._unsub_decoders is not None:
            self._unsub_decoders()
            self._unsub_decoders = None

    async def async_will_remove_from_hass(self) -> None:
        """Drop this zone from the pool's books when the entity goes away.

        Removal happens on every integration reload, options change and
        entity_id rename, none of which is a request to silence a room, so no
        frame is sent: the amplifiers and the decoder keep playing and only
        the group bookkeeping is cleared.
        """
        runtime = self._runtime_data
        if runtime is not None:
            runtime.media_players.pop(self.entity_id, None)
        pool = self._get_pool()
        if pool:
            leader_id = pool.get_leader(self.entity_id)
            members = pool.get_members(self.entity_id)
            await pool.release(self.entity_id)
            for zone_id in [*members, *([leader_id] if leader_id else [])]:
                zone_ent = runtime.media_players.get(zone_id) if runtime else None
                if zone_ent is not None and zone_ent.hass is not None:
                    zone_ent.async_write_ha_state()
        await super().async_will_remove_from_hass()

    # ── Proxy: play_media ─────────────────────────────────────────────────────

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Intercept a Music Assistant / Spotify play command and route it.

        Steps
        -----
        1. Claim an idle decoder from the pool (thread-safe).
        2. Wake the decoder if it is in standby / off.
        3. Turn on the BTicino zone amplifier and route the matrix to the
           decoder's source input.
        4. Forward the stream URL to the backend decoder.

        Args:
            media_type: The media content type (e.g. ``"music"``, ``"internet_radio"``).
            media_id: The stream URL or content identifier.
            **kwargs: Additional kwargs forwarded to the decoder's play_media call
                (e.g. ``announce``, ``enqueue``, ``extra``).

        Raises:
            HomeAssistantError: If all decoders are busy or the decoder fails
                to start playback.
        """
        pool = self._get_pool()
        if not pool or not pool.is_configured:
            LOGGER.warning(
                "%s: play_media called but no decoders configured — ignoring",
                self.entity_id,
            )
            return

        # 1. Claim an idle decoder (thread-safe via asyncio.Lock). With routing
        #    configured the claim is per environment: the zones of one
        #    environment share a matrix output, so they cannot play two streams.
        route = self._routing_configured()
        # Decoders whose integration cannot take this media are skipped rather
        # than claimed and failed, so another idle decoder can play it.
        exclude = self._decoders_refusing(pool, media_type)
        # Playing on a member takes it out of its group (claim() detaches it);
        # the leader's group_members has to be republished.
        old_leader = pool.get_leader(self.entity_id)
        try:
            result = await pool.claim(
                self.entity_id,
                preferred_source=self._default_source(),
                environment=_zone_environment(self._where) if route else None,
                exclude=exclude,
            )
        except EnvironmentBusyError as err:
            raise HomeAssistantError(
                f"{self.entity_id}: {err.owner} is already streaming in environment "
                f"{err.environment}, and zones in one environment share a matrix input",
                translation_domain=DOMAIN,
                translation_key="environment_busy",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "owner": err.owner,
                    "environment": err.environment,
                },
            ) from err
        self._write_zone_state(old_leader)
        if result is None:
            if exclude and set(pool.decoder_entity_ids) <= exclude:
                # Nothing is busy: no configured decoder can take this media.
                decoder_id = sorted(exclude)[0]
                platform = self._decoder_platform(decoder_id)
                raise HomeAssistantError(
                    f"{self.entity_id}: decoder {decoder_id} ({platform}) does not support "
                    "streaming URLs; configure it via DLNA DMR instead",
                    translation_domain=DOMAIN,
                    translation_key="decoder_incompatible_platform",
                    translation_placeholders={
                        "entity_id": str(self.entity_id),
                        "decoder": str(decoder_id),
                        "platform": str(platform),
                    },
                )
            raise HomeAssistantError(
                f"{self.entity_id}: All audio matrix inputs are currently in use by other rooms!",
                translation_domain=DOMAIN,
                translation_key="decoders_busy",
                translation_placeholders={"entity_id": str(self.entity_id)},
            )
        decoder_id, source_num = result
        self._active_decoder = decoder_id

        # 2. Wake an off decoder. IDLE decoders are already ready to play.
        dec_state = self.hass.states.get(decoder_id)
        if dec_state and dec_state.state == MediaPlayerState.OFF:
            await self.hass.services.async_call(
                "media_player", "turn_on", {"entity_id": decoder_id}
            )
            # Poll until the decoder wakes up (max 5 seconds)
            for _ in range(10):
                await asyncio.sleep(0.5)
                dec_state = self.hass.states.get(decoder_id)
                if dec_state and dec_state.state != MediaPlayerState.OFF:
                    break
            else:
                LOGGER.warning(
                    "%s: decoder %s did not wake up within 5 s",
                    self.entity_id,
                    decoder_id,
                )
                await self._async_release_after_failure(pool)
                raise HomeAssistantError(
                    f"{self.entity_id}: decoder {decoder_id} did not wake up within 5 seconds",
                    translation_domain=DOMAIN,
                    translation_key="decoder_wake_timeout",
                    translation_placeholders={
                        "entity_id": str(self.entity_id),
                        "decoder": str(decoder_id),
                    },
                )

        # 3. Activate the BTicino zone amplifier and route it to the decoder.
        #
        # The zone has to listen to the input this decoder is wired to,
        # otherwise the stream plays into a room that is listening elsewhere.
        # Unconfigured installations keep trusting the wall-panel routing.
        await self._async_wake_zone()
        self.async_write_ha_state()
        if route:
            await self._route_to(source_num)

        # If this zone is a group leader, route all group members to the source as well
        if pool:
            runtime = self._runtime_data
            members = pool.get_members(self.entity_id)
            for member_id in members:
                member_ent = runtime.media_players.get(member_id) if runtime else None
                member_env = _zone_environment(member_ent._where) if member_ent else None
                if member_env:
                    owner = pool.environment_owner(member_env, exclude=member_id)
                    if owner is not None and pool.get_assignment(owner) != decoder_id:
                        LOGGER.warning(
                            "%s: dropping member %s from group — environment %s is already streaming to %s",
                            self.entity_id,
                            member_id,
                            member_env,
                            owner,
                        )
                        await pool.remove_group_member(member_id)
                        if member_ent:
                            member_ent.async_write_ha_state()
                        continue

                # Routing follows the same opt-in as the leader's own; the
                # member's amplifier is switched on either way.
                if member_ent:
                    if route:
                        await member_ent._route_to(source_num)
                    await member_ent._async_wake_zone()
                    member_ent.async_write_ha_state()

        # 4. Forward the stream URL to the backend decoder
        service_data: dict[str, Any] = {
            "entity_id": decoder_id,
            "media_content_type": media_type,
            "media_content_id": media_id,
        }
        for key in ("announce", "enqueue", "extra"):
            if key in kwargs:
                service_data[key] = kwargs[key]

        # Error recovery: if the play_media call fails, release the decoder so
        # it does not remain permanently "stuck" as busy.
        try:
            await self.hass.services.async_call("media_player", "play_media", service_data)
        except Exception as err:
            LOGGER.error(
                "%s: failed to forward play_media to %s: %s — releasing decoder",
                self.entity_id,
                decoder_id,
                err,
            )
            await self._async_release_after_failure(pool)
            raise HomeAssistantError(
                f"{self.entity_id}: decoder {decoder_id} failed to start playback: {err}",
                translation_domain=DOMAIN,
                translation_key="decoder_start_failed",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "decoder": str(decoder_id), "error": str(err),
                },
            ) from err

        self.async_schedule_update_ha_state()

    async def _async_release_after_failure(self, pool: DecoderPool) -> None:
        """Give back a decoder that could not be started, and republish the group.

        Releasing a leader disbands its group, so the members' ``group_members``
        change as well as this zone's.
        """
        members = pool.get_members(self.entity_id)
        await pool.release(self.entity_id)
        self._active_decoder = None
        self.async_write_ha_state()
        for member_id in members:
            self._write_zone_state(member_id)

    # ── Multi-room grouping ───────────────────────────────────────────────────

    async def async_join_players(self, group_members: list[str]) -> None:
        """Join multiple sound players into a shared multi-room audio group.

        Replaces the group members with the desired group list (snapshot semantics).
        Any previously grouped member not in ``group_members`` is dropped (amplifier turned off).
        Any newly specified member is validated, routed to the leader's source
        (once matrix routing is configured), and turned on. All members are
        validated before any of them is touched.
        """
        runtime = self._runtime_data
        if runtime is None:
            return

        pool = self._get_pool()
        if not pool:
            raise HomeAssistantError(
                f"{self.entity_id}: audio grouping is not available yet; "
                "the decoder pool has not been initialised",
                translation_domain=DOMAIN,
                translation_key="grouping_unavailable",
                translation_placeholders={"entity_id": str(self.entity_id)},
            )

        leader_env = _zone_environment(self._where)
        if leader_env in (None, "0"):
            raise HomeAssistantError(
                f"{self.entity_id}: amplifier {self._where} has no matrix routing address",
                translation_domain=DOMAIN,
                translation_key="routing_unsupported",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "where": str(self._where),
                },
            )

        desired_members = {m for m in group_members if m != self.entity_id}

        for member_id in desired_members:
            if member_id not in runtime.media_players:
                raise HomeAssistantError(
                    f"{self.entity_id}: cannot join foreign entity {member_id}; only MyHOME sound zones can be grouped",
                    translation_domain=DOMAIN,
                    translation_key="foreign_entity_not_supported",
                    translation_placeholders={
                        "entity_id": str(self.entity_id),
                        "member": str(member_id),
                    },
                )
            member_ent = runtime.media_players[member_id]
            member_env = _zone_environment(member_ent._where)
            if member_env in (None, "0"):
                raise HomeAssistantError(
                    f"{member_id}: amplifier {member_ent._where} has no matrix routing address",
                    translation_domain=DOMAIN,
                    translation_key="routing_unsupported",
                    translation_placeholders={
                        "entity_id": str(member_id),
                        "where": str(member_ent._where),
                    },
                )

        # Book the whole group in one step. The pool checks every member's
        # environment before it changes anything, so a refused join leaves
        # groups, decoders and amplifiers exactly as they were.
        old_leader = pool.get_leader(self.entity_id)
        try:
            change = await pool.set_group(
                self.entity_id,
                {
                    member_id: _zone_environment(runtime.media_players[member_id]._where)
                    for member_id in sorted(desired_members)
                },
            )
        except EnvironmentBusyError as err:
            raise HomeAssistantError(
                f"{self.entity_id}: {err.owner} is already streaming in environment "
                f"{err.environment}, and zones in one environment share a matrix input",
                translation_domain=DOMAIN,
                translation_key="environment_busy",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "owner": err.owner,
                    "environment": err.environment,
                },
            ) from err
        self._write_zone_state(old_leader)

        # Decoders the joining zones held are no longer anyone's: stop them.
        for decoder_id in change.released:
            try:
                await self.hass.services.async_call(
                    "media_player", "media_stop", {"entity_id": decoder_id}
                )
            except Exception:  # pylint: disable=broad-except
                pass  # Best-effort — the zone joins the group either way
        for member_id in change.joined:
            runtime.media_players[member_id]._active_decoder = None

        # Rooms that left, and rooms of groups a joining zone used to lead,
        # would keep listening to a stream nobody controls any more.
        for zone_id in [*change.left, *change.orphaned]:
            zone_ent = runtime.media_players.get(zone_id)
            if zone_ent:
                await self._async_power_off_zone(zone_ent)

        source_num: int | None = None
        if self._active_decoder:
            source_num = pool.decoder_source(self._active_decoder)
        if source_num is None and self._attr_source:
            source_num = self._source_number(self._attr_source)
        if source_num is None:
            source_num = self._default_source()

        # Routing follows the opt-in of _routing_configured(): until the matrix
        # is described in the options, the wall-panel routing is trusted.
        route = self._routing_configured()
        for member_id in change.joined:
            member_ent = runtime.media_players[member_id]
            if source_num is not None:
                if route:
                    await member_ent._route_to(source_num)
                await member_ent._async_wake_zone()
            member_ent.async_write_ha_state()

        self.async_write_ha_state()

    async def async_unjoin_player(self) -> None:
        """Unjoin this player from whichever group it belongs to."""
        pool = self._get_pool()
        if not pool:
            return
        runtime = self._runtime_data
        members = pool.get_members(self.entity_id)
        if members:
            # We are the leader: disband all members
            await pool.disband_group(self.entity_id)
            for member_id in members:
                member_ent = runtime.media_players.get(member_id) if runtime else None
                if member_ent:
                    await member_ent._gateway_handler.send(
                        OWNSoundCommand.turn_off(member_ent._where)
                    )
                    member_ent._attr_state = MediaPlayerState.OFF
                    member_ent.async_write_ha_state()
            self.async_write_ha_state()
        else:
            # We are a member: leave our group
            leader_id = pool.get_leader(self.entity_id)
            if leader_id:
                await pool.remove_group_member(self.entity_id)
                await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))
                self._attr_state = MediaPlayerState.OFF
                self.async_write_ha_state()
                leader_ent = runtime.media_players.get(leader_id) if runtime else None
                if leader_ent:
                    leader_ent.async_write_ha_state()

    # ── Transport controls ────────────────────────────────────────────────────

    async def _forward_to_decoder(self, service: str) -> None:
        """Forward a media_player service call to the active backend decoder.

        Args:
            service: HA service name e.g. ``"media_pause"``.
        """
        pool = self._get_pool()
        if pool:
            leader_id = pool.get_leader(self.entity_id)
            if leader_id and leader_id != self.entity_id:
                LOGGER.debug(
                    "%s: ignoring %s on group member; transport is managed by leader %s",
                    self.entity_id,
                    service,
                    leader_id,
                )
                return

        eff_dec = self._effective_decoder
        if eff_dec:
            await self.hass.services.async_call(
                "media_player", service, {"entity_id": eff_dec}
            )

    async def async_media_pause(self) -> None:
        """Pause playback on the active decoder."""
        await self._forward_to_decoder("media_pause")

    async def async_media_play(self) -> None:
        """Resume playback on the active decoder."""
        await self._forward_to_decoder("media_play")

    async def async_media_stop(self) -> None:
        """Stop playback on the active decoder, or leave group if caller is a member."""
        pool = self._get_pool()
        if pool:
            leader_id = pool.get_leader(self.entity_id)
            if leader_id and leader_id != self.entity_id:
                await self.async_turn_off()
                return

        await self._forward_to_decoder("media_stop")

    async def async_media_next_track(self) -> None:
        """Skip to next track on the active decoder."""
        await self._forward_to_decoder("media_next_track")

    async def async_media_previous_track(self) -> None:
        """Go to previous track on the active decoder."""
        await self._forward_to_decoder("media_previous_track")

    # ── Zone on / off ─────────────────────────────────────────────────────────

    async def _async_wake_zone(self) -> None:
        """Wake a zone amplifier using the hardware-required OFF → ON sequence.

        The gateway reports the OFF back on the event session. The time it was
        sent is kept so :meth:`handle_event` can tell that echo from a wall
        switch; treating it as a real OFF would release the decoder this
        zone just claimed, or drop the member that is joining a group.
        """
        if self._attr_state != MediaPlayerState.ON:
            self._wake_off_sent_at = time.monotonic()
            await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))
            await asyncio.sleep(0.5)
            await self._gateway_handler.send(OWNSoundCommand.turn_on(self._where))
            await asyncio.sleep(0.5)
            self._attr_state = MediaPlayerState.ON

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the zone amplifier on.

        Uses a simple OFF → ON sequence.  When a default source is configured
        for this zone's environment the matrix is routed there as well, so a
        room left on a stale input by a wall panel comes back on the right
        source.  Without that setting the existing routing is kept untouched,
        and a zone that is already on is never re-routed: the route is shared
        by the whole environment and may be carrying a stream.
        """
        if self._attr_state != MediaPlayerState.ON:
            await self._async_wake_zone()
            await self._apply_default_source()

    async def _async_handle_turn_off(self, from_bus: bool = False) -> None:
        """Coordinated turn-off sequence for zones, groups, and decoders."""
        if self._turning_off:
            return
        self._turning_off = True
        try:
            self._attr_state = MediaPlayerState.OFF
            if not from_bus:
                await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))

            pool = self._get_pool()
            runtime = self._runtime_data

            if self._active_decoder:
                try:
                    await self.hass.services.async_call(
                        "media_player", "media_stop", {"entity_id": self._active_decoder}
                    )
                except Exception:
                    pass

            if pool:
                members = pool.get_members(self.entity_id)
                if members:
                    for member_id in members:
                        member_ent = runtime.media_players.get(member_id) if runtime else None
                        if member_ent:
                            # The member's OFF echo runs its own turn-off later;
                            # all that does is leave a group released below.
                            try:
                                await member_ent._gateway_handler.send(
                                    OWNSoundCommand.turn_off(member_ent._where)
                                )
                            except Exception:
                                pass
                            member_ent._attr_state = MediaPlayerState.OFF
                            member_ent.async_write_ha_state()
                    await pool.release(self.entity_id)
                else:
                    leader_id = pool.get_leader(self.entity_id)
                    await pool.release(self.entity_id)
                    if leader_id and runtime:
                        leader_ent = runtime.media_players.get(leader_id)
                        if leader_ent:
                            leader_ent.async_write_ha_state()

            self._active_decoder = None
            self.async_schedule_update_ha_state()
        finally:
            self._turning_off = False

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the zone amplifier off and release any claimed decoder.

        Stops playback on the decoder before releasing it so that it returns
        to the idle pool in a clean state.
        """
        await self._async_handle_turn_off(from_bus=False)

    # ── Volume control ────────────────────────────────────────────────────────

    async def async_volume_up(self) -> None:
        """Increase zone volume one step."""
        await self._gateway_handler.send(OWNSoundCommand.volume_up(self._where))

    async def async_volume_down(self) -> None:
        """Decrease zone volume one step."""
        await self._gateway_handler.send(OWNSoundCommand.volume_down(self._where))

    async def async_set_volume_level(self, volume: float) -> None:
        """Set zone volume and apply gain staging to the active decoder.

        Gain staging strategy
        ---------------------
        Keep the decoder volume proportionally higher than the BTicino zone
        volume to maximise signal level in the analog chain and minimise
        amplification of the bus noise floor.

        Decoder volume = ``min(1.0, zone_volume + pre_gain / 100)``.

        The ``_syncing_volume`` flag prevents a feedback loop:
        ``zone.set_volume → decoder.volume_set → state_changed event
        → zone._async_decoder_state_changed → zone.set_volume → …``

        Args:
            volume: Target volume in the range 0.0–1.0.
        """
        # Auto-unmute if the user slides the volume up
        if self._attr_is_volume_muted and volume > 0:
            self._attr_is_volume_muted = False

        # BTicino hardware uses a 0–31 integer scale
        hw_volume = int(round(volume * 31.0))
        await self._gateway_handler.send(OWNSoundCommand.set_volume(self._where, hw_volume))

        # Gain staging: keep decoder louder than the BTicino analog stage
        if self._active_decoder:
            pool = self._get_pool()
            if pool:
                pre_gain_pct = pool.get_pre_gain(self._active_decoder)
                decoder_volume = min(1.0, volume + pre_gain_pct / 100.0)
                self._syncing_volume = True
                try:
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_set",
                        {
                            "entity_id": self._active_decoder,
                            "volume_level": decoder_volume,
                        },
                    )
                finally:
                    self._syncing_volume = False

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute the zone and propagate to the active decoder.

        Muting is emulated by driving the BTicino zone volume to 0 (or
        restoring it).  If the active decoder supports hardware mute, that is
        also applied for immediate effect.

        Args:
            mute: ``True`` to mute, ``False`` to unmute.
        """
        if mute:
            self._pre_mute_volume = self._attr_volume_level if self._attr_volume_level is not None else 0.5
            await self.async_set_volume_level(0.0)
        else:
            restore_volume = self._pre_mute_volume if self._pre_mute_volume is not None else 0.3
            await self.async_set_volume_level(restore_volume)

        self._attr_is_volume_muted = mute

        # Propagate mute to decoder if it supports the attribute
        if self._active_decoder:
            dec_state = self.hass.states.get(self._active_decoder)
            if dec_state and dec_state.attributes.get("is_volume_muted") is not None:
                try:
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_mute",
                        {"entity_id": self._active_decoder, "is_volume_muted": mute},
                    )
                except Exception:  # pylint: disable=broad-except
                    pass  # Not all decoders support mute; volume=0 covers the rest

        self.async_schedule_update_ha_state()

    # ── Source selection ──────────────────────────────────────────────────────

    @property
    def source_list(self) -> list[str]:
        """Return the sources that can be selected.

        Once sources are named in the options only those are offered: an input
        with nothing wired to it is not a valid destination, and offering it
        would let the user route a room to silence or tuner hiss.  Without any
        configuration the legacy ``Source 1..4`` list is returned unchanged.
        """
        names = self._source_names()
        if names:
            return [names[number] for number in sorted(names)]
        return [f"Source {number}" for number in range(1, CONF_SOURCE_SLOTS + 1)]

    async def async_select_source(self, source: str) -> None:
        """Route this zone's environment to ``source``.

        Two frames are sent, the same pair a wall panel puts on the bus:
        ``*16*3*10S##`` activates the source device and ``*16*3*1ES##`` routes
        environment ``E`` to it.  The F441M switches per output, so every
        amplifier sharing this zone's environment follows along — that is
        matrix hardware behaviour, not a limitation of this integration.
        For the same reason the switch is refused while another zone of the
        environment streams from a decoder: it would take that zone off its
        stream while Home Assistant still showed it playing.

        Raises:
            HomeAssistantError: If ``source`` is not a known source label, the
                zone has no routing address (environment 0, or not a two-digit
                amplifier), or another zone of the environment is streaming.
        """
        source_num = self._source_number(source)
        if source_num is None:
            raise HomeAssistantError(
                f'{self.entity_id}: unknown source "{source}"',
                translation_domain=DOMAIN,
                translation_key="unknown_source",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "source": str(source),
                },
            )
        if self._routing_frames(source_num) is None:
            raise HomeAssistantError(
                f"{self.entity_id}: amplifier {self._where} has no matrix routing address",
                translation_domain=DOMAIN,
                translation_key="routing_unsupported",
                translation_placeholders={
                    "entity_id": str(self.entity_id), "where": str(self._where),
                },
            )
        streamer = self._environment_streamer()
        if streamer is not None:
            environment = str(_zone_environment(self._where))
            raise HomeAssistantError(
                f"{self.entity_id}: {streamer} is already streaming in environment "
                f"{environment}, and zones in one environment share a matrix input",
                translation_domain=DOMAIN,
                translation_key="environment_busy",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "owner": streamer,
                    "environment": environment,
                },
            )

        await self._route_to(source_num)
        self.async_schedule_update_ha_state()

    # ── State and metadata mirroring ──────────────────────────────────────────

    @property
    def state(self) -> MediaPlayerState | None:
        """Mirror the decoder's playback state when streaming.

        When the zone is actively streaming or passively routed to a decoder, the
        playback state (PLAYING, PAUSED, BUFFERING) is mirrored from the
        decoder.  A directly claimed decoder also mirrors IDLE.  The zone's
        own ON/OFF state (from BTicino hardware events) is used as the fallback.
        """
        if self._attr_state == MediaPlayerState.OFF:
            return MediaPlayerState.OFF
        if self._active_decoder and self.hass:
            dec_state = self.hass.states.get(self._active_decoder)
            if dec_state and dec_state.state in (
                MediaPlayerState.PLAYING,
                MediaPlayerState.PAUSED,
                MediaPlayerState.BUFFERING,
                MediaPlayerState.IDLE,
            ):
                return MediaPlayerState(dec_state.state)
        eff_dec = self._effective_decoder
        if eff_dec and self.hass:
            dec_state = self.hass.states.get(eff_dec)
            if dec_state and dec_state.state in (
                MediaPlayerState.PLAYING,
                MediaPlayerState.PAUSED,
                MediaPlayerState.BUFFERING,
            ):
                return MediaPlayerState(dec_state.state)
        return self._attr_state

    @property
    def media_title(self) -> str | None:
        """Return the current track title from the active decoder."""
        val = self._get_decoder_attr("media_title")
        return str(val) if val is not None else None

    @property
    def media_artist(self) -> str | None:
        """Return the current artist name from the active decoder."""
        val = self._get_decoder_attr("media_artist")
        return str(val) if val is not None else None

    @property
    def media_album_name(self) -> str | None:
        """Return the current album name from the active decoder."""
        val = self._get_decoder_attr("media_album_name")
        return str(val) if val is not None else None

    @property
    def entity_picture(self) -> str | None:
        """Return the album art URL from the active decoder."""
        val = self._get_decoder_attr("entity_picture")
        return str(val) if val is not None else None

    def _get_decoder_attr(self, attr: str) -> Any:
        """Read an attribute from the active or effective decoder's current HA state.

        Args:
            attr: The state attribute name (e.g. ``"media_title"``).

        Returns:
            The attribute value, or ``None`` if no decoder is active or the
            attribute is not present.
        """
        eff_dec = self._effective_decoder
        if eff_dec and self.hass:
            dec_state = self.hass.states.get(eff_dec)
            if dec_state:
                return dec_state.attributes.get(attr)
        return None

    # ── Decoder state change listener ─────────────────────────────────────────

    @callback
    def _async_decoder_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Update UI when the active decoder changes playback state or volume.

        This fires whenever *any* configured decoder changes state (all are
        tracked).  The handler ignores events from decoders that are not
        currently assigned to this zone.

        Volume reverse-sync
        -------------------
        If the user changes the decoder volume externally (e.g. in the
        Cambridge StreamMagic app), the zone UI is updated to reflect the
        approximate zone volume (decoder_volume − pre_gain_offset).

        The ``_syncing_volume`` flag suppresses this path when the change was
        triggered by our own ``async_set_volume_level`` to avoid a feedback
        loop.
        """
        eff_dec = self._effective_decoder
        if not eff_dec:
            return
        if event.data.get("entity_id") != eff_dec:
            return

        if self._active_decoder and not self._syncing_volume:
            new_state = event.data.get("new_state")
            if new_state:
                ext_vol = new_state.attributes.get("volume_level")
                if ext_vol is not None and self._attr_volume_level != ext_vol:
                    pool = self._get_pool()
                    if pool:
                        pre_gain_pct = pool.get_pre_gain(eff_dec)
                        # Reverse the pre_gain offset to get approximate zone volume
                        zone_vol = max(0.0, float(ext_vol) - pre_gain_pct / 100.0)
                        self._attr_volume_level = zone_vol

        self.async_schedule_update_ha_state()

    # ── BTicino OWN event handlers ────────────────────────────────────────────

    async def async_update(self) -> None:
        """Request a status update from the gateway."""
        await self._gateway_handler.send_status_request(OWNSoundCommand.status(self._where))

    def _is_wake_echo(self) -> bool:
        """Return ``True`` while an OFF frame is most likely our wake sequence's own.

        A wall-switch OFF inside the same window is taken for the echo too;
        the zone's next status report corrects that rare case.
        """
        sent = self._wake_off_sent_at
        return sent is not None and time.monotonic() - sent < _WAKE_ECHO_WINDOW

    async def _async_drop_from_group(self, pool: DecoderPool, leader_id: str) -> None:
        """Drop this member from group when its source changes on the bus."""
        await pool.remove_group_member(self.entity_id)
        self.async_write_ha_state()
        runtime = self._runtime_data
        leader_ent = runtime.media_players.get(leader_id) if runtime else None
        if leader_ent:
            leader_ent.async_write_ha_state()

    @callback
    def handle_event(self, message: OWNSoundEvent) -> None:
        """Handle incoming state updates directly from the bus."""
        # `where`, not `zone`: the frame's own address, in every OWNd version.
        zone_str = message.where or ""
        if getattr(message, "is_source_event", False):
            # *16*3*10S## reports a source device switching on or off. It says
            # nothing about this zone: acting on it would turn zones on that
            # were never addressed.
            return
        # Parse matrix routing events (e.g. 121 -> route the amplifiers of
        # environment 2 to source 1). These come from wall panels or
        # scenarios.
        # NOTE: Only update the source label here, NOT the state. The F441M
        # matrix re-broadcasts routing info for ALL zones whenever ANY zone
        # changes source. If we unconditionally set state=ON here, a zone
        # that was just turned OFF would be resurrected as a ghost "On" entity
        # whenever a different zone turns on.
        routing = _parse_routing_address(zone_str)
        if routing is not None:
            source_num, environment = routing
            if _zone_environment(self._where) != environment:
                pass
            elif 1 <= source_num <= CONF_SOURCE_SLOTS:
                self._attr_source = self._source_label(source_num)
                self._warn_unconfigured_source(source_num)
                pool = self._get_pool()
                if pool:
                    leader_id = pool.get_leader(self.entity_id)
                    if leader_id:
                        runtime = self._runtime_data
                        leader_ent = runtime.media_players.get(leader_id) if runtime else None
                        expected_source = None
                        if leader_ent:
                            if leader_ent._active_decoder:
                                expected_source = pool.decoder_source(leader_ent._active_decoder)
                            if expected_source is None and leader_ent._attr_source:
                                expected_source = leader_ent._source_number(leader_ent._attr_source)
                        if expected_source is not None and expected_source != source_num:
                            LOGGER.info(
                                "%s: source changed to %d on bus while grouped with %s (source %s) — leaving group",
                                self.entity_id,
                                source_num,
                                leader_id,
                                expected_source,
                            )
                            self.hass.async_create_task(
                                self._async_drop_from_group(pool, leader_id)
                            )
            else:
                # The F441M has inputs S1-S4; anything else is not a source
                # this zone can be on, so the label is left as it was.
                LOGGER.debug(
                    "%s: ignoring routing to matrix source %d outside S1-S%d",
                    self.entity_id,
                    source_num,
                    CONF_SOURCE_SLOTS,
                )
        elif message.is_on:
            self._attr_state = MediaPlayerState.ON
        elif message.is_off:
            if self._is_wake_echo():
                # Our own wake sequence's OFF: the ON follows it.
                LOGGER.debug("%s: ignoring the OFF echo of the wake sequence", self.entity_id)
            else:
                self._attr_state = MediaPlayerState.OFF
                if not self._turning_off:
                    self.hass.async_create_task(self._async_handle_turn_off(from_bus=True))

        if message.volume is not None:
            self._attr_volume_level = message.volume / 31.0
            # Sync mute state if physical intervention drives volume to 0 / above 0
            if message.volume == 0 and not self._attr_is_volume_muted:
                self._attr_is_volume_muted = True
            elif message.volume > 0 and self._attr_is_volume_muted:
                self._attr_is_volume_muted = False

        self._publish_state()
