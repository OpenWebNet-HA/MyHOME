"""Keep gateway ownership independent from the lifetime of its panel client."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic
from typing import Any

# Calibration is measuring an unknown travel time. Existing profile values are
# estimates, so use the full supported travel bound, including automatic runs.
GUARD_SECONDS = 600


class CalibrationReservation:
    """Observe possibly dispatched movement until Stop feedback or a full guard."""

    def __init__(self, session: Any, clock: Callable[[], float] = monotonic, reply_seconds: float = 1.0) -> None:
        self.session = session
        self.timer: asyncio.TimerHandle | None = None
        self.generation = 0
        self.clock = clock
        self.reply_seconds = reply_seconds
        # Our status requests to this cover still waiting for their reply as the monitor session reads it (unmarked):
        # when each was sent, and whether the actuator's last status then was a movement.
        self.awaiting: list[tuple[float, bool]] = []
        self.moving = False

    @property
    def pending(self) -> bool:
        return self.timer is not None

    def dispatched(self) -> None:
        """A worker guard is conservative: transmission may still fail later."""
        if self.timer:
            self.timer.cancel()
        self.timer = self.session.hass.loop.call_later(GUARD_SECONDS, self.completed)

    def asked(self) -> None:
        """One of our status requests reaching this cover is sent: one reply to it is still to come."""
        self.awaiting.append((self.clock(), self.moving))

    def answers(self, event: Any) -> bool:
        """The actuator's status as the monitor session reads it may be the reply to one of our requests: it answers the
        oldest one still open, at most `reply_seconds` old. A stop answers it whatever it was; a movement only if the
        actuator was already reported moving when it was sent, since one sent around a direction command may still be
        answered "stopped" after that command's movement. A marked reply (its copy on the command session) or a command
        translation answers nothing and leaves the actuator's last status as it is."""
        if getattr(event, "myhome_status_reply", False) is True or getattr(event, "translated", False) is True:
            return False
        moving = bool(event.is_opening or event.is_closing)
        if not moving and event.state != 0:
            return False
        now = self.clock()
        self.awaiting = [wait for wait in self.awaiting if now - wait[0] <= self.reply_seconds]
        answered = bool(self.awaiting) and (self.awaiting[0][1] or not moving)
        if answered:
            self.awaiting.pop(0)
        self.moving = moving
        return answered

    def observe(self, event: Any) -> None:
        reply = self.answers(event)
        if event.is_opening or event.is_closing:
            self.dispatched()
        elif event.state == 0 and reply:
            # So close to one of our status requests not answered yet, a stop may be that reply, as old as the request:
            # it is left to the actuator's next stop or the guard.
            return
        elif event.state == 0 and getattr(event, "myhome_status_reply", False) is not True:
            # A stop read as the answer to a status request (the gateway marks it) may predate our own command, so
            # the motor may still run: only the actuator's own stop, or the guard, frees the gateway, even once the
            # session is closed and its Stop is still queued.
            self.completed()

    def completed(self) -> None:
        """A guard expiry permits reuse; it does not prove a physical endpoint."""
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.session.closed:
            self.session.release()

