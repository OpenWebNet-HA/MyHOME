"""Venetian blind slat-tilt and curtain strategy for WHO=2 shutters (#492)."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.cover import CoverEntityFeature
from OWNd.message import OWNAutomationCommand

from .timed import TimedCurtainStrategy

MIN_TILT_PULSE: float = 0.2


class VenetianTiltStrategy(TimedCurtainStrategy):
    """Timed curtain + Venetian slat-tilt strategy with short-pulse tilt control."""

    def __init__(self, tilt_time: float = 1.5) -> None:
        """Initialize Venetian tilt strategy with slat full-rotation time in seconds."""
        self.tilt_time = max(MIN_TILT_PULSE, float(tilt_time))
        self.tilt_position: int = 50

    @property
    def name(self) -> str:
        """Return strategy identifier."""
        return "venetian"

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return curtain features plus OPEN_TILT | CLOSE_TILT | STOP_TILT | SET_TILT_POSITION."""
        return (
            super().supported_features
            | CoverEntityFeature.OPEN_TILT
            | CoverEntityFeature.CLOSE_TILT
            | CoverEntityFeature.STOP_TILT
            | CoverEntityFeature.SET_TILT_POSITION
        )

    async def async_open_tilt(self, entity: Any) -> None:
        """Open Venetian slats fully (100%)."""
        await self.async_set_tilt_position(entity, 100)

    async def async_close_tilt(self, entity: Any) -> None:
        """Close Venetian slats fully (0%)."""
        await self.async_set_tilt_position(entity, 0)

    async def async_stop_tilt(self, entity: Any) -> None:
        """Stop slat rotation immediately."""
        await entity._gateway_handler.send(
            OWNAutomationCommand.stop_shutter(entity._full_where)
        )
        entity._publish_state()

    async def async_set_tilt_position(self, entity: Any, tilt_position: int) -> None:
        """Pulse motor for the fraction of tilt_time needed to reach tilt_position."""
        target = max(0, min(100, int(tilt_position)))
        diff = target - self.tilt_position
        if diff == 0:
            return
        pulse = max(MIN_TILT_PULSE, (abs(diff) / 100.0) * self.tilt_time)
        cmd = (
            OWNAutomationCommand.raise_shutter(entity._full_where)
            if diff > 0
            else OWNAutomationCommand.lower_shutter(entity._full_where)
        )
        await entity._gateway_handler.send(cmd)
        await asyncio.sleep(pulse)
        await entity._gateway_handler.send(
            OWNAutomationCommand.stop_shutter(entity._full_where)
        )
        self.tilt_position = target
        entity._publish_state()
