"""TCP/IP implementation of OpenWebNet transport."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway

from .base import OWNTransport

_LOGGER = logging.getLogger(__name__)


class AsyncTcpTransport(OWNTransport):
    """Dual-session TCP transport for IP gateways (MH200N, F454, AM4890)."""

    def __init__(
        self,
        gateway: OWNGateway,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        super().__init__(log_id=gateway.log_id)
        self.gateway = gateway
        self._logger = logger or _LOGGER
        self._event_session: Optional[OWNEventSession] = None
        self._command_session: Optional[OWNCommandSession] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._is_connected = False
        self._terminate = False

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def transport_type(self) -> str:
        return "tcp"

    async def connect(self) -> bool:
        """Connect both Event and Command sessions."""
        self._terminate = False

        self._event_session = OWNEventSession(gateway=self.gateway, logger=self._logger)
        event_res = await self._event_session.connect()
        if isinstance(event_res, dict) and not event_res.get("Success", True):
            self._logger.error(
                "%s Failed to connect Event session: %s",
                self.log_id,
                event_res.get("Message"),
            )
            return False

        self._command_session = OWNCommandSession(
            gateway=self.gateway, logger=self._logger
        )
        cmd_res = await self._command_session.connect()
        if isinstance(cmd_res, dict) and not cmd_res.get("Success", True):
            self._logger.error(
                "%s Failed to connect Command session: %s",
                self.log_id,
                cmd_res.get("Message"),
            )
            await self._event_session.close()
            return False

        self._is_connected = True
        self._listener_task = asyncio.create_task(self._listen_loop())
        return True

    async def _listen_loop(self) -> None:
        """Background loop reading from event session and notifying listeners."""
        while not self._terminate and self._event_session:
            try:
                msg = await self._event_session.get_next()
                if msg is not None:
                    self.notify_listeners(msg)
            except asyncio.CancelledError:
                break
            except Exception as ex:  # pylint: disable=broad-except
                self._logger.exception("%s Exception in TCP listen loop: %s", self.log_id, ex)
                await asyncio.sleep(1)

    async def disconnect(self) -> None:
        """Close sessions and background tasks."""
        self._terminate = True
        self._is_connected = False

        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listener_task = None

        if self._event_session:
            await self._event_session.close()
            self._event_session = None

        if self._command_session:
            await self._command_session.close()
            self._command_session = None

    async def send(self, message: str, is_status_request: bool = False) -> Any:
        """Send command on command session and return response."""
        if not self._command_session:
            raise RuntimeError("Command session is not connected.")
        return await self._command_session.send(
            message=message, is_status_request=is_status_request
        )
