"""Pluggable kinematics strategies for MyHOME WHO=2 covers."""
from __future__ import annotations

from .base import CoverKinematicsStrategy
from .impulse import ImpulseDoorStrategy
from .native import NativePositionStrategy
from .simple import SimpleUpDownStopStrategy
from .timed import TimedCurtainStrategy
from .venetian import MIN_TILT_PULSE, VenetianTiltStrategy

__all__ = [
    "CoverKinematicsStrategy",
    "ImpulseDoorStrategy",
    "MIN_TILT_PULSE",
    "NativePositionStrategy",
    "SimpleUpDownStopStrategy",
    "TimedCurtainStrategy",
    "VenetianTiltStrategy",
    "resolve_cover_strategy",
]


def resolve_cover_strategy(
    *,
    advanced: bool = False,
    strategy_name: str | None = None,
    tilt_time: float | None = None,
    pulse_duration: float | None = None,
) -> CoverKinematicsStrategy:
    """Resolve the appropriate CoverKinematicsStrategy for a cover entity."""
    if strategy_name is not None:
        normalized = strategy_name.strip().lower()
        if normalized == "native":
            return NativePositionStrategy()
        if normalized == "simple":
            return SimpleUpDownStopStrategy()
        if normalized == "venetian":
            return VenetianTiltStrategy(tilt_time=tilt_time or 1.5)
        if normalized == "impulse":
            return ImpulseDoorStrategy(pulse_duration=pulse_duration or 0.5)
        if normalized == "timed":
            return TimedCurtainStrategy()

    if advanced:
        return NativePositionStrategy()
    if tilt_time is not None and tilt_time > 0:
        return VenetianTiltStrategy(tilt_time=tilt_time)
    if pulse_duration is not None and pulse_duration > 0:
        return ImpulseDoorStrategy(pulse_duration=pulse_duration)
    return TimedCurtainStrategy()
