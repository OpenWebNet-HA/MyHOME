"""Gateway session runners and command worker pool for MyHOME.

Manages the persistent event session background loop (watchdog heartbeat,
stall detection, exponential reconnect backoff) and the command session
worker pool (command pacing, send queue, idle disconnect, delivery futures).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway
from OWNd.message import OWNCommand, OWNMessage

from .const import LOGGER

if TYPE_CHECKING:
    from .bus_monitor import BusMonitor
    from .gateway import MyHOMEGatewayHandler

EVENT_READY_TIMEOUT: float = 120.0
COMMAND_SESSION_IDLE_TIMEOUT: float = 15.0
EVENT_STALL_TIMEOUT: float = 600.0
EVENT_RESTART_BACKOFF_MIN: float = 5.0
EVENT_RESTART_BACKOFF_MAX: float = 60.0


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
        _event_session = event_session_cls(
            gateway=self.gateway,
            logger=LOGGER,
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
            self._event_watchdog.reschedule(None)
            self._event_watchdog = None


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
        self.send_buffer: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_max_size)

        self._event_session_ready = event_session_ready if event_session_ready is not None else asyncio.Event()

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

    async def send(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a command; the returned future resolves to the monotonic write time."""
        return await self._enqueue(message, is_status_request=False)

    async def send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a status request; the returned future resolves to the monotonic write time."""
        return await self._enqueue(message, is_status_request=True)

    async def _enqueue(self, message: OWNCommand, *, is_status_request: bool) -> asyncio.Future[float]:
        """Put a frame on the send queue and hand back its delivery future."""
        written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        await self.send_buffer.put(
            {"message": message, "is_status_request": is_status_request, "written": written}
        )
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
                try:
                    task = await asyncio.wait_for(
                        self.send_buffer.get(),
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

        for _ in range(max(1, len(self.sending_workers))):
            try:
                self.send_buffer.put_nowait(None)
            except (asyncio.QueueFull, Exception):
                pass
