"""Serial/USB implementation of OpenWebNet transport for Legrand 3578 USB/ZigBee interface.

Based on openwebnet4j by Massimo Valla (@mvalla, OpenHAB OpenWebNet code owner):
- Default 19200 baud, 8N1, no flow control (Issue #233)
- In-band single-pipe multiplexing:
    - Frames starting with '*' (and not '*#') are unsolicited broadcast events,
      dispatched immediately to registered listeners.
    - Frames starting with '*#' are query responses or signaling (ACK/NACK),
      routed to resolve the active command request.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from OWNd.message import OWNMessage, OWNSignaling

from .base import OWNTransport

_LOGGER = logging.getLogger(__name__)

SEPARATOR = b"##"
DEFAULT_BAUDRATE = 19200


class AsyncSerialTransport(OWNTransport):
    """Single-pipe serial transport with multiplexed event and command handling."""

    def __init__(
        self,
        port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        log_id: Optional[str] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(log_id=log_id or f"[Legrand 3578 USB - {port}]")
        self.port = port
        self.baudrate = baudrate
        self._logger = logger or _LOGGER

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._terminate = False

        # Command synchronization on single pipe
        self._command_lock = asyncio.Lock()
        self._pending_future: Optional[asyncio.Future[Any]] = None
        self._pending_collected: list[Any] = []

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def transport_type(self) -> str:
        return "serial"

    async def connect(self) -> bool:
        """Open serial port connection and launch background reader task."""
        self._terminate = False
        try:
            # When serial-asyncio is available, open serial connection
            # If simulated stream reader/writer are already injected (e.g. for testing), use them
            if self._reader is None or self._writer is None:
                try:
                    import serial_asyncio  # type: ignore

                    self._reader, self._writer = await serial_asyncio.open_serial_connection(
                        url=self.port, baudrate=self.baudrate
                    )
                except (ImportError, Exception) as ex:
                    self._logger.error(
                        "%s Could not open serial connection on %s: %s",
                        self.log_id,
                        self.port,
                        ex,
                    )
                    return False

            self._is_connected = True
            self._reader_task = asyncio.create_task(self._read_loop())
            self._logger.info("%s Serial transport connected on %s @ %d baud", self.log_id, self.port, self.baudrate)
            return True
        except Exception as ex:  # pylint: disable=broad-except
            self._logger.exception("%s Connection error on serial port %s: %s", self.log_id, self.port, ex)
            return False

    async def _read_loop(self) -> None:
        """Continuously read frames separated by '##' and demultiplex them."""
        while not self._terminate and self._reader:
            try:
                raw_bytes = await self._reader.readuntil(SEPARATOR)
                frame_str = raw_bytes.decode(errors="replace").strip()
                if not frame_str or frame_str == "##":
                    continue

                self._process_inbound_frame(frame_str)
            except asyncio.CancelledError:
                break
            except Exception as ex:  # pylint: disable=broad-except
                if self._terminate:
                    break
                self._logger.warning("%s Serial read error: %s", self.log_id, ex)
                await asyncio.sleep(0.5)

    def _process_inbound_frame(self, frame_str: str) -> None:
        """Demultiplex an incoming OpenWebNet frame on the single serial pipe.

        Credit: Massimo Valla (@mvalla / openwebnet4j OpenWebNetSerialPipe)
        - Frames starting with '*' (and not '*#') are unsolicited broadcast events.
        - Frames starting with '*#' are query responses or signaling (ACK/NACK).
        """
        parsed = OWNMessage.parse(frame_str)

        # Unsolicited event on serial: starts with '*' but not '*#'
        is_unsolicited = frame_str.startswith("*") and not frame_str.startswith("*#")

        if is_unsolicited:
            # Dispatch directly to bus listeners
            self.notify_listeners(parsed if parsed else frame_str)
            return

        # Query response or ACK/NACK (*#...##): route to pending command future if present
        if self._pending_future is not None and not self._pending_future.done():
            is_ack = False
            is_nack = False

            if isinstance(parsed, OWNSignaling):
                is_ack = parsed.is_ack()
                is_nack = parsed.is_nack()
            elif frame_str == "*#*1##":
                is_ack = True
            elif frame_str == "*#*0##":
                is_nack = True

            if is_ack:
                res = self._pending_collected if self._pending_collected else True
                self._pending_future.set_result(res)
            elif is_nack:
                self._pending_future.set_result(None)
            else:
                # Intermediate dimension / status response frame
                self._pending_collected.append(parsed if parsed else frame_str)
        else:
            # No command pending; dispatch to bus listeners
            self.notify_listeners(parsed if parsed else frame_str)

    async def send(self, message: str, is_status_request: bool = False, timeout: float = 5.0) -> Any:
        """Send command on serial pipe with locked request/response synchronization."""
        if not self._is_connected or not self._writer:
            raise RuntimeError(f"{self.log_id} Serial transport is not connected.")

        async with self._command_lock:
            loop = asyncio.get_running_loop()
            self._pending_future = loop.create_future()
            self._pending_collected = []

            try:
                frame_bytes = str(message).encode()
                if not frame_bytes.endswith(SEPARATOR):
                    frame_bytes += SEPARATOR

                self._writer.write(frame_bytes)
                await self._writer.drain()

                result = await asyncio.wait_for(self._pending_future, timeout=timeout)
                return result
            except asyncio.TimeoutError:
                self._logger.warning(
                    "%s Timed out waiting for response to command `%s` on serial pipe.",
                    self.log_id,
                    message,
                )
                return None
            finally:
                self._pending_future = None
                self._pending_collected = []

    async def disconnect(self) -> None:
        """Disconnect serial port and terminate background reader."""
        self._terminate = True
        self._is_connected = False

        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        self._reader = None
