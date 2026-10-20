"""Gateway session runners and command worker pool for MyHOME.

Manages the persistent event session background loop (watchdog heartbeat,
stall detection, exponential reconnect backoff) and the command session
worker pool (command pacing, send queue, idle disconnect, delivery futures).
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import itertools
import re
import time
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway
from OWNd.message import OWNCommand, OWNMessage

from .const import LOGGER, area_of_where
from .where_grammar import normalize_where, where_param

if TYPE_CHECKING:
    from .bus_monitor import BusMonitor
    from .gateway import MyHOMEGatewayHandler

EVENT_READY_TIMEOUT: float = 120.0
COMMAND_SESSION_IDLE_TIMEOUT: float = 15.0
EVENT_STALL_TIMEOUT: float = 600.0
EVENT_RESTART_BACKOFF_MIN: float = 5.0
EVENT_RESTART_BACKOFF_MAX: float = 60.0
# OWNd's default EVENT_INACTIVITY_TIMEOUT (3900s) proactively recycles quiet event
# sessions. On installations with only lights and shutters, no bus traffic occurs
# overnight, causing cyclic reconnect warning bursts on gateways like MH201.
# None relies on OS TCP keepalive (TCP_KEEPIDLE=30) and the runner watchdog while
# leaving sessions open indefinitely. Installations desiring a generous hard ceiling
# for "TCP up, bus dead" conditions can override this constant (e.g. 43200.0 / 12h).
EVENT_INACTIVITY_TIMEOUT: float | None = None


def _resolve_written(task: dict[str, Any], when: float) -> None:
    """Complete a queued frame's delivery future with the write timestamp."""
    written = task.get("written")
    if isinstance(written, asyncio.Future) and not written.done():
        written.set_result(when)


def _cancel_written(task: dict[str, Any]) -> None:
    """Cancel a queued frame's delivery future (the frame will never be written)."""
    written = task.get("written")
    if isinstance(written, asyncio.Future) and not written.done():
        written.cancel()


def _session_is_open(session: Any) -> bool:
    """Whether an OWNd session has an open socket.

    Not ``is_connected``: OWNd's ``close()`` only drops the streams and leaves
    that flag as ``connect()`` last set it, so after the idle close it still
    reads ``True``. The streams are what ``send()`` would reopen.
    """
    return getattr(session, "_stream_reader", None) is not None and getattr(session, "_stream_writer", None) is not None


