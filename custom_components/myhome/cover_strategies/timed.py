"""Travel-time interpolated curtain strategy for calibrated standard WHO=2 covers."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationEvent

from ..cover_motion import compute_interpolated_position


class TimedCurtainStrategy:
    """Timed curtain kinematics strategy with direction-aware travel times and echo anchoring."""

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "timed"

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
        """Interpolate current position from start position, motor anchor, and travel time."""
        is_opening = bool(entity._attr_is_opening)
        is_closing = bool(entity._attr_is_closing)
        return compute_interpolated_position(
            entity._attr_current_cover_position,
            entity._start_position,
            entity._move_start_time,
            now,
            entity._travel_for(is_opening),
            is_opening,
            is_closing,
        )

    async def async_open(self, entity: Any) -> Any:
        """Start timed open run."""
        return await entity._async_move("open")

    async def async_close(self, entity: Any) -> Any:
        """Start timed close run."""
        return await entity._async_move("close")

    async def async_stop(self, entity: Any) -> None:
        """Stop timed run and freeze interpolated position on write."""
        await entity.async_stop_cover()

    async def async_set_position(self, entity: Any, position: int) -> None:
        """Drive cover to target position using travel-time auto-stop."""
        await entity.async_set_cover_position(position=position)

    def handle_event(self, entity: Any, message: OWNAutomationEvent, now: float) -> None:
        """Delegate event processing to entity's echo-window and freeze state machine."""
        entity.handle_event(message)
