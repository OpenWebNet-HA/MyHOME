"""Shared serial transport runtime and session adapters for USB OpenZigBee gateways."""
from __future__ import annotations

import asyncio
import collections
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from OWNd.exceptions import OWNError
from OWNd.message import OWNMessage
from OWNd.transport.serial import AsyncSerialTransport

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

__all__ = [
    "SerialCommandAdapter",
    "SerialEventAdapter",
    "SharedSerialRuntime",
]


class SharedSerialRuntime:
    """Owns the single ``AsyncSerialTransport`` shared by event and command adapters."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        port: str,
        baudrate: int,
        logger: logging.Logger,
    ) -> None:
        """Initialize the shared serial runtime for ``handler``."""
        self.handler = handler
        self.port = port
        self.baudrate = baudrate
        self._logger = logger
        self._lock = asyncio.Lock()
        self._refcount = 0
        self._preflight_held = False
        self._state_callbacks: set[Callable[[bool], None]] = set()
        self.unparsed_raw_frames: collections.deque[str] = collections.deque(maxlen=100)
        self.transport = AsyncSerialTransport(
            port=port,
            baudrate=baudrate,
            log_id=handler.log_id,
            logger=logger,
            gateway=handler.profile,
            on_state_change=self._fanout_state_change,
            raw_frame_callback=self._on_raw_frame,
            busy_retry=False,
        )

    def _fanout_state_change(self, connected: bool) -> None:
        for cb in tuple(self._state_callbacks):
            cb(connected)

    def _on_raw_frame(self, first: str, second: str | None = None) -> None:
        """Record every raw ``##`` token once in ``BusMonitor``."""
        direction, raw = ("rx", first) if second is None else (first, second)
        parsed = OWNMessage.parse(raw, gateway=self.handler.profile)
        if parsed is None:
            self.unparsed_raw_frames.append(raw)
        self.handler.bus_monitor.record_frame(direction=direction, raw=raw, parsed=parsed)

    async def acquire(self, *, adopt_preflight: bool = False) -> bool:
        """Acquire a reference to the shared serial transport, connecting if closed."""
        async with self._lock:
            if adopt_preflight and self._preflight_held:
                self._preflight_held = False
                if self.transport.is_connected:
                    return True
                self._refcount = 0
            if self.transport.is_connected:
                self._refcount += 1
                return True
            ok = await self.transport.connect()
            if ok:
                self._refcount += 1
            return ok

    async def release(self) -> None:
        """Release a transport reference and disconnect once no holder remains."""
        async with self._lock:
            if self._refcount > 0:
                self._refcount -= 1
            if (
                self._refcount == 0
                and not self._preflight_held
                and self.transport.is_connected
            ):
                await self.transport.disconnect()

    async def shutdown(self) -> None:
        """Drop all references and close the underlying serial transport."""
        async with self._lock:
            self._preflight_held = False
            self._refcount = 0
            await self.transport.disconnect()

    async def test_connection(self) -> dict[str, Any]:
        """Connect during setup preflight and keep the open port for ``SerialEventAdapter``."""
        async with self._lock:
            if self.transport.is_connected:
                return {"Success": True, "Message": "connected"}
            ok = await self.transport.connect()
            if not ok:
                reason = (
                    getattr(self.transport.last_error, "reason", None)
                    or "cannot_connect"
                )
                return {"Success": False, "Message": reason}
            self._refcount = 1
            self._preflight_held = True
            return {"Success": True, "Message": "connected"}

    def create_event_session(
        self, *, on_state_change: Callable[[bool], None] | None = None
    ) -> SerialEventAdapter:
        """Create an ``OWNEventSession``-compatible adapter over the shared runtime."""
        return SerialEventAdapter(self, on_state_change=on_state_change)

    def create_command_session(self) -> SerialCommandAdapter:
        """Create an ``OWNCommandSession``-compatible adapter over the shared runtime."""
        return SerialCommandAdapter(self)


