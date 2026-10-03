"""Multi-room grouping of MyHOME audio zones: join, hand-over, park and wake."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player.const import MediaPlayerState
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNSoundCommand

from .const import (
    CONF_AUTO_JOIN_STREAMING,
    DEFAULT_AUTO_JOIN_STREAMING,
    DOMAIN,
    LOGGER,
)
from .data import MyHOMERuntimeData
from .decoder_pool import DecoderPool, EnvironmentBusyError
from .media_player_decoder import ZoneDecoderLayer
from .media_player_routing import zone_environment

if TYPE_CHECKING:
    from .media_player import MyHOMEMediaPlayer

# A member dropped from a group without an explicit handover to it — its own
# unjoin, or left out of a join snapshot — is not switched off right away.
# Music Assistant sometimes removes a departing member from its old group
# first and only starts play_media on it as a new leader a couple of seconds
# later (the leader->member handover in async_unjoin_player, via
# DecoderPool.transfer_leadership, already covers the deselect-the-leader
# case atomically and needs no grace period). Sending the OFF immediately
# here would silence the room and immediately wake it again. Waiting lets a
# follow-up play_media (or turn_on/join) cancel the OFF and keep playing
# without a gap. A room that is not reused this way is switched off once the
# grace period elapses, same as before, just delayed.
_GROUP_LEAVE_GRACE = 5.0  # seconds


def _get_group_members(runtime: MyHOMERuntimeData | None, entity_id: str) -> list[str] | None:
    """Return group members for entity_id (leader first), or None if not grouped."""
    if runtime is None or runtime.decoder_pool is None:
        return None
    return runtime.decoder_pool.get_group_members(entity_id)


class ZoneGroupLayer(ZoneDecoderLayer):
    """The group a zone leads or belongs to, and its power-down and wake-up."""

    @property
    def group_members(self) -> list[str] | None:
        """Return a list of entity ids belonging to this entity's group, leader first."""
        return _get_group_members(self._runtime_data, self.entity_id)

    async def async_join_players(self, group_members: list[str]) -> None:
        """Add players to this zone's group (additive; existing members stay).

        ``group_members`` is treated as the members to add, not the desired
        total membership: Music Assistant's own HA player provider calls this
        with only the newly added entities, and removes a member with a
        separate ``unjoin`` call rather than a smaller ``group_members`` list
        (confirmed against its source — see ``set_members`` in
        ``music_assistant/providers/hass_players/player.py``, upstream). A
        snapshot interpretation silently dropped every existing member on the
        next add: adding a third zone to a two-zone group replaced the second
        zone instead of joining the third.

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

        # Leading a group means this zone is in active use, even though the
        # loop below never calls _async_wake_zone() on self (it is presumed
        # already playing).
        self._cancel_pending_off()
        self._cancel_auto_off()  # a stray-room timer must not take the new leader down

        leader_env = zone_environment(self._where)
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

        # Additive: keep every current member, add the newly requested ones.
        # See the docstring for why — dropping to a snapshot of just
        # group_members is exactly the bug this guards against.
        current_members = set(pool.get_members(self.entity_id))
        desired_members = current_members | {m for m in group_members if m != self.entity_id}

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
            member_env = zone_environment(member_ent._where)
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
                    member_id: zone_environment(runtime.media_players[member_id]._where)
                    for member_id in sorted(desired_members)
                },
            )
        except EnvironmentBusyError as err:
            raise self._environment_busy_error(err.owner, err.environment) from err
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

        # change.left is always empty via this additive call (desired_members
        # is a superset of current_members); kept for symmetry with
        # set_group's general contract. Rooms of a group a joining zone used
        # to lead (change.orphaned) would keep listening to a stream nobody
        # controls any more.
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
                    await member_ent._route_to(source_num, coalesce=True)
                await member_ent._async_wake_zone()
            member_ent.async_write_ha_state()

        self.async_write_ha_state()

    async def _async_hand_over_leadership(
        self, pool: DecoderPool, members: list[str], from_bus: bool = False
    ) -> None:
        """Switch this leader's amplifier off and pass the group to its first member.

        The bus audio does not run through the leader's amplifier, so the decoder
        keeps streaming and the other rooms keep their routes: only this room
        goes quiet. Music Assistant cannot deselect its group leader, and a
        leader turned off from Home Assistant or a wall panel must not take the
        whole house down with it.

        The bus side is uninterrupted, but Music Assistant still moves its queue
        to the new leader by stopping it and starting a new stream, so the
        listener hears a short gap. That is inherent to a room owning the queue
        and is documented in docs/configuration/media_player.md ("Why
        deselecting the group leader gives a short gap"); do not try to hide it
        here.
        """
        if self._turning_off:
            return  # a second OFF (HA plus the bus echo) while the first is handing over
        self._turning_off = True
        try:
            runtime = self._runtime_data
            new_leader_id = members[0]
            new_leader_ent = runtime.media_players.get(new_leader_id) if runtime else None
            if new_leader_ent is None:
                LOGGER.warning(
                    "%s: handing the group to %s, which is not a MyHOME sound zone here",
                    self.entity_id,
                    new_leader_id,
                )

            result = await pool.transfer_leadership(self.entity_id, new_leader_id)
            if result is None:
                LOGGER.debug("%s: the group was already handed on; nothing to do", self.entity_id)
                return
            if new_leader_ent:
                new_leader_ent._active_decoder = result[0]

            self._cancel_pending_off()
            self._cancel_auto_off()
            self._parked = False
            self._wake_pending = False
            if not from_bus:
                await self._gateway_handler.send(OWNSoundCommand.turn_off(self._where))
        finally:
            self._turning_off = False
        self._attr_state = MediaPlayerState.OFF
        self._active_decoder = None
        self.async_write_ha_state()

        if new_leader_ent:
            new_leader_ent.async_write_ha_state()
        for mem_id in members[1:]:
            mem_ent = runtime.media_players.get(mem_id) if runtime else None
            if mem_ent:
                mem_ent.async_write_ha_state()

    async def async_unjoin_player(self) -> None:
        """Unjoin this player from whichever group it belongs to.

        A departing member's amplifier is not switched off immediately — see
        :data:`_GROUP_LEAVE_GRACE` — since it may be dropped from its old
        group right before becoming a new leader elsewhere.
        """
        pool = self._get_pool()
        if not pool:
            return
        runtime = self._runtime_data
        members = pool.get_members(self.entity_id)
        if members:
            # We are the leader: handover to the first remaining member
            await self._async_hand_over_leadership(pool, members)
        else:
            # We are a member: leave our group
            leader_id = pool.get_leader(self.entity_id)
            if leader_id:
                await pool.remove_group_member(self.entity_id)
                self._schedule_delayed_off()
                self.async_write_ha_state()
                leader_ent = runtime.media_players.get(leader_id) if runtime else None
                if leader_ent:
                    leader_ent.async_write_ha_state()
            else:
                # Standalone player: power off cleanly
                await self.async_turn_off()

    async def _async_power_off_zone(self, zone: MyHOMEMediaPlayer) -> None:
        """Schedule ``zone``'s amplifier off because its group no longer includes it.

        Not sent immediately: see :data:`_GROUP_LEAVE_GRACE`.
        """
        zone._schedule_delayed_off()

    @callback
    def _schedule_delayed_off(self) -> None:
        """Turn this zone off after :data:`_GROUP_LEAVE_GRACE`, unless reclaimed first.

        Replaces any grace period already pending, so repeated departures
        (e.g. dropped from one group, then another) do not stack up timers.
        """
        self._cancel_pending_off()
        self._pending_off_task = self.hass.async_create_task(
            self._async_delayed_off(), f"{self.entity_id} group-leave OFF"
        )

    async def _async_delayed_off(self) -> None:
        """Switch the room off once the grace period elapses without a reclaim.

        The full turn-off, not just the frame: it also releases whatever the
        room still holds and stops a decoder it owns, instead of leaving that
        to the bus echo of the OFF, which may never arrive.
        """
        await asyncio.sleep(_GROUP_LEAVE_GRACE)
        self._pending_off_task = None
        # A room that is already off (parked, say) needs no second OFF frame:
        # each one costs the single command session about 0.8 s.
        already_off = self._attr_state == MediaPlayerState.OFF and not self._wake_pending
        await self._async_handle_turn_off(from_bus=already_off)
        self.async_write_ha_state()

    @callback
    def _cancel_pending_off(self) -> None:
        """Cancel a scheduled group-leave OFF: the zone is in use again.

        Called wherever a zone is woken, joined, or otherwise put back to
        work — see :data:`_GROUP_LEAVE_GRACE` for why the OFF is delayed at
        all. Cancelling a task that already finished sending its OFF is a
        harmless no-op; the reference is cleared either way.
        """
        if self._pending_off_task is not None:
            self._pending_off_task.cancel()
            self._pending_off_task = None

    def _group_entities(self, pool: DecoderPool) -> list[MyHOMEMediaPlayer]:
        """Return this room and the entities of its group members."""
        runtime = self._runtime_data
        members = [
            runtime.media_players[member_id]
            for member_id in pool.get_members(self.entity_id)
            if runtime and member_id in runtime.media_players
        ]
        return [self, *members]

    def _forget_recent_routing(self) -> None:
        """Forget the routing frames sent lately: the amplifiers are about to go off."""
        if self._runtime_data is not None:
            self._runtime_data.routing_recent.clear()

    async def _async_park_group(self) -> None:
        """Switch the amplifiers of a leader and its members off, but keep the group.

        The anti-hiss timer must silence the rooms once the music has stopped,
        yet the group is something the listener built (in Music Assistant, say)
        and expects to find again when they press play. So the amplifiers go
        off and the books stay as they are: the leader keeps its decoder and
        its members, and the next play, resume or turn-on wakes them all.
        """
        pool = self._get_pool()
        if pool is None or self._turning_off:
            return
        self._forget_recent_routing()
        self._turning_off = True
        try:
            LOGGER.info(
                "%s: decoder stopped — switching the group's amplifiers off and keeping the group",
                self.entity_id,
            )
            for ent in self._group_entities(pool):
                ent._parked = True  # before the frame: its OFF echo must not leave the group
                ent._wake_pending = False
                ent._cancel_auto_off()
                ent._attr_state = MediaPlayerState.OFF
                try:
                    await ent._gateway_handler.send(OWNSoundCommand.turn_off(ent._where))
                except Exception as err:
                    LOGGER.debug("%s: could not switch off while parking: %s", ent.entity_id, err)
                ent.async_write_ha_state()
        finally:
            self._turning_off = False

    def _begin_wake_of_parked_group(self, pool: DecoderPool) -> None:
        """Show every parked room of this group as on before the slow wake frames go out.

        Waking an amplifier takes about a second a frame. Music Assistant looks
        at the group the moment the leader is on, and drops a member that is
        still reporting paused or idle, so the members must already say "on".
        """
        for ent in self._group_entities(pool):
            if ent._parked:
                ent._wake_pending = True
                ent.async_write_ha_state()

    async def _async_unpark_group(self) -> asyncio.Task[None] | None:
        """Wake the leader of a parked group on its input and start waking the members.

        Only the leader's frames go out in front of the caller: it is audible
        after about three frames, and the caller can start the stream while the
        members' frames follow in a background task. That task is returned (None
        when there is none) and has to be handed to :meth:`_async_finish_group_wake`.
        """
        pool = self._get_pool()
        if pool is None:
            await self._async_wake_zone()
            return None
        self._begin_wake_of_parked_group(pool)
        decoder_id = self._active_decoder or pool.owned_decoder(self.entity_id)
        source_num = pool.decoder_source(decoder_id) if decoder_id else None
        route = self._routing_configured() and source_num is not None
        sent: set[str] = set()
        await self._async_wake_zone()
        if route and source_num is not None:
            await self._route_to(source_num, sent)
        self.async_write_ha_state()
        members = self._group_entities(pool)[1:]
        if not members:
            return None
        return self.hass.async_create_background_task(
            self._async_wake_parked_members(members, source_num if route else None, sent),
            f"{self.entity_id} wake parked group members",
        )

    async def _async_wake_parked_members(
        self,
        members: list[MyHOMEMediaPlayer],
        source_num: int | None,
        sent: set[str],
    ) -> None:
        """Wake the members of a parked group one after another, routing each to the source."""
        for ent in members:
            await ent._async_wake_zone()
            if source_num is not None:
                await ent._route_to(source_num, sent)
            ent.async_write_ha_state()

    async def _async_finish_group_wake(
        self,
        members_task: asyncio.Task[None] | None,
        play: Coroutine[Any, Any, None] | None = None,
    ) -> None:
        """Run ``play`` next to the members' wake, then wait for the wake to end.

        Whatever way it ends, no room is left reporting on while its amplifier is
        still off, and a failed or cancelled play stops the members' wake.
        """
        try:
            if play is not None:
                await play
            if members_task is not None:
                await members_task
        except BaseException:
            if members_task is not None:
                members_task.cancel()
                await asyncio.gather(members_task, return_exceptions=True)
            raise
        finally:
            pool = self._get_pool()
            for ent in self._group_entities(pool) if pool else [self]:
                if ent._wake_pending:
                    ent._wake_pending = False
                    ent.async_write_ha_state()

    async def _async_drop_from_group(self, pool: DecoderPool, leader_id: str) -> None:
        """Drop this member from group when its source changes on the bus."""
        await pool.remove_group_member(self.entity_id)
        self._cancel_pending_off()
        self._cancel_auto_off()
        self._parked = False
        self._wake_pending = False
        self.async_write_ha_state()
        runtime = self._runtime_data
        leader_ent = runtime.media_players.get(leader_id) if runtime else None
        if leader_ent:
            leader_ent.async_write_ha_state()

    async def _async_drop_leader_on_source_change(
        self, pool: DecoderPool, source_num: int, environment: str
    ) -> None:
        """Drop this leader from its streaming session when its source changes on the bus."""
        runtime = self._runtime_data
        members = pool.get_members(self.entity_id)

        # Any members sharing this environment also switch to source_num
        same_env_members: list[str] = []
        if runtime is not None:
            same_env_members = [
                m
                for m in members
                if m in runtime.media_players
                and zone_environment(runtime.media_players[m]._where) == environment
            ]
            for same_m in same_env_members:
                await pool.remove_group_member(same_m)
                same_ent = runtime.media_players.get(same_m)
                if same_ent:
                    same_ent._attr_source = self._source_label(source_num)
                    same_ent._cancel_pending_off()
                    same_ent._cancel_auto_off()
                    same_ent._parked = False
                    same_ent._wake_pending = False
                    same_ent.async_write_ha_state()

        remaining_members = [m for m in members if m not in same_env_members]

        if remaining_members:
            new_leader_id = remaining_members[0]
            new_leader_ent = runtime.media_players.get(new_leader_id) if runtime else None
            result = await pool.transfer_leadership(self.entity_id, new_leader_id)
            if result and new_leader_ent:
                new_leader_ent._active_decoder = result[0]
                new_leader_ent._cancel_pending_off()
                new_leader_ent._cancel_auto_off()

            self._active_decoder = None
            self._cancel_pending_off()
            self._cancel_auto_off()
            self._parked = False
            self._wake_pending = False
            self.async_write_ha_state()

            if new_leader_ent:
                new_leader_ent.async_write_ha_state()
            for mem_id in remaining_members[1:]:
                mem_ent = runtime.media_players.get(mem_id) if runtime else None
                if mem_ent:
                    mem_ent.async_write_ha_state()

            LOGGER.info(
                "%s: leader source changed to %d on bus — transferred leadership to %s",
                self.entity_id,
                source_num,
                new_leader_id,
            )
        else:
            # Standalone leader or solo player on decoder: release the decoder and stop playback
            active_dec = self._active_decoder or pool.owned_decoder(self.entity_id)
            if active_dec:
                target_dec = self._streaming_target(active_dec) or active_dec
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
                if target_dec != active_dec:
                    try:
                        await self.hass.services.async_call(
                            "media_player", "media_stop", {"entity_id": active_dec}
                        )
                    except Exception as err:
                        LOGGER.debug(
                            "%s: failed to stop hardware decoder %s: %s",
                            self.entity_id,
                            active_dec,
                            err,
                        )
            await pool.release(self.entity_id)
            self._active_decoder = None
            self._cancel_pending_off()
            self._cancel_auto_off()
            self._parked = False
            self._wake_pending = False
            self.async_write_ha_state()
            LOGGER.info(
                "%s: source changed to %d on bus — released streaming decoder",
                self.entity_id,
                source_num,
            )

    async def _async_wake_members(
        self,
        pool: DecoderPool,
        decoder_id: str,
        source_num: int,
        route: bool,
        sent: set[str],
    ) -> None:
        """Wake and route the members of this leader's group, one after another."""
        runtime = self._runtime_data
        for member_id in pool.get_members(self.entity_id):
            member_ent = runtime.media_players.get(member_id) if runtime else None
            member_env = zone_environment(member_ent._where) if member_ent else None
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
                    await member_ent._route_to(source_num, sent)
                await member_ent._async_wake_zone()
                member_ent.async_write_ha_state()

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

    async def _async_auto_join_active_stream(self) -> None:
        """Auto-join an active streaming group when this room turns on or adjusts volume."""
        if self._auto_joining:
            return
        self._auto_joining = True
        try:
            options = self._options()
            if not options.get(CONF_AUTO_JOIN_STREAMING, DEFAULT_AUTO_JOIN_STREAMING):
                return

            pool = self._get_pool()
            runtime = self._runtime_data
            if pool is None or runtime is None or not pool.is_configured:
                return

            # Already in a group or owns a decoder
            if pool.get_leader(self.entity_id) or pool.is_leader(self.entity_id) or self._active_decoder:
                return

            if self._attr_state != MediaPlayerState.ON or self._parked or self._turning_off:
                return

            source_num = self._source_number(self._attr_source) if self._attr_source else None
            if source_num is None:
                source_num = self._default_source()
            if source_num is None:
                return

            decoder_id = pool.get_decoder_for_source(source_num)
            if decoder_id is None:
                return

            leader_id = pool.get_decoder_owner(decoder_id)
            if not leader_id or leader_id == self.entity_id:
                return

            leader_ent = runtime.media_players.get(leader_id)
            if leader_ent is None:
                return

            dec_state = self._resolve_playback_state(decoder_id, allow_idle=False)
            if dec_state not in (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING):
                return

            member_env = zone_environment(self._where)
            try:
                await pool.add_member(leader_id, self.entity_id, member_env)
            except EnvironmentBusyError as err:
                LOGGER.debug("%s: cannot auto-join group of %s: %s", self.entity_id, leader_id, err)
                return
            except Exception as err:
                LOGGER.warning("%s: unexpected error auto-joining group of %s: %s", self.entity_id, leader_id, err)
                return

            self._active_decoder = None
            self._cancel_auto_off()
            self._cancel_pending_off()
            if self._attr_source is None:
                self._attr_source = self._source_label(source_num)

            self.async_write_ha_state()
            leader_ent.async_write_ha_state()
            LOGGER.info(
                "%s: physical wall activation auto-joined active streaming group of %s on source %d",
                self.entity_id,
                leader_id,
                source_num,
            )
        finally:
            self._auto_joining = False
