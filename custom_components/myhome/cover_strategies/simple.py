"""Simple up/down/stop cover strategy without synthetic stopwatch position."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverEntityFeature

from .base import BaseCoverStrategy, CoverKinematicsStrategy
from .timed import TimedCurtainStrategy
from .venetian import VenetianTiltStrategy


class SimpleUpDownStopStrategy(BaseCoverStrategy):
    """Stateless UP / DOWN / STOP cover strategy without position estimation."""

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "simple"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return OPEN | CLOSE | STOP (no synthetic position slider)."""
        return (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
        )

    @property
    def assumed_state(self) -> bool:
        """Simple UP/DOWN/STOP relays do not report or estimate intermediate position."""
        return True

    def travel_for_direction(self, opening: bool) -> float:  # pylint: disable=unused-argument
        """Simple UP/DOWN/STOP covers have no travel duration."""
        return 0.0

    def position_after(
        self,
        start: int,
        elapsed: float,
        direction: str,
        *,
        entity: Any = None,
        lead: float | None = None,
    ) -> int | None:
        """Simple covers do not estimate percentage position over time."""
        return None

    def duration_for(
        self,
        start: int,
        target: int,
        direction: str | None = None,
        *,
        entity: Any = None,
    ) -> float:
        """Simple covers do not run timed intermediate moves."""
        return 0.0

    def reanchor_on_stop_echo(
        self,
        entity: Any,
        ctx: dict[str, Any],
        now: float,
    ) -> int | None:
        """Simple covers have no dead-reckoning anchor to update on stop echoes."""
        return None

    def current_position(self, entity: Any, now: float) -> int | None:
        """Simple covers do not fabricate a percentage position."""
        return None

    def promoted(self, *, slat_tilt: bool) -> CoverKinematicsStrategy:
        """Become timed (or Venetian) once travel times are calibrated; a subclass stays itself."""
        if type(self) is not SimpleUpDownStopStrategy:
            return self._kept()
        return self._rebuilt_as(VenetianTiltStrategy if slat_tilt else TimedCurtainStrategy)
