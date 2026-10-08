"""Pure slat/roll motion model for the next calibration backend increment.

Implements sections 1.2 and 1.4 of the shared calibration contract. This module
does not activate nonlinear profiles, change storage, schedule commands or infer
measurements. Times passed to ``advance`` are actual motor seconds, not queue or
browser time. See docs/cover-nonlinear-model.md for the integration boundary.
"""
from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace


def _number(name: str, value: float, minimum: float, maximum: float) -> None:
    """Fail instead of silently clipping invalid configuration or readings."""
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number in [{minimum}, {maximum}]")


@dataclass(frozen=True, slots=True)
class MotionPosition:
    """Physical fractions: bottom-edge height and fraction of separated slats.

    Height zero is ambiguous without ``slats``: the motor may still have work
    left although the curtain rests on the sill. Keep this state through Stop
    and reversal; never round it to an integer HA percentage internally.
    """

    height: float
    slats: float

    def __post_init__(self) -> None:
        _number("height", self.height, 0, 1)
        _number("slats", self.slats, 0, 1)
        if self.height > 0 and self.slats != 1:
            raise ValueError("Slats must be fully separated while the curtain is raised")

    @classmethod
    def at_height(cls, height: float) -> MotionPosition:
        """An explicit target: zero closes the slats as well as the curtain."""
        return cls(height, 0.0 if height == 0 else 1.0)


@dataclass(frozen=True, slots=True)
class CoverMotionModel:
    """Full motor runs, a common slat phase and one roll ratio per direction.

    Default geometry is the linear degenerate case, not measured geometry.
    Delays are carried for future command planning and are never height-scaled;
    they are deliberately excluded from motor-time position calculations.
    """

    opening_time_s: float
    closing_time_s: float
    slat_time_s: float = 0.0
    opening_roll: float = 1.0
    closing_roll: float = 1.0
    start_delay_s: float = 0.0
    stop_latency_s: float = 0.0

    def __post_init__(self) -> None:
        for name in ("opening_time_s", "closing_time_s"):
            _number(name, getattr(self, name), 1, 600)
        _number("slat_time_s", self.slat_time_s, 0, 600)
        if self.slat_time_s >= min(self.opening_time_s, self.closing_time_s):
            raise ValueError("The slat phase must be shorter than both complete runs")
        # This numerical ceiling also permits effective rolls obtained by
        # scaling a measured coefficient to a taller cover; it is not a fit bound.
        for name in ("opening_roll", "closing_roll"):
            _number(name, getattr(self, name), 1, 10000)
        for name in ("start_delay_s", "stop_latency_s"):
            _number(name, getattr(self, name), 0, 600)

    def _direction(self, opening: bool) -> tuple[float, float]:
        if type(opening) is not bool:
            raise ValueError("opening must be a boolean direction")
        return (self.opening_time_s, self.opening_roll) if opening else (self.closing_time_s, self.closing_roll)

    @staticmethod
    def _winding_fraction(height: float, roll: float) -> float:
        # Rationalized inverse of h = (2*u + (k-1)*u*u)/(k+1).
        # Unlike subtracting square roots, this is stable near k=1 and h=0.
        return height * (roll + 1) / (math.sqrt(1 + (roll * roll - 1) * height) + 1)

    def _coordinate(self, position: MotionPosition, opening: bool) -> float:
        total, roll = self._direction(opening)
        return (self.slat_time_s * position.slats
                + (total - self.slat_time_s) * self._winding_fraction(position.height, roll))

    def duration(self, start: MotionPosition, target: MotionPosition, *, opening: bool) -> float:
        """Motor seconds between two physical states, including remaining slats.

        Use the selected direction's curve at *both* ends. Multiplying the
        difference in height by the full travel time is wrong for a roll.
        """
        delta = self._coordinate(target, opening) - self._coordinate(start, opening)
        seconds = delta if opening else -delta
        if seconds < 0:
            raise ValueError("The target is opposite to the requested direction")
        return seconds

    def advance(self, start: MotionPosition, *, opening: bool, motor_seconds: float) -> MotionPosition:
        """Advance from a known state; saturate at the physical end stops.

        No clock is read here. A caller must freeze this model for a run and
        supply elapsed time from the bus write/motor anchor, also after Stop.
        """
        _number("motor_seconds", motor_seconds, 0, sys.float_info.max)
        total, roll = self._direction(opening)
        if motor_seconds == 0:
            return start
        coordinate = self._coordinate(start, opening) + (motor_seconds if opening else -motor_seconds)
        if coordinate <= 0:
            return MotionPosition(0.0, 0.0)
        if coordinate >= total:
            return MotionPosition(1.0, 1.0)
        if coordinate < self.slat_time_s:
            return MotionPosition(0.0, coordinate / self.slat_time_s)
        winding = (coordinate - self.slat_time_s) / (total - self.slat_time_s)
        height = winding * (2 + (roll - 1) * winding) / (roll + 1)
        return MotionPosition(height, 1.0)

    def scaled(
        self, *, reference_travel_cm: float | None, travel_cm: float | None,
        overrides: Mapping[str, float] | None = None,
    ) -> CoverMotionModel:
        """Resolve geometry, then apply personal values verbatim, key by key.

        Only an explicitly supplied pair of dimensions enables scaling. Both
        curtain times use the closing roll's geometric scale, while the rolls
        themselves transform independently. Overrides are applied *after* this
        calculation and the final effective model is validated as a whole.
        """
        for name, value in (("reference_travel_cm", reference_travel_cm), ("travel_cm", travel_cm)):
            if value is not None:
                _number(name, value, 0.1, 10000)
        values: dict[str, float] = {}
        if reference_travel_cm is not None and travel_cm is not None:
            ratio = travel_cm / reference_travel_cm
            for key in ("opening_roll", "closing_roll"):
                roll = getattr(self, key)
                values[key] = math.sqrt(1 + (roll * roll - 1) * ratio)
            # Equivalent to (k-1)/(k_ref-1), including exactly k_ref=1.
            curtain_scale = ratio * (self.closing_roll + 1) / (values["closing_roll"] + 1)
            values["slat_time_s"] = self.slat_time_s * ratio
            for key in ("opening_time_s", "closing_time_s"):
                values[key] = values["slat_time_s"] + (getattr(self, key) - self.slat_time_s) * curtain_scale
        if overrides is not None:
            if set(overrides) - set(self.__dataclass_fields__):
                raise ValueError("Unknown motion override")
            values.update(overrides)
        return replace(self, **values)


