"""Adapt #349's open/close/open cycle to the panel's owned session and profile store.

Actuator stop feedback is not proof of a physical endpoint. No write-clock
fallback or automatic persistence: both timings require review before Save.
"""
import asyncio
from typing import Any, cast

from homeassistant.core import CoreState

from . import cover_calibration as guided
from .cover_profile_provenance import evidence
from .cover_profiles import ProfileError, snapshot, target

SETTLE_SECONDS = 1
CUTOFF_MIN = 59
CUTOFF_MAX = 65


class AutomaticCalibrationSession(guided.CalibrationSession):
    """The same lease, ownership, queued commands and persistence as guided mode."""

    mode = "automatic"
    travel_seconds = 180
    travel_reason = "automatic_timeout"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.phase = "confirm_automatic"
        self.run_index = 0

    def view(self) -> Any:
        return {**super().view(), "run_index": self.run_index}

    def on_event(self, event: Any) -> None:
        if event.current_position is not None and self.active:
            self.interrupt("unexpected_movement")
            return
        if self.phase in {"opening", "closing"} and event.state == 0:
            elapsed = guided.monotonic() - cast(float, self.started_at)
            if elapsed < 0.15:  # Trailing relay echo, as in #349; keep the deadline.
                return
            self.reservation.observe(event)
            if CUTOFF_MIN <= elapsed <= CUTOFF_MAX:
                self.interrupt("automatic_cutoff")
                return
            if elapsed > self.travel_seconds or (self.run_index > 0 and elapsed < 1):
                self.interrupt("automatic_invalid")
                return
            cast(asyncio.TimerHandle, self.deadline).cancel()
            self.started_at = None
            if self.run_index > 0:
                direction = "closing" if self.run_index == 1 else "opening"
                self.values[f"{direction}_time"] = round(elapsed, 2)
                self.provenance[direction] = evidence("automatic", self.cover.unique_id)
            if self.run_index == 2:
                self.finish_measurement()
            else:
                self.run_index += 1
                self.phase = "settling"
                self.settle = self.hass.loop.call_later(SETTLE_SECONDS, self.next_run)
            self.emit()
            return
        super().on_event(event)

    def finish_measurement(self) -> None:
        self.phase = "review"

    def next_run(self) -> None:
        """Revalidate after the pause; no cancelled or unavailable session can move."""
        self.settle = None
        if self.phase != "settling" or self.store.calibration is not self:
            return
        try:
            entry, entity = target(self.hass, self.entry_id, self.cover.entity_id)
            if (self.hass.state in (CoreState.stopping, CoreState.final_write, CoreState.stopped)
                    or not snapshot(self.hass, self.store, entry, entity)["writable"]):
                raise ProfileError("cover_unavailable")
            self.queue_move("close" if self.run_index == 1 else "open")
        except ProfileError as error:
            self.interrupt(str(error))
