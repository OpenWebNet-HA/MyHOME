"""A verification run: from an end stop towards a height, timed by the motion model, judged by the tape.

The run lasts the motor seconds the model needs to bring the bottom edge from the end
stop to the target height. The expectation is where the model puts the edge after the
seconds the motor really ran, up to the Stop write, so a late Stop is not counted
against the model. The verdict is on whole centimetres, rounded half away from zero:
Python's ``round()`` rounds half to even and would pass 4.5 cm as 4.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import voluptuous as vol

from .cover_motion import CoverMotionModel, MotionPosition
from .cover_profiles import ProfileError

# A check passes when the bottom edge stopped within this many whole centimetres of
# the expectation: the precision a basic calibration declares.
CHECK_THRESHOLD_CM = 4
CHECK_DIRECTIONS = ("opening", "closing")
# Upwards from the bottom end stop: an ascent crosses the slat phase and the opening
# roll, where an inherited profile is most often wrong; a descent from the top crosses
# neither and can pass a profile that misplaces the edge by several centimetres.
DEFAULT_CHECK_DIRECTION = "opening"
# Half the travel: the one height a person at the window can verify in their head, and
# the check of an assigned profile (the default of `plan`).
HALF_TRAVEL_FRACTION = 0.5
# The check of a model just fitted by the roll measurement aims where nothing was fitted:
# its two intermediate readings land near 40 % of the travel (37.5 % for a roll of 3,
# 42 % for 2), so half the travel would mostly repeat them.
GEOMETRY_CHECK_FRACTION = 0.75
# A check aims between these fractions of the travel, away from both end stops, and its
# run must turn the motor at least MIN_CURTAIN_SECONDS beyond the slat phase: a run that
# barely moves the curtain would pass whatever the model says.
MIN_TARGET_FRACTION = 0.1
MAX_TARGET_FRACTION = 0.9
MIN_CURTAIN_SECONDS = 1.0
# The smallest tape reading, as for every other reading of the measurement (`centimetres`).
MIN_READING_CM = 0.1


def _quantize(value: Decimal, step: str) -> Decimal:
    return value.quantize(Decimal(step), rounding=ROUND_HALF_UP)


def tenth(value: float) -> float:
    """A height as published: to a tenth of a centimetre, ties away from zero."""
    return float(_quantize(Decimal(repr(value)), "0.1"))


def deviation_cm(measured_cm: float, expected_cm: float) -> int:
    """Tape minus expectation in whole centimetres, ties away from zero; positive is above.

    Both operands are taken as written (``repr``), so 9.7 - 5.2 is 4.5 and not
    4.499999999999999.
    """
    return int(_quantize(Decimal(repr(measured_cm)) - Decimal(repr(expected_cm)), "1"))


def passes(deviation: int) -> bool:
    return abs(deviation) <= CHECK_THRESHOLD_CM


def _finite(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


@dataclass
class CalibrationCheck:
    """One verification of one motion model on one travel; nothing here moves the cover."""

    model: CoverMotionModel
    travel_cm: float
    direction: str
    target_cm: float
    check_seconds: float | None = None
    expected_cm: float | None = None
    measured_cm: float | None = None
    deviation_cm: int | None = None
    passed: bool | None = None

    @classmethod
    def plan(cls, model: CoverMotionModel, travel_cm: float, direction: Any = None,
             target_cm: Any = None, *, fraction: float = HALF_TRAVEL_FRACTION) -> CalibrationCheck:
        """A check the cover can make and that says something about the curtain, or `invalid_check`."""
        direction = DEFAULT_CHECK_DIRECTION if direction is None else direction
        target = travel_cm * fraction if target_cm is None else target_cm
        if (direction not in CHECK_DIRECTIONS or not _finite(target)
                or not MIN_TARGET_FRACTION <= target / travel_cm <= MAX_TARGET_FRACTION):
            raise ProfileError("invalid_check")
        check = cls(model, travel_cm, direction, float(target))
        if check.planned_seconds <= model.slat_time_s + MIN_CURTAIN_SECONDS:
            raise ProfileError("invalid_check")
        return check

    def again(self) -> CalibrationCheck:
        """The same run, with nothing of the previous one."""
        return CalibrationCheck(self.model, self.travel_cm, self.direction, self.target_cm)

    @property
    def opening(self) -> bool:
        return self.direction == "opening"

    @property
    def start(self) -> MotionPosition:
        """The end stop the run starts from: closed with the slats closed, or fully open."""
        return MotionPosition(0.0, 0.0) if self.opening else MotionPosition(1.0, 1.0)

    @property
    def planned_seconds(self) -> float:
        """Motor seconds from the end stop to the target height, slat phase included."""
        target = MotionPosition.at_height(self.target_cm / self.travel_cm)
        return self.model.duration(self.start, target, opening=self.opening)

    def ran(self, motor_seconds: float) -> None:
        """The motor ran this long, from its start to the Stop write: expect where that puts the edge."""
        position = self.model.advance(self.start, opening=self.opening, motor_seconds=motor_seconds)
        self.check_seconds = motor_seconds
        self.expected_cm = tenth(position.height * self.travel_cm)

    def read(self, value: Any) -> None:
        """The tape reading of the bottom edge above its rest, and the verdict."""
        if self.expected_cm is None:
            raise ProfileError("calibration_step")
        if not _finite(value) or not MIN_READING_CM <= value <= self.travel_cm:
            raise vol.Invalid("The reading must lie between the rest and the travel")
        self.measured_cm = float(value)
        self.deviation_cm = deviation_cm(self.measured_cm, self.expected_cm)
        self.passed = passes(self.deviation_cm)

    def view(self) -> dict[str, Any]:
        return {"direction": self.direction, "target_cm": self.target_cm, "check_seconds": self.check_seconds,
                "expected_cm": self.expected_cm, "measured_cm": self.measured_cm,
                "deviation_cm": self.deviation_cm, "passed": self.passed}
