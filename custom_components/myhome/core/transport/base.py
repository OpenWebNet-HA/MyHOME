"""Base abstract class for OpenWebNet transports."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Set

_LOGGER = logging.getLogger(__name__)


class OWNTransport(ABC):
    """Abstract base class for all OpenWebNet transports (TCP, Serial/USB)."""

    def __init__(self, log_id: str = "[OpenWebNet Transport]") -> None:
        self.log_id = log_id
        self._listeners: Set[Callable[[Any], Any]] = set()

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if transport is currently connected and ready."""

    @property
    @abstractmethod
    def transport_type(self) -> str:
        """Return transport type descriptor (e.g. 'tcp' or 'serial')."""

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to the gateway hardware."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and clean up resources."""

    @abstractmethod
    async def send(self, message: str, is_status_request: bool = False) -> Any:
        """Send a command frame and await ACK / response."""

    def register_listener(self, callback: Callable[[Any], Any]) -> Callable[[], None]:
        """Register a callback for inbound bus events. Returns unsubscribe callable."""
        self._listeners.add(callback)

        def unsubscribe() -> None:
            self._listeners.discard(callback)

        return unsubscribe

    def notify_listeners(self, message: Any) -> None:
        """Broadcast an inbound message to all registered listeners."""
        for callback in list(self._listeners):
            try:
                callback(message)
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.warning("%s Listener notification error: %s", self.log_id, ex)
