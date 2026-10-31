"""A shutter's command translation stands in for the movement status the gateway did not relay.

For a direction command addressed to one shutter a MyHOMEServer1 puts on the monitor its
command translation (`*2*1000#1*WHERE##` up, `*2*1000#2*WHERE##` down) within about 0.1 s of
the write, then the actuator's own status (`*2*1*WHERE##`, `*2*2*WHERE##`) about 0.4 s later.
Now and then that status is never relayed, to any client, while the motor runs: the cover
then looks stationary and a calibration run is discarded as never started.

The translation is held for `TRANSLATION_GRACE` seconds. Any status of the same shutter in
that time supersedes it, so the usual sequence is handled exactly as before; so does the
translation of a Stop, after which the run is over. Only a translation that nothing followed
reaches the cover, as the movement status it stands for, dated when the translation arrived.

The translation of a Stop (`*2*1000#0*WHERE##`) is never relayed: the gateway sends one around
every direction command, before the motor starts. Group, area and general translations are
never relayed either: they name no single actuator.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant, callback
from OWNd.message import OWNAutomationEvent

from .const import BUS_ROUTING, LOGGER

#: Seconds a direction translation waits for the actuator's own status (which follows in ~0.4 s).
TRANSLATION_GRACE = 1.0

# `*2*1000#<1|2>*<where>[#4#<interface>]##`: one shutter's direction, nothing else.
_DIRECTION_TRANSLATION = re.compile(r"^\*2\*1000#(?P<what>[12])\*(?P<where>\d+(?:#\d+)*)##$")


class TranslatedMovement(OWNAutomationEvent):
    """The movement status a command translation stands for.

    It reads as `*2*1*WHERE##` / `*2*2*WHERE##` (opening / closing, `state` 1 / 2), while
    `str()` stays the frame that was received. `age` is how long before its delivery the
    translation arrived: the movement is dated from then.
    """

    translated = True

    def __init__(self, translation: OWNAutomationEvent, status: str) -> None:
        super().__init__(status)
        self._raw = str(translation)
        self._human_readable_log = f"{self._human_readable_log.rstrip('.')} (command translation)."
        self.age = 0.0


def movement_translation(message: Any) -> TranslatedMovement | None:
    """The movement a point-to-point direction translation stands for, or None for any other frame."""
    if not isinstance(message, OWNAutomationEvent) or not message.is_translation:
        return None
    if message.is_general or message.is_area or message.is_group:
        return None
    match = _DIRECTION_TRANSLATION.match(str(message))
    if match is None:
        return None
    return TranslatedMovement(message, f"*2*{match['what']}*{match['where']}##")


def event_age(event: Any) -> float:
    """Seconds between a movement's arrival on the bus and its delivery (0 for a status frame)."""
    return event.age if isinstance(event, TranslatedMovement) else 0.0


@dataclass
class _Held:
    movement: TranslatedMovement
    arrived: float
    timer: asyncio.TimerHandle


class CoverTranslationRelay:
    """Hold each shutter's direction translation until its own status supersedes it, or deliver it."""

    def __init__(self, hass: HomeAssistant, deliver: Callable[[TranslatedMovement], None],
                 grace: float = TRANSLATION_GRACE) -> None:
        self.hass = hass
        self.deliver = deliver
        self.grace = grace
        self._held: dict[str, _Held] = {}

    @staticmethod
    def _key(message: OWNAutomationEvent) -> str:
        interface = message.interface
        return f"{message.where}{BUS_ROUTING}{interface}" if interface else str(message.where)

    @callback
    def handle_message(self, message: Any) -> None:
        """Watch every frame of the gateway; only shutter events concern the held translations."""
        if not isinstance(message, OWNAutomationEvent):
            return
        key = self._key(message)
        if not message.is_translation:
            if message.is_opening is not None or message.is_closing is not None:
                self.discard(key)  # The actuator's own status: the usual path handles it.
            return
        movement = movement_translation(message)
        if movement is None:
            if message.effective_what == 0:
                self.discard(key)  # A Stop translated after the direction: that run is over.
            return
        held = self._held.get(key)
        if held is not None and held.movement.state == movement.state:
            return  # The same direction again: the first one dates the movement.
        self.discard(key)
        timer = self.hass.loop.call_later(self.grace, self._release, key)
        self._held[key] = _Held(movement, monotonic(), timer)

    @callback
    def _release(self, key: str) -> None:
        held = self._held.pop(key)
        held.movement.age = monotonic() - held.arrived
        LOGGER.debug(
            "No status followed the command translation `%s` within %.1f s: delivered as the movement of cover %s",
            held.movement, self.grace, key,
        )
        self.deliver(held.movement)

    @callback
    def discard(self, key: str) -> None:
        """Forget the translation held for `key`, if any."""
        held = self._held.pop(key, None)
        if held is not None:
            held.timer.cancel()

    @callback
    def close(self) -> None:
        """Forget every held translation (the config entry unloads)."""
        for key in list(self._held):
            self.discard(key)
