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
    def _time(self) -> Any:
        from . import gateway as gw_module

        return getattr(gw_module, "time", time)

    @property
    def recent_ptp(self) -> collections.deque[tuple[float, str, str | None]]:
        """Return recent point-to-point frame deque."""
        return self._recent_ptp

    def handle_ptp_echo(self, message: OWNLightingEvent) -> None:
        """Handle point-to-point lighting message echo cancellation."""
        now = float(self._time.monotonic())
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            self._recent_ptp.popleft()
        area = area_of_where(message.where)
        self._recent_ptp.append((now, str(message.where), area))

        if area and area in self._resync_timers:
            LOGGER.debug("%s area %s echoed point status, cancelling sweep", self.handler.log_id, area)
            self._resync_timers.pop(area)()
        for g in [k for k in self._resync_timers if k.startswith("#")]:
            self._resync_group_echoes[g] = self._resync_group_echoes.get(g, 0) + 1
            if self._resync_group_echoes[g] >= 2:
                LOGGER.debug("%s group %s saw member echoes, cancelling sweep", self.handler.log_id, g)
                self._resync_timers.pop(g)()
                self._resync_group_echoes.pop(g, None)

    def known_light_areas(self) -> list[str]:
        """Return sorted list of configured light/switch areas."""
        areas: set[str] = set()
        config_entry = getattr(self.handler, "config_entry", None)
        if not config_entry or not hasattr(config_entry, "entry_id") or not isinstance(config_entry.entry_id, str):
            return []

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
                    areas.add(area)
        return sorted(list(areas))

    _known_light_areas = known_light_areas

    def schedule_resync(self, message: Any) -> None:
        """Schedule a debounced state resynchronization sweep."""
        if not getattr(self.handler, "broadcast_resync", True):
            return

        now = float(self._time.monotonic())
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            self._recent_ptp.popleft()

        targets: list[str] = []
        if getattr(message, "is_group", False):
            recent_count = sum(1 for t, _, _ in self._recent_ptp if t >= now - RESYNC_LEADING_WINDOW_S)
            if recent_count >= 2:
                LOGGER.debug(
                    "%s group #%s had %d leading member echoes, skipping sweep",
                    self.handler.log_id,
                    message.group,
                    recent_count,
                )
                return
            targets.append(f"#{message.group}")
        elif getattr(message, "is_area", False):
            raw_where = str(message.where)
            if any(a == raw_where for _, _, a in self._recent_ptp):
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
                if any(entry_a == a for _, _, entry_a in self._recent_ptp):
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

            @callback
            def _cb(_now_cb: Any, w: str = where) -> None:
                self.hass.async_create_task(self.handler._resync_broadcast(w))

            self._resync_timers[where] = call_later(self.hass, RESYNC_DEBOUNCE_S, _cb)

    _schedule_resync = schedule_resync

    async def execute_resync(self, where: str) -> None:
        """Execute the status query for the given area or group."""
        self._resync_timers.pop(where, None)
        self._resync_group_echoes.pop(where, None)
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
        self._recent_ptp.clear()
