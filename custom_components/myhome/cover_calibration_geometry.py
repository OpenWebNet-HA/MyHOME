"""Explicitly briefed basic slat/roll measurement, owned by the existing session."""
from __future__ import annotations

import asyncio
from typing import Any, cast

import voluptuous as vol

from . import cover_calibration as guided
from .cover_calibration_fit import closing_fit, opening_fit
from .cover_profile_provenance import evidence
from .cover_profiles import ProfileError, travel_time, write_profile
from .cover_settings import centimetres


class GeometryCalibrationSession(guided.CalibrationSession):
    """No browser timings, unattended continuation, or partially saved geometry."""

    mode = "geometry"
    safe_phases = guided.CalibrationSession.safe_phases | {"briefing", "reading"}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.phase, self.step = "briefing", "home"
        self.after_position = "lift"
        self.after_stop = "briefing"
        self.stop_written = False
        self.samples: dict[str, float] = {}
        self.readings: dict[str, float] = {}
        self.geometry: dict[str, float] = {}
        self.geometry_provenance: dict[str, Any] = {}

    def view(self) -> Any:
        return {**super().view(), "step": self.step, "save_modes": ["new"],
                "travel_cm": self.measured_travel, "geometry": dict(self.geometry),
                "readings": dict(self.readings), "samples": dict(self.samples),
                "accuracy": None, "independent_check": False,
                "can_repeat": self.phase in {"reading", "review"} or (self.phase == "briefing" and self.step == "half_open"),
                "reading_kind": "travel" if self.step == "opening" else self.step,
                "expected_cm": self.measured_travel / 2 if self.measured_travel and self.step.startswith("half_") else None}

    def interrupt(self, reason: str, send_stop: Any = True) -> None:
        if not self.active or self.phase == "saving":
            return
        self.geometry.clear()
        self.geometry_provenance = {}
        self.samples.clear()
        self.readings.clear()
        self.measured_travel = None
        super().interrupt(reason, send_stop)

    def geometry_action(self, msg: dict[str, Any]) -> None:
        action = msg["action"]
        if action == "next" and self.phase == "briefing":
            direction = "close" if self.step in {"home", "reset", "closing", "half_close"} else "open"
            written = self.queue_move(direction)
            written.add_done_callback(self._movement_delivered)
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
        else:
            raise ProfileError("calibration_step")

    def _movement_delivered(self, future: asyncio.Future[float]) -> None:
        if future.cancelled() and self.active and not self.closed:
            self.interrupt("not_delivered")

    def on_event(self, event: Any) -> None:
        if self.phase == "geometry_wait_stop":
            self.reservation.observe(event)
            opening = self.step in {"lift", "opening", "half_open", "top"}
            if (event.is_closing if opening else event.is_opening):
                self.interrupt("unexpected_movement")
            elif event.state == 0 and self.stop_written:
                self._stopped()
            return
        starting = self.phase.startswith("starting_")
        super().on_event(event)
        if starting and self.phase in {"opening", "closing"} and self.step.startswith("half_"):
            seconds = ((self.samples["lift"] + self.values["opening_time"]) / 2 if self.step == "half_open"
                       else (self.values["closing_time"] - self.geometry["slat_time_s"]) / 2)
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
        written.add_done_callback(self._sample_written)
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
        self.confirm_position(0 if old_step in {"home", "reset", "closing"} else 100)
        self.after_stop = "reading" if old_step == "opening" else "briefing"
        self.phase, self.stop_written = "geometry_wait_stop", True
        self.started_at = None
        self.arm_deadline(guided.STOP_QUEUE_SECONDS, "not_stopped")
        written = self.queue_stop()
        if written is None:
            self.interrupt("stop_queue_full", send_stop=False)
            return
        written.add_done_callback(self._movement_delivered)
        self.emit()

    def _stopped(self) -> None:
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.started_at = None
        self.phase = self.after_stop
        if self.phase == "briefing":
            self.step = self.after_position if self.step in {"home", "reset", "top"} else "half_open"
        self.emit()

    def _reading(self, value: Any) -> None:
        # Zero is meaningful only for the gap immediately after lift-off.
        number = 0.0 if self.step == "lift" and type(value) in (int, float) and value == 0 else centimetres(value)
        if self.step == "lift":
            self.readings["gap"] = number
            self.step, self.after_position = "reset", "opening"
        elif self.step == "opening":
            if number <= self.readings["gap"]:
                raise vol.Invalid("Travel must exceed the lift-off gap")
            self.measured_travel = number
            self.step = "closing"
        elif self.step == "half_open":
            slat, roll = opening_fit(self.values["opening_time"], self.samples["lift"], self.readings["gap"],
                                     cast(float, self.measured_travel), self.samples["half_open"], number)
            if slat >= self.values["closing_time"]:
                raise vol.Invalid("Slat time exceeds closing time")
            self.geometry = {"slat_time_s": slat, "opening_roll": roll}
            self.geometry_provenance = {key: evidence("guided", self.cover.unique_id) for key in self.geometry}
            self.readings["half_open"] = number
            self.step, self.after_position = "top", "half_close"
        else:
            roll = closing_fit(self.values["closing_time"], self.geometry["slat_time_s"], self.samples["half_close"],
                               number, cast(float, self.measured_travel))
            self.geometry["closing_roll"] = roll
            self.geometry_provenance["closing_roll"] = evidence("guided", self.cover.unique_id)
            self.readings["half_close"] = number
            self.phase = "review"
            self.emit()
            return
        self.phase = "briefing"
        self.emit()

    def _repeat(self) -> None:
        target = "closing" if self.phase == "briefing" and self.step == "half_open" else self.step
        self.step = "top" if target in {"half_close", "closing"} else "reset"
        self.after_position = target
        self.phase = "briefing"
        self.emit()

    async def save_profiles(self, msg: dict[str, Any]) -> Any:
        result = await write_profile(self.hass, {
            "entry_id": self.entry_id, "entity_id": self.cover.entity_id,
            "revision": self.revision, "action": "save", "profile_id": None,
            "profile": {"name": msg.get("name", ""), **self.values,
                        "reference_travel_cm": self.measured_travel, "geometry": self.geometry},
        }, calibration=self)
        # This tape reading is an actual physical observation, not an old-model
        # estimate. Seed the newly applied model only after the atomic save.
        self.confirm_position(100 * self.readings["half_close"] / cast(float, self.measured_travel))
        return result["revision"]
