"""Travel-time interpolated curtain strategy for calibrated standard WHO=2 covers."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from homeassistant.util import dt as dt_util

from ..const import DEFAULT_MIN_MOTOR_PULSE, DEFAULT_TRAVEL_TIME, LOGGER
from ..cover_motion import compute_run_duration
from .base import BaseCoverStrategy, CoverKinematicsStrategy


class TimedCurtainStrategy(BaseCoverStrategy):
    """Timed curtain kinematics strategy owning time<->position mapping, pulse floor, and stop-echo re-anchoring."""

    def __init__(
        self,
        travel_time: float = DEFAULT_TRAVEL_TIME,
        *,
        travel_time_up: float | None = None,
        travel_time_down: float | None = None,
        min_motor_pulse: float = DEFAULT_MIN_MOTOR_PULSE,
        slat_time: float | None = None,
    ) -> None:
        """Initialize timed curtain strategy with direction-aware travel times and motor floor."""
        super().__init__(
            travel_time=travel_time,
            travel_time_up=travel_time_up,
            travel_time_down=travel_time_down,
            min_motor_pulse=min_motor_pulse,
            slat_time=slat_time,
        )

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "timed"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return OPEN | CLOSE | STOP | SET_POSITION."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )

    @property
    def assumed_state(self) -> bool:
        """Timed covers expose an interpolated percentage position."""
        return False

    def position_after(
        self,
        start: int,
        elapsed: float,
        direction: str,
        *,
        entity: Any = None,
        lead: float | None = None,
    ) -> int | None:
        """Compute clamped [0, 100] cover position after ``elapsed`` seconds in ``direction`` from ``start``."""
        clamped_start = max(0, min(100, int(round(start))))
        opening = direction == "open"
        closing = direction == "close"
        if not (opening or closing):
            return clamped_start
        travel_time = self.curtain_travel_for(opening)
        if travel_time <= 0:
            if entity is not None and isinstance(getattr(entity, "_attr_current_cover_position", None), int):
                return int(entity._attr_current_cover_position)
            return clamped_start
        effective_lead = (
            self.lead_time_for(opening, entity=entity)
            if lead is None
            else max(0.0, float(lead))
        )
        curtain_elapsed = max(0.0, float(elapsed) - effective_lead)
        delta = (curtain_elapsed / travel_time) * 100.0
        if opening:
            return min(100, int(round(clamped_start + delta)))
        return max(0, int(round(clamped_start - delta)))

    def duration_for(
        self,
        start: int,
        target: int,
        direction: str | None = None,
        *,
        entity: Any = None,
    ) -> float:
        """Compute motor run duration in seconds from ``start`` to ``target``, enforcing ``min_motor_pulse``."""
        clamped_start = max(0, min(100, int(round(start))))
        clamped_target = max(0, min(100, int(round(target))))
        diff = clamped_target - clamped_start
        if diff == 0:
            return 0.0
        opening = (direction == "open") if direction in ("open", "close") else (diff > 0)
        if (opening and diff < 0) or (not opening and diff > 0):
            return 0.0
        travel_time = self.curtain_travel_for(opening)
        if travel_time <= 0:
            return 0.0
        lead = self.lead_time_for(opening, entity=entity)
        min_pulse = self.min_pulse_for()
        curtain_duration = compute_run_duration(
            diff, travel_time, min_pulse=max(0.0, min_pulse - lead)
        )
        return lead + curtain_duration

    def _reanchor_slats_on_stop_echo(
        self,
        entity: Any,
        ctx: dict[str, Any],
        elapsed: float,
        is_opening: bool,
        is_closing: bool,
    ) -> None:
        """Hook for subclasses (e.g. VenetianTiltStrategy) to re-anchor slat tilt on stop echo."""

    def reanchor_on_stop_echo(
        self,
        entity: Any,
        ctx: dict[str, Any],
        now: float,
    ) -> int | None:
        """Re-anchor cover position and travel telemetry on a relayed stop status echo (#705, #707)."""
        if not ctx or ctx.get("run_started_at") is None or ctx.get("run_start_position") is None:
            pos: int | None = getattr(entity, "_attr_current_cover_position", None)
            return pos
        start_anchor = float(ctx["run_started_at"])
        run_start_pos = int(ctx["run_start_position"])
        is_opening = bool(ctx.get("is_opening"))
        is_closing = bool(ctx.get("is_closing"))
        auto_dur = ctx.get("auto_stop_duration")
        lead = float(ctx.get("lead") or 0.0)
        raw_elapsed = max(0.0, now - start_anchor)
        elapsed = max(raw_elapsed, float(auto_dur)) if auto_dur is not None else raw_elapsed
        direction = "open" if is_opening else "close" if is_closing else ""
        travel_time = self.curtain_travel_for(is_opening)
        reanchored: int | None = getattr(entity, "_attr_current_cover_position", None)
        if travel_time > 0 and (is_opening or is_closing):
            reanchored = self.position_after(
                run_start_pos, elapsed, direction, entity=entity, lead=lead
            )
            entity._attr_current_cover_position = reanchored
            entity._start_position = reanchored
            entity._attr_is_closed = reanchored == 0
        self._reanchor_slats_on_stop_echo(entity, ctx, elapsed, is_opening, is_closing)
        entity._last_run = {
            "seconds": round(elapsed, 2),
            "direction": "open" if is_opening else "close" if is_closing else None,
            "ended_at": dt_util.utcnow().isoformat(timespec="milliseconds"),
        }
        if hasattr(entity, "_refresh_travel_attributes"):
            entity._refresh_travel_attributes()
        log_id = getattr(getattr(entity, "_gateway_handler", None), "log_id", "")
        LOGGER.debug(
            "%s Stop status echo for %s; re-anchored to %s%% (elapsed %.2f s).",
            log_id,
            getattr(entity, "_full_where", ""),
            entity._attr_current_cover_position,
            elapsed,
        )
        return reanchored

    def current_position(self, entity: Any, now: float) -> int | None:
        """Interpolate current position from start position, motor anchor, and strategy kinematics."""
        is_opening = getattr(entity, "_attr_is_opening", False) is True
        is_closing = getattr(entity, "_attr_is_closing", False) is True
        move_start = getattr(entity, "_move_start_time", None)
        if (
            isinstance(move_start, (int, float))
            and (is_opening or is_closing)
            and self.curtain_travel_for(is_opening) > 0
        ):
            direction = "open" if is_opening else "close"
            elapsed = max(0.0, now - float(move_start))
            # The lead still ahead of this segment, as the entity anchored it.
            move_lead = getattr(entity, "_move_lead", None)
            return self.position_after(
                int(getattr(entity, "_start_position", 50)),
                elapsed,
                direction,
                entity=entity,
                lead=float(move_lead) if isinstance(move_lead, (int, float)) else None,
            )
        pos: int | None = getattr(entity, "_attr_current_cover_position", None)
        return pos

    def with_slat_tilt(self) -> CoverKinematicsStrategy:
        """The built-in timed strategy becomes Venetian; a subclass stays itself."""
        if type(self) is not TimedCurtainStrategy:
            return self._kept()
        from .venetian import VenetianTiltStrategy  # pylint: disable=import-outside-toplevel

        return self._rebuilt_as(VenetianTiltStrategy)

    def demoted(self) -> CoverKinematicsStrategy:
        """The built-in timed strategy becomes simple again; a subclass stays itself."""
        if type(self) is not TimedCurtainStrategy:
            return self._kept()
        from .simple import SimpleUpDownStopStrategy  # pylint: disable=import-outside-toplevel

        return self._rebuilt_as(SimpleUpDownStopStrategy)
