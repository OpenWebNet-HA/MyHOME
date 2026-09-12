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
      The matrix is trusted to already be (or to stay) routed to the decoder's
      physical source input. No explicit source-selection commands are sent.
   d. Forwards the stream URL to the backend decoder via the HA service bus.
5. State, metadata (title, artist, album art), and volume are mirrored from
   the backend decoder back to the BTicino zone entity.
6. Volume changes on the zone apply **gain staging** (decoder volume =
   zone_volume + pre_gain) to keep the analog signal level high and reduce bus noise.
7. When the zone is turned off, the decoder is released back to the pool.

Why we avoid sending routing commands
-------------------------------------
The F441M matrix cannot be source-switched hiss-free via IP on MH200-class
gateways. Direct `*16*3*1XY##` (and similar) commands produce relay clicks.
Native wall-panel commands on the bus do not have this problem.

Backward compatibility
----------------------
If no decoders are configured in Options Flow the entity behaves exactly as
before — it controls the BTicino amplifier zone via WHO=16 commands only.
``PLAY_MEDIA`` is not advertised and Music Assistant will not try to use it.
"""
import asyncio

from homeassistant.components.media_player import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event
from OWNd.message import OWNSoundCommand, OWNSoundEvent

from .const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    CONF_ENTITY,
    DOMAIN,
    LOGGER,
)
from .decoder_pool import DecoderPool
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0


def _build_pool(hass: HomeAssistant, config_entry) -> DecoderPool:
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

    for i in range(1, CONF_DECODER_SLOTS + 1):
        entity_id = options.get(CONF_DECODER_ENTITY.format(i), "").strip()
        source_num = options.get(CONF_DECODER_SOURCE.format(i), i)      # int
        pre_gain = options.get(CONF_DECODER_PRE_GAIN.format(i), 0)      # int

        if entity_id and entity_id.startswith("media_player."):
            decoder_map[entity_id] = int(source_num)   # always int — never f"Source N"
            pre_gain_map[entity_id] = int(pre_gain)

    return DecoderPool(hass, decoder_map, pre_gain_map)


async def async_setup_entry(hass: HomeAssistant, config_entry, async_add_entities):
    """Set up the MyHOME media player platform and initialise the decoder pool."""
    known_media_players: set[str] = set()

    # ── Build and store the decoder pool ─────────────────────────────────────
    pool = _build_pool(hass, config_entry)
    hass.data[DOMAIN][config_entry.data[CONF_MAC]]["decoder_pool"] = pool

    LOGGER.info(
        "MyHOME media player: decoder pool initialised with %d decoder(s)",
        len(pool.decoder_entity_ids),
    )

    # ── Restore previously discovered entities from the Entity Registry ───────
    entity_registry = er.async_get(hass)
    existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    restored_players: list[MyHOMEMediaPlayer] = []

    gateway = hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY]

    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{config_entry.data[CONF_MAC]}-", "", 1)
            parts = after_mac.split("-", 1)
            device_id = parts[-1] if len(parts) > 1 else after_mac
            zone = device_id.replace("#16", "")

            _player = MyHOMEMediaPlayer(
                hass=hass,
                name=f"Audio Zone {zone}",
                entity_name=None,
                device_id=device_id,
                who="16",
                where=zone,
                manufacturer="BTicino",
                model="Audio System",
                gateway=gateway,
            )
            known_media_players.add(device_id)
            restored_players.append(_player)

    if restored_players:
        async_add_entities(restored_players)

    # ── Discovery listener ────────────────────────────────────────────────────
    @callback
    def async_add_media_player(message):
        """Add a media player from a discovered message."""
        if not hasattr(message, "zone") or not message.zone:
            return

        # Do not discover native sources (e.g. 101, 102, 103, 104)
        if getattr(message, "is_source_event", False):
            return

        zone = str(message.zone)

        # Intercept stereo module pseudo-zones used for source selection
        # (e.g. 10x, 11x, 12x, 13x, 14x representing source 0-4 for amplifier output x)
        if len(zone) == 3 and zone[:2] in ("10", "11", "12", "13", "14"):
            point = zone[-1]
            for player_id in known_media_players:
                if player_id.split("#")[0].endswith(point):
                    async_dispatcher_send(
                        hass,
                        f"myhome_update_{config_entry.data[CONF_MAC]}_16_{player_id}",
                        message
                    )
            return


        unique_id = f"{zone}#16"

        if unique_id not in known_media_players:
            _player = MyHOMEMediaPlayer(
                hass=hass,
                name=f"Audio Zone {zone}",
                entity_name=None,
                device_id=unique_id,
                who=str(message.who),
                where=zone,
                manufacturer="BTicino",
                model="Audio System",
                gateway=hass.data[DOMAIN][config_entry.data[CONF_MAC]][CONF_ENTITY],
            )
            known_media_players.add(unique_id)
            async_add_entities([_player])
            _player.handle_event(message)

        async_dispatcher_send(hass, f"myhome_update_{config_entry.data[CONF_MAC]}_16_{unique_id}", message)

    @callback
    def _handle_media_player_message(msg):
        """Filter and forward media player messages."""
        if isinstance(msg, OWNSoundEvent):
            async_add_media_player(msg)

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"myhome_message_{config_entry.data[CONF_MAC]}",
            _handle_media_player_message,
        )
    )


async def async_unload_entry(hass, config_entry):
    """Unload media player platform."""
    return True


class MyHOMEMediaPlayer(MyHOMEEntity, MediaPlayerEntity):
    """MyHome media player with optional Dynamic Proxy for streaming services.

    When decoders are configured via Options Flow this entity acts as a proxy:
    it intercepts ``play_media`` calls from Music Assistant / Spotify, claims
    an idle backend decoder, routes the BTicino analog matrix, and mirrors
    playback state back to the zone UI.

    Without decoders configured it behaves exactly like the original entity —
    full WHO=16 hardware control with no streaming features advertised.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        manufacturer: str,
        model: str,
        gateway,
    ) -> None:
        """Initialise the MyHOME media player entity."""
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

        # ── Base hardware state ────────────────────────────────────────────
        self._attr_state = MediaPlayerState.OFF
        self._attr_source_list = ["Source 0", "Source 1", "Source 2", "Source 3", "Source 4"]
        self._attr_source = None
        self._attr_volume_level = None
        self._attr_is_volume_muted = False

        # ── Proxy state ────────────────────────────────────────────────────
        self._active_decoder: str | None = None  # entity_id of the claimed decoder
        self._syncing_volume: bool = False        # guard flag — prevents volume feedback loop
        self._pre_mute_volume: float | None = None  # volume to restore on unmute

        # ── Base hardware features (always available) ──────────────────────
        self._attr_supported_features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )

    # ── Pool helpers ──────────────────────────────────────────────────────────

    def _get_pool(self) -> DecoderPool | None:
        """Return the shared :class:`DecoderPool` from ``hass.data``.

        Returns ``None`` if the pool has not yet been initialised (e.g.
        during early startup) or if no decoders are configured.
        """
        mac_data = self.hass.data.get(DOMAIN, {}).get(self._gateway_handler.mac, {})
        return mac_data.get("decoder_pool")

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
        # Existing OWN event dispatcher connections
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_update_{self._gateway_handler.mac}_16_{self._device_id}",
                self.handle_event,
            )
        )
        # ── Decoder state listener ────────────────────────────────────────
        pool = self._get_pool()
        if pool and pool.is_configured:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    pool.decoder_entity_ids,
                    self._async_decoder_state_changed,
                )
            )

        # ── Pool rebuild listener (Options Flow saved) ────────────────────
        # When the user configures decoders via the UI, supported_features
        # changes.  We must fire a state update so Music Assistant re-reads
        # our features and discovers the new PLAY_MEDIA capability.
        @callback
        def _pool_updated(*args) -> None:
            """Re-publish state after decoder pool rebuild."""
            LOGGER.debug("%s: decoder pool updated — re-publishing features", self.entity_id)
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_pool_updated_{self._gateway_handler.mac}",
                _pool_updated,
            )
        )

    # ── Proxy: play_media ─────────────────────────────────────────────────────

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
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

        # 1. Claim an idle decoder (thread-safe via asyncio.Lock)
        result = await pool.claim(self.entity_id)
        if result is None:
            raise HomeAssistantError(
                f"{self.entity_id}: All audio matrix inputs are currently in use by other rooms!"
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

        # 3. Activate the BTicino zone amplifier.
        #
        # "Hardware Routing First" model:
        # The matrix routing is managed physically (wall panels) or by a
        # power-on scenario on the gateway. The integration deliberately does
        # NOT send source-selection commands (*16*3*10X## / *16*3*1XY##)
        # because they produce audible hiss on MH200-class gateways.
        #
        # A simple OFF → ON is sufficient. The F441M restores the last
        # (physically or scenario-set) source assignment and volume level.
        if self._attr_state != MediaPlayerState.ON:
            await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))
            await asyncio.sleep(0.5)
            await self._gateway_handler.send(OWNSoundCommand.turn_on(self._where))
            await asyncio.sleep(0.5)
            self._attr_state = MediaPlayerState.ON
            self.async_write_ha_state()

        # 4. Forward the stream URL to the backend decoder
        service_data: dict = {
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
        except Exception as err:  # pylint: disable=broad-except
            LOGGER.error(
                "%s: failed to forward play_media to %s: %s — releasing decoder",
                self.entity_id,
                decoder_id,
                err,
            )
            await pool.release(self.entity_id)
            self._active_decoder = None
            raise HomeAssistantError(
                f"{self.entity_id}: decoder {decoder_id} failed to start playback: {err}"
            ) from err

        self.async_schedule_update_ha_state()

    # ── Transport controls ────────────────────────────────────────────────────

    async def _forward_to_decoder(self, service: str) -> None:
        """Forward a media_player service call to the active backend decoder.

        Args:
            service: HA service name e.g. ``"media_pause"``.
        """
        if self._active_decoder:
            await self.hass.services.async_call(
                "media_player", service, {"entity_id": self._active_decoder}
            )

    async def async_media_pause(self) -> None:
        """Pause playback on the active decoder."""
        await self._forward_to_decoder("media_pause")

    async def async_media_play(self) -> None:
        """Resume playback on the active decoder."""
        await self._forward_to_decoder("media_play")

    async def async_media_stop(self) -> None:
        """Stop playback on the active decoder."""
        await self._forward_to_decoder("media_stop")

    async def async_media_next_track(self) -> None:
        """Skip to next track on the active decoder."""
        await self._forward_to_decoder("media_next_track")

    async def async_media_previous_track(self) -> None:
        """Go to previous track on the active decoder."""
        await self._forward_to_decoder("media_previous_track")

    # ── Zone on / off ─────────────────────────────────────────────────────────

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the zone amplifier on.

        Uses a simple OFF → ON sequence.  The F441M matrix remembers
        its source routing from the wall-panel configuration; sending
        explicit source-selection commands corrupts the audio path.
        """
        if self._attr_state != MediaPlayerState.ON:
            await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))
            await asyncio.sleep(0.5)
            await self._gateway_handler.send(OWNSoundCommand.turn_on(self._where))
            await asyncio.sleep(0.5)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the zone amplifier off and release any claimed decoder.

        Stops playback on the decoder before releasing it so that it returns
        to the idle pool in a clean state.
        """
        # Optimistically set state to OFF so the UI updates immediately.
        # The gateway may not echo the OFF event back via the event session,
        # leaving the dashboard card stuck on "On" indefinitely.
        self._attr_state = MediaPlayerState.OFF

        await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))

        if self._active_decoder:
            # Stop playback on the decoder first
            try:
                await self.hass.services.async_call(
                    "media_player", "media_stop", {"entity_id": self._active_decoder}
                )
            except Exception:  # pylint: disable=broad-except
                pass  # Best-effort — don't prevent turn-off if the decoder is unreachable

            pool = self._get_pool()
            if pool:
                await pool.release(self.entity_id)
            self._active_decoder = None

        self.async_schedule_update_ha_state()

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

    async def async_select_source(self, source: str) -> None:
        """Source selection is intentionally ignored.

        The recommended model is "Hardware Routing First":

        - Wire your streaming decoder(s) to the desired physical source input(s).
        - Use physical wall panels (or a gateway power-on/startup scenario) to
          set the default routing for zones to that source.
        - The integration only activates zones with a simple OFF→ON. It never
          sends matrix source-routing commands over IP because they produce
          audible hiss on MH200-class gateways.

        Changing the active source from Home Assistant is not supported for
        the same reason. Use the physical controls or reconfigure your
        power-on scenario instead.

        The source attribute is still updated when the bus reports routing
        changes (e.g. from wall panels).
        """
        LOGGER.info(
            "%s: Source selection via HA is ignored (Hardware Routing First model). "
            "Use wall panels or a gateway power-on scenario instead. "
            "Ignoring select_source('%s').",
            self.entity_id,
            source,
        )

    # ── State and metadata mirroring ──────────────────────────────────────────

    @property
    def state(self) -> MediaPlayerState:
        """Mirror the decoder's playback state when streaming.

        When the zone is actively streaming (``_active_decoder`` is set), the
        playback state (PLAYING, PAUSED, BUFFERING, IDLE) is mirrored from the
        decoder.  The zone's own ON/OFF state (from BTicino hardware events)
        is used as the fallback.
        """
        if self._attr_state == MediaPlayerState.OFF:
            return MediaPlayerState.OFF
        if self._active_decoder:
            dec_state = self.hass.states.get(self._active_decoder)
            if dec_state and dec_state.state in (
                MediaPlayerState.PLAYING,
                MediaPlayerState.PAUSED,
                MediaPlayerState.BUFFERING,
                MediaPlayerState.IDLE,
            ):
                return dec_state.state  # type: ignore[return-value]
        return self._attr_state

    @property
    def media_title(self) -> str | None:
        """Return the current track title from the active decoder."""
        return self._get_decoder_attr("media_title")

    @property
    def media_artist(self) -> str | None:
        """Return the current artist name from the active decoder."""
        return self._get_decoder_attr("media_artist")

    @property
    def media_album_name(self) -> str | None:
        """Return the current album name from the active decoder."""
        return self._get_decoder_attr("media_album_name")

    @property
    def entity_picture(self) -> str | None:
        """Return the album art URL from the active decoder."""
        return self._get_decoder_attr("entity_picture")

    def _get_decoder_attr(self, attr: str):
        """Read an attribute from the active decoder's current HA state.

        Args:
            attr: The state attribute name (e.g. ``"media_title"``).

        Returns:
            The attribute value, or ``None`` if no decoder is active or the
            attribute is not present.
        """
        if self._active_decoder:
            dec_state = self.hass.states.get(self._active_decoder)
            if dec_state:
                return dec_state.attributes.get(attr)
        return None

    # ── Decoder state change listener ─────────────────────────────────────────

    @callback
    def _async_decoder_state_changed(self, event) -> None:
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
        if not self._active_decoder:
            return
        if event.data.get("entity_id") != self._active_decoder:
            return

        if not self._syncing_volume:
            new_state = event.data.get("new_state")
            if new_state:
                ext_vol = new_state.attributes.get("volume_level")
                if ext_vol is not None and self._attr_volume_level != ext_vol:
                    pool = self._get_pool()
                    if pool:
                        pre_gain_pct = pool.get_pre_gain(self._active_decoder)
                        # Reverse the pre_gain offset to get approximate zone volume
                        zone_vol = max(0.0, float(ext_vol) - pre_gain_pct / 100.0)
                        self._attr_volume_level = zone_vol

        self.async_schedule_update_ha_state()

    # ── BTicino OWN event handlers ────────────────────────────────────────────

    async def async_update(self) -> None:
        """Request a status update from the gateway."""
        await self._gateway_handler.send_status_request(OWNSoundCommand.status(self._where))

    @callback
    def handle_event(self, message: OWNSoundEvent) -> None:
        """Handle incoming state updates directly from the bus."""
        zone_str = str(message.zone)
        # Parse matrix routing events (e.g. 111 -> Route Source 1 to Zone x1,
        # or 121 -> Source 2). These come from wall panels or scenarios.
        # NOTE: Only update the source label here, NOT the state. The F441M
        # matrix re-broadcasts routing info for ALL zones whenever ANY zone
        # changes source. If we unconditionally set state=ON here, a zone
        # that was just turned OFF would be resurrected as a ghost "On" entity
        # whenever a different zone turns on.
        if len(zone_str) == 3 and zone_str[:2] in ("10", "11", "12", "13", "14"):
            if self._where.endswith(zone_str[-1]):
                source_num = int(zone_str[1])
                self._attr_source = f"Source {source_num}"
        elif message.is_on:
            self._attr_state = MediaPlayerState.ON
        elif message.is_off:
            self._attr_state = MediaPlayerState.OFF
            if self._active_decoder:
                pool = self._get_pool()
                if pool:
                    self.hass.async_create_task(pool.release(self.entity_id))
                self._active_decoder = None

        if message.volume is not None:
            self._attr_volume_level = message.volume / 31.0
            # Sync mute state if physical intervention drives volume to 0 / above 0
            if message.volume == 0 and not self._attr_is_volume_muted:
                self._attr_is_volume_muted = True
            elif message.volume > 0 and self._attr_is_volume_muted:
                self._attr_is_volume_muted = False

        if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
            try:
                self.async_schedule_update_ha_state()
            except RuntimeError:
                pass
