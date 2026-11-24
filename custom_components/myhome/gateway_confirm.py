"""Read back a light whose status never followed our command.

A WHO 1 entity takes its state from the actuator's status frame on the event session,
never from the command or its ACK ("ACK ... does not independently prove the final
physical state of an actuator", OpenWebNet Encyclopedia ``protocol/acknowledgements.md``).
That status can go missing: an F455 (firmware 1.0.76) acknowledges a command in about
4 ms, before the bus telegram, and when several commands follow each other within tens of
milliseconds its event session leaves some of the actuators' statuses out. Measured on a
physical F455 with Home Assistant, 2026-11-18 (two of the runs are replayed by
``tests/test_light_echo_confirm.py``): 41 point-to-point light commands, 9 without a
status, all in bursts of four to six commands written within 60-140 ms; the other statuses
arrived up to 0.46 s after the ACK. The lights had switched, Home Assistant kept the old
state until the light changed again. An F461 2.0.11 on the same bus relayed every status,
and in the real-user traces of this repository every ``*1*0|1*<where>##`` this integration
sent was followed by the light's status within 0.33 s (F454, F461, MH200N, MH201, MH202,
MyHomeServer1).

So the watchdog adds no traffic where the status arrives: it only notes each acknowledged
point-to-point WHO 1 command and, when no matching status of that light was received
``ECHO_CONFIRM_DELAY_S`` later, sends one status request ``*#1*<where>##``. The gateway
answers it on the command session (F455 and F461 both do) and the reply is dispatched to
the entities like any status frame. A read never arms the watchdog, so a light that does
not answer costs one request per command and nothing more.

Every gateway entry has this watchdog and nothing in it looks at the gateway model: the F455
is where the loss was measured, not a condition. A gateway that relays every status (the
F461, the traces above) is never sent a request; any gateway that acknowledges a command and
leaves the status out, whatever its model or the load on its bus, gets the same read-back
(``tests/test_light_echo_confirm.py`` runs the worker path once per supported model).

Not covered on purpose: general / area / group commands (``LightingResyncManager`` sweeps
their members) and WHO 2 covers (their position model is anchored on the write time and
does not depend on the relayed status, ``cover.py`` ``_track_write``).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from OWNd.message import OWNLightingCommand, OWNLightingEvent, OWNMessage

from .actuator_lock import actuator_locks_of
from .const import LOGGER
from .data import get_runtime_data
from .where_grammar import expose_message_where, is_valid_hs_tuple, normalize_expose_where

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

# How long after the gateway's ACK a light's status may still arrive before it is read.
# The slowest status seen after one of our commands took 0.46 s (the F455 above; 0.33 s in
# the real-user traces); the cover echo model (ECHO_WINDOW) and the resync leading window
# use the same 1.5 s.
ECHO_CONFIRM_DELAY_S = 1.5
# Statuses are remembered this long to match one that arrived before the ACK of its command
# (a gateway that acknowledges only after the device did may relay the status first).
_RECENT_STATUS_TTL_S = 10.0
_RECENT_STATUS_PRUNE_AT = 64
# A point-to-point WHERE as written on the wire: digits, then ``#``-separated numeric tails
# (``09#4#01``, ``701234501#9``). Anything else (no address, a group ``#1``) is not watched.
_POINT_WHERE = re.compile(r"[0-9]+(?:#[0-9]+)*")


@dataclass(slots=True)
class _Watch:
    """One acknowledged command whose status is still awaited."""

    frame: str
    expected_on: bool | None
    dimension: int | None
    written_at: float
    cancel: CALLBACK_TYPE
    plain_polled: bool = False


def _light_where(message: Any) -> str | None:
    """Full point-to-point WHERE of a WHO 1 frame as it is written on the wire, None for any other frame.

    With its tail: ``09#4#01`` behind an interface, ``701234501#9`` on a Zigbee gateway
    (``message.entity`` drops the ``#9``, and the read-back has to carry it). The same string
    for the command and for the status of one light, so that the two meet under one key.
    """
    if not isinstance(message, OWNMessage) or getattr(message, "who", None) != 1:
        return None
    if (
        getattr(message, "is_general", False)
        or getattr(message, "is_area", False)
        or getattr(message, "is_group", False)
    ):
        return None
    where = expose_message_where(message)
    if not _POINT_WHERE.fullmatch(where) or where.split("#", 1)[0].strip("0") == "":
        return None  # no address, or a broadcast (``0``, the Zigbee unit broadcast ``0#01#9``)
    return _canon_light_where(where)


def _commanded_on(message: Any) -> bool | None:
    """Whether a WHO 1 command turns the light on (True), off (False) or does not say (None)."""
    what = getattr(message, "what", None)
    if isinstance(what, int) and not isinstance(what, bool):
        if what == 0:
            return False
        if 1 <= what <= 18:
            return True  # on, dimmer level 2..10, timed on 11..18
        return None  # blink 20..29, dim step 30 / 31
    # A dimension write: its level decides. Without it, a late status of the command before
    # (an "off" between the write and the ACK of a level write, MyHOME#964 review, MH200)
    # would be taken as this one's answer.
    dimension = getattr(message, "dimension", None)
    if dimension in (1, 4):  # ``*#1*<where>*#1*<100 + level>*<speed>##``, level 0 is off
        level = getattr(message, "brightness", None)
        if isinstance(level, int) and not isinstance(level, bool):
            return level > 0
    elif dimension == 12:  # ``*#1*<where>*#12*<H>*<S>*<V>##``, V 0 is off
        values = getattr(message, "dimension_value", None)
        if isinstance(values, (list, tuple)) and len(values) > 2 and str(values[2]).isdigit():
            return int(values[2]) > 0
    elif dimension == 14:  # ``*#1*<where>*#14*<mireds>##`` (> 1 mireds; <= 1 is DALI sentinel)
        values = getattr(message, "dimension_value", None)
        if isinstance(values, (list, tuple)) and values and str(values[0]).isdigit():
            if int(values[0]) > 1:
                return True
    return None


def _commanded_dimension(message: Any, where: str = "") -> int | None:
    """Return the written read-back dimension (1, 12, or 14) of a WHO 1 write, else None."""
    if "#9" in where:
        return None
    dimension = getattr(message, "dimension", None)
    if dimension in (1, 4):
        return 1
    if dimension in (12, 14):
        return int(dimension)
    return None


def _matches(expected_on: bool | None, reported_on: bool) -> bool:
    """Whether a status with ``reported_on`` can be the answer to a command expecting ``expected_on``."""
    if expected_on is None:
        return True
    return reported_on is expected_on


def _canon_light_where(address: str) -> str:
    """Canonicalize a point light WHERE including zero-padded ``#4#II`` interface suffix."""
    norm = normalize_expose_where(1, address)
    base, sep, suffix = norm.rpartition("#4#")
    if sep and suffix.isdigit():
        return f"{base}#4#{int(suffix):02d}"
    return norm


def _entity_still_not_on(handler: Any, where: str) -> bool:
    """Return True when a registered light entity at ``where`` still has ``_attr_is_on is not True``."""
    entries: list[Any] = []
    if getattr(handler, "is_standby", False) is True:
        pri_fn = getattr(handler, "_get_primary_gateway", None)
        pri_gw = pri_fn() if callable(pri_fn) else None
        if pri_gw is not None and getattr(pri_gw, "config_entry", None) is not None:
            entries.append(pri_gw.config_entry)
    own_entry = getattr(handler, "config_entry", None)
    if own_entry is not None and own_entry not in entries:
        entries.append(own_entry)
    if (mgr := actuator_locks_of(handler)) is not None:
        for bus_entry in mgr._bus_config_entries():
            if bus_entry not in entries:
                entries.append(bus_entry)
    target_where = _canon_light_where(where)
    for entry in entries:
        runtime = get_runtime_data(entry)
        if runtime is None:
            continue
        for ent in (runtime.entities.get("light") or {}).values():
            full = getattr(ent, "_full_where", None)
            if isinstance(full, str) and _canon_light_where(full) == target_where:
                return getattr(ent, "_attr_is_on", None) is not True
    return False


def echo_watchdog_of(handler: Any) -> LightEchoWatchdog | None:
    """The watchdog of a gateway handler; None for a handler double that has none."""
    watchdog = getattr(handler, "_echo_watchdog", None)
    return watchdog if isinstance(watchdog, LightEchoWatchdog) else None


class LightEchoWatchdog:
    """Requests the status of a light that did not report after an acknowledged command."""

    def __init__(self, handler: MyHOMEGatewayHandler) -> None:
        """Initialize the watchdog of one gateway."""
        self.handler = handler
        self._watches: dict[tuple[str, int | None], _Watch] = {}
        self._recent_status: dict[str, dict[int | None, tuple[float, bool]]] = {}

    @property
    def hass(self) -> HomeAssistant:
        """Return the Home Assistant instance."""
        return self.handler.hass

    @property
    def pending(self) -> dict[str, str]:
        """WHERE -> frame of every command still waiting for its status (diagnostics, tests)."""
        return {where: watch.frame for (where, _dim), watch in self._watches.items()}

    def _cancel_where_watches(
        self, where: str, *, conflicting_expected_on: tuple[bool, bool | None] | None = None
    ) -> None:
        """Cancel watches for ``where`` (all, or only those conflicting with ``expected_on``)."""
        for key in [k for k in self._watches if k[0] == where]:
            if conflicting_expected_on is not None:
                _, expected_on = conflicting_expected_on
                if self._watches[key].expected_on is expected_on:
                    continue
            prev = self._watches.pop(key)
            prev.cancel()

    def _record_recent(
        self, where: str, dimension: int | None, now: float, reported_on: bool
    ) -> None:
        """Remember a received status on ``(where, dimension)`` for pre-ACK correlation."""
        existing = self._recent_status.get(where)
        self._recent_status[where] = (
            {**existing, dimension: (now, reported_on)}
            if existing is not None
            else {dimension: (now, reported_on)}
        )
        if len(self._recent_status) > _RECENT_STATUS_PRUNE_AT:
            pruned: dict[str, dict[int | None, tuple[float, bool]]] = {}
            for w, by_dim in self._recent_status.items():
                kept = {
                    d: seen for d, seen in by_dim.items() if now - seen[0] <= _RECENT_STATUS_TTL_S
                }
                if kept:
                    pruned[w] = kept
            self._recent_status = pruned

    def _recent_matches(
        self,
        where: str,
        written_at: float,
        expected_on: bool | None,
        dimension: int | None,
    ) -> bool:
        """Return True when a status for ``(where, dimension)`` already arrived since ``written_at``."""
        by_dim = self._recent_status.get(where)
        if not by_dim:
            return False
        seen = by_dim.get(dimension)
        if seen is not None and seen[0] >= written_at and _matches(expected_on, seen[1]):
            return True
        if dimension == 1 and expected_on is False:
            plain = by_dim.get(None)
            if plain is not None and plain[0] >= written_at and plain[1] is False:
                return True
        return False

    @callback
    def _arm_watch(
        self,
        where: str,
        dimension: int | None,
        frame: str,
        expected_on: bool | None,
        written_at: float,
        delay: float = ECHO_CONFIRM_DELAY_S,
    ) -> None:
        """Arm a single ``(where, dimension)`` expiry timer."""
        from . import gateway as gw_module

        call_later = getattr(gw_module, "async_call_later", async_call_later)

        @callback
        def _expired(_now: Any, w: str = where, d: int | None = dimension) -> None:
            self._confirm(w, d)

        self._watches[(where, dimension)] = _Watch(
            frame=frame,
            expected_on=expected_on,
            dimension=dimension,
            written_at=written_at,
            cancel=call_later(self.hass, delay, _expired),
        )

    @callback
    def command_acknowledged(self, message: Any, written_at: float) -> None:
        """Note a command the gateway acknowledged; ``written_at`` is its monotonic write time."""
        where = _light_where(message)
        if where is None or self._closing():
            return  # nothing is armed for a send that finished after the gateway was closed
        if getattr(message, "is_status_request", False) or getattr(message, "is_dimension_request", False):
            return
        if (mgr := actuator_locks_of(self.handler)) is not None and mgr.is_locked(where) is True:
            self._cancel_where_watches(where)
            return
        expected_on = _commanded_on(message)
        cmd_dim = _commanded_dimension(message, where)
        previous = self._watches.pop((where, cmd_dim), None)
        if previous is not None:
            previous.cancel()
        self._cancel_where_watches(where, conflicting_expected_on=(True, expected_on))
        if self._recent_matches(where, written_at, expected_on, cmd_dim):
            if (
                cmd_dim == 14
                and expected_on is True
                and (where, None) not in self._watches
                and _entity_still_not_on(self.handler, where)
                and not self._recent_matches(where, written_at, True, None)
            ):
                self._arm_watch(where, None, str(message), True, written_at)
            return  # the status overtook the ACK
        self._arm_watch(where, cmd_dim, str(message), expected_on, written_at)

    @callback
    def status_seen(self, message: Any) -> None:
        """Note a frame received from the gateway (event session or command-session reply).

        Only a status that says on or off (or a matching Dimension 12 / 14 reply) counts. Not a
        request or a write echoed on the event session (an MH202 relays ``*#1*32*#1*130*0##``
        30 ms before the dimmer's status, tests/fixtures/traces/issue_466), not a frame with no
        on / off meaning (``*#1*32*13*...##``, toggle 32, motion 34 / 39, anomaly 19, DALI
        sentinels), not a command translation, and not a dim step (``*1*30*<where>##`` /
        ``*1*31*<where>##``), which OWNd reads as "on" and the light entity ignores. Any of
        these would otherwise end a watch, or replace the status remembered for a command whose
        ACK is still to come.
        """
        if not isinstance(message, OWNLightingEvent):
            return
        where = _light_where(message)
        if where is None or getattr(message, "is_translation", None) is True:
            return
        dim = getattr(message, "dimension", None)
        now = time.monotonic()
        raw_ct = getattr(message, "color_temp", None)
        if dim == 14 and isinstance(raw_ct, int) and not isinstance(raw_ct, bool) and raw_ct > 1:
            self._record_recent(where, 14, now, True)
            watch_14 = self._watches.pop((where, 14), None)
            if watch_14 is not None:
                watch_14.cancel()
                if (
                    watch_14.expected_on is True
                    and (where, None) not in self._watches
                    and _entity_still_not_on(self.handler, where)
                    and not self._recent_matches(where, watch_14.written_at, True, None)
                ):
                    remaining = max(0.01, (watch_14.written_at + ECHO_CONFIRM_DELAY_S) - now)
                    self._arm_watch(where, None, watch_14.frame, True, watch_14.written_at, remaining)
            return
        if (
            dim == 12
            and isinstance((dim12_val := getattr(message, "value", None)), int)
            and not isinstance(dim12_val, bool)
            and is_valid_hs_tuple(getattr(message, "hs", None), dim12_val)
        ):
            dim12_on = dim12_val > 0
            self._record_recent(where, 12, now, dim12_on)
            watch_12 = self._watches.get((where, 12))
            if watch_12 is not None and _matches(watch_12.expected_on, dim12_on):
                del self._watches[(where, 12)]
                watch_12.cancel()
            watch_plain = self._watches.get((where, None))
            if watch_plain is not None and _matches(watch_plain.expected_on, dim12_on):
                del self._watches[(where, None)]
                watch_plain.cancel()
            return
        reported_on = getattr(message, "is_on", None)
        if not isinstance(reported_on, bool) or getattr(message, "what", None) in (30, 31):
            return
        self._record_recent(where, None, now, reported_on)
        if dim in (1, 4):
            self._record_recent(where, 1, now, reported_on)
        watch_none = self._watches.get((where, None))
        if watch_none is not None and _matches(watch_none.expected_on, reported_on):
            del self._watches[(where, None)]
            watch_none.cancel()
        watch_1 = self._watches.get((where, 1))
        if (
            watch_1 is not None
            and _matches(watch_1.expected_on, reported_on)
            and (dim in (1, 4) or (watch_1.expected_on is False and reported_on is False))
        ):
            del self._watches[(where, 1)]
            watch_1.cancel()

    @callback
    def _confirm(self, where: str, dimension: int | None) -> None:
        """The status of ``where`` did not arrive: request it once."""
        watch = self._watches.pop((where, dimension), None)
        if watch is None or self._closing():
            return
        if (mgr := actuator_locks_of(self.handler)) is not None and mgr.is_locked(where) is True:
            return
        LOGGER.debug(
            "%s No status of light %s %.1f s after `%s`; requesting it.",
            self.handler.log_id,
            where,
            ECHO_CONFIRM_DELAY_S,
            watch.frame,
        )
        extra_cmd: Any = None
        if watch.dimension == 1:
            extra_cmd = OWNLightingCommand.get_brightness(where)
        elif watch.dimension == 12:
            extra_cmd = OWNLightingCommand.get_hsv_color(where)
        elif watch.dimension == 14:
            extra_cmd = OWNLightingCommand.get_color_temperature(where)

        send_plain = not watch.plain_polled and (
            extra_cmd is None
            or not self._recent_matches(where, watch.written_at, watch.expected_on, None)
        )
        if not send_plain and extra_cmd is None:
            return
        if send_plain:
            for (sib_w, _sib_d), sib in self._watches.items():
                if sib_w == where and sib.expected_on == watch.expected_on:
                    sib.plain_polled = True

        async def _poll() -> None:
            if send_plain:
                await self.handler.send_status_request(OWNLightingCommand.status(where))
            if extra_cmd is not None:
                await self.handler.send_status_request(extra_cmd)

        self.hass.async_create_task(
            _poll(),
            name=f"myhome_light_{where}_echo_confirm",
        )

    def _closing(self) -> bool:
        """Whether the gateway's listener or its sender is being closed: nothing is armed or sent."""
        return (
            getattr(self.handler, "_terminate_listener", False) is True
            or getattr(self.handler, "_terminate_sender", False) is True
        )

    def cancel_all(self) -> None:
        """Forget every awaited status (listener closing, entry unloading)."""
        for watch in self._watches.values():
            watch.cancel()
        self._watches.clear()
        self._recent_status.clear()
