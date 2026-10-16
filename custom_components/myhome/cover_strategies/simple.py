"""Simple up/down/stop cover strategy without synthetic stopwatch position."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationCommand, OWNAutomationEvent


class SimpleUpDownStopStrategy:
    """Stateless UP / DOWN / STOP cover strategy without position estimation."""

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "simple"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return OPEN | CLOSE | STOP (no synthetic position slider)."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )

    def current_position(self, entity: Any, now: float) -> int | None:
        """Simple covers do not fabricate a percentage position."""
        return None

    async def async_open(self, entity: Any) -> Any:
        """Send raise_shutter and update directional flags."""
        entity._attr_is_opening = True
        entity._attr_is_closing = False
        entity._attr_is_closed = False
        written = await entity._gateway_handler.send(
            OWNAutomationCommand.raise_shutter(entity._full_where)
        )
        entity._publish_state()
        return written

    async def async_close(self, entity: Any) -> Any:
        """Send lower_shutter and update directional flags."""
        entity._attr_is_opening = False
        entity._attr_is_closing = True
        written = await entity._gateway_handler.send(
            OWNAutomationCommand.lower_shutter(entity._full_where)
        )
        entity._publish_state()
        return written

    async def async_stop(self, entity: Any) -> None:
        """Send stop_shutter and clear directional flags."""
        entity._attr_is_opening = False
        entity._attr_is_closing = False
        await entity._gateway_handler.send(
            OWNAutomationCommand.stop_shutter(entity._full_where)
        )
        entity._publish_state()

    async def async_set_position(self, entity: Any, position: int) -> None:
        """Map 0% to close, 100% to open, and intermediate targets to stop."""
        if position <= 0:
            await self.async_close(entity)
        elif position >= 100:
            await self.async_open(entity)
        else:
            await self.async_stop(entity)

    def handle_event(self, entity: Any, message: OWNAutomationEvent, now: float) -> None:
        """Apply directional state from bus event without stopwatch estimation."""
        if message.is_opening:
            entity._attr_is_opening = True
            entity._attr_is_closing = False
            entity._attr_is_closed = False
        elif message.is_closing:
            entity._attr_is_opening = False
            entity._attr_is_closing = True
        else:
            entity._attr_is_opening = False
            entity._attr_is_closing = False
            if message.is_closed is not None:
                entity._attr_is_closed = message.is_closed
        entity._publish_state()
