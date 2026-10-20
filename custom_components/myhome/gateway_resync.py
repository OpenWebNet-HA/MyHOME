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
    BUS_ROUTING,
    LOGGER,
    RESYNC_DEBOUNCE_S,
    RESYNC_LEADING_WINDOW_S,
    area_of_where,
    normalize_where,
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
        self._ptp_polarities: dict[tuple[float, str], bool | None] = {}
        self._resync_area_echoes: dict[str, set[str]] = {}
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

    @staticmethod
    def _message_interface(message: Any) -> str | None:
        """Extract the F422 bus interface string (e.g. ``"01"``) from a message, if any."""
        iface = getattr(message, "interface", None)
        if isinstance(iface, str) and iface:
            return iface
        params = getattr(message, "where_param", getattr(message, "_where_param", None))
        if isinstance(params, (list, tuple)) and len(params) > 1 and str(params[0]) == "4":
            return str(params[1])
        return None

    @staticmethod
    def _normalize_group_key(group_where: str | int, interface: str | None = None) -> str:
        """Normalize a group address to ``#G`` or ``#G#4#<interface>``."""
        raw = str(group_where).strip()
        body = raw[1:] if raw.startswith("#") else raw
        grp_part, sep, iface_part = body.partition(BUS_ROUTING)
        grp_norm = str(int(grp_part)) if grp_part.isdigit() else grp_part
        iface = iface_part if (sep and iface_part) else (str(interface).strip() if interface else "")
        if iface:
            iface_norm = iface.zfill(2) if iface.isdigit() else iface
            return f"#{grp_norm}{BUS_ROUTING}{iface_norm}"
        return f"#{grp_norm}"

    @staticmethod
    def _normalize_ptp_where(raw_where: str | int, interface: str | None = None) -> str:
        """Normalize a point-to-point WHERE while preserving any ``#4#<interface>`` suffix."""
        where_str = str(raw_where).strip()
        base_part, sep, iface_part = where_str.partition(BUS_ROUTING)
        clean_base = str(normalize_where(base_part.split("#", 1)[0]))
        iface = iface_part if (sep and iface_part) else (str(interface).strip() if interface else "")
        if iface:
            iface_norm = iface.zfill(2) if iface.isdigit() else iface
            return f"{clean_base}{BUS_ROUTING}{iface_norm}"
        return clean_base

    def register_group_members(self, group_where: str, members: list[str] | set[str]) -> None:
        """Register the point-to-point member WHERE addresses belonging to a group."""
        key = self._normalize_group_key(group_where)
        cleaned = {self._normalize_ptp_where(m) for m in members if str(m).strip()}
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

    def _prune_recent_ptp(self, now: float) -> None:
        """Prune expired point-to-point echo records older than the leading window."""
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            old_ts, old_where, _ = self._recent_ptp.popleft()
            self._ptp_polarities.pop((old_ts, old_where), None)

    def handle_ptp_echo(self, message: OWNLightingEvent) -> None:
        """Handle point-to-point lighting message echo cancellation."""
        now = float(self._time.monotonic())
        self._prune_recent_ptp(now)
        ptp_where = self._normalize_ptp_where(message.where, self._message_interface(message))
        clean_where = normalize_where(str(message.where).split("#", 1)[0])
        area = area_of_where(clean_where)
        msg_on = getattr(message, "is_on", None)
        self._recent_ptp.append((now, ptp_where, area))
        self._ptp_polarities[(now, ptp_where)] = msg_on

        if area and area in self._resync_timers:
            expected_area_on = self._resync_expected_on.get(area)
            has_known = bool(self.known_area_entities(area))
            if (
                has_known
                and expected_area_on is not None
                and isinstance(msg_on, bool)
                and msg_on != expected_area_on
            ):
                if area in self._resync_area_echoes:
                    self._resync_area_echoes[area].discard(clean_where)
            else:
                echoed = self._resync_area_echoes.setdefault(area, set())
                echoed.add(clean_where)
                if self._area_fully_echoed(area, echoed):
                    LOGGER.debug("%s area %s echoed point status, cancelling sweep", self.handler.log_id, area)
                    self._resync_timers.pop(area)()
                    self._resync_area_echoes.pop(area, None)
                    self._resync_expected_on.pop(area, None)
        for g in [k for k in self._resync_timers if k.startswith("#")]:
            expected_on = self._resync_expected_on.get(g)
            if expected_on is not None and isinstance(msg_on, bool) and msg_on != expected_on:
                if g in self._resync_echoed_members:
                    self._resync_echoed_members[g].discard(ptp_where)
                    self._resync_group_echoes[g] = len(self._resync_echoed_members[g])
                continue
            members = self._group_members.get(g)
            if members is not None:
                if ptp_where not in members:
                    continue
            elif g.partition(BUS_ROUTING)[2] != ptp_where.partition(BUS_ROUTING)[2]:
                continue
            echoed_grp = self._resync_echoed_members.setdefault(g, set())
            echoed_grp.add(ptp_where)
            self._resync_group_echoes[g] = len(echoed_grp)
            required = len(members) if members else 2
            if self._resync_group_echoes[g] >= required:
                LOGGER.debug("%s group %s saw member echoes, cancelling sweep", self.handler.log_id, g)
                self._resync_timers.pop(g)()
                self._resync_group_echoes.pop(g, None)
                self._resync_echoed_members.pop(g, None)
                self._resync_expected_on.pop(g, None)

    def _known_area_map(self) -> dict[str, set[str]]:
        """Return mapping of area code to registered light/switch WHERE addresses."""
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
                clean_where = normalize_where(address.clean_where)
                area = area_of_where(clean_where)
                if area:
                    area_map.setdefault(area, set()).add(clean_where)
        return area_map

    def known_light_areas(self) -> list[str]:
        """Return sorted list of configured light/switch areas."""
        return sorted(self._known_area_map().keys())

    _known_light_areas = known_light_areas

    def known_area_entities(self, area: str) -> set[str]:
        """Return configured light/switch WHERE addresses in a given area."""
        return self._known_area_map().get(area, set())

    def _area_fully_echoed(self, area: str, echoed_wheres: set[str]) -> bool:
        """Return True if all known entities in area have emitted point-to-point echoes.

        When the entity registry has one or more configured entities for ``area``,
        every registered entity in that area must have echoed before the area
        resync query (`*#1*<area>##`) can be skipped or cancelled. When no
        entities are registered for ``area`` (e.g. unit tests mocking
        ``_known_light_areas`` without populating the entity registry), any
        echo in ``area`` is treated as sufficient.
        """
        if not echoed_wheres:
            return False
        known = self.known_area_entities(area)
        if known:
            return known.issubset(echoed_wheres)
        return True

    def schedule_resync(self, message: Any) -> None:
        """Schedule a debounced state resynchronization sweep."""
        if not getattr(self.handler, "broadcast_resync", True):
            return

        dim = getattr(message, "dimension", None)
        is_dimension = isinstance(dim, int) and not isinstance(dim, bool)
        msg_is_on = getattr(message, "is_on", None)
        if msg_is_on is None or is_dimension:
            return

        now = float(self._time.monotonic())
        self._prune_recent_ptp(now)

        def _leading_echoes_for_area(target_area: str) -> set[str]:
            has_known = bool(self.known_area_entities(target_area))
            latest_pol: dict[str, bool | None] = {}
            for t, w, entry_a in self._recent_ptp:
                if entry_a != target_area:
                    continue
                clean_w = normalize_where(str(w).split("#", 1)[0])
                latest_pol[clean_w] = self._ptp_polarities.get((t, str(w)))
            return {
                clean_w
                for clean_w, entry_pol in latest_pol.items()
                if not has_known or entry_pol is None or msg_is_on is None or entry_pol == msg_is_on
            }

        targets: list[str] = []
        area_seed_echoes: dict[str, set[str]] = {}
        if getattr(message, "is_group", False):
            group_key = self._normalize_group_key(message.group, self._message_interface(message))
            members = self._group_members.get(group_key)
            latest_grp_pol: dict[str, bool | None] = {}
            for t, w, _ in self._recent_ptp:
                ptp_w = self._normalize_ptp_where(w)
                if members is not None:
                    if ptp_w not in members:
                        continue
                elif group_key.partition(BUS_ROUTING)[2] != ptp_w.partition(BUS_ROUTING)[2]:
                    continue
                latest_grp_pol[ptp_w] = self._ptp_polarities.get((t, str(w)))
            matching_wheres = {
                ptp_w
                for ptp_w, entry_pol in latest_grp_pol.items()
                if not isinstance(msg_is_on, bool) or entry_pol is None or entry_pol == msg_is_on
            }
            required = len(members) if members else 2
            if len(matching_wheres) >= required:
                LOGGER.debug(
                    "%s group %s had %d leading member echoes, skipping sweep",
                    self.handler.log_id,
                    group_key,
                    len(matching_wheres),
                )
                return
            targets.append(group_key)
        elif getattr(message, "is_area", False):
            raw_where = str(message.where)
            leading_echoes = _leading_echoes_for_area(raw_where)
            if self._area_fully_echoed(raw_where, leading_echoes):
                LOGGER.debug("%s area %s had leading member echoes, skipping sweep", self.handler.log_id, raw_where)
                return
            area_seed_echoes[raw_where] = leading_echoes
            targets.append(raw_where)
        elif getattr(message, "is_general", False):
            known_areas = (
                self.handler._known_light_areas()
                if hasattr(self.handler, "_known_light_areas")
                else self.known_light_areas()
            )
            for a in known_areas:
                leading_echoes = _leading_echoes_for_area(a)
                if self._area_fully_echoed(a, leading_echoes):
                    LOGGER.debug("%s general sweep skipping area %s (had leading echoes)", self.handler.log_id, a)
                    continue
                area_seed_echoes[a] = leading_echoes
                targets.append(f"{a}")

        from . import gateway as gw_module

        call_later = getattr(gw_module, "async_call_later", async_call_later)

        for where in targets:
            if where in self._resync_timers:
                self._resync_timers.pop(where)()
            self._resync_expected_on[where] = bool(msg_is_on)
            if where.startswith("#"):
                self._resync_group_echoes[where] = 0
                self._resync_echoed_members[where] = set()
            else:
                self._resync_area_echoes[where] = set(area_seed_echoes.get(where, set()))

            @callback
            def _cb(_now_cb: Any, w: str = where) -> None:
                self.hass.async_create_task(self.handler._resync_broadcast(w))

            self._resync_timers[where] = call_later(self.hass, RESYNC_DEBOUNCE_S, _cb)

    _schedule_resync = schedule_resync

    async def execute_resync(self, where: str) -> None:
        """Execute the status query for the given area or group."""
        self._resync_timers.pop(where, None)
        self._resync_group_echoes.pop(where, None)
        self._resync_area_echoes.pop(where, None)
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
        self._resync_area_echoes.clear()
        self._resync_echoed_members.clear()
        self._resync_expected_on.clear()
        self._recent_ptp.clear()
        self._ptp_polarities.clear()
