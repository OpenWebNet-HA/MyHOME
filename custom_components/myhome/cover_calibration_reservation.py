"""Keep gateway ownership independent from the lifetime of its panel client."""
from __future__ import annotations

import asyncio
from typing import Any

# Calibration is measuring an unknown travel time. Existing profile values are
# estimates, so use the full supported travel bound, including automatic runs.
GUARD_SECONDS = 600


class CalibrationReservation:
    """Observe possibly dispatched movement until Stop feedback or a full guard."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.timer: asyncio.TimerHandle | None = None
        self.generation = 0

    @property
    def pending(self) -> bool:
        return self.timer is not None

    def dispatched(self) -> None:
        """A worker guard is conservative: transmission may still fail later."""
        if self.timer:
            self.timer.cancel()
        self.timer = self.session.hass.loop.call_later(GUARD_SECONDS, self.completed)

    def observe(self, event: Any) -> None:
        if event.is_opening or event.is_closing:
            self.dispatched()
        elif event.state == 0:
            self.completed()

    def completed(self) -> None:
        """A guard expiry permits reuse; it does not prove a physical endpoint."""
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.session.closed:
            self.session.release()

