"""Motor-anchored nonlinear position tracking, independent of command scheduling."""
from __future__ import annotations

from .cover_motion import CoverMotionModel, MotionPosition


class CoverMotionTracker:
    """Preserve slat progress and retain the old run until a new frame is written."""

    def __init__(self) -> None:
        self.model: CoverMotionModel | None = None
        self.position: MotionPosition | None = None
        self.anchor: float | None = None
        self.opening = True
        self.pending: bool | None = None

    def configure(self, model: CoverMotionModel | None) -> None:
        if self.model is None or model is None:
            self.position = None
        self.model = model
        self.anchor = None
        self.pending = None

    def sample(self, now: float) -> MotionPosition | None:
        if self.model is None or self.anchor is None:
            return self.position
        elapsed = max(0.0, now - self.anchor)
        if self.position is None:
            full = self.model.opening_time_s if self.opening else self.model.closing_time_s
            return MotionPosition.at_height(1 if self.opening else 0) if elapsed >= full else None
        return self.model.advance(self.position, opening=self.opening, motor_seconds=elapsed)

    def prepare(self, opening: bool) -> None:
        self.pending = opening

    def start(self, at: float) -> None:
        if self.pending is not None:
            self.position = self.sample(at)
            self.opening = self.pending
            self.pending = None
        self.anchor = at

    def stop(self, at: float) -> None:
        self.position = self.sample(at)
        self.anchor = None
        self.pending = None

    def abort(self) -> None:
        # No frame reached the bus. An earlier physical run is still in progress.
        self.pending = None

    def confirm(self, percentage: float) -> None:
        """Only an explicit physical endpoint or actual reported position qualifies."""
        self.position = MotionPosition.at_height(percentage / 100)
        self.anchor = None
        self.pending = None

    def remaining(self, target: MotionPosition) -> float:
        if self.model is None or self.position is None:
            raise ValueError("Position unknown; complete an opening or closing run first")
        # Called after the motor anchor. A queued reversal may have passed the
        # target before reaching the bus: stop immediately rather than continue.
        try:
            return self.model.duration(self.position, target, opening=self.opening)
        except ValueError:
            return 0.0
