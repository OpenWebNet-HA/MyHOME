"""Tests for BusMonitor and BusFrame in-band bus tap."""
from OWNd.message import OWNEvent, OWNSignaling

from custom_components.myhome.bus_monitor import BusFrame, BusMonitor


def test_bus_frame_parsing():
    """Verify BusFrame extracts semantics from parsed messages and strings."""
    raw_event = "*1*1*12##"
    parsed = OWNEvent.parse(raw_event)
    frame = BusFrame(direction="rx", raw=raw_event, parsed=parsed)

    assert frame.direction == "rx"
    assert frame.raw == "*1*1*12##"
    assert frame.who == 1
    assert frame.where == "12"
    assert frame.what == 1
    assert frame.is_ack is False
    assert frame.is_nack is False

    d = frame.to_dict()
    assert d["direction"] == "rx"
    assert d["who"] == "1"
    assert d["where"] == "12"
    assert d["what"] == "1"
    assert "iso_time" in d
    assert "timestamp" in d


def test_bus_frame_signaling():
    """Verify BusFrame identifies ACK and NACK frames."""
    ack_frame = BusFrame(direction="rx", raw="*#*1##", parsed=OWNSignaling("*#*1##"))
    assert ack_frame.is_ack is True
    assert ack_frame.is_nack is False

    nack_frame = BusFrame(direction="rx", raw="*#*0##", parsed=OWNSignaling("*#*0##"))
    assert nack_frame.is_ack is False
    assert nack_frame.is_nack is True

    # Fallback when parsed is None
    raw_ack = BusFrame(direction="tx", raw="*#*1##")
    assert raw_ack.is_ack is True
    assert raw_ack.is_nack is False

    raw_nack = BusFrame(direction="rx", raw="*#*0##")
    assert raw_nack.is_ack is False
    assert raw_nack.is_nack is True


def test_bus_monitor_bounded_ring_buffer():
    """Verify BusMonitor caps frame storage at maxlen and maintains counters."""
    monitor = BusMonitor(maxlen=5)
    assert monitor.maxlen == 5

    for i in range(10):
        direction = "rx" if i % 2 == 0 else "tx"
        monitor.record_frame(direction=direction, raw=f"*1*{i}*12##")

    assert monitor.total_rx == 5
    assert monitor.total_tx == 5

    stats = monitor.get_stats()
    assert stats["capacity"] == 5
    assert stats["captured"] == 5
    assert stats["total_rx"] == 5
    assert stats["total_tx"] == 5

    recent = monitor.get_recent_frames(limit=3)
    assert len(recent) == 3
    # Check that the most recent frame is the last recorded (i=9)
    assert recent[-1]["raw"] == "*1*9*12##"

    monitor.clear()
    assert monitor.get_stats()["captured"] == 0
    assert monitor.get_stats()["total_rx"] == 0


def test_bus_monitor_subscription():
    """Verify subscribers receive live captured frames and can unsubscribe."""
    monitor = BusMonitor(maxlen=10)
    received = []

    def callback(frame: BusFrame):
        received.append(frame)

    unsub = monitor.subscribe(callback)
    assert monitor.get_stats()["subscribers"] == 1

    monitor.record_frame(direction="rx", raw="*1*1*12##")
    assert len(received) == 1
    assert received[0].raw == "*1*1*12##"

    # Unsubscribe
    unsub()
    assert monitor.get_stats()["subscribers"] == 0
    monitor.record_frame(direction="tx", raw="*1*0*12##")
    # No new frames received by callback
    assert len(received) == 1


def test_bus_monitor_subscriber_exception():
    """Verify subscriber exceptions are safely handled without breaking bus flow."""
    monitor = BusMonitor()

    def faulty_callback(frame: BusFrame):
        raise RuntimeError("Subscriber failed")

    monitor.subscribe(faulty_callback)
    frame = monitor.record_frame(direction="rx", raw="*1*1*12##")
    assert frame.raw == "*1*1*12##"


def test_bus_monitor_deduplication():
    """Verify immediate duplicate frames within dedup window are suppressed."""
    monitor = BusMonitor(maxlen=10, dedup_window=0.2)

    # First frame recorded normally
    f1 = monitor.record_frame(direction="rx", raw="*1*1*12##")
    assert f1.raw == "*1*1*12##"
    assert monitor.total_rx == 1
    assert len(monitor.get_recent_frames()) == 1

    # Immediate duplicate within dedup window (e.g. command session + event session echo)
    f2 = monitor.record_frame(direction="rx", raw="*1*1*12##")
    assert monitor.total_rx == 1  # Not incremented
    assert len(monitor.get_recent_frames()) == 1  # Not duplicated in ring buffer
    assert f2.raw == "*1*1*12##"
    assert f1.is_duplicate is False
    assert f2.is_duplicate is True
    assert f2.to_dict()["is_duplicate"] is True

    # Different frame recorded normally
    f3 = monitor.record_frame(direction="rx", raw="*1*0*19##")
    assert f3.raw == "*1*0*19##"
    assert monitor.total_rx == 2
    assert len(monitor.get_recent_frames()) == 2

    # Different direction for same raw frame is NOT suppressed
    f4 = monitor.record_frame(direction="tx", raw="*1*0*19##")
    assert f4.raw == "*1*0*19##"
    assert monitor.total_tx == 1
    assert len(monitor.get_recent_frames()) == 3

    # An entry beyond the dedup window breaks loop and allows re-recording
    monitor._recent_signatures.clear()
    monitor._recent_signatures.append((0.0, "rx", "*1*1*12##"))
    f5 = monitor.record_frame(direction="rx", raw="*1*1*12##")
    assert f5.raw == "*1*1*12##"
    assert monitor.total_rx == 3


def test_bus_monitor_has_frame_since():
    """Verify has_frame_since accurately detects recent frames."""
    import time
    monitor = BusMonitor(maxlen=50, dedup_window=0.0)

    t0 = time.time()
    monitor.record_frame(direction="rx", raw="*#2*1*0*0##")
    monitor.record_frame(direction="tx", raw="*#2*0##")

    assert monitor.has_frame_since(t0, direction="rx", raw="*#2*1*0*0##") is True
    assert monitor.has_frame_since(t0, direction="tx", raw="*#2*0##") is True
    assert monitor.has_frame_since(t0, direction="rx", raw="*#2*0##") is False
    assert monitor.has_frame_since(t0, direction="rx", raw="*#2*99*0*0##") is False
    # Timestamp in the future returns False
    assert monitor.has_frame_since(t0 + 100.0, direction="rx", raw="*#2*1*0*0##") is False





