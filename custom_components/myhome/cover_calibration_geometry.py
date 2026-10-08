"""Explicitly briefed basic slat/roll measurement, owned by the existing session."""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import Any, cast

import voluptuous as vol

from . import cover_calibration as guided
from .cover_calibration_check import CHECK_THRESHOLD_CM, GEOMETRY_CHECK_FRACTION, CalibrationCheck
from .cover_calibration_fit import (
    closing_fit,
    closing_range,
    opening_fit,
    opening_range,
    opening_roll_fit,
    opening_roll_range,
)
from .cover_geometry import profile_motion
from .cover_motion import CoverMotionModel
from .cover_profile_provenance import evidence
from .cover_profiles import ProfileError, travel_time, write_profile
from .cover_settings import centimetres

# Lift-off gap limits in cm. A reading below TOUCHING_CM means the edge still
# rests on its sill; the 1 cm rule is to be validated on real installations.
# The joint fit loses precision as the gap grows: from GAP_WARN_CM a reading is
# accepted with a warning, above MAX_GAP_CM it is refused.
TOUCHING_CM = 1.0
GAP_WARN_CM = 10.0
MAX_GAP_CM = 20.0
# True: such a reading discards the lift-off run and repeats it. False: refuse it.
LIFT_REPEAT_BELOW_TOUCHING = True
# Floating-point slack on the ends of an intermediate reading range, far below any tape.
RANGE_SLACK_CM = 1e-9
# During a check these end it and return to review instead of ending the session.
CHECK_RETURNS = frozenset({"stopped", "unexpected_stop", "unexpected_movement"})


