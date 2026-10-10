"""In-band OpenWebNet Bus Monitor.

Maintains a bounded circular ring buffer of recent bus transactions (RX and TX)
without opening additional gateway sockets. Provides real-time event subscription
for diagnostics and Lovelace dashboard streaming.
"""
from __future__ import annotations

import collections
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from OWNd.message import OWNMessage, OWNSignaling

_LOGGER = logging.getLogger(__name__)

DEFAULT_RING_BUFFER_SIZE = 500


class BusFrame:
    """Represents a single captured bus transaction."""

    __slots__ = (
        "timestamp",
        "iso_time",
        "direction",
        "raw",
        "who",
        "where",
        "what",
        "dimension",
        "is_ack",
        "is_nack",
        "is_duplicate",
    )

    def __init__(
        self,
        direction: str,
        raw: str,
        parsed: Optional[OWNMessage] = None,
        timestamp: Optional[float] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.timestamp = timestamp if timestamp is not None else now.timestamp()
        self.iso_time = now.isoformat()
        self.direction = direction.lower()
        self.raw = str(raw).strip()
        self.is_duplicate = False

        # Extract semantics if parsed message is available
        self.who = getattr(parsed, "who", getattr(parsed, "_who", None)) if parsed else None
        self.where = getattr(parsed, "where", getattr(parsed, "_where", None)) if parsed else None
        self.what = getattr(parsed, "what", getattr(parsed, "_what", None)) if parsed else None
        self.dimension = getattr(parsed, "dimension", getattr(parsed, "_dimension", None)) if parsed else None

        if isinstance(parsed, OWNSignaling):
            self.is_ack = parsed.is_ack()
            self.is_nack = parsed.is_nack()
        elif self.raw in ("*#*1##", "*#*1"):
            self.is_ack = True
            self.is_nack = False
        elif self.raw in ("*#*0##", "*#*0"):
            self.is_ack = False
            self.is_nack = True
        else:
            self.is_ack = False
            self.is_nack = False

    def to_dict(self) -> dict[str, Any]:
        """Convert frame into a JSON-serializable dictionary."""
        return {
            "timestamp": self.timestamp,
            "iso_time": self.iso_time,
            "direction": self.direction,
            "raw": self.raw,
            "who": str(self.who) if self.who is not None else None,
            "where": str(self.where) if self.where is not None else None,
            "what": str(self.what) if self.what is not None else None,
            "dimension": str(self.dimension) if self.dimension is not None else None,
            "is_ack": self.is_ack,
            "is_nack": self.is_nack,
            "is_duplicate": self.is_duplicate,
        }


class BusMonitor:
    """Non-blocking circular buffer tap for OpenWebNet traffic."""

    def __init__(self, maxlen: int = DEFAULT_RING_BUFFER_SIZE, dedup_window: float = 0.2) -> None:
        self._maxlen = maxlen
        self._dedup_window = dedup_window
        self._frames: collections.deque[BusFrame] = collections.deque(maxlen=maxlen)
        self._recent_signatures: collections.deque[tuple[float, str, str]] = collections.deque(maxlen=maxlen)
        self._subscribers: set[Callable[[BusFrame], Any]] = set()
        self._total_rx = 0
        self._total_tx = 0

    @property
    def maxlen(self) -> int:
        return self._maxlen

    @property
    def total_rx(self) -> int:
        return self._total_rx

    @property
    def total_tx(self) -> int:
        return self._total_tx

    def has_frame_since(self, since: float, direction: str, raw: str) -> bool:
        """Check if an identical frame was already recorded since a given timestamp."""
        dir_lower = direction.lower()
        raw_str = str(raw).strip()
        for frame in reversed(self._frames):
            if frame.timestamp < since - 0.1:
                break
            if frame.direction == dir_lower and frame.raw == raw_str:
                return True
        return False

    def record_frame(
        self,
        direction: str,
        raw: str,
        parsed: Optional[OWNMessage] = None,
    ) -> BusFrame:
        """Record a frame into the circular buffer and notify subscribers."""
        frame = BusFrame(direction=direction, raw=raw, parsed=parsed)

        # Sliding-window duplicate suppression (e.g. concurrent command & event session echo)
        if self._dedup_window > 0:
            for prev_ts, prev_dir, prev_raw in reversed(self._recent_signatures):
                if (frame.timestamp - prev_ts) > self._dedup_window:
                    break
                if prev_dir == frame.direction and prev_raw == frame.raw:
                    frame.is_duplicate = True
                    return frame

        self._recent_signatures.append((frame.timestamp, frame.direction, frame.raw))
        self._frames.append(frame)

        if frame.direction == "rx":
            self._total_rx += 1
        else:
            self._total_tx += 1

        for subscriber in list(self._subscribers):
            try:
                subscriber(frame)
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.warning("Error notifying bus monitor subscriber: %s", ex)

        return frame

    def subscribe(self, callback: Callable[[BusFrame], Any]) -> Callable[[], None]:
        """Subscribe a listener to live frames. Returns an unsubscribe callable."""
        self._subscribers.add(callback)

        def unsubscribe() -> None:
            self._subscribers.discard(callback)

        return unsubscribe

    def get_recent_frames(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent frames up to limit as dictionaries."""
        limit = min(limit, len(self._frames))
        recent = list(self._frames)[-limit:]
        return [f.to_dict() for f in recent]

    def clear(self) -> None:
        """Clear all captured frames."""
        self._frames.clear()
        self._recent_signatures.clear()
        self._total_rx = 0
        self._total_tx = 0

    def get_stats(self) -> dict[str, Any]:
        """Return buffer runtime statistics."""
        return {
            "capacity": self._maxlen,
            "captured": len(self._frames),
            "total_rx": self._total_rx,
            "total_tx": self._total_tx,
            "subscribers": len(self._subscribers),
        }
