"""Lighting resync manager for MyHOME.

Encapsulates debounced state sweeps following group (#G) or general (WHERE=0)
lighting frames, and manages group echo cancellation.
"""
from __future__ import annotations

import collections
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from OWNd.message import OWNLightingCommand, OWNLightingEvent

from .const import (
    LOGGER,
    RESYNC_DEBOUNCE_S,
    RESYNC_LEADING_WINDOW_S,
    area_of_where,
)
from .discovery import Address, parse_unique_id

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler


class LightingResyncManager:
    """Manages lighting resynchronization sweeps and echo suppression."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        *,
        resync_timers: dict[str, CALLBACK_TYPE] | None = None,
        resync_group_echoes: dict[str, int] | None = None,
        recent_ptp: collections.deque[tuple[float, str, str | None]] | None = None,
    ) -> None:
        """Initialize the lighting resync manager."""
        self.handler = handler
        self._resync_timers: dict[str, CALLBACK_TYPE] = (
            resync_timers if resync_timers is not None else {}
        )
        self._resync_group_echoes: dict[str, int] = (
            resync_group_echoes if resync_group_echoes is not None else {}
        )
        self._recent_ptp: collections.deque[tuple[float, str, str | None]] = (
            recent_ptp if recent_ptp is not None else collections.deque()
        )
        self._recent_ptp_polarity: dict[str, bool] = {}
        self._group_members: dict[str, set[str]] = {}
        self._resync_expected_on: dict[str, bool] = {}
        self._resync_echoed_members: dict[str, set[str]] = {}

    @property
    def hass(self) -> HomeAssistant:
        """Return HomeAssistant instance."""
        return self.handler.hass

    @property
    def resync_timers(self) -> dict[str, CALLBACK_TYPE]:
        """Return active resync timers dictionary."""
        return self._resync_timers

    @property
    def resync_group_echoes(self) -> dict[str, int]:
        """Return group echoes counter dictionary."""
        return self._resync_group_echoes

    @property
    def group_members(self) -> dict[str, set[str]]:
        """Return registered group members mapping (#G -> set of point WHEREs)."""
        return self._group_members

    def register_group_members(self, group_where: str, members: list[str] | set[str]) -> None:
        """Register the point-to-point member WHERE addresses belonging to a group."""
        key = group_where if group_where.startswith("#") else f"#{group_where}"
        cleaned = {str(m) for m in members if str(m)}
        if cleaned:
            self._group_members[key] = cleaned
        else:
            self._group_members.pop(key, None)

    @property
    def _time(self) -> Any:
        from . import gateway as gw_module

        return getattr(gw_module, "time", time)

    @property
    def recent_ptp(self) -> collections.deque[tuple[float, str, str | None]]:
        """Return recent point-to-point frame deque."""
        return self._recent_ptp

    def _known_area_map(self) -> dict[str, set[str]]:
        """Return mapping of area -> set of point-to-point WHERE addresses."""
        area_map: dict[str, set[str]] = {}
        config_entry = getattr(self.handler, "config_entry", None)
        if not config_entry or not hasattr(config_entry, "entry_id") or not isinstance(config_entry.entry_id, str):
            return area_map

        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
        for entry in entries:
            if entry.domain in ("light", "switch"):
                _, key = parse_unique_id(entry.unique_id, self.handler.mac)
                if not key:
                    continue
                address = Address.from_device_id(key)
                area = area_of_where(address.where)
                if area:
                    area_map.setdefault(area, set()).add(str(address.where))
        return area_map

    def known_area_entities(self, area: str) -> set[str]:
        """Return the set of known point-to-point light/switch WHERE addresses in an area."""
        return self._known_area_map().get(area, set())

    def _area_fully_echoed(self, area: str, echoed_wheres: set[str]) -> bool:
        """Return True if all known entities in area (or at least one if unpopulated) echoed."""
        if not echoed_wheres:
            return False
        known = self.known_area_entities(area)
        if len(known) > 1:
            return known.issubset(echoed_wheres)
        return True

    def handle_ptp_echo(self, message: OWNLightingEvent) -> None:
        """Handle point-to-point lighting message echo cancellation."""
        now = float(self._time.monotonic())
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            _, evicted_w, _ = self._recent_ptp.popleft()
            self._recent_ptp_polarity.pop(evicted_w, None)
        where_str = str(message.where)
        area = area_of_where(message.where)
        msg_on = getattr(message, "is_on", None)
        self._recent_ptp.append((now, where_str, area))
        if isinstance(msg_on, bool):
            self._recent_ptp_polarity[where_str] = msg_on

        if area and area in self._resync_timers:
            echoed_in_area = {w for _, w, a in self._recent_ptp if a == area}
            if self._area_fully_echoed(area, echoed_in_area):
                LOGGER.debug("%s area %s echoed point status, cancelling sweep", self.handler.log_id, area)
                self._resync_timers.pop(area)()
        for g in [k for k in self._resync_timers if k.startswith("#")]:
            expected_on = self._resync_expected_on.get(g)
            if expected_on is not None and isinstance(msg_on, bool) and msg_on != expected_on:
                continue
            members = self._group_members.get(g)
            if members is not None and where_str not in members:
                continue
            echoed = self._resync_echoed_members.setdefault(g, set())
            echoed.add(where_str)
            self._resync_group_echoes[g] = len(echoed)
            required = len(members) if members else 2
            if self._resync_group_echoes[g] >= required:
                LOGGER.debug("%s group %s saw member echoes, cancelling sweep", self.handler.log_id, g)
                self._resync_timers.pop(g)()
                self._resync_group_echoes.pop(g, None)
                self._resync_echoed_members.pop(g, None)
                self._resync_expected_on.pop(g, None)

    def known_light_areas(self) -> list[str]:
        """Return sorted list of configured light/switch areas."""
        return sorted(self._known_area_map().keys())

    _known_light_areas = known_light_areas

    def schedule_resync(self, message: Any) -> None:
        """Schedule a debounced state resynchronization sweep."""
        if not getattr(self.handler, "broadcast_resync", True):
            return

        dim = getattr(message, "dimension", None)
        is_dimension = isinstance(dim, int) and not isinstance(dim, bool)
        msg_on = getattr(message, "is_on", None)
        if msg_on is None or is_dimension:
            return

        now = float(self._time.monotonic())
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            _, evicted_w, _ = self._recent_ptp.popleft()
            self._recent_ptp_polarity.pop(evicted_w, None)

        targets: list[str] = []
        if getattr(message, "is_group", False):
            group_key = f"#{message.group}"
            members = self._group_members.get(group_key)
            matching_wheres = {
                w
                for t, w, _ in self._recent_ptp
                if t >= now - RESYNC_LEADING_WINDOW_S
                and (members is None or w in members)
                and (not isinstance(msg_on, bool) or self._recent_ptp_polarity.get(w, msg_on) == msg_on)
            }
            required = len(members) if members else 2
            if len(matching_wheres) >= required:
                LOGGER.debug(
                    "%s group #%s had %d leading member echoes, skipping sweep",
                    self.handler.log_id,
                    message.group,
                    len(matching_wheres),
                )
                return
            targets.append(group_key)
        elif getattr(message, "is_area", False):
            raw_where = str(message.where)
            echoed_in_area = {w for _, w, a in self._recent_ptp if a == raw_where}
            if self._area_fully_echoed(raw_where, echoed_in_area):
                LOGGER.debug("%s area %s had leading member echoes, skipping sweep", self.handler.log_id, raw_where)
                return
            targets.append(raw_where)
        elif getattr(message, "is_general", False):
            known_areas = (
                self.handler._known_light_areas()
                if hasattr(self.handler, "_known_light_areas")
                else self.known_light_areas()
            )
            for a in known_areas:
                echoed_in_area = {w for _, w, entry_a in self._recent_ptp if entry_a == a}
                if self._area_fully_echoed(a, echoed_in_area):
                    LOGGER.debug("%s general sweep skipping area %s (had leading echoes)", self.handler.log_id, a)
                    continue
                targets.append(f"{a}")

        from . import gateway as gw_module

        call_later = getattr(gw_module, "async_call_later", async_call_later)

        for where in targets:
            if where in self._resync_timers:
                self._resync_timers.pop(where)()
            if where.startswith("#"):
                self._resync_group_echoes[where] = 0
                self._resync_echoed_members[where] = set()
                if isinstance(msg_on, bool):
                    self._resync_expected_on[where] = msg_on

            @callback
            def _cb(_now_cb: Any, w: str = where) -> None:
                self.hass.async_create_task(self.handler._resync_broadcast(w))

            self._resync_timers[where] = call_later(self.hass, RESYNC_DEBOUNCE_S, _cb)

    _schedule_resync = schedule_resync

    async def execute_resync(self, where: str) -> None:
        """Execute the status query for the given area or group."""
        self._resync_timers.pop(where, None)
        self._resync_group_echoes.pop(where, None)
        self._resync_echoed_members.pop(where, None)
        self._resync_expected_on.pop(where, None)
        if getattr(self.handler, "_terminate_listener", False):
            return

        await self.handler.send_status_request(OWNLightingCommand.status(where))

    _execute_resync = execute_resync
    _resync_broadcast = execute_resync

    def cancel_all(self) -> None:
        """Cancel all pending resync timers and clear state."""
        for t in self._resync_timers.values():
            t()
        self._resync_timers.clear()
        self._resync_group_echoes.clear()
        self._resync_echoed_members.clear()
        self._resync_expected_on.clear()
        self._recent_ptp.clear()
        self._recent_ptp_polarity.clear()
