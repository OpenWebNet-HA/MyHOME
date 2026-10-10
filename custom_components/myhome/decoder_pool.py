"""Decoder pool manager for the MyHOME Dynamic Proxy.

Manages a pool of streaming decoders (squeezelite / Cambridge Audio) that are
physically connected to the BTicino F441M matrix source inputs.  The proxy
intercepts Music Assistant / Spotify play_media commands, claims an idle decoder
from this pool, routes the BTicino matrix to the correct source input, and
forwards the stream URL to the backend decoder.

Architecture
------------
- One ``DecoderPool`` instance per gateway, keyed by MAC address in
  ``entry.runtime_data.decoder_pool``.
- Survives entity reloads (lives on the config entry, not inside an entity).
- Thread-safe: all claim/release operations are serialised with a single
  ``asyncio.Lock`` to prevent race conditions when multiple zones compete for
  the last available decoder.
- State-aware: inspects the live HA entity state of each decoder to determine
  whether it is truly idle before claiming.
- Persistent: the books (who holds which decoder, who is grouped with whom) are
  saved to ``.storage`` after every change and restored at startup, because the
  amplifiers keep playing through a Home Assistant restart.  A restored zone
  stays *unconfirmed* until the bus reports its amplifier; see
  :meth:`DecoderPool.confirm_zone` and :meth:`DecoderPool.drop_unconfirmed`.

Gain staging (anti-hiss)
------------------------
Each decoder carries an optional ``pre_gain`` offset (0–100 %).  When the user
adjusts the BTicino zone volume the proxy also sets the decoder volume to
``zone_volume + pre_gain``, capped at 1.0.  This keeps the analog signal level
high and the BTicino amplifier gain low, which reduces the inherent noise floor
of the 2-wire bus.

Typical values
--------------
- Cambridge Audio with Pre-Amp OFF: ``pre_gain = 0`` (already at full line level)
- Squeezelite / piCorePlayer:       ``pre_gain = 20``
"""
import asyncio
import time
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER

STORAGE_VERSION = 1
"""Version of the saved books; bump when their shape changes."""

_SAVE_DELAY = 1.0
"""Seconds to wait after a change before writing, so a burst becomes one write."""

PAUSE_TAKEOVER_AFTER = 300.0
"""Seconds a decoder nobody here owns must stay paused before it can be claimed.

A decoder paused by a native session (Spotify Connect straight to the device)
is somebody's music, not an idle input; a stale pause must not lock the input
forever, though, so it becomes claimable after this long.
"""


def decoder_pool_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store that keeps one config entry's pool books."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.decoder_pool.{entry_id}")


@dataclass
class GroupChange:
    """What :meth:`DecoderPool.set_group` changed, for the caller to act on.

    The pool only keeps the books; the amplifiers and decoders behind these
    zones are switched by the media player entities.
    """

    joined: list[str] = field(default_factory=list)
    """Zones that were not in the group before."""
    left: list[str] = field(default_factory=list)
    """Former members that are no longer in the group."""
    orphaned: list[str] = field(default_factory=list)
    """Members of groups that a joining zone used to lead, now disbanded."""
    released: list[str] = field(default_factory=list)
    """Decoders that joining zones held and gave up."""


class EnvironmentBusyError(Exception):
    """Another zone in the same environment already streams from a decoder.

    The F441M routes per output and an output serves a whole environment, so
    one environment can only ever listen to one matrix input.  Handing a second
    decoder to a zone in that environment would re-route the first zone onto
    the new stream while Home Assistant still shows it playing the old one.
    """

    def __init__(self, environment: str, owner: str) -> None:
        super().__init__(f"environment {environment} is already streaming to {owner}")
        self.environment = environment
        self.owner = owner


