"""Monostable impulse cover strategy for garage doors and motorized gates."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationCommand, OWNAutomationEvent


class ImpulseDoorStrategy:
    """Monostable pulse strategy for garage doors and gates without position slider."""

    def __init__(self, pulse_duration: float = 0.5) -> None:
        """Initialize impulse door strategy with relay pulse duration in seconds."""
        self.pulse_duration = max(0.1, float(pulse_duration))

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "impulse"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return OPEN | CLOSE | STOP without SET_POSITION."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )

    def current_position(self, entity: Any, now: float) -> int | None:
        """Impulse doors do not expose a percentage position."""
        return None

    async def _send_pulse(self, entity: Any, direction: str) -> Any:
        """Send directional relay activation followed by stop after pulse_duration."""
        cmd = (
            OWNAutomationCommand.raise_shutter(entity._full_where)
            if direction == "open"
            else OWNAutomationCommand.lower_shutter(entity._full_where)
        )
        entity._attr_is_opening = direction == "open"
        entity._attr_is_closing = direction == "close"
        entity._attr_is_closed = direction == "close"
        written = await entity._gateway_handler.send(cmd)
        entity._publish_state()
        await asyncio.sleep(self.pulse_duration)
        await entity._gateway_handler.send(
            OWNAutomationCommand.stop_shutter(entity._full_where)
        )
        entity._attr_is_opening = False
        entity._attr_is_closing = False
        entity._publish_state()
        return written

    async def async_open(self, entity: Any) -> Any:
        """Pulse open contact."""
        return await self._send_pulse(entity, "open")

    async def async_close(self, entity: Any) -> Any:
        """Pulse close contact."""
        return await self._send_pulse(entity, "close")

    async def async_stop(self, entity: Any) -> None:
        """Send immediate stop command."""
        entity._attr_is_opening = False
        entity._attr_is_closing = False
        await entity._gateway_handler.send(
            OWNAutomationCommand.stop_shutter(entity._full_where)
        )
        entity._publish_state()

    async def async_set_position(self, entity: Any, position: int) -> None:
        """Map 0% to close pulse and >0% to open pulse."""
        if position <= 0:
            await self.async_close(entity)
        else:
            await self.async_open(entity)

    def handle_event(self, entity: Any, message: OWNAutomationEvent, now: float) -> None:
        """Update door state from bus event."""
        if message.is_opening:
            entity._attr_is_opening = True
            entity._attr_is_closing = False
            entity._attr_is_closed = False
        elif message.is_closing:
            entity._attr_is_opening = False
            entity._attr_is_closing = True
            entity._attr_is_closed = True
        else:
            entity._attr_is_opening = False
            entity._attr_is_closing = False
        entity._publish_state()