class EventSessionRunner:
    """Manages the persistent background monitor session for a MyHOME gateway."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        *,
        stall_timeout: float = EVENT_STALL_TIMEOUT,
    ) -> None:
        """Initialize the event session runner."""
        self.handler = handler
        self.stall_timeout = stall_timeout
        self._terminate_listener: bool = False
        self._event_session_ready: asyncio.Event = asyncio.Event()
        self._event_watchdog: asyncio.Timeout | None = None
        self.is_connected: bool = False

    @property
    def gateway(self) -> OWNGateway:
        """Return the underlying OWNGateway instance."""
        return self.handler.gateway

    @property
    def bus_monitor(self) -> BusMonitor:
        """Return the bus monitor instance."""
        return self.handler.bus_monitor

    @property
    def log_id(self) -> str:
        """Return the gateway log id."""
        return str(self.handler.log_id)

    @property
    def event_session_ready(self) -> asyncio.Event:
        """Return the event session ready event."""
        return self._event_session_ready

    def _update_event_watchdog(self, *, progress: bool) -> None:
        """Arm the stall deadline while disconnected, disarm it while connected.

        ``progress`` means get_next() just returned, which restarts the deadline;
        a bare state change only arms it when it is not already running.
        """
        watchdog = self._event_watchdog
        if watchdog is None or watchdog.expired():
            return
        if self.is_connected:
            watchdog.reschedule(None)
        elif progress or watchdog.when() is None:
            from . import gateway as gw_module

            current_stall_timeout = float(getattr(gw_module, "EVENT_STALL_TIMEOUT", self.stall_timeout))
            watchdog.reschedule(asyncio.get_running_loop().time() + current_stall_timeout)

    async def listening_loop(self) -> None:
        """Run the event session, recreating it whenever it dies or stalls.

        OWNd re-establishes a dropped socket inside get_next(); this loop covers
        the rest: an exception escaping the read loop, or a connect() / get_next()
        that stays disconnected without returning.
        """
        self._terminate_listener = False
        self._event_session_ready.clear()

        LOGGER.debug("%s Creating listening worker.", self.log_id)

        try:
            from . import gateway as gw_module

            failures = 0
            started = time.monotonic()
            while await self._run_event_session():
                self.handler._on_event_connection_state_change(False)
                backoff_min = float(getattr(gw_module, "EVENT_RESTART_BACKOFF_MIN", EVENT_RESTART_BACKOFF_MIN))
                backoff_max = float(getattr(gw_module, "EVENT_RESTART_BACKOFF_MAX", EVENT_RESTART_BACKOFF_MAX))
                failures = 1 if time.monotonic() - started >= backoff_max else failures + 1
                delay = min(backoff_max, backoff_min * 2 ** (failures - 1))
                LOGGER.warning(
                    "%s Recreating the event session in %ss (attempt %d).",
                    self.log_id,
                    delay,
                    failures,
                )
                await asyncio.sleep(delay)
                started = time.monotonic()
        except asyncio.CancelledError:
            # Unload or shutdown: the gateway is going away, not losing its
            # connection, so no availability grace timer.
            self._terminate_listener = True
            raise
        finally:
            # Also when the task is cancelled mid back-off.
            self.handler._on_event_connection_state_change(False)
            LOGGER.debug("%s Destroying listening worker.", self.log_id)

    async def _run_event_session(self) -> bool:
        """Open one event session and dispatch its frames until it ends.

        Returns True when the session ended unexpectedly (an exception, or the
        stall watchdog) and should be recreated; False when the listener is
        terminating, or when the gateway refused the session outright.
        """
        if self._terminate_listener:
            return False
        from . import gateway as gw_module

        event_session_cls = getattr(gw_module, "OWNEventSession", OWNEventSession)
        inactivity_timeout = getattr(
            gw_module, "EVENT_INACTIVITY_TIMEOUT", EVENT_INACTIVITY_TIMEOUT
        )
        _event_session = event_session_cls(
            gateway=self.gateway,
            logger=LOGGER,
            inactivity_timeout=inactivity_timeout,
            on_state_change=self.handler._on_event_connection_state_change,
        )
        watchdog = asyncio.timeout(None)
        current_stall_timeout = float(getattr(gw_module, "EVENT_STALL_TIMEOUT", self.stall_timeout))
        try:
            async with watchdog:
                self._event_watchdog = watchdog
                # Armed before connect(): a connect that never returns is a stall too.
                self._update_event_watchdog(progress=True)
                await self._read_event_session(_event_session)
            return False
        except Exception as err:
            if isinstance(err, TimeoutError) and watchdog.expired():
                LOGGER.warning(
                    "%s Event session stalled: disconnected with no reconnect "
                    "progress for %ss.",
                    self.log_id,
                    current_stall_timeout,
                )
            else:
                LOGGER.exception("%s Event listener failed.", self.log_id)
        finally:
            self._event_watchdog = None
            with contextlib.suppress(Exception):
                await asyncio.shield(_event_session.close())
        return not self._terminate_listener

    async def _read_event_session(self, _event_session: OWNEventSession) -> None:
        """Connect ``_event_session`` and dispatch its frames.

        Returns when the listener terminates or the gateway refuses the session;
        any other end is an exception, which the caller answers by recreating it.
        """
        from . import gateway as gw_module

        session_is_open = getattr(gw_module, "_session_is_open", _session_is_open)

        res = await _event_session.connect()
        if (
            isinstance(res, dict)
            and res.get("Success", False)
            and getattr(_event_session, "is_connected", True)
        ):
            self.handler._on_event_connection_state_change(True)
            LOGGER.debug(
                "%s Event session ready, command sessions can now start.",
                self.log_id,
            )
        elif isinstance(res, dict) and not res.get("Success", True):
            if res.get("Message") in ("password_error", "password_required", "negotiation_refused", "connection_refused"):
                LOGGER.error(
                    "%s Event session authentication or connection refused (%s). "
                    "Terminating event listener to prevent gateway lockout.",
                    self.log_id,
                    res.get("Message"),
                )
                self.handler._on_event_connection_state_change(False)
                return
        else:
            LOGGER.warning(
                "%s Initial event session was not established; reconnecting "
                "without allowing command sessions to start.",
                self.log_id,
            )
        self._update_event_watchdog(progress=True)

        was_reachable = True
        while not self._terminate_listener:
            message = await _event_session.get_next()
            self._update_event_watchdog(progress=True)
            if message is None:
                reachable = session_is_open(_event_session)
                if reachable != was_reachable:
                    LOGGER.info(
                        "%s Event session %s.",
                        self.log_id,
                        "reconnected" if reachable else "lost; gateway not reachable, retrying",
                    )
                else:
                    LOGGER.debug(
                        "%s Event session reconnect cycle finished (%s).",
                        self.log_id,
                        "connected" if reachable else "gateway not reachable",
                    )
                was_reachable = reachable
                continue
            self.bus_monitor.record_frame(
                direction="rx",
                raw=str(message),
                parsed=message if isinstance(message, OWNMessage) else None,
            )
            LOGGER.debug("%s Message received: `%s`", self.log_id, message)
            try:
                await self.handler._process_message(message)
            except Exception:
                LOGGER.exception("%s Failed to process `%s`.", self.log_id, message)

    def close(self) -> None:
        """Signal listener termination and unblock waiting workers."""
        self._terminate_listener = True
        self._event_session_ready.set()
        if self._event_watchdog is not None:
            # An expired timeout cannot be rescheduled; unload racing the stall lands here.
            if not self._event_watchdog.expired():
                self._event_watchdog.reschedule(None)
            self._event_watchdog = None


_COVER_FRAME = re.compile(r"^\*(?P<who>#?2)\*(?P<a>[^*]+)(?:\*(?P<b>[^*]+))?(?:\*[^*]+)*##$")
_COVER_AREAS = frozenset({"00", "100", *"123456789"})


def _cover_frame_parts(message: Any) -> tuple[str, str] | None:
    """Return ``(what, where)`` of a WHO 2 write, or ``None`` for anything else.

    Covers ``*2*<WHAT>*<WHERE>##`` and dimension writes ``*#2*<WHERE>*#<DIM>*<V>##``;
    status and dimension requests carry no motion and are not cover frames.
    """
    match = _COVER_FRAME.match(str(message))
    if match is None:
        return None
    if match["who"] == "#2":
        # *#2*<WHERE>*#<DIM>*<VALUE>##: only a write (``*#`` dimension) moves the motor.
        raw = str(message)
        return ("#dim", match["a"]) if "*#" in raw[len("*#2*") :] else None
    if match["b"] is None:
        return None
    return match["a"], match["b"]


def _cover_where(message: Any) -> str | None:
    """The WHERE a WHO 2 motion frame addresses, or ``None`` if it is not one."""
    parts = _cover_frame_parts(message)
    return parts[1] if parts else None


def _is_cover_stop(message: Any) -> bool:
    """Whether ``message`` is a WHO 2 stop: ``*2*0*<WHERE>##`` or advanced ``*2*10#...*<WHERE>##``."""
    parts = _cover_frame_parts(message)
    return parts is not None and (parts[0] == "0" or parts[0] == "10" or parts[0].startswith("10#"))