class DecoderPool:
    """Thread-safe pool of streaming decoders mapped to BTicino source inputs.

    Each decoder (squeezelite, Cambridge Audio, etc.) is physically connected
    to one of the 4 BTicino source inputs.  This class handles:

    - Thread-safe allocation via ``asyncio.Lock``
    - State-aware idle detection (inspects live HA entity state)
    - Pre-gain configuration per decoder (gain staging / anti-hiss)
    - Graceful release on zone turn-off or options reload
    """

    # HA states that mean "this decoder is available for claiming".
    # UNAVAILABLE is intentionally excluded: treat an offline Cambridge as busy
    # rather than risking a claim on a device that cannot actually play.
    # ON counts as idle: it means "powered, not known to be playing" (the audio
    # decoder sits in it for good after its first stream and reports playing
    # when it plays); a decoder that is busy says playing, buffering or paused.
    _IDLE_STATES: frozenset[MediaPlayerState | str | None] = frozenset({
        MediaPlayerState.IDLE,
        MediaPlayerState.OFF,
        MediaPlayerState.PAUSED,
        MediaPlayerState.ON,
        "idle",
        "off",
        "on",
        "paused",
        "standby",
        None,  # entity not yet registered / state unknown
    })

    def __init__(
        self,
        hass: HomeAssistant,
        decoder_map: dict[str, int],
        pre_gain_map: dict[str, int] | None = None,
        stream_incompatible: Collection[str] = (),
        companion_map: Mapping[str, str] | None = None,
        store: Store[dict[str, Any]] | None = None,
    ) -> None:
        """Initialise the decoder pool.

        Args:
            hass: Home Assistant instance (used to read entity states).
            decoder_map: Mapping of ``{entity_id: source_num (int)}``, e.g.::

                {
                    "media_player.cambridge_audio_cxn": 1,
                    "media_player.hifiberry_zone": 2,
                }

                The source number tells the integration which physical F441M input
                the decoder is wired to. For normal streaming the integration activates
                the zone with a simple OFF→ON sequence and trusts the matrix routing
                (set physically or by gateway scenario). The number is available if
                explicit routing commands are ever needed.

            pre_gain_map: Optional mapping of ``{entity_id: pre_gain_pct}``
                where ``pre_gain_pct`` is an integer between 0 and 100.
                Defaults to 0 for any decoder not listed.

            stream_incompatible: Decoders whose integration does not accept
                a stream URL through ``play_media`` (``cambridge_audio``).
                They stay in the pool for passive mirroring and for the
                media types they do accept, but a URL stream skips them.

            companion_map: Optional mapping of ``{decoder_id: streaming_companion_id}``
                where a hardware decoder (e.g. ``cambridge_audio``) is dynamically
                bridged to its companion DLNA DMR entity for URL streaming.

            store: Where to keep the books across restarts.  ``None`` keeps
                them in memory only.

        Example::

            pool = DecoderPool(
                hass,
                decoder_map={"media_player.cambridge_audio_cxn": 1},
                pre_gain_map={"media_player.cambridge_audio_cxn": 0},
            )
        """
        self._hass = hass
        self._decoder_map: dict[str, int] = decoder_map          # entity_id → source_num
        self._pre_gain_map: dict[str, int] = pre_gain_map or {}  # entity_id → pre_gain %
        self._assignments: dict[str, str | None] = {             # entity_id → zone_entity_id (leader) or None
            entity_id: None for entity_id in decoder_map
        }
        self._groups: dict[str, set[str]] = {}                   # leader_entity_id → set of member_entity_ids
        self._environments: dict[str, str] = {}                   # zone_entity_id → environment
        self._stream_incompatible: frozenset[str] = frozenset(stream_incompatible)
        self._companion_map: dict[str, str] = dict(companion_map or {})
        self._former_leaders: dict[str, tuple[str, float]] = {}  # member -> (former_leader, monotonic_time)
        self._lock = asyncio.Lock()
        self._store = store
        self._saved: dict[str, Any] | None = None                # last books handed to the store
        self._released_at: dict[str, datetime] = {}               # decoder → when a zone of ours last let go of it
        self._unconfirmed: set[str] = set()                      # restored zones the bus has not reported yet

    # ── Persistence ───────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _books(self) -> AsyncIterator[None]:
        """Hold the lock, then save the books if the change altered them."""
        async with self._lock:
            try:
                yield
            finally:
                self._persist()

    def _snapshot(self) -> dict[str, Any]:
        """Return the books in the form that is saved."""
        return {
            "assignments": {dec: zone for dec, zone in self._assignments.items() if zone},
            "sources": {dec: self._decoder_map[dec] for dec, zone in self._assignments.items() if zone},
            "groups": {leader: sorted(members) for leader, members in self._groups.items() if members},
            "environments": dict(self._environments),
        }

    def _persist(self) -> None:
        """Schedule a save of the books when they differ from the last saved ones."""
        if self._store is None:
            return
        snapshot = self._snapshot()
        if snapshot != self._saved:
            self._saved = snapshot
            self._store.async_delay_save(self._snapshot, _SAVE_DELAY)

    async def async_load(self) -> None:
        """Restore the books saved before the last restart, if there are any."""
        if self._store is None:
            return
        self.restore(await self._store.async_load())

    async def async_save(self) -> None:
        """Write the books now, e.g. before the config entry unloads."""
        if self._store is not None:
            self._saved = self._snapshot()
            await self._store.async_save(self._saved)

    def restore(self, data: object) -> None:
        """Take over saved books, ignoring whatever no longer fits the configuration.

        The amplifiers keep playing while Home Assistant restarts, so the
        books that describe who listens to which decoder must survive it: an
        environment's claim is what stops a second stream from re-routing a
        room that is already playing.  Every zone restored this way is
        *unconfirmed* until its amplifier reports on the bus (see
        :meth:`confirm_zone`); the saved books can be stale, and the bus is
        the authority on which amplifiers are actually on.
        """
        if not isinstance(data, dict):
            return
        assignments = data.get("assignments")
        sources = data.get("sources")
        rewired: set[str] = set()
        for dec_id, zone in (assignments.items() if isinstance(assignments, dict) else ()):
            if dec_id in self._decoder_map and isinstance(zone, str) and zone:
                self._assignments[dec_id] = zone
                saved_source = sources.get(dec_id) if isinstance(sources, dict) else None
                if saved_source is not None and saved_source != self._decoder_map[dec_id]:
                    rewired.add(zone)
        groups = data.get("groups")
        taken: set[str] = set()
        for leader, members in (groups.items() if isinstance(groups, dict) else ()):
            if not isinstance(leader, str) or not isinstance(members, list):
                continue
            kept = {m for m in members if isinstance(m, str) and m != leader and m not in taken}
            if kept:
                self._groups[leader] = kept
                taken |= kept
        environments = data.get("environments")
        for zone, environment in (environments.items() if isinstance(environments, dict) else ()):
            if isinstance(zone, str) and isinstance(environment, str):
                self._environments[zone] = environment
        # A decoder moved to another matrix input while Home Assistant was down
        # no longer feeds the rooms the books put on it: its claim is stale.
        for zone in rewired:
            LOGGER.info("DecoderPool: decoder for %s was rewired while Home Assistant was down, dropping its claim", zone)
            self._forget_zone_locked(zone)
        self._unconfirmed = {
            *(zone for zone in self._assignments.values() if zone),
            *self._groups,
            *(member for members in self._groups.values() for member in members),
        }
        self._saved = self._snapshot()
        if self._unconfirmed:
            LOGGER.info(
                "DecoderPool: restored books for %d zone(s), waiting for the bus to confirm them",
                len(self._unconfirmed),
            )

    @property
    def has_unconfirmed(self) -> bool:
        """Return ``True`` while restored zones have not been reported on the bus."""
        return bool(self._unconfirmed)

    def confirm_zone(self, zone_entity_id: str) -> None:
        """Note that the bus has reported ``zone_entity_id``'s amplifier.

        An amplifier that reports *off* is cleaned up by the zone's own
        turn-off, which releases its books; one that reports *on* keeps them.
        """
        self._unconfirmed.discard(zone_entity_id)

    async def drop_unregistered(self, is_registered: Callable[[str], bool]) -> list[str]:
        """Forget the restored zones whose entity no longer exists.

        A zone that was renamed or deleted while Home Assistant was down will
        never report under its old entity id; waiting out the confirm window
        would keep its decoder and environment busy for nothing.  Returns the
        zones that were dropped.
        """
        async with self._books():
            gone = sorted(zone for zone in self._unconfirmed if not is_registered(zone))
            self._unconfirmed.difference_update(gone)
            for zone in gone:
                self._forget_zone_locked(zone)
        if gone:
            LOGGER.info("DecoderPool: dropped restored zones that are no longer registered: %s", gone)
        return gone

    async def drop_unconfirmed(self) -> list[str]:
        """Forget the restored zones that never showed up on the bus.

        Without this a zone that no longer exists would hold its decoder for
        good.  Returns the zones that were dropped.
        """
        async with self._books():
            gone = sorted(self._unconfirmed)
            self._unconfirmed.clear()
            for zone in gone:
                self._forget_zone_locked(zone)
        if gone:
            LOGGER.info("DecoderPool: dropped restored zones the bus never reported: %s", gone)
        return gone

    def _unassign_locked(self, dec_id: str) -> None:
        """Leave a decoder without an owner, noting when: a pause that began before then was ours."""
        self._assignments[dec_id] = None
        self._released_at[dec_id] = dt_util.utcnow()

    def _forget_zone_locked(self, zone_entity_id: str) -> None:
        """Remove every trace of a zone from the books while holding the lock."""
        self._remove_member_locked(zone_entity_id)
        self._disband_group_locked(zone_entity_id)
        for dec_id, owner in self._assignments.items():
            if owner == zone_entity_id:
                self._unassign_locked(dec_id)
        self._environments.pop(zone_entity_id, None)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if at least one decoder has been mapped."""
        return len(self._decoder_map) > 0

    @property
    def stream_incompatible(self) -> frozenset[str]:
        """Return the decoders that cannot be handed a stream URL."""
        return self._stream_incompatible

    @property
    def companion_map(self) -> dict[str, str]:
        """Return mapping of decoder_id -> streaming companion entity_id."""
        return dict(self._companion_map)

    def get_streaming_decoder(self, decoder_id: str) -> str:
        """Return the streaming companion entity for decoder_id if one exists, else decoder_id."""
        return self._companion_map.get(decoder_id, decoder_id)

    def _is_decoder_hw_idle(self, dec_id: str) -> bool:
        """Return True if decoder entity (and any companion) is in an idle state."""
        state = self._hass.states.get(dec_id)
        state_val = state.state if state else None
        target_dec_id = self.get_streaming_decoder(dec_id)
        target_state = self._hass.states.get(target_dec_id) if target_dec_id != dec_id else None
        target_state_val = target_state.state if target_state else None
        return state_val in self._IDLE_STATES and (target_dec_id == dec_id or target_state_val in self._IDLE_STATES)

    def _paused_while_ours(self, dec_id: str, paused_at: datetime) -> bool:
        """Return True if the pause began while one of our zones held the decoder.

        The room's own stream that was paused (and whose room then switched
        itself off) is not "another player": pressing play again must be able
        to take the decoder back.
        """
        released = self._released_at.get(dec_id)
        return released is not None and paused_at <= released

    def _recently_paused(self, dec_id: str) -> bool:
        """Return True if the decoder or its companion has been paused for less than :data:`PAUSE_TAKEOVER_AFTER`."""
        now = dt_util.utcnow()
        for entity_id in {dec_id, self.get_streaming_decoder(dec_id)}:
            state = self._hass.states.get(entity_id)
            if (
                state is not None
                and state.state in (MediaPlayerState.PAUSED, "paused")
                and (now - state.last_changed).total_seconds() < PAUSE_TAKEOVER_AFTER
                and not self._paused_while_ours(dec_id, state.last_changed)
            ):
                return True
        return False

    async def claim(
        self,
        zone_entity_id: str,
        preferred_source: int | None = None,
        environment: str | None = None,
        exclude: Collection[str] = (),
    ) -> tuple[str, int] | None:
        """Claim an idle decoder for *zone_entity_id*.

        Thread-safe: uses ``asyncio.Lock`` to prevent two zones from claiming
        the same decoder simultaneously.

        If *zone_entity_id* already owns a decoder (e.g. song change), the
        existing assignment is returned immediately without re-locking.

        Args:
            zone_entity_id: The ``entity_id`` of the BTicino zone requesting
                a decoder (e.g. ``"media_player.audio_zone_3"``).
            preferred_source: Matrix input this zone would rather use. A
                decoder wired to it is claimed first when it is idle;
                otherwise the usual slot order applies.
            environment: Environment digit of the zone's amplifier address.
                When given, the claim is refused while another zone in the
                same environment holds a decoder: the matrix can route an
                environment to one input only.
            exclude: Decoders not to hand out, e.g. those that cannot take
                the media about to be played.

        Returns:
            ``(decoder_entity_id, source_num: int)`` if an idle decoder was
            found and claimed, or ``None`` if all decoders are busy.

        Raises:
            EnvironmentBusyError: If another zone in ``environment`` already
                holds a decoder.

        Example::

            result = await pool.claim("media_player.audio_zone_3")
            if result is None:
                raise HomeAssistantError("All inputs are busy!")
            decoder_id, source_num = result
        """
        displaced_owner: str | None = None
        claimed: tuple[str, int] | None = None
        async with self._books():
            # A member playing on its own leaves its group, but only once it
            # has a decoder: a refused or failed claim keeps it in the group.

            # If this zone already owns a decoder, reuse it (idempotent).
            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    LOGGER.debug("Decoder %s already claimed by %s", dec_id, zone_entity_id)
                    self._remove_member_locked(zone_entity_id)
                    return (dec_id, self._decoder_map[dec_id])

            if environment is not None:
                # The zone's own members follow it onto the new decoder.
                ignore = {zone_entity_id, *self._groups.get(zone_entity_id, ())}
                former_leader, former_time = self._former_leaders.get(
                    zone_entity_id, (None, 0.0)
                )
                if (
                    former_leader is not None
                    and (time.monotonic() - former_time) < 30.0
                    and len(self._groups.get(former_leader, set())) == 0
                ):
                    ignore.add(former_leader)
                owners = self._environment_owners(environment, ignore)
                if owners:
                    raise EnvironmentBusyError(environment, owners[0])

            # Candidates in slot order, but a decoder wired to the caller's
            # preferred source comes first: routing the matrix to the input
            # the room already defaults to avoids an audible source switch.
            # Unassigned decoders are always prioritized over assigned ones.
            candidates = list(self._assignments)
            if preferred_source is not None:
                candidates.sort(
                    key=lambda dec: self._decoder_map.get(dec) != preferred_source
                )
            candidates.sort(key=lambda dec: self._assignments[dec] is not None)

            # Find the first decoder that is unassigned AND idle, or can be handed over.
            for dec_id in candidates:
                if dec_id in exclude:
                    continue
                owner = self._assignments[dec_id]
                if owner is None and self._recently_paused(dec_id):
                    LOGGER.info(
                        "DecoderPool: %s was paused by another player less than %d s ago — not taking it over",
                        dec_id,
                        PAUSE_TAKEOVER_AFTER,
                    )
                    continue
                is_hw_idle = self._is_decoder_hw_idle(dec_id)
                former_leader, former_time = self._former_leaders.get(
                    zone_entity_id, (None, 0.0)
                )
                is_handover = (
                    owner is not None
                    and owner == former_leader
                    and (time.monotonic() - former_time) < 30.0
                    and len(self._groups.get(owner, set())) == 0
                )

                if owner is not None:
                    owner_state = self._hass.states.get(owner)
                    owner_val = owner_state.state if owner_state else None
                    is_owner_off = (
                        is_hw_idle and owner_val in (MediaPlayerState.OFF, "off")
                    )
                    if is_owner_off or is_handover:
                        LOGGER.info(
                            "DecoderPool: reassigning decoder %s from zone %s (state=%s, hw_idle=%s, handover=%s) to %s",
                            dec_id,
                            owner,
                            owner_val,
                            is_hw_idle,
                            is_handover,
                            zone_entity_id,
                        )
                        if owner != zone_entity_id and owner_val not in (MediaPlayerState.OFF, "off"):
                            displaced_owner = owner
                        self._disband_group_locked(owner)
                        self._environments.pop(owner, None)
                        self._unassign_locked(dec_id)
                        owner = None
                    else:
                        continue  # already in use by an active zone

                if is_hw_idle or is_handover:
                    self._remove_member_locked(zone_entity_id)
                    self._assignments[dec_id] = zone_entity_id
                    if environment is not None:
                        self._environments[zone_entity_id] = environment
                    LOGGER.info(
                        "DecoderPool: %s claimed by zone %s (source %s)",
                        dec_id,
                        zone_entity_id,
                        self._decoder_map[dec_id],
                    )
                    claimed = (dec_id, self._decoder_map[dec_id])
                    break

        if displaced_owner:
            try:
                await self._hass.services.async_call(
                    "media_player", "turn_off", {"entity_id": displaced_owner}
                )
            except Exception as err:
                LOGGER.debug("DecoderPool: failed to turn off displaced zone %s: %s", displaced_owner, err)

        if claimed:
            return claimed

        # All decoders are busy.
        LOGGER.warning(
            "DecoderPool: all decoders busy — zone %s cannot play", zone_entity_id
        )
        return None

    async def set_group(
        self,
        leader_entity_id: str,
        members: Mapping[str, str | None],
    ) -> GroupChange:
        """Make ``members`` the complete member list of ``leader_entity_id``'s group.

        Snapshot semantics, as ``media_player.join`` expects: members not
        listed leave, listed zones join. Every environment is checked before
        anything changes, so a refused join leaves the pool exactly as it
        was. A joining zone gives up any decoder it held and the group it
        led; both are reported back so the caller can stop that decoder and
        switch those amplifiers.

        Args:
            leader_entity_id: The zone that leads the group.
            members: ``{member_entity_id: environment}``; the environment may
                be ``None`` when the zone has no routing address.

        Returns:
            A :class:`GroupChange` describing the zones and decoders affected.

        Raises:
            EnvironmentBusyError: If a member's environment is streaming from
                a decoder other than the leader's.
        """
        async with self._books():
            return self._set_group_locked(leader_entity_id, dict(members))

    def _set_group_locked(
        self, leader_entity_id: str, members: dict[str, str | None]
    ) -> GroupChange:
        """Validate, then apply, a group snapshot while holding ``self._lock``."""
        members.pop(leader_entity_id, None)
        current = self._groups.get(leader_entity_id, set())
        joining = [zone for zone in members if zone not in current]
        leaving = sorted(current - members.keys())

        # Zones whose environment claims are about to go: the members
        # themselves, those leaving, and the groups joining zones used to lead.
        vacated = set(members) | set(leaving)
        for zone in joining:
            vacated |= self._groups.get(zone, set())
        if self.get_leader(leader_entity_id) is not None:
            vacated.add(leader_entity_id)

        decoder = self._owned_decoder(leader_entity_id)
        for zone, environment in members.items():
            if environment is None:
                continue
            for owner in self._environment_owners(environment, vacated):
                if self.get_assignment(owner) != decoder:
                    raise EnvironmentBusyError(environment, owner)

        # Validated: from here on nothing raises.
        change = GroupChange(joined=joining, left=leaving)
        self._remove_member_locked(leader_entity_id)
        for zone in leaving:
            self._remove_member_locked(zone)
        for zone in joining:
            change.orphaned.extend(self._disband_group_locked(zone))
            owned = self._owned_decoder(zone)
            if owned is not None:
                self._unassign_locked(owned)
                change.released.append(owned)
            self._remove_member_locked(zone)
        change.orphaned = sorted(set(change.orphaned) - set(members))

        if members:
            self._groups[leader_entity_id] = set(members)
        else:
            self._groups.pop(leader_entity_id, None)
        for zone, environment in members.items():
            if environment is None:
                self._environments.pop(zone, None)
            else:
                self._environments[zone] = environment

        if joining or leaving:
            LOGGER.info(
                "DecoderPool: group of %s is now %s (decoder %s)",
                leader_entity_id,
                sorted(members),
                decoder,
            )
        return change

    async def add_member(
        self,
        leader_entity_id: str,
        member_entity_id: str,
        environment: str | None = None,
    ) -> tuple[str, int] | None:
        """Add a member zone to the group of leader_entity_id.

        The member shares the leader's claimed decoder (if one is active).
        Nothing changes when the member's environment is streaming from a
        different decoder: :class:`EnvironmentBusyError` is raised first.

        Args:
            leader_entity_id: The zone entity ID that leads the group.
            member_entity_id: The zone entity ID joining the group.
            environment: Optional environment digit of the member zone.

        Returns:
            ``(decoder_entity_id, source_num)`` if the leader holds a decoder,
            or ``None`` if the group is passive / not currently streaming.

        Raises:
            EnvironmentBusyError: If the member's environment is streaming from
                a different decoder.
        """
        async with self._books():
            members: dict[str, str | None] = {
                zone: self._environments.get(zone)
                for zone in self._groups.get(leader_entity_id, set())
            }
            if member_entity_id not in members:
                members[member_entity_id] = environment
            self._set_group_locked(leader_entity_id, members)
            decoder = self._owned_decoder(leader_entity_id)
            if decoder is None:
                return None
            return (decoder, self._decoder_map[decoder])

    # Alias for explicit group naming
    add_group_member = add_member

    async def remove_member(self, member_entity_id: str) -> str | None:
        """Remove a member zone from whichever group it joined.

        Args:
            member_entity_id: The zone entity ID leaving the group.

        Returns:
            The decoder entity ID the member was listening to, or None if not found.
        """
        async with self._books():
            return self._remove_member_locked(member_entity_id)

    remove_group_member = remove_member

    def _remove_member_locked(self, member_entity_id: str) -> str | None:
        """Remove a member zone while already holding self._lock."""
        for leader_id, members in list(self._groups.items()):
            if member_entity_id in members:
                members.remove(member_entity_id)
                self._former_leaders[member_entity_id] = (leader_id, time.monotonic())
                if not members:
                    self._groups.pop(leader_id, None)
                self._environments.pop(member_entity_id, None)
                LOGGER.info(
                    "DecoderPool: member %s removed from leader %s",
                    member_entity_id,
                    leader_id,
                )
                for dec_id, owner in self._assignments.items():
                    if owner == leader_id:
                        return dec_id
                return None
        return None

    def _disband_group_locked(self, leader_entity_id: str) -> list[str]:
        """Disband group members while holding lock."""
        members = list(self._groups.pop(leader_entity_id, set()))
        now = time.monotonic()
        for mem in members:
            self._former_leaders[mem] = (leader_entity_id, now)
            self._environments.pop(mem, None)
        if members:
            LOGGER.info(
                "DecoderPool: group of %s disbanded (%d members)",
                leader_entity_id,
                len(members),
            )
        return members

    async def disband_group(self, leader_entity_id: str) -> list[str]:
        """Disband a group owned by leader_entity_id."""
        async with self._books():
            return self._disband_group_locked(leader_entity_id)

    async def transfer_leadership(
        self, old_leader: str, new_leader: str
    ) -> tuple[str, int] | None:
        """Transfer group leadership and active decoder from old_leader to new_leader.

        Args:
            old_leader: Current group leader entity_id.
            new_leader: Member entity_id that will become the new leader.

        Returns:
            ``(decoder_entity_id, source_num)`` if a decoder was transferred, or ``None``.
        """
        async with self._books():
            return self._transfer_leadership_locked(old_leader, new_leader)

    def _transfer_leadership_locked(
        self, old_leader: str, new_leader: str
    ) -> tuple[str, int] | None:
        """Transfer group leadership while holding self._lock."""
        if old_leader not in self._groups or new_leader not in self._groups[old_leader]:
            return None
        current_members = self._groups.pop(old_leader, set())
        remaining = current_members - {new_leader}
        if remaining:
            self._groups[new_leader] = remaining
        else:
            self._groups.pop(new_leader, None)

        decoder = None
        for dec_id, owner in self._assignments.items():
            if owner == old_leader:
                self._assignments[dec_id] = new_leader
                decoder = dec_id
                break

        self._environments.pop(old_leader, None)
        LOGGER.info(
            "DecoderPool: leadership of group transferred from %s to %s (members: %s, decoder: %s)",
            old_leader,
            new_leader,
            sorted(remaining),
            decoder,
        )
        if decoder:
            return (decoder, self._decoder_map[decoder])
        return None

    async def release(self, zone_entity_id: str) -> str | None:
        """Release the decoder assigned to *zone_entity_id* or detach from group.

        If *zone_entity_id* is a member of a group, only the member is removed.
        If *zone_entity_id* is the group leader, the decoder is released and
        all members are disbanded.

        Args:
            zone_entity_id: The ``entity_id`` of the BTicino zone releasing
                its decoder.

        Returns:
            The decoder the zone was listening to, or ``None`` if it had no
            active assignment.  Only a leader's decoder is actually freed: for
            a member this is the leader's decoder, which stays claimed.

        Example::

            freed = await pool.release("media_player.audio_zone_3")
        """
        async with self._books():
            # A member only leaves; the leader keeps the decoder.
            leader_decoder = self._remove_member_locked(zone_entity_id)
            if leader_decoder is not None:
                return leader_decoder

            # Check if this zone is a leader with a group
            self._disband_group_locked(zone_entity_id)

            # Check if this zone owns a decoder
            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    self._unassign_locked(dec_id)
                    self._environments.pop(zone_entity_id, None)
                    LOGGER.info(
                        "DecoderPool: %s released by leader %s (group disbanded)",
                        dec_id,
                        zone_entity_id,
                    )
                    return dec_id
        return None

    def books(self) -> dict[str, Any]:
        """Return a copy of the books (assignments, sources, groups, environments), as saved."""
        return self._snapshot()

    @property
    def unconfirmed(self) -> frozenset[str]:
        """Return the restored zones the bus has not reported on yet."""
        return frozenset(self._unconfirmed)

    def get_assignment(self, zone_entity_id: str) -> str | None:
        """Return the decoder entity_id assigned to *zone_entity_id*, or ``None``.

        Lock-free read — safe because ``_assignments`` and ``_groups`` mutations
        only happen inside the asyncio event loop under the lock.

        Args:
            zone_entity_id: The ``entity_id`` of the zone to query.

        Returns:
            The ``decoder_entity_id`` currently assigned to the zone, or
            ``None`` if the zone has no active decoder.
        """
        for dec_id, owner in self._assignments.items():
            if owner == zone_entity_id:
                return dec_id
        for leader_id, members in self._groups.items():
            if zone_entity_id in members:
                for dec_id, owner in self._assignments.items():
                    if owner == leader_id:
                        return dec_id
        return None

    def get_group_members(self, entity_id: str) -> list[str] | None:
        """Return group members for entity_id (leader first), or None if not grouped."""
        if entity_id in self._groups and self._groups[entity_id]:
            return [entity_id, *sorted(self._groups[entity_id])]
        for leader_id, members in self._groups.items():
            if members and entity_id in members:
                return [leader_id, *sorted(members)]
        return None

    def get_leader(self, member_entity_id: str) -> str | None:
        """Return the leader entity_id of the group member_entity_id belongs to, or None."""
        for leader_id, members in self._groups.items():
            if member_entity_id in members:
                return leader_id
        return None

    def is_leader(self, entity_id: str) -> bool:
        """Return True if entity_id is currently the leader of an active group."""
        return bool(self._groups.get(entity_id))

    def get_members(self, leader_entity_id: str) -> list[str]:
        """Return the list of member entity IDs joined with *leader_entity_id*."""
        return sorted(self._groups.get(leader_entity_id, set()))

    def get_decoder_for_source(self, source_num: int) -> str | None:
        """Return the decoder entity ID wired to ``source_num``, or ``None``."""
        for dec_id, src in self._decoder_map.items():
            if src == source_num:
                return dec_id
        return None

    def get_decoder_owner(self, decoder_entity_id: str) -> str | None:
        """Return the zone entity ID that directly owns decoder_entity_id, or None."""
        return self._assignments.get(decoder_entity_id)

    def decoder_source(self, decoder_entity_id: str) -> int | None:
        """Return the physical source number (1–4) for *decoder_entity_id*, or ``None``."""
        return self._decoder_map.get(decoder_entity_id)

    def environment_owner(self, environment: str, exclude: str | None = None) -> str | None:
        """Return the zone that streams from a decoder in ``environment``.

        Lock-free read, like :meth:`get_assignment`.  Used to keep automatic
        routing (a zone's default source) from switching an environment away
        from a stream another zone in it is playing.

        Args:
            environment: Environment digit of an amplifier address.
            exclude: Zone to ignore, normally the caller itself.

        Returns:
            The ``entity_id`` of that zone, or ``None`` when the environment
            has no active stream.  Members of a group whose leader holds no
            decoder are not streaming and do not count.
        """
        owners = self._environment_owners(environment, {exclude} if exclude else set())
        return owners[0] if owners else None

    def _environment_owners(self, environment: str, exclude: Collection[str]) -> list[str]:
        """Return every zone outside ``exclude`` actively streaming from a decoder in ``environment``."""
        active = []
        for zone, zone_environment in self._environments.items():
            if zone_environment == environment and zone not in exclude and self.get_assignment(zone) is not None:
                state = self._hass.states.get(zone)
                state_val = state.state if state else None
                if state_val not in (MediaPlayerState.OFF, "off"):
                    active.append(zone)
        return active

    def _owned_decoder(self, zone_entity_id: str) -> str | None:
        """Return the decoder ``zone_entity_id`` claimed itself (not one it shares as a member)."""
        for dec_id, owner in self._assignments.items():
            if owner == zone_entity_id:
                return dec_id
        return None

    def owned_decoder(self, zone_entity_id: str) -> str | None:
        """Return the decoder ``zone_entity_id`` holds itself, not one it shares as a member."""
        return self._owned_decoder(zone_entity_id)

    def get_pre_gain(self, decoder_entity_id: str) -> int:
        """Return the configured pre-gain offset for *decoder_entity_id*.

        Args:
            decoder_entity_id: The ``entity_id`` of the decoder.

        Returns:
            The pre-gain percent (0–100).  Defaults to ``0`` if not configured.

        Example::

            gain_pct = pool.get_pre_gain("media_player.cambridge_audio_cxn")
            decoder_volume = min(1.0, zone_volume + gain_pct / 100.0)
        """
        return self._pre_gain_map.get(decoder_entity_id, 0)

    # ── Introspection (for listeners and tests) ───────────────────────────────

    @property
    def decoder_entity_ids(self) -> list[str]:
        """Return all configured decoder entity IDs, including any companions."""
        ids = list(self._decoder_map.keys())
        for comp in self._companion_map.values():
            if comp not in ids:
                ids.append(comp)
        return ids

    def __repr__(self) -> str:  # pragma: no cover
        busy = sum(1 for v in self._assignments.values() if v is not None)
        members = sum(len(m) for m in self._groups.values())
        return (
            f"<DecoderPool decoders={len(self._decoder_map)} "
            f"busy={busy}/{len(self._decoder_map)} members={members}>"
        )
