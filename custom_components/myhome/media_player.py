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

Module layout
-------------
The zone entity is one class cut into layers, each in its own module and each
extending the one above it in this list (they are a chain, not independent
mixins). The layers reach each other through ``self``; calls that go down the
chain are declared as hooks on ``ZoneBase``::

    media_player_zone     ZoneBase          state every layer reads, pool access
    media_player_source   ZoneSourceLayer   source names, matrix routing frames
    media_player_decoder  ZoneDecoderLayer  decoder state mirroring, anti-hiss auto-off
    media_player_group    ZoneGroupLayer    join / hand-over / park / wake of a group
    media_player          MyHOMEMediaPlayer setup, service entry points, power, bus events

``media_player_routing`` holds the pure address helpers and ``media_player_pool``
builds the :class:`~.decoder_pool.DecoderPool` from the options.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from OWNd.message import OWNSoundCommand, OWNSoundEvent

from .const import (
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SOURCE_TUNER,
    DOMAIN,
    LOGGER,
    SERVICE_TUNER_SEEK_DOWN,
    SERVICE_TUNER_SEEK_UP,
)
from .data import MyHOMEConfigEntry
from .decoder_pool import EnvironmentBusyError
from .discovery import Address, DeviceContext, PlatformDiscovery
from .media_player_group import ZoneGroupLayer
from .media_player_pool import (
    STREAM_INCOMPATIBLE_PLATFORMS,
    build_pool,
    sync_multiple_audio_gateways,
)
from .media_player_routing import parse_routing_address, route_pseudo_zones, zone_environment
from .sound_source import MyHOMESoundSource, source_address

PARALLEL_UPDATES = 0