def _split_where(where: str) -> tuple[str, str | None]:
    """Split ``11#4#02`` into ``("11", "2")``; an address on the main bus has no interface."""
    if "#4#" not in where:
        return where, None
    base, _, interface = where.rpartition("#4#")
    norm_if = (interface.lstrip("0") or "0") if interface.isdigit() else interface
    return base, norm_if


def _same_cover_where(first: str, second: str) -> bool:
    """Whether two WHO 2 WHEREs address the exact same physical cover actuator."""
    if first == second:
        return True
    base_a, if_a = _split_where(first)
    base_b, if_b = _split_where(second)
    return if_a == if_b and (
        base_a == base_b
        or (
            base_a not in ("0", "00")
            and base_b not in ("0", "00")
            and base_a.isdigit()
            and base_b.isdigit()
            and int(base_a) == int(base_b)
        )
    )


def _wheres_overlap(first: str, second: str) -> bool:
    """Whether two WHO 2 WHEREs can move the same actuator (conservative: unknown means overlap).

    A group's membership lives in the actuators and is never seen on the bus, so a
    group overlaps every address on a compatible bus.
    """
    if _same_cover_where(first, second):
        return True
    base_a, if_a = _split_where(first)
    base_b, if_b = _split_where(second)
    compatible = if_a is None or if_b is None or if_a == if_b

    def _is_scope(base: str) -> bool:
        return base == "0" or base in _COVER_AREAS or base.startswith("#")

    for scope, scope_if, other, other_if in ((base_a, if_a, base_b, if_b), (base_b, if_b, base_a, if_a)):
        if not _is_scope(scope):
            continue
        if scope == "0":
            if scope_if is None or scope_if == other_if:
                return True
        elif scope.startswith("#"):
            if compatible:
                return True
        elif scope_if == other_if and (other == scope or area_of_where(other) == scope):
            return True
    return False


_TIER_COMMANDS = 0
_TIER_FADE_STEPS = 1
_TIER_STATUS = 2
_TIER_OTHERS = 3

_FRAME_TARGET_RE = re.compile(
    r"^\*(?:#(?P<dim_who>\d+)\*(?P<dim_where>[^*]+)(?:\*.*)?|(?P<act_who>\d+)\*[^*]+\*(?P<act_where>[^*]+))##$"
)


def _classify_tier(item: Any) -> int:
    """Classify item into priority tier: 0 (commands), 1 (fade steps), 2 (status), 3 (others/sentinels).

    Expected envelope dictionaries are produced by CommandWorkerPool._enqueue:
    {
        "message": OWNCommand,
        "is_status_request": bool,
        "is_fade_step": bool (optional),
        "is_cancel": bool (optional),
        "written": asyncio.Future[float],
    }
    """
    if isinstance(item, dict):
        if item.get("is_status_request", False):
            return _TIER_STATUS
        if item.get("is_fade_step", False):
            return _TIER_FADE_STEPS
        if "message" in item or "is_status_request" in item:
            return _TIER_COMMANDS
    return _TIER_OTHERS


def _normalize_target_where(who: int, raw_where: str) -> str:
    """Canonicalize a target WHERE string across OpenWebNet subsystems.

    Normalizes leading zeros on non-APL numeric addresses via ``normalize_where``,
    canonicalizes local bus interface suffixes (``#4#2`` -> ``#4#02``), and strips
    leading zeros on group numbers (``#03`` -> ``#3``).
    """
    where_s = str(raw_where).strip()
    if not where_s:
        return ""
    if "#4#" in where_s:
        base, _, iface = where_s.rpartition("#4#")
        norm_iface = (iface.lstrip("0") or "0").zfill(2) if iface.isdigit() else iface
        suffix = f"#4#{norm_iface}"
    else:
        base = where_s
        suffix = ""

    norm_base = normalize_where(base)
    if norm_base.startswith("#") and norm_base[1:].isdigit():
        norm_base = "#" + (norm_base[1:].lstrip("0") or "0")
    elif who not in (1, 2) and norm_base.isdigit() and norm_base not in ("0", "00", "100"):
        norm_base = str(int(norm_base))
    return f"{norm_base}{suffix}"


def _extract_target_key(item: Any) -> tuple[int, str] | None:
    """Extract a canonical (who, where) target key from an envelope dict."""
    if not isinstance(item, dict):
        return None
    target = item.get("target")
    if isinstance(target, tuple) and len(target) == 2:
        who_i = int(target[0])
        norm_w = _normalize_target_where(who_i, str(target[1]))
        return (who_i, norm_w) if norm_w else None
    msg = item.get("message")
    if msg is None:
        return None
    who = getattr(msg, "who", None)
    where = getattr(msg, "where", None)
    if who is not None and where is not None:
        wp = where_param(msg)
        raw_where = f"{where}#{'#'.join(str(p) for p in wp)}" if wp else str(where)
        who_i = int(who)
        norm_w = _normalize_target_where(who_i, raw_where)
        if norm_w:
            return (who_i, norm_w)
    raw = str(msg).strip()
    m = _FRAME_TARGET_RE.match(raw)
    if m:
        if m.group("dim_who") is not None:
            who_i = int(m.group("dim_who"))
            norm_w = _normalize_target_where(who_i, m.group("dim_where"))
        else:
            who_i = int(m.group("act_who"))
            norm_w = _normalize_target_where(who_i, m.group("act_where"))
        if norm_w:
            return (who_i, norm_w)
    return None


def _same_target(first: tuple[int, str], second: tuple[int, str]) -> bool:
    """Return True if two target keys represent the same physical target."""
    if first[0] != second[0]:
        return False
    if first[1] == second[1]:
        return True
    if first[0] == 2:
        return _same_cover_where(first[1], second[1])
    return False