class GeometryCalibrationSession(guided.CalibrationSession):
    """No browser timings, unattended continuation, or partially saved geometry."""

    mode = "geometry"
    safe_phases = guided.CalibrationSession.safe_phases | {"briefing", "reading"}

    def __init__(self, *args: Any, slats: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.phase, self.step = "briefing", "home"
        # Without slats there is no lift-off: the full ascent follows the first close.
        self.slats = slats
        self.after_position = "lift" if slats else "opening"
        self.lift_attempts = 1
        self.still_resting = False
        self.gap_warning = False
        self.after_stop = "briefing"
        self.stop_written = False
        self.samples: dict[str, float] = {}
        self.readings: dict[str, float] = {}
        self.geometry: dict[str, float] = {}
        self.geometry_provenance: dict[str, Any] = {}
        # A verification of the measured model, requested from review; None until one is.
        self.check: CalibrationCheck | None = None
        # While a check runs: the verdict it replaces, given back if it is stopped before moving.
        self.checking = False
        self.check_before: CalibrationCheck | None = None
        self.check_interrupted: str | None = None
        # Where the bottom edge last was for certain, in percent; None once it moved unread.
        self.edge_position: float | None = None

    def view(self) -> Any:
        view = super().view()
        return {**view, "step": self.step, "save_modes": ["new"],
                "travel_cm": self.measured_travel, "geometry": dict(self.geometry),
                "readings": dict(self.readings), "samples": dict(self.samples),
                "accuracy": None, "independent_check": False,
                "can_repeat": self.phase in {"reading", "review"} or (self.phase == "briefing" and self.step == "half_open")
                or self.gap_warning,
                "reading_kind": "travel" if self.step == "opening" else self.step,
                "expected_cm": self.measured_travel / 2 if self.measured_travel and self.step.startswith("half_") else None,
                "slats": self.slats, "lift_attempts": self.lift_attempts, "still_resting": self.still_resting,
                "touching_cm": TOUCHING_CM, "max_gap_cm": MAX_GAP_CM, "lift_repeat": LIFT_REPEAT_BELOW_TOUCHING,
                "gap_warn_cm": GAP_WARN_CM, "gap_warning": self.gap_warning,
                "reading_range": self._reading_range(),
                # travel_cm is this measurement's; the travel already saved for the cover stays readable.
                "saved_travel_cm": view["travel_cm"],
                "check": self.check.view() if self.check is not None and self.active else None,
                "check_threshold_cm": CHECK_THRESHOLD_CM,
                "check_interrupted": self.check_interrupted if self.active else None}

    def interrupt(self, reason: str, send_stop: Any = True) -> None:
        if not self.active or self.phase == "saving":
            return
        if self.checking and reason in CHECK_RETURNS:
            # Only the check ends; the measurement stays. Stop before anything moved keeps everything.
            self._leave_check(reason, keep=reason == "stopped" and self.phase in {"briefing", "reading"},
                              send_stop=send_stop)
            return
        self.checking, self.check_before = False, None
        self.geometry.clear()
        self.geometry_provenance = {}
        self.samples.clear()
        self.readings.clear()
        self.measured_travel = None
        self.still_resting = self.gap_warning = False
        self.check = None
        super().interrupt(reason, send_stop)

    def geometry_action(self, msg: dict[str, Any]) -> None:
        action = msg["action"]
        if action == "next" and self.phase == "briefing":
            # Moving on accepts a wide gap and ends the still-resting notice of the briefing.
            self.gap_warning = self.still_resting = False
            direction = "open" if self._rising() else "close"
            written = self.queue_move(direction)
            self.edge_position = None
            written.add_done_callback(self._bound(self._movement_delivered))
        elif action == "lift" and self.step == "lift" and self.phase == "opening":
            self._sample_stop()
        elif action == "endpoint" and self.phase in {"opening", "closing"} and self.step in {"home", "reset", "opening", "closing", "top"}:
            self._endpoint()
        elif action == "reading" and self.phase == "reading":
            try:
                self._reading(msg.get("reading_cm"))
            except vol.Invalid as error:
                raise ProfileError("invalid_reading") from error
        elif action == "repeat" and self.view()["can_repeat"]:
            self._repeat()
        elif action == "check" and self.phase == "review":
            self._check(msg)
        else:
            raise ProfileError("calibration_step")

    def _rising(self) -> bool:
        """The direction of the current step's run."""
        if self.step == "check":
            return cast(CalibrationCheck, self.check).opening
        return self.step in {"lift", "opening", "half_open", "top"}

    def _check(self, msg: dict[str, Any]) -> None:
        """Verify the measured model before Save: back to the end stop, then a timed run and a reading."""
        model = profile_motion({**self.values, "geometry": self.geometry})
        check = CalibrationCheck.plan(cast(CoverMotionModel, model), cast(float, self.measured_travel),
                                           msg.get("direction"), msg.get("target_cm"), fraction=GEOMETRY_CHECK_FRACTION)
        self.check, self.check_before, self.checking = check, self.check, True
        self.check_interrupted = None
        self._home_for_check()

    def _home_for_check(self) -> None:
        # Each movement keeps its own briefing: first the end stop, then the check run.
        self.step = "home" if cast(CalibrationCheck, self.check).opening else "top"
        self.after_position = "check"
        self.phase = "briefing"
        self.emit()

    def _leave_check(self, reason: str, *, keep: bool, send_stop: Any) -> None:
        """Back to review with the measurement. A check that moved the cover leaves no verdict."""
        if self.deadline:
            self.deadline.cancel()
        self.started_at = None
        self.stop_written = self.checking = False
        self.check, self.check_before = self.check_before if keep else None, None
        self.check_interrupted = reason
        self.step = self.after_position = "half_close"
        self.phase = "review"
        # Whatever the check queued is superseded: a run still in the queue is never written,
        # and its delivery, or that of its Stop, no longer speaks for this session.
        self._motion_token = object()
        if not keep:  # Moved by the check, by a wall switch or by a scenario: nobody read where it stopped.
            self.edge_position = None
        if send_stop:
            self.queue_stop()
        self.emit()

    def _forget_position(self) -> None:
        """Nobody read where the cover stopped: no position, as after a restart, until an end stop."""
        cover = self.cover
        cover._cancel_stop_task()
        cover._move_start_time = None
        cover._motion.configure(cover._motion.model)
        cover._motion.position = None
        cover._attr_current_cover_position = None
        cover._attr_is_opening = cover._attr_is_closing = False
        cover._attr_is_closed = False
        cover.async_write_ha_state()

    def _bound(self, callback: Callable[[asyncio.Future[float]], None]) -> Callable[[asyncio.Future[float]], None]:
        """Deliver to `callback` only while the movement that queued the frame is still the current one.

        A frame refused or acknowledged after a later movement was queued (a new step, a new
        check, or a check cut short) belongs to a movement that is over, in every phase.
        """
        token = self._motion_token

        def deliver(future: asyncio.Future[float]) -> None:
            if self._motion_token is token:
                callback(future)
        return deliver

    def _movement_delivered(self, future: asyncio.Future[float]) -> None:
        if future.cancelled() and self.active and not self.closed:
            self.interrupt("not_delivered")

    def on_event(self, event: Any) -> None:
        if self.phase == "geometry_wait_stop":
            self.reservation.observe(event)
            if (event.is_closing if self._rising() else event.is_opening):
                self.interrupt("unexpected_movement")
            elif event.state == 0 and self.stop_written:
                self._stopped()
            return
        starting = self.phase.startswith("starting_")
        super().on_event(event)
        if starting and self.phase in {"opening", "closing"} and (self.step.startswith("half_") or self.step == "check"):
            seconds = (cast(CalibrationCheck, self.check).planned_seconds if self.step == "check"
                       else ((self.samples["lift"] if self.slats else 0.0) + self.values["opening_time"]) / 2
                       if self.step == "half_open" else (self.values["closing_time"] - self.geometry["slat_time_s"]) / 2)
            # Reuse the motion deadline: interruption/close already cancels it.
            cast(asyncio.TimerHandle, self.deadline).cancel()
            self.deadline = self.hass.loop.call_later(seconds, self._sample_stop)

    def _sample_stop(self) -> None:
        self.phase, self.after_stop, self.stop_written = "geometry_wait_stop", "reading", False
        self.arm_deadline(guided.STOP_QUEUE_SECONDS, "not_stopped")
        written = self.queue_stop()
        if written is None:
            self.interrupt("stop_queue_full", send_stop=False)
            return
        written.add_done_callback(self._bound(self._sample_written))
        self.emit()

    def _sample_written(self, future: asyncio.Future[float]) -> None:
        if self.closed or self.phase != "geometry_wait_stop":
            return
        if future.cancelled():
            self.interrupt("not_delivered")
            return
        elapsed = future.result() - cast(float, self.started_at)
        if not 0 <= elapsed <= guided.MAX_TRAVEL_SECONDS:
            self.interrupt("invalid_measurement")
            return
        if self.step == "check":
            cast(CalibrationCheck, self.check).ran(elapsed)
        else:
            self.samples[self.step] = elapsed
        self.stop_written = True
        if not self.reservation.pending:
            self._stopped()
        else:
            self.emit()

    def _endpoint(self) -> None:
        old_step = self.step
        if old_step in {"opening", "closing"}:
            try:
                value = travel_time(guided.monotonic() - cast(float, self.started_at))
            except vol.Invalid as error:
                self.interrupt("invalid_measurement")
                raise ProfileError("invalid_profile") from error
            self.values[f"{old_step}_time"] = value
            self.provenance[old_step] = evidence("guided", self.cover.unique_id)
        self.edge_position = 0 if old_step in {"home", "reset", "closing"} else 100
        self.confirm_position(self.edge_position)
        self.after_stop = "reading" if old_step == "opening" else "briefing"
        self.phase, self.stop_written = "geometry_wait_stop", True
        self.started_at = None
        self.arm_deadline(guided.STOP_QUEUE_SECONDS, "not_stopped")
        written = self.queue_stop()
        if written is None:
            self.interrupt("stop_queue_full", send_stop=False)
            return
        written.add_done_callback(self._bound(self._movement_delivered))
        self.emit()

    def _stopped(self) -> None:
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.started_at = None
        self.phase = self.after_stop
        if self.phase == "briefing":
            self.step = self.after_position if self.step in {"home", "reset", "top"} else "half_open"
        self.emit()

    def _gap(self, value: Any) -> float:
        """A lift-off gap in cm; below TOUCHING_CM only when that repeats the run."""
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise vol.Invalid("The gap must be a reading in cm")
        if value > MAX_GAP_CM or (value < TOUCHING_CM and not LIFT_REPEAT_BELOW_TOUCHING):
            raise ProfileError("invalid_gap")
        return float(value)

    def _reading_range(self) -> dict[str, float] | None:
        """The heights the fit accepts for this intermediate stop, or None if no reading can fit."""
        if self.phase != "reading" or not self.step.startswith("half_"):
            return None
        travel = cast(float, self.measured_travel)
        if self.step == "half_close":
            span = closing_range(self.values["closing_time"], self.geometry["slat_time_s"],
                                 self.samples["half_close"], travel)
        elif self.slats:
            span = opening_range(self.values["opening_time"], self.samples["lift"], self.readings["gap"], travel,
                                 self.samples["half_open"], self.values["closing_time"])
        else:
            span = opening_roll_range(self.values["opening_time"], 0.0, self.samples["half_open"], travel)
        return None if span is None else {"min_cm": span[0], "max_cm": span[1]}

    def _reading(self, value: Any) -> None:
        if self.step == "check":
            # The verdict is published with the measurements; Save stays available either way.
            check = cast(CalibrationCheck, self.check)
            check.read(value)
            self.edge_position = 100 * cast(float, check.measured_cm) / check.travel_cm
            self.checking, self.check_before = False, None
            self.step, self.phase = "half_close", "review"
            self.emit()
            return
        number = self._gap(value) if self.step == "lift" else centimetres(value)
        span = self._reading_range()
        # Outside the computed range no roll from 1 to 5 fits: say which readings would.
        if span and not span["min_cm"] - RANGE_SLACK_CM <= number <= span["max_cm"] + RANGE_SLACK_CM:
            raise ProfileError("reading_out_of_range")
        if self.step == "lift":
            # Still resting: keep nothing of this run, return to the bottom and lift off again.
            self.still_resting = number < TOUCHING_CM
            if self.still_resting:
                del self.samples["lift"]
                self.lift_attempts += 1
            else:
                self.readings["gap"] = number
            # A wide gap is kept, and the lift-off run may still be repeated before moving on.
            self.gap_warning = number >= GAP_WARN_CM
            self.step, self.after_position = "reset", "lift" if self.still_resting else "opening"
        elif self.step == "opening":
            if number <= self.readings.get("gap", 0.0):
                raise vol.Invalid("Travel must exceed the lift-off gap")
            self.measured_travel = number
            self.step = "closing"
        elif self.step == "half_open":
            if self.slats:
                slat, roll = opening_fit(self.values["opening_time"], self.samples["lift"], self.readings["gap"],
                                         cast(float, self.measured_travel), self.samples["half_open"], number)
            else:
                slat, roll = 0.0, opening_roll_fit(self.values["opening_time"], 0.0, self.samples["half_open"],
                                                   number, cast(float, self.measured_travel))
            if slat >= self.values["closing_time"]:
                raise vol.Invalid("Slat time exceeds closing time")
            self.geometry = {"slat_time_s": slat, "opening_roll": roll}
            self.geometry_provenance = {key: evidence("guided", self.cover.unique_id) for key in self.geometry}
            self.readings["half_open"] = number
            self.edge_position = 100 * number / cast(float, self.measured_travel)
            self.step, self.after_position = "top", "half_close"
        else:
            roll = closing_fit(self.values["closing_time"], self.geometry["slat_time_s"], self.samples["half_close"],
                               number, cast(float, self.measured_travel))
            self.geometry["closing_roll"] = roll
            self.geometry_provenance["closing_roll"] = evidence("guided", self.cover.unique_id)
            self.readings["half_close"] = number
            self.edge_position = 100 * number / cast(float, self.measured_travel)
            self.phase = "review"
            self.emit()
            return
        self.phase = "briefing"
        self.emit()

    def _repeat(self) -> None:
        target = "lift" if self.gap_warning else "closing" if self.phase == "briefing" and self.step == "half_open" else self.step
        if target == "check":  # The same check again, from its end stop, keeping nothing of the run.
            self.check = cast(CalibrationCheck, self.check).again()
            self._home_for_check()
            return
        # Measuring again changes the model: a verification of the old one no longer applies.
        self.check, self.check_interrupted = None, None
        # Without slats the return to the bottom is the same close as the first one.
        self.step = "top" if target in {"half_close", "closing"} else "reset" if self.slats else "home"
        self.after_position = target
        if target == "lift":  # A lift-off run repeated on request is one more attempt, and keeps nothing.
            self.lift_attempts += 1
            self.samples.pop("lift", None)
            self.readings.pop("gap", None)
        self.still_resting = self.gap_warning = False
        self.phase = "briefing"
        self.emit()

    async def save_profiles(self, msg: dict[str, Any]) -> Any:
        result = await write_profile(self.hass, {
            "entry_id": self.entry_id, "entity_id": self.cover.entity_id,
            "revision": self.revision, "action": "save", "profile_id": None,
            "profile": {"name": msg.get("name", ""), **self.values,
                        "reference_travel_cm": self.measured_travel, "geometry": self.geometry},
        }, calibration=self)
        # The last tape reading or confirmed end stop is an actual physical observation, not
        # an old-model estimate. Seed the newly applied model only after the atomic save; after
        # a check that moved the cover unread there is nothing to seed.
        if self.edge_position is None:
            self._forget_position()
        else:
            self.confirm_position(self.edge_position)
        return result["revision"]
