"""Pluggable kinematics strategies for MyHOME WHO=2 covers."""
from __future__ import annotations

from ..const import DEFAULT_MIN_MOTOR_PULSE, DEFAULT_TRAVEL_TIME
from .base import RUN_END_REASONS, BaseCoverStrategy, CoverKinematicsStrategy, clamp_slat_time
from .native import NativePositionStrategy
from .simple import SimpleUpDownStopStrategy
from .timed import TimedCurtainStrategy
from .venetian import MIN_TILT_PULSE, VenetianTiltStrategy

__all__ = [
    "BaseCoverStrategy",
    "CoverKinematicsStrategy",
    "MIN_TILT_PULSE",
    "NativePositionStrategy",
    "RUN_END_REASONS",
    "SimpleUpDownStopStrategy",
    "TimedCurtainStrategy",
    "VenetianTiltStrategy",
    "clamp_slat_time",
    "resolve_cover_strategy",
]


def resolve_cover_strategy(
    *,
    advanced: bool = False,
    strategy_name: str | None = None,
    slat_tilt: bool = False,
    slat_time: float | None = None,
    travel_time: float | None = DEFAULT_TRAVEL_TIME,
    travel_time_up: float | None = None,
    travel_time_down: float | None = None,
    min_motor_pulse: float = DEFAULT_MIN_MOTOR_PULSE,
) -> CoverKinematicsStrategy:
    """Resolve the appropriate ``CoverKinematicsStrategy`` for a cover entity.

    Resolution precedence:
    1. Explicit ``strategy_name`` (``"native"``, ``"simple"``, ``"venetian"``,
       ``"timed"``).
    2. ``advanced=True`` -> ``NativePositionStrategy`` (Dimension 10/11 hardware level).
    3. ``slat_tilt=True`` -> ``VenetianTiltStrategy``.
    4. ``travel_time is None`` or ``travel_time <= 0`` -> ``SimpleUpDownStopStrategy``
       (unpositioned UP/DOWN/STOP relay without synthetic 50% startup position).
    5. Default when ``travel_time > 0`` (including ``DEFAULT_TRAVEL_TIME = 25``) ->
       ``TimedCurtainStrategy`` so existing WHO=2 installations retain percentage
       positioning and interactive UI stopwatch calibration without breaking changes.
    """
    try:
        effective_travel = (
            float(travel_time)
            if travel_time is not None and float(travel_time) > 0
            else float(DEFAULT_TRAVEL_TIME)
        )
        has_positive_travel = travel_time is not None and float(travel_time) > 0
    except (TypeError, ValueError):
        effective_travel = float(DEFAULT_TRAVEL_TIME)
        has_positive_travel = False

    if strategy_name is not None:
        normalized = strategy_name.strip().lower()
        if normalized == "native":
            return NativePositionStrategy(
                travel_time=effective_travel,
                travel_time_up=travel_time_up,
                travel_time_down=travel_time_down,
                min_motor_pulse=min_motor_pulse,
                slat_time=slat_time,
            )
        if normalized == "simple":
            return SimpleUpDownStopStrategy(
                travel_time=effective_travel,
                travel_time_up=travel_time_up,
                travel_time_down=travel_time_down,
                min_motor_pulse=min_motor_pulse,
                slat_time=slat_time,
            )
        if normalized == "venetian":
            return VenetianTiltStrategy(
                slat_time=slat_time,
                travel_time=effective_travel,
                travel_time_up=travel_time_up,
                travel_time_down=travel_time_down,
                min_motor_pulse=min_motor_pulse,
            )
        if normalized == "timed":
            return TimedCurtainStrategy(
                travel_time=effective_travel,
                travel_time_up=travel_time_up,
                travel_time_down=travel_time_down,
                min_motor_pulse=min_motor_pulse,
                slat_time=slat_time,
            )

    if advanced:
        return NativePositionStrategy(
            travel_time=effective_travel,
            travel_time_up=travel_time_up,
            travel_time_down=travel_time_down,
            min_motor_pulse=min_motor_pulse,
            slat_time=slat_time,
        )
    if slat_tilt:
        return VenetianTiltStrategy(
            slat_time=slat_time,
            travel_time=effective_travel,
            travel_time_up=travel_time_up,
            travel_time_down=travel_time_down,
            min_motor_pulse=min_motor_pulse,
        )
    if not has_positive_travel:
        return SimpleUpDownStopStrategy(
            travel_time=effective_travel,
            travel_time_up=travel_time_up,
            travel_time_down=travel_time_down,
            min_motor_pulse=min_motor_pulse,
            slat_time=slat_time,
        )
    return TimedCurtainStrategy(
        travel_time=effective_travel,
        travel_time_up=travel_time_up,
        travel_time_down=travel_time_down,
        min_motor_pulse=min_motor_pulse,
        slat_time=slat_time,
    )
