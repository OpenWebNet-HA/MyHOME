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
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Collection, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER
from .decoder_allocation import (
    DecoderAllocationMixin,
    EnvironmentBusyError,
    GroupChange,
)

__all__ = [
    "DecoderAllocationMixin",
    "DecoderPool",
    "EnvironmentBusyError",
    "GroupChange",
    "PAUSE_TAKEOVER_AFTER",
    "STORAGE_VERSION",
    "_SAVE_DELAY",
    "decoder_pool_store",
]

STORAGE_VERSION = 1
"""Version of the saved books; bump when their shape changes."""

_SAVE_DELAY = 1.0
"""Seconds to wait after a change before writing, so a burst becomes one write."""

PAUSE_TAKEOVER_AFTER = 300.0
"""Seconds a decoder nobody here owns must stay paused before it can be claimed."""


def decoder_pool_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Return the store that keeps one config entry's pool books."""
    return Store(hass, STORAGE_VERSION, f"{DOMAIN}.decoder_pool.{entry_id}")


class DecoderPool(DecoderAllocationMixin):
    """Thread-safe pool of streaming decoders mapped to BTicino source inputs."""

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
        """Initialise the decoder pool."""
        self._hass = hass
        self._decoder_map: dict[str, int] = decoder_map
        self._pre_gain_map: dict[str, int] = pre_gain_map or {}
        self._assignments: dict[str, str | None] = {
            entity_id: None for entity_id in decoder_map
        }
        self._groups: dict[str, set[str]] = {}
        self._environments: dict[str, str] = {}
        self._stream_incompatible: frozenset[str] = frozenset(stream_incompatible)
        self._companion_map: dict[str, str] = dict(companion_map or {})
        self._former_leaders: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self._store = store
        self._saved: dict[str, Any] | None = None
        self._released_at: dict[str, datetime] = {}
        self._unconfirmed: set[str] = set()
        self._displaced_zones: set[str] = set()

    # ── Persistence ───────────────────────────────────────────────────────────

    @asynccontextmanager
    async def _books(self) -> AsyncIterator[None]:
        """Hold the lock, then save the books if the change altered them."""
        async with self._lock:
            try:
                yield
            finally:
                self._persist()

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

    async def drop_unregistered(self, is_registered: Callable[[str], bool]) -> list[str]:
        """Forget the restored zones whose entity no longer exists."""
        async with self._books():
            gone = sorted(zone for zone in self._unconfirmed if not is_registered(zone))
            self._unconfirmed.difference_update(gone)
            for zone in gone:
                self._forget_zone_locked(zone)
        if gone:
            LOGGER.info(
                "DecoderPool: dropped restored zones that are no longer registered: %s",
                gone,
            )
        return gone

    async def drop_unconfirmed(self) -> list[str]:
        """Forget the restored zones that never showed up on the bus."""
        async with self._books():
            gone = sorted(self._unconfirmed)
            self._unconfirmed.clear()
            for zone in gone:
                self._forget_zone_locked(zone)
        if gone:
            LOGGER.info(
                "DecoderPool: dropped restored zones the bus never reported: %s",
                gone,
            )
        return gone

    # ── Public API & Bus / HA State Orchestration ─────────────────────────────

    def _is_decoder_hw_idle(self, dec_id: str) -> bool:
        """Return True if decoder entity (and any companion) is in an idle state."""
        state = self._hass.states.get(dec_id)
        state_val = state.state if state else None
        target_dec_id = self.get_streaming_decoder(dec_id)
        target_state = (
            self._hass.states.get(target_dec_id) if target_dec_id != dec_id else None
        )
        target_state_val = target_state.state if target_state else None
        return state_val in self._IDLE_STATES and (
            target_dec_id == dec_id or target_state_val in self._IDLE_STATES
        )

    def _paused_while_ours(self, dec_id: str, paused_at: datetime) -> bool:
        """Return True if the pause began while one of our zones held the decoder."""
        released = self._released_at.get(dec_id)
        return released is not None and paused_at <= released

    def _recently_paused(self, dec_id: str) -> bool:
        """Return True if the decoder or its companion has been paused for less than PAUSE_TAKEOVER_AFTER."""
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
        """Claim an idle decoder for *zone_entity_id*."""
        displaced_owner: str | None = None
        claimed: tuple[str, int] | None = None
        async with self._books():
            for dec_id, owner in self._assignments.items():
                if owner == zone_entity_id:
                    LOGGER.debug(
                        "Decoder %s already claimed by %s", dec_id, zone_entity_id
                    )
                    prev_leader = self.get_leader(zone_entity_id)
                    self._remove_member_locked(zone_entity_id)
                    if (
                        prev_leader
                        and not self.is_leader(prev_leader)
                        and self.owned_decoder(prev_leader) is None
                    ):
                        self._displaced_zones.add(prev_leader)
                    return (dec_id, self._decoder_map[dec_id])

            if environment is not None:
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

            candidates = list(self._assignments)
            if preferred_source is not None:
                candidates.sort(
                    key=lambda dec: self._decoder_map.get(dec) != preferred_source
                )
            candidates.sort(key=lambda dec: self._assignments[dec] is not None)

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
                    is_owner_off = is_hw_idle and owner_val in (
                        MediaPlayerState.OFF,
                        MediaPlayerState.IDLE,
                        MediaPlayerState.PAUSED,
                        "off",
                        "idle",
                        "paused",
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
                        if owner != zone_entity_id and not is_owner_off:
                            displaced_owner = owner
                        disbanded = self._disband_group_locked(owner)
                        if owner != zone_entity_id:
                            self._displaced_zones.add(owner)
                        self._displaced_zones.update(
                            m for m in disbanded if m != zone_entity_id
                        )
                        self._environments.pop(owner, None)
                        self._unassign_locked(dec_id)
                        owner = None
                    else:
                        continue

                if is_hw_idle or is_handover:
                    prev_leader = self.get_leader(zone_entity_id)
                    self._remove_member_locked(zone_entity_id)
                    if (
                        prev_leader
                        and not self.is_leader(prev_leader)
                        and self.owned_decoder(prev_leader) is None
                    ):
                        self._displaced_zones.add(prev_leader)
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
                LOGGER.debug(
                    "DecoderPool: failed to turn off displaced zone %s: %s",
                    displaced_owner,
                    err,
                )

        if claimed:
            return claimed

        LOGGER.warning(
            "DecoderPool: all decoders busy — zone %s cannot play", zone_entity_id
        )
        return None

    async def set_group(
        self,
        leader_entity_id: str,
        members: Mapping[str, str | None],
    ) -> GroupChange:
        """Make ``members`` the complete member list of ``leader_entity_id``'s group."""
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
        """Add a member zone to the group of leader_entity_id."""
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

    add_group_member = add_member

    async def remove_member(self, member_entity_id: str) -> str | None:
        """Remove a member zone from whichever group it joined."""
        async with self._books():
            return self._remove_member_locked(member_entity_id)

    remove_group_member = remove_member

    async def disband_group(self, leader_entity_id: str) -> list[str]:
        """Disband a group owned by leader_entity_id."""
        async with self._books():
            return self._disband_group_locked(leader_entity_id)

    async def transfer_leadership(
        self, old_leader: str, new_leader: str
    ) -> tuple[str, int] | None:
        """Transfer group leadership and active decoder from old_leader to new_leader."""
        async with self._books():
            return self._transfer_leadership_locked(old_leader, new_leader)

    async def release(self, zone_entity_id: str) -> str | None:
        """Release the decoder assigned to *zone_entity_id* or detach from group."""
        async with self._books():
            leader_decoder = self._remove_member_locked(zone_entity_id)
            if leader_decoder is not None:
                return leader_decoder

            self._disband_group_locked(zone_entity_id)

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

    def environment_owner(
        self, environment: str, exclude: str | None = None
    ) -> str | None:
        """Return the zone that streams from a decoder in ``environment``."""
        owners = self._environment_owners(environment, {exclude} if exclude else set())
        return owners[0] if owners else None

    def _environment_owners(
        self, environment: str, exclude: Collection[str]
    ) -> list[str]:
        """Return every zone outside ``exclude`` actively streaming from a decoder in ``environment``."""
        active = []
        for zone, zone_environment in self._environments.items():
            if (
                zone_environment == environment
                and zone not in exclude
                and self.get_assignment(zone) is not None
            ):
                state = self._hass.states.get(zone)
                state_val = state.state if state else None
                if state_val not in (
                    MediaPlayerState.OFF,
                    MediaPlayerState.IDLE,
                    MediaPlayerState.PAUSED,
                    "off",
                    "idle",
                    "paused",
                ):
                    active.append(zone)
        return active

    def __repr__(self) -> str:  # pragma: no cover
        busy = sum(1 for v in self._assignments.values() if v is not None)
        members = sum(len(m) for m in self._groups.values())
        return (
            f"<DecoderPool decoders={len(self._decoder_map)} "
            f"busy={busy}/{len(self._decoder_map)} members={members}>"
        )
