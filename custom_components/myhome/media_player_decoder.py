"""A MyHOME audio zone mirroring the decoder it listens to, and the anti-hiss auto-off."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.core import Event, EventStateChangedData, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import LOGGER
from .decoder_pool import DecoderPool
from .media_player_pool import STREAM_INCOMPATIBLE_PLATFORMS
from .media_player_source import ZoneSourceLayer

# Anti-hiss auto-off: how long a room stays on after the decoder it hears
# stops (idle, standby or off) or pauses.
_AUTO_OFF_IDLE_DELAY = 3.0  # seconds
_AUTO_OFF_PAUSED_DELAY = 60.0  # seconds

# Decoder states that mean music is coming out, or about to: a track change
# or a Spotify Connect handshake passes through "buffering".
_DECODER_PLAYING_STATES = frozenset({
    MediaPlayerState.PLAYING,
    MediaPlayerState.BUFFERING,
    "playing",
    "buffering",
})


class ZoneDecoderLayer(ZoneSourceLayer):
    """Playback state, metadata and volume of the decoder a zone hears."""

    @property
    def _effective_decoder(self) -> str | None:
        """Return the active decoder, or the decoder associated with the current source."""
        if self._active_decoder:
            return self._active_decoder
        pool = self._get_pool()
        source_num = (
            self._source_number(self._attr_source)
            if self._attr_source
            else self._default_source()
        )
        if pool and source_num is not None:
            assigned = pool.get_assignment(self.entity_id)
            # A group member listens to the leader's decoder only while its
            # environment is routed there. Without automatic routing it may
            # still be on another input, and an input never reported on the
            # bus is not evidence either way, so only a known match mirrors.
            if assigned and source_num == pool.decoder_source(assigned):
                return assigned
        if self._attr_state == MediaPlayerState.ON and source_num is not None and pool:
            return pool.get_decoder_for_source(source_num)
        return None

    def _decoders_refusing(self, pool: DecoderPool, media_type: str) -> set[str]:
        """Return the decoders whose integration cannot play media_type."""
        refusing: set[str] = set()
        for decoder_id in pool.stream_incompatible:
            companion_id = self._streaming_target(decoder_id)
            if companion_id and companion_id != decoder_id:
                continue
            accepted = STREAM_INCOMPATIBLE_PLATFORMS.get(
                self._decoder_platform(decoder_id) or "", frozenset()
            )
            if media_type not in accepted:
                refusing.add(decoder_id)
        return refusing

    @callback
    def _track_decoders(self) -> None:
        """Watch the state of the current pool's decoders, replacing any earlier watch."""
        self._untrack_decoders()
        pool = self._get_pool()
        if pool and hasattr(pool, "companion_map") and isinstance(pool.companion_map, dict):
            self._companion_cache = dict(pool.companion_map)
        else:
            self._companion_cache = {}
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
            target_dec = self._streaming_target(eff_dec) or eff_dec
            await self.hass.services.async_call("media_player", service, {"entity_id": target_dec})
            if target_dec != eff_dec and service == "media_stop":
                try:
                    await self.hass.services.async_call(
                        "media_player", service, {"entity_id": eff_dec}
                    )
                except Exception as err:
                    LOGGER.debug(
                        "%s: failed to forward stop to hardware decoder %s: %s",
                        self.entity_id,
                        eff_dec,
                        err,
                    )

    def _resolve_playback_state(
        self, decoder_id: str, allow_idle: bool = False
    ) -> MediaPlayerState | None:
        """Resolve playback state from decoder and optional streaming companion."""
        if not self.hass:
            return None
        companion = self._streaming_target(decoder_id)
        if companion and companion != decoder_id:
            comp_state = self.hass.states.get(companion)
            if comp_state and comp_state.state in (
                MediaPlayerState.PLAYING,
                MediaPlayerState.PAUSED,
                MediaPlayerState.BUFFERING,
            ):
                return MediaPlayerState(comp_state.state)

        dec_state = self.hass.states.get(decoder_id)
        if dec_state:
            valid_states = (
                (
                    MediaPlayerState.PLAYING,
                    MediaPlayerState.PAUSED,
                    MediaPlayerState.BUFFERING,
                    MediaPlayerState.IDLE,
                )
                if allow_idle
                else (
                    MediaPlayerState.PLAYING,
                    MediaPlayerState.PAUSED,
                    MediaPlayerState.BUFFERING,
                )
            )
            if dec_state.state in valid_states:
                return MediaPlayerState(dec_state.state)
        return None

    @property
    def state(self) -> MediaPlayerState | None:
        """Mirror the decoder's playback state when streaming.

        When the zone is actively streaming or passively routed to a decoder, the
        playback state (PLAYING, PAUSED, BUFFERING) is mirrored from the
        decoder.  A directly claimed decoder also mirrors IDLE.  The zone's
        own ON/OFF state (from BTicino hardware events) is used as the fallback.
        """
        if self._attr_state == MediaPlayerState.OFF:
            # A parked room has its amplifier off but its group intact. Music
            # Assistant dissolves a group whose leader reports "off", so a
            # parked room says what is true of the music: it can resume.
            if self._wake_pending:
                return MediaPlayerState.ON
            return self._parked_state() if self._parked else MediaPlayerState.OFF
        if self._active_decoder:
            active_state = self._resolve_playback_state(self._active_decoder, allow_idle=True)
            if active_state is not None:
                return active_state
        eff_dec = self._effective_decoder
        if eff_dec:
            eff_state = self._resolve_playback_state(eff_dec, allow_idle=False)
            if eff_state is not None:
                return eff_state
        return self._attr_state

    def _parked_state(self) -> MediaPlayerState:
        """State shown for a parked room: paused if its decoder is paused, else idle."""
        pool = self._get_pool()
        if pool is not None:
            leader_id = pool.get_leader(self.entity_id) or self.entity_id
            decoder_id = pool.owned_decoder(leader_id)
            state = self.hass.states.get(decoder_id) if decoder_id else None
            if state is not None and state.state == MediaPlayerState.PAUSED:
                return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

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
            companion = self._streaming_target(eff_dec)
            if companion and companion != eff_dec:
                comp_state = self.hass.states.get(companion)
                if comp_state and comp_state.attributes.get(attr) is not None:
                    return comp_state.attributes.get(attr)
            dec_state = self.hass.states.get(eff_dec)
            if dec_state:
                return dec_state.attributes.get(attr)
        return None

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
        watched = self._active_decoder or self._stray_decoder()
        eff_dec = self._effective_decoder or watched
        if not eff_dec:
            return
        companion = self._streaming_target(eff_dec)
        event_entity = event.data.get("entity_id")
        if event_entity != eff_dec and event_entity != companion:
            return

        if self._active_decoder and not self._syncing_volume:
            new_state = event.data.get("new_state")
            if new_state:
                ext_vol = new_state.attributes.get("volume_level")
                if ext_vol is not None and self._attr_volume_level != ext_vol:
                    pool = self._get_pool()
                    if pool and event_entity == eff_dec:
                        pre_gain_pct = pool.get_pre_gain(eff_dec)
                        # Reverse the pre_gain offset to get approximate zone volume
                        zone_vol = max(0.0, float(ext_vol) - pre_gain_pct / 100.0)
                        self._attr_volume_level = zone_vol

        # Auto power-off when decoder stops playing (anti-hiss). Applies to the
        # zone that owns the decoder and to any other room that is on and
        # listening to its input (see _stray_decoder).
        new_state = event.data.get("new_state")
        event_entity = event.data.get("entity_id")
        if watched and new_state and event_entity:
            target_dec = self._streaming_target(watched) or watched
            if event_entity in (watched, target_dec):
                old_state = event.data.get("old_state")
                unchanged = old_state is not None and old_state.state == new_state.state
                # A room without a claim only follows real transitions: an
                # attribute update on an idle decoder (volume, position) must
                # not switch off a room someone just turned on to start playing.
                if not (unchanged and not self._active_decoder):
                    self._apply_decoder_state(new_state.state, watched)

        if (
            new_state
            and new_state.state in _DECODER_PLAYING_STATES
            and self._attr_state == MediaPlayerState.ON
            and not self._active_decoder
        ):
            pool = self._get_pool()
            if not (pool and pool.get_leader(self.entity_id)):
                self.hass.async_create_task(self._async_auto_join_active_stream())

        self.async_schedule_update_ha_state()

    @callback
    def _apply_decoder_state(self, new_state_val: str, decoder_id: str | None = None) -> None:
        """Arm or cancel the anti-hiss auto-off for a decoder state this zone hears.

        ``decoder_id`` is the decoder being heard. A timer only switches the
        room off if the room still hears that decoder when the timer fires:
        the input can be changed at a wall panel, or the room given a decoder
        of its own, while the timer runs.

        A decoder that goes ``off`` gets the short timer rather than an
        immediate OFF: decoder integrations report ``off`` while reloading or
        reconnecting, and a decoder that comes back playing within the delay
        should not have taken every room down with it. ``unavailable`` and
        ``unknown`` say nothing about playback and are ignored.
        """
        if new_state_val in _DECODER_PLAYING_STATES:
            self._cancel_auto_off()
        elif new_state_val in (MediaPlayerState.OFF, "off"):
            self._arm_auto_off(_AUTO_OFF_IDLE_DELAY, decoder_id)
        elif new_state_val in (MediaPlayerState.IDLE, "idle", "standby"):
            self._arm_auto_off(_AUTO_OFF_IDLE_DELAY, decoder_id)
        elif new_state_val in (MediaPlayerState.PAUSED, "paused"):
            self._arm_auto_off(_AUTO_OFF_PAUSED_DELAY, decoder_id)

    @callback
    def _cancel_auto_off(self) -> None:
        """Drop a pending anti-hiss auto-off: the music is (about to be) playing."""
        if self._auto_off_unsub:
            self._auto_off_unsub()
            self._auto_off_unsub = None
        self._auto_off_key = None

    @callback
    def _arm_auto_off(self, delay: float, decoder_id: str | None) -> None:
        """Switch this room off in ``delay`` seconds unless the music resumes first.

        A timer already running for the same decoder and delay is kept, so
        repeated state reports do not push the deadline back. One for another
        decoder (the room was moved to another input) or another delay (a
        pause turned into a stop, or the other way round) is replaced.
        """
        if self._attr_state == MediaPlayerState.OFF or self._turning_off:
            return
        key = (delay, decoder_id)
        if self._auto_off_unsub:
            if self._auto_off_key == key:
                return
            self._cancel_auto_off()
        self._auto_off_key = key

        @callback
        def _auto_turn_off(_now: Any) -> None:
            self._auto_off_unsub = None
            self._auto_off_key = None
            if self._attr_state == MediaPlayerState.OFF or self._turning_off:
                return
            if (self._active_decoder or self._stray_decoder()) != decoder_id:
                return  # re-routed, grouped or given a decoder of its own meanwhile
            pool = self._get_pool()
            if pool is not None and pool.get_members(self.entity_id):
                self.hass.async_create_task(self._async_park_group())
            else:
                self.hass.async_create_task(self.async_turn_off())

        self._auto_off_unsub = async_call_later(self.hass, delay, _auto_turn_off)

    def _stray_decoder(self) -> str | None:
        """Return the decoder this room hears without holding a claim on it.

        A room that is on plays whatever its environment is routed to, whether
        or not Home Assistant or Music Assistant grouped it: after a restart
        the group books are empty while the amplifiers stay on, and a wall
        panel can switch a room onto a decoder's input at any time. Such a
        room has no decoder assigned, so nothing would ever turn it off when
        the music stops.

        The input is the one reported on the bus, or else the environment's
        default source from the options: nothing is reported after a restart
        and the bus has no query for it, so the default is the best evidence
        there is. ``None`` when the room is off, is about to be switched off
        anyway, is a member of a group (its leader handles it), or listens to
        an input no decoder is wired to (a tuner, say).
        """
        if self._attr_state != MediaPlayerState.ON or self._pending_off_task is not None:
            return None
        pool = self._get_pool()
        if pool is None or pool.get_leader(self.entity_id) is not None:
            return None  # a group member goes off with its leader
        source_num = self._source_number(self._attr_source) if self._attr_source else None
        if source_num is None:
            source_num = self._default_source()
        if source_num is None:
            return None
        return pool.get_decoder_for_source(source_num)

    @callback
    def _mark_status_seen(self) -> None:
        """Note the first word from the bus about this room, and tell the pool.

        The pool restores its books after a restart but only trusts a zone once
        its amplifier has been heard from.
        """
        self._status_seen = True
        pool = self._get_pool()
        if pool is not None:
            pool.confirm_zone(self.entity_id)

    @callback
    def _restore_claim(self) -> None:
        """Take back the decoder the restored books say this room holds.

        The room was playing it before the restart and its amplifier is still
        on, so the claim is as good as ever; the decoder's own state decides
        whether that music is still going (see :meth:`_check_stray_at_startup`).
        """
        pool = self._get_pool()
        if pool is not None and self._active_decoder is None:
            self._active_decoder = pool.owned_decoder(self.entity_id)

    @callback
    def _check_stray_at_startup(self) -> None:
        """Switch a room off that was found on while its decoder is not playing.

        Runs once, on the first status report after the entity is added:
        there is no "decoder stopped" moment to react to after a restart.
        Later ON reports (a wall panel, or Home Assistant's own turn-on) are
        left alone, since the music may be about to start.
        """
        decoder_id = self._active_decoder or self._stray_decoder()
        if decoder_id is None:
            return
        # A decoder can be two entities (hardware and streaming companion),
        # one of which may be idle while the other plays: the room is only a
        # leftover if none of them is playing. An entity that does not report
        # yet says nothing; the state change that follows covers it.
        states = {
            state.state
            for entity_id in {decoder_id, self._streaming_target(decoder_id) or decoder_id}
            if (state := self.hass.states.get(entity_id)) is not None
            and state.state not in ("unavailable", "unknown")
        }
        if not states or states & _DECODER_PLAYING_STATES:
            return
        for candidate in (MediaPlayerState.PAUSED, MediaPlayerState.IDLE, "standby", MediaPlayerState.OFF):
            if candidate in states:
                LOGGER.info(
                    "%s: found on at startup while decoder %s is %s — switching it off",
                    self.entity_id,
                    decoder_id,
                    candidate,
                )
                self._apply_decoder_state(str(candidate), decoder_id)
                return
