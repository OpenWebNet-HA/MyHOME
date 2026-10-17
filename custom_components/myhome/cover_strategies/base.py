"""Base protocol and abstract class for pluggable MyHOME cover kinematics strategies."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationEvent


@runtime_checkable
class CoverKinematicsStrategy(Protocol):
    """Protocol implemented by all pluggable WHO=2 cover kinematics strategies."""

    @property
    def name(self) -> str:
        """Return strategy identifier."""

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return Home Assistant CoverEntityFeature bitmask for this strategy."""

    def current_position(self, entity: Any, now: float) -> int | None:
        """Return current cover position (0..100) or None if position is unknown."""

    async def async_open(self, entity: Any) -> Any:
        """Open the cover."""

    async def async_close(self, entity: Any) -> Any:
        """Close the cover."""

    async def async_stop(self, entity: Any) -> None:
        """Stop the cover."""

    async def async_set_position(self, entity: Any, position: int) -> None:
        """Move the cover to a target position (0..100)."""

    def handle_event(self, entity: Any, message: OWNAutomationEvent, now: float) -> None:
        """Process an incoming WHO=2 bus event for the cover."""
