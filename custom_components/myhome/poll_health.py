"""Remember which addresses never answer a status request, so they stop costing the queue.

A restored zone (or probe) that is no longer on the bus keeps the command queue waiting
for its status request (about 6.4 s per request on a MyHomeServer1 capture, where no
answer frame is recorded), and the queue is serial: eleven such zones cost a startup
about 70 s (#466).

A failed poll counts only when the gateway is connected and no frame of the address
arrived meanwhile. Two failed polls in a row (two restarts) make it *unresponsive*: it
is left out of the startup poll until ``REPROBE_AFTER`` has passed. Any frame from the
address clears it at once, so a thermostat that was merely unpowered recovers by itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SKIP_AFTER_FAILED_POLLS = 2
REPROBE_AFTER = 7 * 24 * 3600.0  # seconds

ATTR_FAILED_POLLS = "failed_polls"
ATTR_UNRESPONSIVE_SINCE = "unresponsive_since"


class PollHealth:
    """Failed-poll bookkeeping of one entity; persisted through its state attributes."""

    def __init__(self) -> None:
        self.failed_polls = 0
        self.since: float | None = None  # epoch seconds of the last failed poll
        self.frames = 0  # frames routed to the entity, to tell a refusal from a slow answer

    @property
    def unresponsive(self) -> bool:
        return self.failed_polls >= SKIP_AFTER_FAILED_POLLS

    def restore(self, attributes: Mapping[str, Any]) -> None:
        try:
            failed = int(attributes.get(ATTR_FAILED_POLLS) or 0)
            since = attributes.get(ATTR_UNRESPONSIVE_SINCE)
            self.failed_polls = max(failed, 0)
            self.since = float(since) if since is not None else None
        except (TypeError, ValueError):
            self.failed_polls, self.since = 0, None

    def attributes(self) -> dict[str, Any]:
        if not self.failed_polls:
            return {}
        return {ATTR_FAILED_POLLS: self.failed_polls, ATTR_UNRESPONSIVE_SINCE: self.since}

    def should_skip(self, now: float) -> bool:
        """Whether the startup poll leaves this address out."""
        return self.unresponsive and self.since is not None and now - self.since < REPROBE_AFTER

    def frame_seen(self) -> bool:
        """Note a frame of the address; True when that clears an unresponsive mark."""
        self.frames += 1
        return self.answered()

    def answered(self) -> bool:
        """The address answered: forget the failures; True if it was marked unresponsive."""
        was = self.unresponsive
        self.failed_polls, self.since = 0, None
        return was

    def failed(self, now: float) -> bool:
        """A poll went unanswered; True when this one makes the address unresponsive."""
        was = self.unresponsive
        self.failed_polls += 1
        self.since = now
        return self.unresponsive and not was
