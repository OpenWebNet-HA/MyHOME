"""Frame router: delivers bus frames to the entities that own their addresses.

One router per gateway (config entry). A platform subscribes each entity it
creates under the keys the entity answers to (its address, every spelling of
it, ``general`` for covers ...) and publishes every frame it receives under
the keys derived from the frame. A handler runs once per frame however many
of its keys match.

This replaces the ``myhome_update_{mac}_{who}_{key}`` dispatcher signals the
platforms and entities used to agree on by string: the WHO and the key are
now arguments, and only this module knows how they are combined.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import CALLBACK_TYPE, callback

from .const import LOGGER

FrameHandler = Callable[[Any], Any]


class FrameRouter:
    """Subscriptions of one gateway, keyed on ``(who, key)``."""

    def __init__(self) -> None:
        self._handlers: defaultdict[tuple[str, str], list[FrameHandler]] = defaultdict(list)

    @callback
    def subscribe(self, who: str, keys: Iterable[str], handler: FrameHandler) -> CALLBACK_TYPE:
        """Deliver frames of ``who`` published under any of ``keys`` to ``handler``.

        Returns the function that cancels the subscription.
        """
        pairs = [(str(who), key) for key in dict.fromkeys(keys) if key]
        for pair in pairs:
            self._handlers[pair].append(handler)

        @callback
        def unsubscribe() -> None:
            for pair in pairs:
                handlers = self._handlers.get(pair)
                if handlers and handler in handlers:
                    handlers.remove(handler)
                if not handlers:
                    self._handlers.pop(pair, None)

        return unsubscribe

    @callback
    def publish(self, who: str, keys: Iterable[str], message: Any) -> int:
        """Deliver ``message`` to every handler subscribed under ``who`` and one of ``keys``.

        Returns the number of handlers reached. A handler that raises is
        logged and skipped: one broken entity must not starve the others.
        """
        who = str(who)
        reached: list[FrameHandler] = []
        for key in dict.fromkeys(keys):
            for handler in list(self._handlers.get((who, key), ())):
                if handler not in reached:
                    reached.append(handler)
        for handler in reached:
            try:
                handler(message)
            except Exception:  # noqa: BLE001 - keep delivering to the other entities
                LOGGER.exception("Error handling frame %s in %s", message, handler)
        return len(reached)

    def subscribers(self, who: str, key: str) -> int:
        """How many handlers listen under ``(who, key)`` (diagnostics / tests)."""
        return len(self._handlers.get((str(who), key), ()))

    def __len__(self) -> int:
        return sum(len(h) for h in self._handlers.values())