def _targets_overlap(first: tuple[int, str], second: tuple[int, str]) -> bool:
    """Return True if two target keys overlap for in-flight worker serialization."""
    if first[0] != second[0]:
        return False
    if first[1] == second[1]:
        return True
    if first[0] == 2:
        return _wheres_overlap(first[1], second[1])
    return False


def _is_cancel_item(item: Any) -> bool:
    """Return True if an envelope dict represents a cancellation or WHO=2 stop command."""
    if not isinstance(item, dict) or item.get("is_status_request", False):
        return False
    if "is_cancel" in item:
        return bool(item["is_cancel"])
    msg = item.get("message")
    return msg is not None and _is_cover_stop(msg)


class _PriorityQueueStorage:
    """Multi-tier FIFO storage backing FrameScheduler.

    Implements the sequence and deque interface expected by queue inspection
    code and unit tests (e.g. list(handler.send_buffer._queue)).
    """

    def __init__(self) -> None:
        """Initialize empty priority tiers."""
        self._commands: collections.deque[Any] = collections.deque()
        self._fade_steps: collections.deque[Any] = collections.deque()
        self._status: collections.deque[Any] = collections.deque()
        self._others: collections.deque[Any] = collections.deque()

    def __len__(self) -> int:
        """Return total number of items across all tiers."""
        return (
            len(self._commands)
            + len(self._fade_steps)
            + len(self._status)
            + len(self._others)
        )

    def __bool__(self) -> bool:
        """Return True if any tier has items."""
        return bool(self._commands or self._fade_steps or self._status or self._others)

    def __iter__(self) -> Iterator[Any]:
        """Iterate in priority order: commands, fade_steps, status, others."""
        return itertools.chain(
            self._commands,
            self._fade_steps,
            self._status,
            self._others,
        )

    def __reversed__(self) -> Iterator[Any]:
        """Iterate in reverse priority order."""
        return itertools.chain(
            reversed(self._others),
            reversed(self._status),
            reversed(self._fade_steps),
            reversed(self._commands),
        )

    def __repr__(self) -> str:
        """Return string representation of priority tiers."""
        return (
            f"_PriorityQueueStorage(commands={list(self._commands)!r}, "
            f"fade_steps={list(self._fade_steps)!r}, "
            f"status={list(self._status)!r}, others={list(self._others)!r})"
        )

    def __eq__(self, other: object) -> bool:
        """Return True if other has identical items in the same priority order."""
        if isinstance(other, _PriorityQueueStorage):
            return (
                self._commands == other._commands
                and self._fade_steps == other._fade_steps
                and self._status == other._status
                and self._others == other._others
            )
        if isinstance(other, (collections.deque, list)):
            return list(self) == list(other)
        return False

    def __contains__(self, item: Any) -> bool:
        """Return True if item exists in any tier."""
        return (
            item in self._commands
            or item in self._fade_steps
            or item in self._status
            or item in self._others
        )

    def __getitem__(self, index: int | slice) -> Any:
        """Get item by index or slice in priority order."""
        if isinstance(index, slice):
            return list(self)[index]
        if not isinstance(index, int):
            raise TypeError(f"queue indices must be integers or slices, not {type(index).__name__}")
        total = len(self)
        if index < 0:
            index += total
        if index < 0 or index >= total:
            raise IndexError("queue index out of range")
        if index < len(self._commands):
            return self._commands[index]
        index -= len(self._commands)
        if index < len(self._fade_steps):
            return self._fade_steps[index]
        index -= len(self._fade_steps)
        if index < len(self._status):
            return self._status[index]
        index -= len(self._status)
        return self._others[index]

    def append(self, item: Any) -> None:
        """Append item to its corresponding priority tier.

        A WHO 2 stop is the exception inside the command tier (#645): it goes behind
        the contiguous run of stops at the head, behind any queued cover frame whose
        WHERE overlaps its own, or to the front when none does, so it never overtakes
        a frame it has to stop while allowing unrelated stops to jump ahead.
        """
        tier = _classify_tier(item)
        if tier == _TIER_COMMANDS:
            self._commands.insert(self._stop_position(item), item)
        elif tier == _TIER_FADE_STEPS:
            self._fade_steps.append(item)
        elif tier == _TIER_STATUS:
            self._status.append(item)
        else:
            self._others.append(item)

    def _stop_position(self, item: Any) -> int:
        """Index in the command tier for ``item``: its end, or earlier for a cover stop.

        Stops preserve FIFO with the run of stops at the head of the command tier,
        while ensuring a stop never overtakes earlier queued frames whose WHERE
        overlaps its own (including earlier stops for the same cover or overlapping
        scopes). Stops for unrelated covers can overtake non-overlapping frames even
        when earlier stops are queued deeper behind motion (#645).
        """
        end = len(self._commands)
        if not isinstance(item, dict) or not _is_cover_stop(item.get("message")):
            return end
        # ``_is_cover_stop`` guarantees a parsed frame, hence a WHERE.
        stop_where = str(_cover_where(item["message"]))
        head_run_end = 0
        while head_run_end < end:
            queued = self._commands[head_run_end]
            msg = queued.get("message") if isinstance(queued, dict) else None
            if not _is_cover_stop(msg):
                break
            head_run_end += 1
        overlap_pos = 0
        for index, queued in enumerate(self._commands):
            msg = queued.get("message") if isinstance(queued, dict) else None
            queued_where = _cover_where(msg)
            if queued_where is not None and _wheres_overlap(stop_where, queued_where):
                overlap_pos = max(overlap_pos, index + 1)
        return max(overlap_pos, head_run_end)

    def appendleft(self, item: Any) -> None:
        """Append item to the head of its corresponding priority tier."""
        tier = _classify_tier(item)
        if tier == _TIER_COMMANDS:
            self._commands.appendleft(item)
        elif tier == _TIER_FADE_STEPS:
            self._fade_steps.appendleft(item)
        elif tier == _TIER_STATUS:
            self._status.appendleft(item)
        else:
            self._others.appendleft(item)

    def extend(self, iterable: Iterable[Any]) -> None:
        """Extend storage by appending elements from the iterable."""
        for item in iterable:
            self.append(item)

    def extendleft(self, iterable: Iterable[Any]) -> None:
        """Extend storage by prepending elements from the iterable."""
        for item in iterable:
            self.appendleft(item)

    def popleft(self) -> Any:
        """Pop and return the highest priority item."""
        if self._commands:
            return self._commands.popleft()
        if self._fade_steps:
            return self._fade_steps.popleft()
        if self._status:
            return self._status.popleft()
        if self._others:
            return self._others.popleft()
        raise IndexError("pop from an empty priority queue")

    def pop_eligible(self, in_flight_targets: set[tuple[int, str]]) -> Any:
        """Pop and return the highest priority item whose target does not overlap any in-flight target."""
        if not in_flight_targets:
            return self.popleft()
        for q in (self._commands, self._fade_steps, self._status, self._others):
            for idx, item in enumerate(q):
                target = _extract_target_key(item)
                if target is None or not any(
                    _targets_overlap(target, active) for active in in_flight_targets
                ):
                    del q[idx]
                    return item
        raise IndexError("no eligible item in priority queue")

    def has_eligible(self, in_flight_targets: set[tuple[int, str]]) -> bool:
        """Return True if at least one queued item's target does not overlap any in-flight target."""
        if not in_flight_targets:
            return bool(self)
        for q in (self._commands, self._fade_steps, self._status, self._others):
            for item in q:
                target = _extract_target_key(item)
                if target is None or not any(
                    _targets_overlap(target, active) for active in in_flight_targets
                ):
                    return True
        return False

    def drop_superseded(self, new_item: Any) -> list[dict[str, Any]]:
        """Drop queued frames superseded by new_item for the same physical (who, where) target."""
        target = _extract_target_key(new_item)
        if target is None or not isinstance(new_item, dict):
            return []
        tier = _classify_tier(new_item)
        dropped: list[dict[str, Any]] = []
        if tier in (_TIER_COMMANDS, _TIER_FADE_STEPS) and self._fade_steps:
            kept_fades: collections.deque[Any] = collections.deque()
            for existing in self._fade_steps:
                existing_target = _extract_target_key(existing)
                if (
                    existing_target is not None
                    and _same_target(existing_target, target)
                    and isinstance(existing, dict)
                ):
                    dropped.append(existing)
                else:
                    kept_fades.append(existing)
            self._fade_steps = kept_fades
        if _is_cancel_item(new_item) and self._commands:
            kept_cmds: collections.deque[Any] = collections.deque()
            for existing in self._commands:
                existing_target = _extract_target_key(existing)
                if (
                    existing_target is not None
                    and _same_target(existing_target, target)
                    and isinstance(existing, dict)
                    and not _is_cancel_item(existing)
                ):
                    dropped.append(existing)
                else:
                    kept_cmds.append(existing)
            self._commands = kept_cmds
        return dropped

    def pop(self) -> Any:
        """Pop and return the lowest priority item from the tail."""
        if self._others:
            return self._others.pop()
        if self._status:
            return self._status.pop()
        if self._fade_steps:
            return self._fade_steps.pop()
        if self._commands:
            return self._commands.pop()
        raise IndexError("pop from an empty priority queue")

    def remove(self, value: Any) -> None:
        """Remove first occurrence of value."""
        tier = _classify_tier(value)
        if tier == _TIER_COMMANDS:
            tiers = (self._commands, self._fade_steps, self._status, self._others)
        elif tier == _TIER_FADE_STEPS:
            tiers = (self._fade_steps, self._commands, self._status, self._others)
        elif tier == _TIER_STATUS:
            tiers = (self._status, self._commands, self._fade_steps, self._others)
        else:
            tiers = (self._others, self._commands, self._fade_steps, self._status)
        for q in tiers:
            try:
                q.remove(value)
                return
            except ValueError:
                pass
        raise ValueError(f"{value!r} not in priority queue")

    def clear(self) -> None:
        """Clear all priority tiers."""
        self._commands.clear()
        self._fade_steps.clear()
        self._status.clear()
        self._others.clear()

    def copy(self) -> _PriorityQueueStorage:
        """Return shallow copy of storage."""
        new_storage = _PriorityQueueStorage()
        new_storage._commands = self._commands.copy()
        new_storage._fade_steps = self._fade_steps.copy()
        new_storage._status = self._status.copy()
        new_storage._others = self._others.copy()
        return new_storage

    def count(self, value: Any) -> int:
        """Return total occurrences of value across all tiers."""
        return (
            self._commands.count(value)
            + self._fade_steps.count(value)
            + self._status.count(value)
            + self._others.count(value)
        )

    def index(self, value: Any, start: int = 0, stop: int | None = None) -> int:
        """Return first index of value."""
        items = list(self)
        if stop is None:
            return items.index(value, start)
        return items.index(value, start, stop)