# The amplifier wake sequence starts with an OFF frame, and the gateway reports
# that frame back on the event session like any other bus traffic. An OFF that
# arrives this soon after a wake is our own and must not tear the zone down.
_WAKE_ECHO_WINDOW = 3.0  # seconds
_RESTORE_CONFIRM_WINDOW = 120.0  # seconds a restored zone has to show up on the bus


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the MyHOME media player platform and initialise the decoder pool."""
    runtime = config_entry.runtime_data

    # ── Build and store the decoder pool ─────────────────────────────────────
    pool = build_pool(hass, config_entry)
    # The amplifiers keep playing through a restart or reload: pick the groups
    # up again, and let the bus vouch for each zone (or not) as it reports.
    await pool.async_load()
    # A zone renamed or deleted meanwhile cannot report under its old id.
    ent_reg = er.async_get(hass)
    await pool.drop_unregistered(lambda entity_id: ent_reg.async_get(entity_id) is not None)
    runtime.decoder_pool = pool
    if pool.has_unconfirmed:

        @callback
        def _drop_unconfirmed(_now: datetime) -> None:
            hass.async_create_task(pool.drop_unconfirmed())

        config_entry.async_on_unload(
            async_call_later(hass, _RESTORE_CONFIRM_WINDOW, _drop_unconfirmed)
        )

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

    # Declared tuner sources exist before any bus traffic; zones are discovered.
    sound_sources = _build_sound_sources(hass, config_entry, runtime.gateway)
    for source in sound_sources:
        source.async_on_remove(
            runtime.router.subscribe("16", [source.device_key], source.handle_event)
        )
    if sound_sources:
        async_add_entities(sound_sources)

    discovery = PlatformDiscovery(
        hass,
        config_entry,
        async_add_entities,
        platform=Platform.MEDIA_PLAYER,
        who="16",
        event_type=OWNSoundEvent,
        build=build,
        address=_zone_address,
        pre_message=route_pseudo_zones(runtime.router),
        route_keys=_sound_route_keys,
        key_suffix="#16",
    )
    # Audio zones are keyed "<zone>#16" in unique ids; the registry restore reads that key back.
    discovery.start()

    platform = entity_platform.current_platform.get()
    if platform is not None:
        platform.async_register_entity_service(
            SERVICE_TUNER_SEEK_UP,
            {},
            "async_seek_up",
        )
        platform.async_register_entity_service(
            SERVICE_TUNER_SEEK_DOWN,
            {},
            "async_seek_down",
        )


def _zone_address(message: Any) -> Address | None:
    """Sound-system frames address a zone (amplifier); sources are never devices.

    Reads ``where``, not ``zone``: ``where`` is the frame's address in every
    OWNd version, whereas ``zone`` is OWNd's reading of it, and a routing frame
    (``1ES``) must reach ``route_pseudo_zones`` whatever OWNd calls it.
    """
    zone = getattr(message, "where", None)
    if not zone or getattr(message, "is_source_event", False):
        return None
    return Address(str(zone), key_suffix="#16")


def _sound_route_keys(message: Any, address: Address | None) -> list[str]:
    """Return the entity keys a WHO=16 frame belongs to.

    Source frames carry no zone address, so without an explicit key they would
    be dropped before reaching a declared tuner entity.
    """
    if getattr(message, "is_source_event", False):
        where = str(getattr(message, "zone", "") or "")
        return [f"{where}#16"] if where else []
    return [address.key] if address is not None else []


def _build_sound_sources(
    hass: HomeAssistant, config_entry: MyHOMEConfigEntry, gateway: Any
) -> list[MyHOMESoundSource]:
    """Create an entity for every matrix input the user declared to be a tuner."""
    options = config_entry.options
    sources: list[MyHOMESoundSource] = []
    for i in range(1, CONF_SOURCE_SLOTS + 1):
        if not options.get(CONF_SOURCE_TUNER.format(i)):
            continue
        where = source_address(i)
        name = str(options.get(CONF_SOURCE_NAME.format(i), "") or "").strip()
        sources.append(
            MyHOMESoundSource(
                hass=hass,
                name=name or f"Audio Source {i}",
                device_id=f"{where}#16",
                who="16",
                where=where,
                manufacturer="BTicino",
                model="Audio Source",
                gateway=gateway,
            )
        )
    return sources


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    """Unload media player platform."""
    return True


class MyHOMEMediaPlayer(ZoneGroupLayer):
    """MyHome media player with optional Dynamic Proxy for streaming services.

    When decoders are configured via Options Flow this entity acts as a proxy:
    it intercepts ``play_media`` calls from Music Assistant / Spotify, claims
    an idle backend decoder, routes the BTicino analog matrix, and mirrors
    playback state back to the zone UI.

    Without decoders configured it behaves exactly like the original entity —
    full WHO=16 hardware control with no streaming features advertised.
    """

    def diagnostics_state(self) -> dict[str, Any]:
        """Return what a bug report needs to know about this zone (no entity ids)."""
        return {
            "state": str(self._attr_state) if self._attr_state is not None else None,
            "source": self._source_number(self._attr_source) if self._attr_source else None,
            "volume_level": self._attr_volume_level,
            "is_volume_muted": self._attr_is_volume_muted,
            "has_decoder": self._active_decoder is not None,
            "parked": self._parked,
            "wake_pending": self._wake_pending,
            "status_seen": self._status_seen,
        }

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Return supported features, adding streaming controls when decoders are configured.

        Music Assistant inspects ``supported_features`` to decide whether this
        entity is a valid playback target.  Streaming features are only
        advertised when at least one decoder is configured, which keeps the
        entity backward-compatible for users without a streaming setup.
        """
        features = self._attr_supported_features
        if zone_environment(self._where) in (None, "0"):
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

    async def async_added_to_hass(self) -> None:
        """Register listeners when entity is added to Home Assistant."""
        self._register_availability_listener()
        runtime = self._runtime_data
        if runtime is not None:
            runtime.media_players[self.entity_id] = self
        sync_multiple_audio_gateways(self.hass)

        # ── Decoder state listener ────────────────────────────────────────
        self._track_decoders()
        self.async_on_remove(self._untrack_decoders)

        # Ask the bus whether this amplifier is on when the startup sweep will
        # not: profiles that skip the collective WHO=16 status request (the
        # MH200's) would otherwise leave every room looking off after a
        # restart, whatever the amplifier is doing. Gateways that do send it
        # get one frame for all zones instead of one per zone.
        if not self._gateway_handler.profile_supports_who(16):
            await self.async_update()

    async def async_will_remove_from_hass(self) -> None:
        """Drop this zone from the pool's books when the entity goes away.

        Removal happens on every integration reload, options change and
        entity_id rename, none of which is a request to silence a room, so no
        frame is sent: the amplifiers and the decoder keep playing and only
        the group bookkeeping is cleared. A pending group-leave OFF is
        cancelled outright rather than sent, for the same reason.
        """
        self._cancel_pending_off()
        self._cancel_auto_off()
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

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Play media, showing a parked group as on for as long as the wake takes.

        Whatever way the play ends, no room is left reporting on while its
        amplifier is still off; see :meth:`_async_play_media` for the steps.
        """
        pool = self._get_pool()
        group = self._group_entities(pool) if pool and pool.is_configured else []
        if pool is not None and group:
            self._begin_wake_of_parked_group(pool)
        try:
            await self._async_play_media(media_type, media_id, **kwargs)
        finally:
            for ent in group:
                if ent._wake_pending:
                    ent._wake_pending = False
                    ent.async_write_ha_state()

    async def _async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
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
        self._cancel_auto_off()

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
                environment=zone_environment(self._where) if route else None,
                exclude=exclude,
            )
        except EnvironmentBusyError as err:
            raise self._environment_busy_error(err.owner, err.environment) from err
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

        target_decoder = decoder_id
        companion_id = self._streaming_target(decoder_id)
        if companion_id and companion_id != decoder_id:
            platform = self._decoder_platform(decoder_id)
            accepted = STREAM_INCOMPATIBLE_PLATFORMS.get(platform or "", frozenset())
            if media_type not in accepted:
                target_decoder = companion_id

        # 2. Wake the target decoder. IDLE decoders are already ready to play.
        dec_state = self.hass.states.get(target_decoder)
        if dec_state and dec_state.state == MediaPlayerState.OFF:
            await self.hass.services.async_call(
                "media_player", "turn_on", {"entity_id": target_decoder}
            )
            # Poll until the decoder wakes up (max 5 seconds)
            for _ in range(10):
                await asyncio.sleep(0.5)
                dec_state = self.hass.states.get(target_decoder)
                if dec_state and dec_state.state != MediaPlayerState.OFF:
                    break
            else:
                LOGGER.warning(
                    "%s: decoder %s did not wake up within 5 s",
                    self.entity_id,
                    target_decoder,
                )
                await self._async_release_after_failure(pool)
                raise HomeAssistantError(
                    f"{self.entity_id}: decoder {target_decoder} did not wake up within 5 seconds",
                    translation_domain=DOMAIN,
                    translation_key="decoder_wake_timeout",
                    translation_placeholders={
                        "entity_id": str(self.entity_id),
                        "decoder": str(target_decoder),
                    },
                )

        # 3. Activate the BTicino zone amplifier and route it to the decoder.
        #
        # The zone has to listen to the input this decoder is wired to,
        # otherwise the stream plays into a room that is listening elsewhere.
        # Unconfigured installations keep trusting the wall-panel routing.
        await self._async_wake_zone()
        self.async_write_ha_state()
        sent: set[str] = set()
        if route:
            await self._route_to(source_num, sent)

        # If this zone is a group leader, wake and route the members too. The
        # gateway takes ~0.8 s per frame, so this runs next to the stream start
        # instead of in front of it: the leader is audible right away and the
        # members join as their frames go out.
        members_task = None
        if pool and pool.get_members(self.entity_id):
            members_task = self.hass.async_create_background_task(
                self._async_wake_members(pool, decoder_id, source_num, route, sent),
                f"{self.entity_id} wake group members",
            )

        # 4. Forward the stream URL to the target decoder (companion or primary)
        service_data: dict[str, Any] = {
            "entity_id": target_decoder,
            "media_content_type": media_type,
            "media_content_id": media_id,
        }
        for key in ("announce", "enqueue", "extra"):
            if key in kwargs:
                service_data[key] = kwargs[key]

        # Error recovery: if the play_media call fails, release the decoder so
        # it does not remain permanently "stuck" as busy.
        try:
            # Blocking: a service error only reaches this except when the call is
            # awaited to completion, and the release below depends on it.
            await self.hass.services.async_call("media_player", "play_media", service_data, blocking=True)
        except Exception as err:
            LOGGER.error(
                "%s: failed to forward play_media to %s: %s — releasing decoder",
                self.entity_id,
                target_decoder,
                err,
            )
            if members_task is not None:
                members_task.cancel()
                await asyncio.gather(members_task, return_exceptions=True)
            await self._async_release_after_failure(pool)
            raise HomeAssistantError(
                f"{self.entity_id}: decoder {target_decoder} failed to start playback: {err}",
                translation_domain=DOMAIN,
                translation_key="decoder_start_failed",
                translation_placeholders={
                    "entity_id": str(self.entity_id),
                    "decoder": str(target_decoder),
                    "error": str(err),
                },
            ) from err

        if members_task is not None:
            await members_task
        self.async_schedule_update_ha_state()

    async def async_media_pause(self) -> None:
        """Pause playback on the active decoder."""
        await self._forward_to_decoder("media_pause")

    async def async_media_play(self) -> None:
        """Resume playback on the active decoder."""
        self._cancel_auto_off()
        members_task = None
        if self._parked:
            members_task = await self._async_unpark_group()
        elif self._attr_state == MediaPlayerState.OFF:
            await self._async_wake_zone()
        await self._async_finish_group_wake(members_task, self._forward_to_decoder("media_play"))

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

    async def _async_wake_zone(self) -> None:
        """Wake a zone amplifier using the hardware-required OFF → ON sequence.

        The gateway reports the OFF back on the event session. The time it was
        sent is kept so :meth:`handle_event` can tell that echo from a wall
        switch; treating it as a real OFF would release the decoder this
        zone just claimed, or drop the member that is joining a group.

        Cancels a pending group-leave OFF unconditionally, even when the zone
        is already on and the wake sequence below is skipped: this is called
        exactly where a zone is put back to work, which is what a pending OFF
        is waiting to find out about.
        """
        self._cancel_pending_off()
        was_parked = self._parked
        self._parked = False
        self._cancel_auto_off()  # put to work: a timer from before no longer applies
        self._mark_status_seen()  # switched on from here: not a leftover of before
        if self._attr_state != MediaPlayerState.ON or was_parked:
            self._wake_off_sent_at = time.monotonic()
            try:
                # No pause between the two: the command worker waits for the
                # gateway's ACK (~0.8 s per audio frame) after each frame.
                written = await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))
                self._stamp_wake_echo_when_written(written)
                await self._gateway_handler.send(OWNSoundCommand.turn_on(self._where))
            except BaseException:
                self._parked = was_parked  # cancelled mid-wake: the amplifier is not on
                raise
            self._attr_state = MediaPlayerState.ON
        self._wake_pending = False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the zone amplifier on.

        Uses a simple OFF → ON sequence.  When a default source is configured
        for this zone's environment the matrix is routed there as well, so a
        room left on a stale input by a wall panel comes back on the right
        source.  Without that setting the existing routing is kept untouched,
        and a zone that is already on is never re-routed: the route is shared
        by the whole environment and may be carrying a stream.
        """
        self._cancel_auto_off()
        if self._parked:
            await self._async_finish_group_wake(await self._async_unpark_group())
        elif self._attr_state != MediaPlayerState.ON:
            await self._async_wake_zone()
            await self._apply_default_source()

    async def _async_handle_turn_off(self, from_bus: bool = False) -> None:
        """Coordinated turn-off sequence for zones, groups, and decoders."""
        if self._auto_off_unsub:
            self._auto_off_unsub()
            self._auto_off_unsub = None
        if self._turning_off:
            return
        # A leader that goes off while rooms are still listening hands the
        # group on instead of stopping it (same path as unjoin). A parked
        # group is silent already: turning its leader off is the "I want
        # silence" and dissolves it, as before.
        handover_pool = self._get_pool()
        if handover_pool is not None and not self._parked:
            handover_members = handover_pool.get_members(self.entity_id)
            if handover_members:
                await self._async_hand_over_leadership(handover_pool, handover_members, from_bus)
                return
        self._turning_off = True
        self._parked = False
        self._wake_pending = False
        # A room that merely leaves a group does not change what the others listen to;
        # a leader that stops does.
        leaving_pool = self._get_pool()
        if self._active_decoder or (leaving_pool is not None and leaving_pool.is_leader(self.entity_id)):
            self._forget_recent_routing()
        try:
            self._attr_state = MediaPlayerState.OFF
            if not from_bus:
                await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))

            pool = self._get_pool()
            runtime = self._runtime_data

            if self._active_decoder:
                target_dec = self._streaming_target(self._active_decoder) or self._active_decoder
                try:
                    await self.hass.services.async_call(
                        "media_player", "media_stop", {"entity_id": target_dec}
                    )
                except Exception as err:
                    LOGGER.debug(
                        "%s: failed to stop streaming decoder %s: %s",
                        self.entity_id,
                        target_dec,
                        err,
                    )
                if target_dec != self._active_decoder:
                    try:
                        await self.hass.services.async_call(
                            "media_player", "media_stop", {"entity_id": self._active_decoder}
                        )
                    except Exception as err:
                        LOGGER.debug(
                            "%s: failed to stop hardware decoder %s: %s",
                            self.entity_id,
                            self._active_decoder,
                            err,
                        )

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
                    target_dec = (
                        self._streaming_target(self._active_decoder) or self._active_decoder
                    )
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_set",
                        {
                            "entity_id": target_dec,
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
            # A repeated mute must not remember the 0.0 the first one produced.
            if not self._attr_is_volume_muted and (self._attr_volume_level or 0.0) > 0.0:
                self._pre_mute_volume = self._attr_volume_level
            elif self._pre_mute_volume is None:
                self._pre_mute_volume = 0.5
            await self.async_set_volume_level(0.0)
        else:
            restore_volume = self._pre_mute_volume if self._pre_mute_volume is not None else 0.3
            await self.async_set_volume_level(restore_volume)

        self._attr_is_volume_muted = mute

        # Propagate mute to decoder if it supports the attribute
        if self._active_decoder:
            target_dec = self._streaming_target(self._active_decoder) or self._active_decoder
            dec_state = self.hass.states.get(target_dec)
            if dec_state and dec_state.attributes.get("is_volume_muted") is not None:
                try:
                    await self.hass.services.async_call(
                        "media_player",
                        "volume_mute",
                        {"entity_id": target_dec, "is_volume_muted": mute},
                    )
                except Exception:  # pylint: disable=broad-except
                    pass  # Not all decoders support mute; volume=0 covers the rest

        self.async_schedule_update_ha_state()

    async def async_update(self) -> None:
        """Request a status update from the gateway."""
        await self._gateway_handler.send_status_request(OWNSoundCommand.status(self._where))

    def _stamp_wake_echo_when_written(self, written: Any) -> None:
        """Start the echo window when the OFF is written, not when it is queued.

        ``send`` only queues; behind a few audio frames the OFF reaches the bus
        seconds later, and its echo would otherwise arrive after the window.
        """
        if not isinstance(written, asyncio.Future):
            return

        def _on_written(fut: asyncio.Future[float]) -> None:
            if not fut.cancelled() and fut.exception() is None:
                self._wake_off_sent_at = fut.result()

        written.add_done_callback(_on_written)

    def _is_wake_echo(self) -> bool:
        """Return ``True`` while an OFF frame is most likely our wake sequence's own.

        A wall-switch OFF inside the same window is taken for the echo too;
        the zone's next status report corrects that rare case.
        """
        sent = self._wake_off_sent_at
        return sent is not None and time.monotonic() - sent < _WAKE_ECHO_WINDOW

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
        trigger_auto_join = False
        routing = parse_routing_address(zone_str)
        if routing is not None:
            source_num, environment = routing
            if zone_environment(self._where) != environment:
                pass
            elif 1 <= source_num <= CONF_SOURCE_SLOTS:
                previous_source = self._source_number(self._attr_source) if self._attr_source else None
                self._attr_source = self._source_label(source_num)
                self._warn_unconfigured_source(source_num)
                dropping = False
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
                            dropping = True
                            self.hass.async_create_task(
                                self._async_drop_from_group(pool, leader_id)
                            )
                    elif pool.is_leader(self.entity_id) or self._active_decoder or pool.owned_decoder(self.entity_id):
                        active_dec = self._active_decoder or pool.owned_decoder(self.entity_id)
                        expected_source = None
                        if active_dec:
                            expected_source = pool.decoder_source(active_dec)
                        if expected_source is None:
                            expected_source = previous_source
                        if expected_source is not None and expected_source != source_num:
                            LOGGER.info(
                                "%s: leader source changed to %d on bus while streaming on source %s — leaving group/session",
                                self.entity_id,
                                source_num,
                                expected_source,
                            )
                            dropping = True
                            self.hass.async_create_task(
                                self._async_drop_leader_on_source_change(pool, source_num, environment)
                            )
                if not dropping and self._attr_state == MediaPlayerState.ON:
                    trigger_auto_join = True
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
            self._cancel_pending_off()  # confirmed on: nothing left to time out
            self._parked = False
            self._attr_state = MediaPlayerState.ON
            if not self._status_seen:
                self._mark_status_seen()
                self._restore_claim()
                self._check_stray_at_startup()
            trigger_auto_join = True
        elif message.is_off:
            if self._is_wake_echo():
                # Our own wake sequence's OFF: the ON follows it.
                LOGGER.debug("%s: ignoring the OFF echo of the wake sequence", self.entity_id)
            elif self._parked:
                # The anti-hiss OFF we sent ourselves: the room stays in its group.
                self._mark_status_seen()
                self._attr_state = MediaPlayerState.OFF
            else:
                self._mark_status_seen()
                # A real OFF (wall switch or otherwise) makes any pending
                # group-leave OFF redundant; _async_handle_turn_off below
                # covers the same cleanup.
                self._cancel_pending_off()
                self._attr_state = MediaPlayerState.OFF
                if not self._turning_off:
                    self.hass.async_create_task(self._async_handle_turn_off(from_bus=True))

        what = getattr(message, "what", getattr(message, "_what", None))
        is_volume_up = False
        if what is not None:
            try:
                is_volume_up = 1001 <= int(what) <= 1015
            except (ValueError, TypeError):
                pass

        if not message.is_off and (is_volume_up or (message.volume is not None and message.volume > 0)):
            if self._attr_state != MediaPlayerState.ON or self._parked:
                self._cancel_pending_off()
                self._parked = False
                self._attr_state = MediaPlayerState.ON
                if not self._status_seen:
                    self._mark_status_seen()
                    self._restore_claim()
                    self._check_stray_at_startup()
                trigger_auto_join = True

        if message.volume is not None:
            self._attr_volume_level = message.volume / 31.0
            # Volume 0 is not a mute: only async_mute_volume() mutes. Music
            # Assistant locks the slider of a muted player and leaves it out of
            # the grouped volume, so a room turned down to 0 would be stuck
            # there. A volume raised above 0 (a wall panel, say) does end a mute.
            if message.volume > 0 and self._attr_is_volume_muted:
                self._attr_is_volume_muted = False

        if trigger_auto_join:
            self.hass.async_create_task(self._async_auto_join_active_stream())

        self._publish_state()

    async def async_seek_up(self) -> None:
        """Seek forward on the tuner; only valid on tuner source entities."""
        raise HomeAssistantError(
            f"{self.entity_id}: seek is only supported on tuner source entities",
            translation_domain=DOMAIN,
            translation_key="seek_not_supported",
            translation_placeholders={"entity_id": str(self.entity_id)},
        )

    async def async_seek_down(self) -> None:
        """Seek backward on the tuner; only valid on tuner source entities."""
        raise HomeAssistantError(
            f"{self.entity_id}: seek is only supported on tuner source entities",
            translation_domain=DOMAIN,
            translation_key="seek_not_supported",
            translation_placeholders={"entity_id": str(self.entity_id)},
        )
