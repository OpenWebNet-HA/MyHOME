"""Synchronous decoder allocation books and group topology state for DecoderPool (#732)."""
from __future__ import annotations

import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import LOGGER


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


class DecoderAllocationMixin:
    """Synchronous in-memory allocation books and group membership state."""

    _hass: HomeAssistant
    _decoder_map: dict[str, int]
    _pre_gain_map: dict[str, int]
    _assignments: dict[str, str | None]
    _groups: dict[str, set[str]]
    _environments: dict[str, str]
    _stream_incompatible: frozenset[str]
    _companion_map: dict[str, str]
    _former_leaders: dict[str, tuple[str, float]]
    _saved: dict[str, Any] | None
    _released_at: dict[str, datetime]
    _unconfirmed: set[str]
    _displaced_zones: set[str]

    def _snapshot(self) -> dict[str, Any]:
        """Return the books in the form that is saved."""
        return {
            "assignments": {dec: zone for dec, zone in self._assignments.items() if zone},
            "sources": {dec: self._decoder_map[dec] for dec, zone in self._assignments.items() if zone},
            "groups": {leader: sorted(members) for leader, members in self._groups.items() if members},
            "environments": dict(self._environments),
        }

    def restore(self, data: object) -> None:
        """Take over saved books, ignoring whatever no longer fits the configuration."""
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
        for zone in rewired:
            LOGGER.info(
                "DecoderPool: decoder for %s was rewired while Home Assistant was down, dropping its claim",
                zone,
            )
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
        """Note that the bus has reported ``zone_entity_id``'s amplifier."""
        self._unconfirmed.discard(zone_entity_id)

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

    def pop_displaced_zones(self) -> set[str]:
        """Return and clear zones whose decoder or group was taken over by :meth:`claim`."""
        displaced = set(self._displaced_zones)
        self._displaced_zones.clear()
        return displaced

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

    def books(self) -> dict[str, Any]:
        """Return a copy of the books (assignments, sources, groups, environments), as saved."""
        return self._snapshot()

    @property
    def unconfirmed(self) -> frozenset[str]:
        """Return the restored zones the bus has not reported on yet."""
        return frozenset(self._unconfirmed)

    def get_assignment(self, zone_entity_id: str) -> str | None:
        """Return the decoder entity_id assigned to *zone_entity_id*, or ``None``."""
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
        """Return the configured pre-gain offset for *decoder_entity_id*."""
        return self._pre_gain_map.get(decoder_entity_id, 0)

    @property
    def decoder_entity_ids(self) -> list[str]:
        """Return all configured decoder entity IDs, including any companions."""
        ids = list(self._decoder_map.keys())
        for comp in self._companion_map.values():
            if comp not in ids:
                ids.append(comp)
        return ids

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