class FrameScheduler:
    """Standalone multi-tier frame scheduler with per-target worker serialization.

    Replaces the CPython-coupled ``asyncio.Queue`` subclass while providing a
    drop-in queue interface plus per-target ``(who, where)`` in-flight locking
    (including WHO=2 cover WHERE overlap rules) and stale-frame superseding
    across concurrent command workers.

    Command workers MUST dequeue via :meth:`acquire_next` / :meth:`acquire_nowait`
    and release via :meth:`complete_task` to enforce per-target serialization.
    Plain :meth:`get` and :meth:`get_nowait` remain lock-free for backwards-compatible
    queue inspection and shutdown draining.
    """

    def __init__(self, maxsize: int = 0) -> None:
        """Initialize the scheduler storage, waiter queues, and target locks."""
        self._maxsize = maxsize
        self._queue = _PriorityQueueStorage()
        self._getters: collections.deque[asyncio.Future[None]] = collections.deque()
        self._putters: collections.deque[asyncio.Future[None]] = collections.deque()
        self._unfinished_tasks = 0
        self._finished = asyncio.Event()
        self._finished.set()
        self._is_shutdown = False
        self._in_flight_targets: set[tuple[int, str]] = set()

    @property
    def maxsize(self) -> int:
        """Number of items allowed in the queue."""
        return self._maxsize

    def qsize(self) -> int:
        """Number of items in the queue."""
        return len(self._queue)

    def empty(self) -> bool:
        """Return True if the queue is empty, False otherwise."""
        return not self._queue

    def full(self) -> bool:
        """Return True if there are maxsize items in the queue."""
        if self._maxsize <= 0:
            return False
        return self.qsize() >= self._maxsize

    @property
    def command_qsize(self) -> int:
        """Return the number of queued user commands."""
        return len(self._queue._commands)

    @property
    def fade_qsize(self) -> int:
        """Return the number of queued software fade steps."""
        return len(self._queue._fade_steps)

    @property
    def status_qsize(self) -> int:
        """Return the number of queued status requests."""
        return len(self._queue._status)

    @property
    def other_qsize(self) -> int:
        """Return the number of queued sentinels and unclassified items."""
        return len(self._queue._others)

    @property
    def in_flight_targets(self) -> frozenset[tuple[int, str]]:
        """Return the set of (who, where) targets currently executing on a worker."""
        return frozenset(self._in_flight_targets)

    def _wakeup_next(self, waiters: collections.deque[asyncio.Future[Any]]) -> None:
        """Wake up the next waiter that is not cancelled."""
        while waiters:
            waiter = waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                break

    def _wakeup_all_getters(self) -> None:
        """Wake up all waiting getters when target locks or items change."""
        while self._getters:
            waiter = self._getters.popleft()
            if not waiter.done():
                waiter.set_result(None)

    def _drop_superseded_and_cancel(self, item: Any) -> None:
        """Drop superseded queued frames for the same target and cancel their futures."""
        dropped = self._queue.drop_superseded(item)
        for old_item in dropped:
            _cancel_written(old_item)
            if self._unfinished_tasks > 0:
                self._unfinished_tasks -= 1
                if self._unfinished_tasks == 0:
                    self._finished.set()

    def put_nowait(self, item: Any) -> None:
        """Put an item into the queue without blocking."""
        if self._is_shutdown:
            raise getattr(asyncio, "QueueShutDown", Exception)
        self._drop_superseded_and_cancel(item)
        if self.full():
            raise asyncio.QueueFull
        self._queue.append(item)
        self._unfinished_tasks += 1
        self._finished.clear()
        self._wakeup_next(self._getters)
        if not self.full():
            self._wakeup_next(self._putters)

    async def put(self, item: Any) -> None:
        """Put an item into the queue, prioritizing command waiters when full."""
        if not self._is_shutdown:
            self._drop_superseded_and_cancel(item)
        while self.full():
            if self._is_shutdown:
                raise getattr(asyncio, "QueueShutDown", Exception)
            putter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            is_cmd = _classify_tier(item) == _TIER_COMMANDS
            setattr(putter, "_is_cmd", is_cmd)
            if is_cmd:
                idx = 0
                for p in self._putters:
                    if getattr(p, "_is_cmd", False):
                        idx += 1
                    else:
                        break
                self._putters.insert(idx, putter)
            else:
                self._putters.append(putter)
            try:
                await putter
            except BaseException:
                putter.cancel()
                with contextlib.suppress(ValueError):
                    self._putters.remove(putter)
                if not self.full() and not putter.cancelled():
                    self._wakeup_next(self._putters)
                raise
            self._drop_superseded_and_cancel(item)
        return self.put_nowait(item)

    def get_nowait(self) -> Any:
        """Remove and return an item without target locking (for inspection/drain)."""
        if self.empty():
            raise asyncio.QueueEmpty
        item = self._queue.popleft()
        self._wakeup_next(self._putters)
        return item

    async def get(self) -> Any:
        """Remove and return the highest-priority item without target locking."""
        while self.empty():
            if self._is_shutdown:
                raise getattr(asyncio, "QueueShutDown", Exception)
            getter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._getters.append(getter)
            try:
                await getter
            except BaseException:
                getter.cancel()
                with contextlib.suppress(ValueError):
                    self._getters.remove(getter)
                raise
        return self.get_nowait()

    def acquire_nowait(self) -> Any:
        """Pop the highest-priority eligible item and lock its (who, where) target."""
        if not self._queue.has_eligible(self._in_flight_targets):
            raise asyncio.QueueEmpty
        item = self._queue.pop_eligible(self._in_flight_targets)
        target = _extract_target_key(item)
        if target is not None:
            self._in_flight_targets.add(target)
        self._wakeup_next(self._putters)
        return item

    async def acquire_next(self) -> Any:
        """Wait for and pop the highest-priority item whose target is not in flight."""
        while not self._queue.has_eligible(self._in_flight_targets):
            if self._is_shutdown and self.empty():
                raise getattr(asyncio, "QueueShutDown", Exception)
            getter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._getters.append(getter)
            try:
                await getter
            except BaseException:
                getter.cancel()
                with contextlib.suppress(ValueError):
                    self._getters.remove(getter)
                raise
        return self.acquire_nowait()

    def release_target(self, target: tuple[int, str] | None) -> None:
        """Release an in-flight target key and wake blocked workers."""
        if target is None:
            return
        who_i = int(target[0])
        norm_w = _normalize_target_where(who_i, str(target[1]))
        norm_target = (who_i, norm_w) if norm_w else target
        matching = [
            t for t in self._in_flight_targets if t == norm_target or _same_target(t, norm_target)
        ]
        if matching:
            for t in matching:
                self._in_flight_targets.discard(t)
            if self._queue.has_eligible(self._in_flight_targets):
                self._wakeup_all_getters()

    def complete_task(self, task: Any) -> None:
        """Release a task's target lock and mark the queue task done."""
        target = _extract_target_key(task)
        self.release_target(target)
        self.task_done()

    def task_done(self) -> None:
        """Indicate that a formerly enqueued task is complete."""
        if self._unfinished_tasks <= 0:
            raise ValueError("task_done() called too many times")
        self._unfinished_tasks -= 1
        if self._unfinished_tasks == 0:
            self._finished.set()

    async def join(self) -> None:
        """Block until all items in the queue have been gotten and processed."""
        if self._unfinished_tasks > 0:
            await self._finished.wait()

    def shutdown(self, immediate: bool = False) -> None:
        """Shut down the queue."""
        self._is_shutdown = True
        if immediate:
            while not self.empty():
                item = self._queue.popleft()
                if isinstance(item, dict):
                    _cancel_written(item)
                if self._unfinished_tasks > 0:
                    self._unfinished_tasks -= 1
            self._in_flight_targets.clear()
            if self._unfinished_tasks == 0:
                self._finished.set()
        self._wakeup_all_getters()
        while self._putters:
            putter = self._putters.popleft()
            if not putter.done():
                putter.set_result(None)