# Native timed-cover echo and linear interpolation helpers.
ECHO_WINDOW: float = 1.5

# Time from the write of a direction frame to the motor start when the gateway
# never relays the direction status (measured 0.55 s on a MyHOMEServer1, #302).
MOTOR_START_DELAY: float = 0.55

# Upper bound on how long we wait for the send queue to write our frame before
# falling back to "now" as the motion anchor. #302 measured the *queue*: with
# twelve covers the last frame goes out 1.5-12 s after enqueue (the ~1.6 s often
# quoted is the per-frame wait), and a reconnect after the 15 s idle close adds
# the handshake on top.
WRITE_TIMEOUT: float = 30.0


def travel_for(travel_time_up: float, travel_time_down: float, opening: bool) -> float:
    """Return full-travel seconds for the given direction (motors are often slower going up)."""
    return travel_time_up if opening else travel_time_down


def is_in_echo_window(echo_until: float | None, now: float) -> bool:
    """Return whether timestamp `now` is within the active echo window."""
    return echo_until is not None and now < echo_until


def compute_interpolated_position(
    current_position: int | None,
    start_position: int,
    move_start_time: float | None,
    now: float,
    travel_time: float,
    is_opening: bool,
    is_closing: bool,
) -> int | None:
    """Calculate current cover position interpolated from elapsed motion time."""
    if move_start_time is not None and travel_time > 0 and (is_opening or is_closing):
        elapsed = max(0.0, now - move_start_time)
        delta = (elapsed / travel_time) * 100.0
        if is_opening:
            return min(100, int(round(start_position + delta)))
        if is_closing:
            return max(0, int(round(start_position - delta)))
    return current_position


def compute_freeze_position(
    start_position: int,
    move_start_time: float | None,
    at: float,
    travel_time: float,
    is_opening: bool,
    is_closing: bool,
    current_position: int | None = None,
) -> int:
    """Calculate the final position when motion freezes as of timestamp `at`.

    When stationary (move_start_time is None or neither opening nor closing),
    correctly preserves current_position or start_position.
    """
    pos = compute_interpolated_position(
        current_position=current_position,
        start_position=start_position,
        move_start_time=move_start_time,
        now=at,
        travel_time=travel_time,
        is_opening=is_opening,
        is_closing=is_closing,
    )
    if pos is not None:
        return pos
    return current_position if current_position is not None else start_position

