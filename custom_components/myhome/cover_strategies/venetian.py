"""Venetian blind slat-tilt and curtain strategy for WHO=2 shutters (#492, #508)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature

from ..const import (
    DEFAULT_MIN_MOTOR_PULSE,
    DEFAULT_TRAVEL_TIME,
)
from .base import clamp_slat_time
from .timed import TimedCurtainStrategy

MIN_TILT_PULSE: float = DEFAULT_MIN_MOTOR_PULSE

__all__ = [
    "MIN_TILT_PULSE",
    "VenetianTiltStrategy",
    "clamp_slat_time",
]


class VenetianTiltStrategy(TimedCurtainStrategy):
    """Shared slat-phase + curtain-roll Venetian blind strategy aligned with #508.

    End-to-end ``travel_time`` includes one full slat rotation (``slat_time``).
    Vertical curtain travel is therefore estimated over ``travel_time - slat_time``
    after the slats finish rotating through their remaining lead angle, avoiding
    double-counting between slat tilt and vertical curtain displacement.
    """

    def __init__(
        self,
        slat_time: float | None = None,
        *,
        travel_time: float = DEFAULT_TRAVEL_TIME,
        travel_time_up: float | None = None,
        travel_time_down: float | None = None,
        min_motor_pulse: float = DEFAULT_MIN_MOTOR_PULSE,
    ) -> None:
        """Initialize Venetian strategy with shared ``slat_time``."""
        super().__init__(
            travel_time=travel_time,
            travel_time_up=travel_time_up,
            travel_time_down=travel_time_down,
            min_motor_pulse=min_motor_pulse,
            slat_time=slat_time,
        )

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "venetian"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return curtain features plus OPEN_TILT | CLOSE_TILT | STOP_TILT | SET_TILT_POSITION."""
        return (
            super().supported_features
            | CoverEntityFeature.OPEN_TILT
            | CoverEntityFeature.CLOSE_TILT
            | CoverEntityFeature.STOP_TILT
            | CoverEntityFeature.SET_TILT_POSITION
        )

    def curtain_travel_for(self, opening: bool) -> float:
        """Return vertical curtain travel seconds (end-to-end travel minus slat rotation phase)."""
        travel = self.travel_for_direction(opening)
        slat = self.slat_time_for()
        if travel > slat:
            return travel - slat
        return travel

    def lead_time_for(self, opening: bool, *, entity: Any = None) -> float:
        """Return seconds a curtain run spends rotating slats before vertical movement starts."""
        if entity is not None and getattr(entity, "_is_tilting", False) is True:
            return 0.0
        curr: int | None = None
        if entity is not None:
            raw_curr = getattr(entity, "_attr_current_cover_tilt_position", None)
            if isinstance(raw_curr, int):
                curr = raw_curr
        if curr is None:
            curr = 0 if opening else 100
        remaining = (100 - curr) if opening else curr
        return max(0.0, (remaining / 100.0) * self.slat_time_for())

    def carry_overshoot_into_curtain(
        self,
        start_position: int,
        start_tilt: int,
        elapsed: float,
        direction: str,
    ) -> int:
        """Convert motor runtime past a full slat rotation into vertical curtain displacement."""
        opening = direction == "open"
        slat = self.slat_time_for()
        to_end = ((100 - start_tilt) if opening else start_tilt) / 100.0 * slat
        excess = max(0.0, float(elapsed)) - to_end
        if excess <= 0:
            return max(0, min(100, int(round(start_position))))
        curtain_travel = self.curtain_travel_for(opening)
        if curtain_travel <= 0:
            return max(0, min(100, int(round(start_position))))
        moved = (excess / curtain_travel) * 100.0
        delta = moved if opening else -moved
        return max(0, min(100, int(round(start_position + delta))))

    def _reanchor_slats_on_stop_echo(
        self,
        entity: Any,
        ctx: dict[str, Any],
        elapsed: float,
        is_opening: bool,
        is_closing: bool,
    ) -> None:
        """Re-anchor slat tilt position on a relayed stop status echo."""
        if not (is_opening or is_closing):
            return
        raw_tilt = ctx.get("run_start_tilt")
        start_tilt = int(raw_tilt) if raw_tilt is not None else None
        direction = "open" if is_opening else "close"
        entity._attr_current_cover_tilt_position = self.turn_slats_after(
            start_tilt, elapsed, direction
        )