CommandPriorityQueue = FrameScheduler


class CommandWorkerPool:
    """Manages the pool of command workers, send queue, and session pacing."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        event_session_ready: asyncio.Event | None = None,
    ) -> None:
        """Initialize the command worker pool."""
        self.handler = handler
        self._terminate_sender: bool = False
        self._sender_stop = asyncio.Event()
        self.sending_workers: list[asyncio.Task[None]] = []

        queue_max_size = (
            self.gateway.profile.max_queue_size
            if hasattr(self.gateway, "profile") and self.gateway.profile
            else 250
        )
        self.send_buffer: FrameScheduler | asyncio.Queue[Any] = FrameScheduler(maxsize=queue_max_size)

        self._event_session_ready = event_session_ready if event_session_ready is not None else asyncio.Event()

    @property
    def _cover_in_flight(self) -> list[str]:
        """List of cover WHEREs currently in flight on a worker."""
        if isinstance(self.send_buffer, FrameScheduler):
            return [where for who, where in self.send_buffer.in_flight_targets if who == 2]
        return []

    @property
    def gateway(self) -> OWNGateway:
        """Return the underlying OWNGateway instance."""
        return self.handler.gateway

    @property
    def hass(self) -> HomeAssistant:
        """Return HomeAssistant instance."""
        return self.handler.hass

    @property
    def bus_monitor(self) -> BusMonitor:
        """Return the bus monitor instance."""
        return self.handler.bus_monitor

    @property
    def log_id(self) -> str:
        """Return the gateway log id."""
        return str(self.handler.log_id)

    @property
    def mac(self) -> str:
        """Return the gateway MAC."""
        return str(self.handler.mac)

    @property
    def sender_stop(self) -> asyncio.Event:
        """Return the sender stop event."""
        return self._sender_stop

    @property
    def command_session_idle_timeout(self) -> float:
        """Idle timeout before releasing the command session socket."""
        return float(self.handler.command_session_idle_timeout)

    async def send(self, message: OWNCommand, *, is_cancel: bool = False) -> asyncio.Future[float]:
        """Queue a command; the returned future resolves to the monotonic write time."""
        return await self._enqueue(message, is_status_request=False, is_cancel=is_cancel)

    async def send_fade_step(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue an intermediate software fade step; superseded by newer steps or commands."""
        return await self._enqueue(message, is_status_request=False, is_fade_step=True)

    async def send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a status request; the returned future resolves to the monotonic write time."""
        return await self._enqueue(message, is_status_request=True)

    async def _enqueue(
        self,
        message: OWNCommand,
        *,
        is_status_request: bool,
        is_fade_step: bool = False,
        is_cancel: bool = False,
    ) -> asyncio.Future[float]:
        """Put a frame on the send queue and hand back its delivery future."""
        written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        envelope: dict[str, Any] = {
            "message": message,
            "is_status_request": is_status_request,
            "is_cancel": is_cancel,
            "written": written,
        }
        if is_fade_step:
            envelope["is_fade_step"] = True
        await self.send_buffer.put(envelope)
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )
        return written

    def _connect_refused(self, result: Any, worker_id: int) -> bool:
        """A command-session ``connect()`` result the worker must not retry on."""
        if isinstance(result, dict) and not result.get("Success", True):
            if result.get("Message") in ("password_error", "password_required", "negotiation_refused", "connection_refused"):
                LOGGER.error(
                    "%s Command session authentication or connection refused (%s). "
                    "Terminating sending worker %s to prevent gateway lockout.",
                    self.log_id,
                    result.get("Message"),
                    worker_id,
                )
                return True
        return False

    async def sending_loop(self, worker_id: int) -> None:
        """Run a single sending worker consuming from send_buffer."""
        from . import gateway as gw_module

        command_session_cls = getattr(gw_module, "OWNCommandSession", OWNCommandSession)
        event_ready_timeout = float(getattr(gw_module, "EVENT_READY_TIMEOUT", EVENT_READY_TIMEOUT))
        session_is_open = getattr(gw_module, "_session_is_open", _session_is_open)
        dispatcher_send = getattr(gw_module, "async_dispatcher_send", async_dispatcher_send)

        LOGGER.debug("%s Creating sending worker %s", self.log_id, worker_id)
        LOGGER.debug("%s Worker %s waiting for event session to be ready...", self.log_id, worker_id)

        while not self._terminate_sender and not self._event_session_ready.is_set():
            try:
                async with asyncio.timeout(event_ready_timeout):
                    await self._event_session_ready.wait()
            except TimeoutError:
                LOGGER.warning(
                    "%s Worker %s: event session was not ready after %ss; "
                    "continuing to wait without consuming queued commands.",
                    self.log_id,
                    worker_id,
                    event_ready_timeout,
                )

        if self._terminate_sender:
            return

        LOGGER.debug(
            "%s Worker %s: event session is ready, proceeding with command session.",
            self.log_id,
            worker_id,
        )

        _command_session = command_session_cls(gateway=self.gateway, logger=LOGGER)
        try:
            try:
                res = await _command_session.connect()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "%s Worker %s: initial command session connection raised; "
                    "queued commands will retry on send.",
                    self.log_id,
                    worker_id,
                )
                res = None

            if self._connect_refused(res, worker_id):
                return

            while not self._terminate_sender:
                idle_timeout = self.command_session_idle_timeout
                acquire_coro = (
                    self.send_buffer.acquire_next()
                    if isinstance(self.send_buffer, FrameScheduler)
                    else self.send_buffer.get()
                )
                try:
                    task = await asyncio.wait_for(
                        acquire_coro,
                        timeout=idle_timeout,
                    )
                except TimeoutError:
                    if session_is_open(_command_session):
                        LOGGER.debug(
                            "%s Command session idle for %ss; closing socket to release gateway resource.",
                            self.log_id,
                            idle_timeout,
                        )
                        await _command_session.close()
                    continue

                try:
                    if task is None:
                        break

                    LOGGER.debug(
                        "%s Message `%s` was successfully unqueued by worker %s.",
                        self.log_id,
                        task["message"],
                        worker_id,
                    )
                    task_start = time.time()
                    self.bus_monitor.record_frame(
                        direction="tx",
                        raw=str(task["message"]),
                        parsed=(
                            task["message"]
                            if isinstance(task["message"], OWNMessage)
                            else None
                        ),
                    )
                    # Reopening a closed session (e.g. after COMMAND_SESSION_IDLE_TIMEOUT
                    # or a prior failed send) calls `connect()` directly, preserving
                    # OWNd's full MAX_CONNECT_ATTEMPTS=5 (~13.3 s) retry loop when
                    # single-slot gateways like MH201 momentarily close TCP during
                    # negotiation (OpenWebNet-HA/OWNd#132). Only a mid-flight socket
                    # drop inside `send()` uses OWNd's single-attempt fast reconnect;
                    # if `send()` returns None, `_cancel_written(task)` aborts without
                    # re-queuing so non-idempotent commands whose ACK was lost are
                    # never replayed.
                    if not session_is_open(_command_session):
                        res = await _command_session.connect()
                        if self._connect_refused(res, worker_id):
                            _cancel_written(task)
                            return
                        if not session_is_open(_command_session):
                            LOGGER.warning(
                                "%s Command session unavailable; message `%s` not sent.",
                                self.log_id,
                                task["message"],
                            )
                            _cancel_written(task)
                            continue
                    written_at = time.monotonic()
                    collected = await _command_session.send(
                        message=task["message"],
                        is_status_request=task["is_status_request"],
                    )
                    if collected is None:
                        _cancel_written(task)
                    else:
                        _resolve_written(task, written_at)
                        self.handler._record_tx(written_at, task["message"])
                    if collected and isinstance(collected, list):
                        for resp in collected:
                            raw_resp = str(resp)
                            if self.bus_monitor.has_frame_since(
                                task_start, direction="rx", raw=raw_resp
                            ):
                                continue
                            frame = self.bus_monitor.record_frame(
                                direction="rx",
                                raw=raw_resp,
                                parsed=resp if isinstance(resp, OWNMessage) else None,
                            )
                            if not getattr(frame, "is_duplicate", False) and isinstance(resp, OWNMessage):
                                dispatcher_send(
                                    self.hass, f"myhome_message_{self.mac}", resp
                                )
                                # A reply to a request sent for an offline primary
                                self.handler._bridge_to_primary(resp)
                except asyncio.CancelledError:
                    _cancel_written(task)
                    raise
                except Exception:
                    _cancel_written(task)
                    LOGGER.exception(
                        "%s Worker %s: unexpected error while sending `%s`; "
                        "delivery is unconfirmed.",
                        self.log_id,
                        worker_id,
                        task.get("message") if isinstance(task, dict) else task,
                    )
                finally:
                    if isinstance(self.send_buffer, FrameScheduler):
                        self.send_buffer.complete_task(task)
                    else:
                        self.send_buffer.task_done()

                if (
                    hasattr(self.gateway, "profile")
                    and self.gateway.profile.command_queue_delay > 0
                ):
                    await asyncio.sleep(self.gateway.profile.command_queue_delay)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.shield(_command_session.close())
            LOGGER.debug("%s Destroying sending worker %s", self.log_id, worker_id)

    def close(self) -> None:
        """Cancel queued frames and unblock workers."""
        self._terminate_sender = True
        self._sender_stop.set()

        while True:
            try:
                task = self.send_buffer.get_nowait()
            except asyncio.QueueEmpty:
                break
            if task is not None:
                _cancel_written(task)
            self.send_buffer.task_done()

        if isinstance(self.send_buffer, FrameScheduler):
            self.send_buffer._in_flight_targets.clear()

        for _ in range(max(1, len(self.sending_workers))):
            try:
                self.send_buffer.put_nowait(None)
            except (asyncio.QueueFull, Exception):
                pass
