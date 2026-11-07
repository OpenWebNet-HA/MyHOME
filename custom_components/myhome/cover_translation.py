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

The relay never stands the translation of a Stop (`*2*1000#0*WHERE##`) for anything: the cover
platform routes it to the cover itself, which halts an active run on it (#645). Group, area and
general translations are never relayed either: they name no single actuator.

A Stop to a group, an area or every shutter (`*2*0*#G##`, `*2*0*A##`, `*2*0*0##`, or its
translation `*2*1000#0*…##`) stops each actuator it addresses, and the gateway may drop their
statuses as well: it drops what is held for every shutter it addresses, or that shutter would
start one second after it stopped. A group's shutters are the members `myhome.yaml` declares for
it; a group nobody declared may hold any shutter of its bus, so its Stop drops all of them.

A shutter locked via WHO 14 (`*14*0*WHERE##`) answers an Up or a Down with the translations
alone and does not move (live F461 + F411/2 trace): from its lock to its unlock (`*14*1*WHERE##`)
its translations stand for nothing. A lock set while Home Assistant was not listening is not
known here.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant, callback
from OWNd.message import OWNActuatorLockEvent, OWNAutomationEvent

from .const import BUS_ROUTING, LOGGER, area_of_where

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
                 grace: float = TRANSLATION_GRACE,
                 group_members: Callable[[str, str | None], Collection[str] | None] | None = None) -> None:
        self.hass = hass
        self.deliver = deliver
        self.grace = grace
        self.group_members = group_members
        self._held: dict[str, _Held] = {}
        self._locked: set[str] = set()

    @staticmethod
    def _key(message: OWNAutomationEvent | OWNActuatorLockEvent) -> str:
        interface = message.interface
        return f"{message.where}{BUS_ROUTING}{interface}" if interface else str(message.where)

    @callback
    def handle_message(self, message: Any) -> None:
        """Watch every frame of the gateway; only shutter and lock events concern the held translations."""
        if isinstance(message, OWNActuatorLockEvent):
            key = self._key(message)
            if message.is_locked:
                self._locked.add(key)
                self.discard(key)
            elif message.is_unlocked:
                self._locked.discard(key)
            return
        if not isinstance(message, OWNAutomationEvent):
            return
        if message.is_general or message.is_area or message.is_group:
            if message.effective_what == 0:
                for key in self._addressed_by(message):
                    self.discard(key)  # The scope's Stop stopped that shutter too.
            return
        key = self._key(message)
        if not message.is_translation:
            if message.is_opening is not None or message.is_closing is not None:
                self.discard(key)  # The actuator's own status: the usual path handles it.
            return
        movement = movement_translation(message)
        if movement is not None and key in self._locked:
            return  # A locked actuator does not move: its translation stands for nothing.
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

    def _addressed_by(self, message: OWNAutomationEvent) -> list[str]:
        """The held shutters a group, area or general frame addresses (as `CoverScope.contains`)."""
        where, interface = str(message.where), message.interface or None

        def bus_of(key: str) -> str | None:
            return key.partition(BUS_ROUTING)[2] or None

        if message.is_general:
            return [k for k in self._held if interface is None or bus_of(k) == interface]
        if message.is_area:
            return [k for k in self._held
                    if bus_of(k) == interface and area_of_where(k.partition(BUS_ROUTING)[0]) == where]
        members = self.group_members(where, interface) if self.group_members else None
        if members:
            return [k for k in self._held if k in members]
        return [k for k in self._held if bus_of(k) == interface]  # Undeclared: any shutter of its bus.

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
