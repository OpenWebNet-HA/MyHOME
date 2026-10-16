"""Native hardware position strategy for advanced WHO=2 shutters (Dimension 10/11)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationCommand, OWNAutomationEvent


class NativePositionStrategy:
    """Hardware-calibrated shutter strategy using OpenWebNet Dimension 10/11."""

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "native"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return OPEN | CLOSE | STOP | SET_POSITION."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    def current_position(self, entity: Any, now: float) -> int | None:
        """Return position reported by the actuator."""
        pos: int | None = entity._attr_current_cover_position
        return pos

    async def async_open(self, entity: Any) -> Any:
        """Delegate open command to the entity's move handler."""
        return await entity._async_move("open")

    async def async_close(self, entity: Any) -> Any:
        """Delegate close command to the entity's move handler."""
        return await entity._async_move("close")

    async def async_stop(self, entity: Any) -> None:
        """Send hardware stop command."""
        await entity._gateway_handler.send(
            OWNAutomationCommand.stop_shutter(entity._full_where)
        )
        entity._publish_state()

    async def async_set_position(self, entity: Any, position: int) -> None:
        """Send native shutter level command (or lower_shutter at 0% for MH201 compatibility)."""
        if position <= 0:
            await entity._gateway_handler.send(
                OWNAutomationCommand.lower_shutter(entity._full_where)
            )
        else:
            await entity._gateway_handler.send(
                OWNAutomationCommand.set_shutter_level(entity._full_where, position)
            )

    def handle_event(self, entity: Any, message: OWNAutomationEvent, now: float) -> None:
        """Apply native position and directional status from actuator event."""
        position = message.current_position
        position_unknown = getattr(message, "is_position_unknown", False) is True or (
            isinstance(position, int) and not 0 <= position <= 100
        )
        if position_unknown:
            position = None
        if position is not None:
            entity._attr_current_cover_position = position
            entity._attr_is_closed = position == 0
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
        if position_unknown:
            entity._attr_current_cover_position = None
            if not (entity._attr_is_opening or entity._attr_is_closing):
                entity._attr_is_closed = None
        entity._publish_state()
