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
from .cover_motion import ECHO_WINDOW, CoverMotionModel
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
# L-9: an intermediate reading is refused only when it cannot be physical: more than the travel plus the tape's error.
READING_TOLERANCE_CM = 3.0
# L-9: a reading further than this share of the travel outside the heights the rolls 1-5 explain is far from the
# model: accepted, fitted with the roll held at its limit, and reported (`reading_discrepancy`).
READING_WARN_SHARE = 0.10
# During a check these end it and return to review instead of ending the session.
CHECK_RETURNS = frozenset({"stopped", "unexpected_stop", "unexpected_movement"})
# Steps whose run ends at an end stop that the operator confirms with `endpoint`. A stop
# status from the bus there dates the end of the run instead of interrupting it.
ENDPOINT_STEPS = frozenset({"home", "reset", "opening", "closing", "top"})


def edge_percent(reading_cm: float, travel_cm: float) -> float:
    """R-1: where a tape reading puts the edge, in percent of the travel. A reading accepted above the travel (L-9: up
    to its physical limit, the tape read past the window's top) is the top, never past it."""
    return min(100.0, max(0.0, 100 * reading_cm / travel_cm))


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
        # The Stop is written and the deadline waits ECHO_WINDOW for a movement status (see
        # `_await_stop_status`); False while it waits for the write or for the stop status.
        self.stop_grace = False
        self.samples: dict[str, float] = {}
        self.readings: dict[str, float] = {}
        self.geometry: dict[str, float] = {}
        self.geometry_provenance: dict[str, Any] = {}
        # L-9: the intermediate readings far from the model's heights, by step (half_open, half_close).
        self.reading_discrepancy: dict[str, dict[str, float]] = {}
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
                "reading_band": self._reading_band(),
                "reading_discrepancy": {step: dict(item) for step, item in self.reading_discrepancy.items()},
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
        self.reading_discrepancy.clear()
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
        elif action == "endpoint" and self.phase in {"opening", "closing"} and self.step in ENDPOINT_STEPS:
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

    def stop_awaits_endpoint(self) -> bool:
        """Only the end stop runs, the returns of a check included, wait for `endpoint`.

        `lift` keeps the motor running while the operator marks the edge leaving its rest:
        a stop before that press is a genuine early stop and still interrupts
        (`unexpected_stop`). The intermediate runs and the check run are stopped by the
        session itself, so a stop before its Stop is unexpected there too.
        """
        return self.step in ENDPOINT_STEPS

    def _rising(self) -> bool:
        """The direction of the current step's run."""
        if self.step == "check":
            return cast(CalibrationCheck, self.check).opening
        return self.step in {"lift", "opening", "half_open", "top"}

    def _check(self, msg: dict[str, Any]) -> None:
        """Verify the measured model before Save: back to the end stop, then a timed run and a reading."""
        model = profile_motion({**self.values, "geometry": self.geometry})
        check = CalibrationCheck.plan_possible(cast(CoverMotionModel, model), cast(float, self.measured_travel),
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
        self.forget_motor_stop()
        self.stop_written = self.stop_grace = self.checking = False
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
            elif self.stop_grace and (event.is_opening or event.is_closing):
                # The run is still reported after our Stop: only its stop status ends it now.
                self.stop_grace = False
                self.arm_deadline(guided.STOP_QUEUE_SECONDS, "not_stopped")
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
        self.stop_grace = False
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
            self._await_stop_status(future.result())
            self.emit()

    def _endpoint_written(self, future: asyncio.Future[float]) -> None:
        self._movement_delivered(future)
        if not future.cancelled() and not self.closed and self.phase == "geometry_wait_stop":
            self._await_stop_status(future.result())

    def _await_stop_status(self, written_at: float) -> None:
        """The Stop is on the bus: without a movement status ECHO_WINDOW after it, the motor has stopped.

        A MyHOMEServer1 sometimes relays only the translation of our Stop (`*2*1000#0*WHERE##`,
        which the gateway handler drops) and never the stop status itself, so the step would
        wait for the `not_stopped` deadline with a delivered Stop. The stop status still ends
        the wait as soon as it arrives; an opposite movement status still interrupts; a
        movement status in the run's direction gives the `not_stopped` deadline back. The
        sample was already taken from the write. That deadline stays armed until the write
        is acknowledged, so a Stop that is never written still ends in `not_stopped`.
        """
        self.stop_grace = True
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.deadline = self.hass.loop.call_later(max(0.0, written_at + ECHO_WINDOW - guided.monotonic()),
                                                  self._stopped)

    def _endpoint(self) -> None:
        old_step = self.step
        if old_step in {"opening", "closing"}:
            # As `run_time()`, except that a press time stays unrounded here, as it always has.
            if self.motor_stopped:  # Confirmed after the bus reported the stop: the stop ends the run.
                seconds, ended_by = cast(float, self.stopped_elapsed), "bus_stop"
            else:
                seconds, ended_by = guided.monotonic() - cast(float, self.started_at), "press"
            try:
                value = travel_time(seconds)
            except vol.Invalid as error:
                self.interrupt("invalid_measurement")
                raise ProfileError("invalid_profile") from error
            self.values[f"{old_step}_time"] = value
            self.provenance[old_step] = evidence("guided", self.cover.unique_id, ended_by)
        self.edge_position = 0 if old_step in {"home", "reset", "closing"} else 100
        self.confirm_position(self.edge_position)
        self.after_stop = "reading" if old_step == "opening" else "briefing"
        if self.motor_stopped:
            # The bus has already reported the stop, so no second stop status is awaited. Stop
            # is still written, as after every confirmed end stop, but the step does not
            # depend on its delivery: the motor is known to be stationary. As in the base
            # `endpoint`, a full queue only leaves `stop_queue_full` as the reason.
            self.forget_motor_stop()
            self.queue_stop()
            self._stopped()
            return
        self.phase, self.stop_written, self.stop_grace = "geometry_wait_stop", True, False
        self.started_at = None
        self.arm_deadline(guided.STOP_QUEUE_SECONDS, "not_stopped")
        written = self.queue_stop()
        if written is None:
            self.interrupt("stop_queue_full", send_stop=False)
            return
        written.add_done_callback(self._bound(self._endpoint_written))
        self.emit()

    def _stopped(self) -> None:
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.stop_grace = False
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
        """L-9: the heights accepted for an intermediate stop: the physical ones, from 0 to the travel plus the tape's error."""
        if self.phase != "reading" or not self.step.startswith("half_"):
            return None
        return {"min_cm": 0.1, "max_cm": cast(float, self.measured_travel) + READING_TOLERANCE_CM}

    def _reading_band(self) -> dict[str, float] | None:
        """L-9: the heights near the model for this stop (the rolls 1-5, widened by READING_WARN_SHARE of the travel),
        and the middle of what the rolls explain as the expected one; None where the model has no heights."""
        span = self._model_span()
        if span is None:
            return None
        travel, slack = cast(float, self.measured_travel), READING_WARN_SHARE * cast(float, self.measured_travel)
        return {"min_cm": max(0.0, span[0] - slack), "max_cm": min(travel, span[1] + slack), "expected_cm": (span[0] + span[1]) / 2,
                # L-30: the heights the rolls 1-5 explain, the «usually between» of the field's hint.
                "model_min_cm": span[0], "model_max_cm": span[1]}

    def _model_span(self) -> tuple[float, float] | None:
        """The heights the rolls 1-5 explain for this intermediate stop, or None if no roll gives one."""
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
        return span

    def _reading(self, value: Any) -> None:
        if self.step == "check":
            # The verdict is published with the measurements; Save stays available either way.
            check = cast(CalibrationCheck, self.check)
            check.read(value)
            self.edge_position = edge_percent(cast(float, check.measured_cm), check.travel_cm)
            self.checking, self.check_before = False, None
            self.step, self.phase = "half_close", "review"
            self.emit()
            return
        number = self._gap(value) if self.step == "lift" else centimetres(value)
        span = self._reading_range()
        # L-9: only a reading that cannot be physical is refused; one far from the model is kept and reported.
        if span and number > span["max_cm"] + RANGE_SLACK_CM:
            raise ProfileError("reading_over_travel")
        band = self._reading_band()
        if band and not band["min_cm"] - RANGE_SLACK_CM <= number <= band["max_cm"] + RANGE_SLACK_CM:
            self.reading_discrepancy[self.step] = {"reading_cm": number, "expected_cm": band["expected_cm"],
                                                   "min_cm": band["min_cm"], "max_cm": band["max_cm"]}
        elif self.step.startswith("half_"):
            self.reading_discrepancy.pop(self.step, None)
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
                                         cast(float, self.measured_travel), self.samples["half_open"], number,
                                         clamp=True, closing=self.values["closing_time"])
            else:
                slat, roll = 0.0, opening_roll_fit(self.values["opening_time"], 0.0, self.samples["half_open"],
                                                   number, cast(float, self.measured_travel), clamp=True)
            # The clamped fit keeps the slat time from 0 to below both full times (`opening_rolls`); without slats it is 0.
            self.geometry = {"slat_time_s": slat, "opening_roll": roll}
            self.geometry_provenance = {key: evidence("guided", self.cover.unique_id) for key in self.geometry}
            self.readings["half_open"] = number
            self.edge_position = edge_percent(number, cast(float, self.measured_travel))
            self.step, self.after_position = "top", "half_close"
        else:
            roll = closing_fit(self.values["closing_time"], self.geometry["slat_time_s"], self.samples["half_close"],
                               number, cast(float, self.measured_travel), clamp=True)
            self.geometry["closing_roll"] = roll
            self.geometry_provenance["closing_roll"] = evidence("guided", self.cover.unique_id)
            self.readings["half_close"] = number
            self.edge_position = edge_percent(number, cast(float, self.measured_travel))
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
        self.reading_discrepancy.pop(target, None)
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
        # L-36: a profile written while the cover still looked in motion waits for its next stop status, and until then
        # the old model keeps estimating; the motor is known to be still here, so the new model applies now, then the
        # position is anchored in it.
        self.cover._attr_is_opening = self.cover._attr_is_closing = False
        self.cover._move_start_time = None
        self.cover._apply_pending_cover_profile()
        if self.edge_position is None:
            self._forget_position()
        else:
            self.confirm_position(self.edge_position)
        return result["revision"]
