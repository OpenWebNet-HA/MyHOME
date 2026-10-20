"""Native hardware position strategy for advanced WHO=2 shutters (Dimension 10/11)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature

from ..const import DEFAULT_MIN_MOTOR_PULSE, DEFAULT_TRAVEL_TIME
from ..cover_motion import MIN_POSITION_DELTA, quantize_position_delta
from .base import BaseCoverStrategy


class NativePositionStrategy(BaseCoverStrategy):
    """Hardware-calibrated shutter strategy using OpenWebNet Dimension 10/11."""

    def __init__(
        self,
        *,
        min_position_delta: int = MIN_POSITION_DELTA,
        travel_time: float = DEFAULT_TRAVEL_TIME,
        travel_time_up: float | None = None,
        travel_time_down: float | None = None,
        min_motor_pulse: float = DEFAULT_MIN_MOTOR_PULSE,
        slat_time: float | None = None,
    ) -> None:
        """Initialize native position strategy with deadband quantum (#466)."""
        super().__init__(
            travel_time=travel_time,
            travel_time_up=travel_time_up,
            travel_time_down=travel_time_down,
            min_motor_pulse=min_motor_pulse,
            slat_time=slat_time,
        )
        self.min_position_delta: int = max(1, int(min_position_delta))

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

    @property
    def assumed_state(self) -> bool:
        """Advanced WHO=2 actuators report authoritative bus position."""
        return False

    def quantize_target(self, current: int | None, target: int) -> int:
        """Widen sub-deadband position deltas to ``min_position_delta`` (#466, #702)."""
        return quantize_position_delta(current, target, self.min_position_delta)

    def position_after(
        self,
        start: int,
        elapsed: float,
        direction: str,
        *,
        entity: Any = None,
        lead: float | None = None,
    ) -> int | None:
        """Native actuators report position on the bus rather than dead-reckoning."""
        if entity is not None:
            pos: int | None = getattr(entity, "_attr_current_cover_position", None)
            return pos
        return max(0, min(100, int(round(start))))

    def duration_for(
        self,
        start: int,
        target: int,
        direction: str | None = None,
        *,
        entity: Any = None,
    ) -> float:
        """Native actuators time their own runs in hardware."""
        return 0.0

    def reanchor_on_stop_echo(
        self,
        entity: Any,
        ctx: dict[str, Any],
        now: float,
    ) -> int | None:
        """Native actuators rely on Dimension 10 status frames rather than stop-echo timing."""
        pos: int | None = getattr(entity, "_attr_current_cover_position", None)
        return pos

    def current_position(self, entity: Any, now: float) -> int | None:
        """Return position reported by the actuator."""
        pos: int | None = entity._attr_current_cover_position
        return pos