class SerialEventAdapter:
    """``OWNEventSession``-compatible adapter fed by ``AsyncSerialTransport`` listeners."""

    def __init__(
        self,
        runtime: SharedSerialRuntime,
        *,
        on_state_change: Callable[[bool], None] | None = None,
    ) -> None:
        """Initialize the serial event session adapter."""
        self._runtime = runtime
        self._on_state_change = on_state_change
        self._queue: asyncio.Queue[OWNMessage | str | None] = asyncio.Queue()
        self._unsub: Callable[[], None] | None = None
        self._held = False
        self._closed = False

    @property
    def is_connected(self) -> bool:
        """Return True while the shared serial transport is connected."""
        return bool(self._runtime.transport.is_connected)

    @property
    def is_session_open(self) -> bool:
        """Return True while this adapter holds an open, active transport."""
        return (
            self._held
            and not self._closed
            and bool(self._runtime.transport.is_connected)
        )

    def _handle_state(self, connected: bool) -> None:
        if self._on_state_change is not None:
            self._on_state_change(connected)
        if not connected:
            self._queue.put_nowait(None)

    async def connect(self) -> dict[str, Any]:
        """Adopt preflight or open the shared serial transport and start receiving."""
        self._closed = False
        if self._on_state_change is not None:
            self._runtime._state_callbacks.add(self._handle_state)
        if self._unsub is None:
            self._unsub = self._runtime.transport.register_listener(
                self._queue.put_nowait
            )
        ok = await self._runtime.acquire(adopt_preflight=True)
        self._held = ok
        if ok:
            return {"Success": True, "Message": "connected"}
        reason = (
            getattr(self._runtime.transport.last_error, "reason", None)
            or "cannot_connect"
        )
        return {"Success": False, "Message": reason}

    async def get_next(self) -> OWNMessage | str | None:
        """Return the next inbound OpenWebNet message or ``None`` on disconnect."""
        if self._closed:
            return None
        if not self.is_connected and self._queue.empty():
            await asyncio.sleep(1.0)
            if not self._closed and not self._held and not self.is_connected:
                ok = await self._runtime.acquire()
                self._held = ok
                if ok and self._on_state_change is not None:
                    self._on_state_change(True)
            return None
        return await self._queue.get()

    async def close(self) -> None:
        """Unsubscribe from transport listeners and release the runtime reference."""
        self._closed = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._runtime._state_callbacks.discard(self._handle_state)
        self._queue.put_nowait(None)
        if self._held:
            self._held = False
            await self._runtime.release()


class SerialCommandAdapter:
    """``OWNCommandSession``-compatible adapter sharing ``SharedSerialRuntime``."""

    def __init__(self, runtime: SharedSerialRuntime) -> None:
        """Initialize the serial command session adapter."""
        self._runtime = runtime
        self._held = False

    @property
    def is_connected(self) -> bool:
        """Return True while the shared serial transport is connected."""
        return bool(self._runtime.transport.is_connected)

    @property
    def is_session_open(self) -> bool:
        """Return True while this adapter holds an open transport."""
        return self._held and bool(self._runtime.transport.is_connected)

    @property
    def last_error(self) -> OWNError | None:
        """Return the last transport error observed on the serial link."""
        return self._runtime.transport.last_error

    async def connect(self) -> dict[str, Any]:
        """Acquire a reference to the shared serial transport."""
        if self._held and self._runtime.transport.is_connected:
            return {"Success": True, "Message": "connected"}
        ok = await self._runtime.acquire()
        self._held = ok
        if ok:
            return {"Success": True, "Message": "connected"}
        reason = (
            getattr(self._runtime.transport.last_error, "reason", None)
            or "cannot_connect"
        )
        return {"Success": False, "Message": reason}

    async def send(
        self,
        message: str | OWNMessage,
        is_status_request: bool = False,
        *,
        timeout: float | None = None,
        raise_on_error: bool = False,
    ) -> list[OWNMessage | str] | bool | None:
        """Send an OpenWebNet command over the shared serial transport."""
        return await self._runtime.transport.send(
            message,
            is_status_request=is_status_request,
            timeout=timeout,
            raise_on_error=raise_on_error,
        )

    async def close(self) -> None:
        """Release the adapter's reference on the shared serial transport."""
        if self._held:
            self._held = False
            await self._runtime.release()
