"""Tests for #466: Real-World BTicino WHO 18 Dimension 1200 Energy Stream Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MyHomeServer1 gateway and BTicino F520 energy meters (WHERE 51, 52, 53)
can be deterministically parsed and validated against the protocol specification
and integration expectations.

Trace sources:
- tests/fixtures/traces/issue_466/myhome_trace_MyHomeServer1_all_2026-10-01T07-59-11.json
  (Authentic production capture from @Interstellar0verdrive in issue #466 comment 5927373969)
- tests/fixtures/traces/issue_466/myhome_trace_energy_dimension1200_stream.json
  (Canonical start -> telemetry -> stop stream lifecycle capture)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OWNd.message import (
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNMessage,
    OWNSignaling,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
CALIBRATION_CAPTURE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_all_2026-10-01T07-59-11.json"
LIFECYCLE_TRACE_FILE = TRACES_DIR / "myhome_trace_energy_dimension1200_stream.json"


def test_myhomeserver1_production_capture_dimension1200_frames() -> None:
    """Verify authentic production trace contains valid WHO 18 Dimension 1200 and 113 frames."""
    assert CALIBRATION_CAPTURE_FILE.is_file(), f"Trace file missing: {CALIBRATION_CAPTURE_FILE}"
    trace: dict[str, Any] = json.loads(CALIBRATION_CAPTURE_FILE.read_text(encoding="utf-8"))
    frames = trace.get("frames", [])
    assert len(frames) == 159

    # Filter WHO 18 energy frames
    who18_frames = [f for f in frames if f.get("who") == "18"]
    assert len(who18_frames) == 95

    # 1. Incoming Dimension 1200 auto-update status replies (3 frames: meters 51, 52, 53)
    dim1200_rx = [f for f in who18_frames if f.get("direction") == "rx" and "1200" in f["raw"]]
    assert len(dim1200_rx) == 3
    expected_rx_frames = [
        "*#18*51*1200#1*125##",
        "*#18*52*1200#1*125##",
        "*#18*53*1200#1*125##",
    ]
    assert [f["raw"] for f in dim1200_rx] == expected_rx_frames

    for f in dim1200_rx:
        parsed = OWNMessage.parse(f["raw"])
        assert isinstance(parsed, OWNEnergyEvent), f"Expected OWNEnergyEvent for {f['raw']}"
        assert parsed.who == 18
        assert parsed.where in ("51", "52", "53")
        assert getattr(parsed, "dimension", None) == 1200
        # Check interval value in either OWNd representation
        if getattr(parsed, "update_interval", None) is not None:
            assert parsed.update_interval == 125
            assert getattr(parsed, "energy_type", None) == 1
        else:
            dim_vals = getattr(parsed, "dimension_values", getattr(parsed, "_dimension_value", []))
            assert dim_vals == ["125"]

    # 2. Incoming Dimension 113 active power telemetry (86 frames)
    dim113_rx = [f for f in who18_frames if f.get("direction") == "rx" and "*113*" in f["raw"]]
    assert len(dim113_rx) == 86
    for f in dim113_rx:
        parsed = OWNMessage.parse(f["raw"])
        assert isinstance(parsed, OWNEnergyEvent)
        assert parsed.active_power is not None
        assert parsed.active_power >= 0
        assert parsed.where in ("51", "52", "53")

    # 3. Outgoing Dimension 1200 status poll requests (6 frames: 2 rounds of 3 meters)
    dim1200_tx = [f for f in who18_frames if f.get("direction") == "tx" and "1200" in f["raw"]]
    assert len(dim1200_tx) == 6
    assert all(f["raw"] in ("*#18*51*1200##", "*#18*52*1200##", "*#18*53*1200##") for f in dim1200_tx)


def test_energy_dimension1200_lifecycle_trace_sequence() -> None:
    """Verify complete start -> telemetry -> stop stream lifecycle trace."""
    assert LIFECYCLE_TRACE_FILE.is_file(), f"Lifecycle trace file missing: {LIFECYCLE_TRACE_FILE}"
    trace: dict[str, Any] = json.loads(LIFECYCLE_TRACE_FILE.read_text(encoding="utf-8"))
    frames = trace.get("frames", [])
    assert len(frames) == 11

    # Frame 0: Start automatic instantaneous active power stream command (tx)
    f0 = frames[0]
    assert f0["direction"] == "tx"
    assert f0["raw"] == "*#18*51*#1200#1*125##"
    cmd_start = OWNMessage.parse(f0["raw"])
    assert isinstance(cmd_start, OWNEnergyCommand)
    assert cmd_start.who == 18
    assert cmd_start.where == "51"

    # Frame 1: Gateway ACK (rx)
    f1 = frames[1]
    assert f1["direction"] == "rx"
    assert f1["raw"] == "*#*1##"
    ack1 = OWNMessage.parse(f1["raw"])
    assert isinstance(ack1, OWNSignaling)
    assert getattr(ack1, "is_ack", True)

    # Frame 2: Meter confirms active auto-update stream interval 125 min (rx)
    f2 = frames[2]
    assert f2["direction"] == "rx"
    assert f2["raw"] == "*#18*51*1200#1*125##"
    event_interval = OWNMessage.parse(f2["raw"])
    assert isinstance(event_interval, OWNEnergyEvent)
    assert event_interval.where == "51"
    assert getattr(event_interval, "dimension", None) == 1200
    if getattr(event_interval, "update_interval", None) is not None:
        assert event_interval.update_interval == 125
        assert getattr(event_interval, "energy_type", None) == 1
    else:
        dim_vals = getattr(event_interval, "dimension_values", getattr(event_interval, "_dimension_value", []))
        assert dim_vals == ["125"]

    # Frames 3-5: Instantaneous active power telemetry (rx)
    expected_power = [377, 424, 500]
    for i, p_val in enumerate(expected_power, start=3):
        fi = frames[i]
        assert fi["direction"] == "rx"
        assert fi["raw"] == f"*#18*51*113*{p_val}##"
        event_p = OWNMessage.parse(fi["raw"])
        assert isinstance(event_p, OWNEnergyEvent)
        assert event_p.where == "51"
        assert event_p.active_power == p_val

    # Frame 6: Stop stream command (tx, duration 0)
    f6 = frames[6]
    assert f6["direction"] == "tx"
    assert f6["raw"] == "*#18*51*#1200#1*0##"
    cmd_stop = OWNMessage.parse(f6["raw"])
    assert isinstance(cmd_stop, OWNEnergyCommand)
    assert cmd_stop.where == "51"

    # Frame 7: Gateway ACK (rx)
    f7 = frames[7]
    assert f7["direction"] == "rx"
    assert f7["raw"] == "*#*1##"

    # Frame 8: Meter confirms stream stopped, interval 0 (rx)
    f8 = frames[8]
    assert f8["direction"] == "rx"
    assert f8["raw"] == "*#18*51*1200#1*0##"
    event_stop = OWNMessage.parse(f8["raw"])
    assert isinstance(event_stop, OWNEnergyEvent)
    assert event_stop.where == "51"
    assert getattr(event_stop, "dimension", None) == 1200
    if getattr(event_stop, "update_interval", None) is not None:
        assert event_stop.update_interval == 0
    else:
        dim_vals = getattr(event_stop, "dimension_values", getattr(event_stop, "_dimension_value", []))
        assert dim_vals == ["0"]

    # Frame 9: On-demand active power status query (tx, dimension 113)
    f9 = frames[9]
    assert f9["direction"] == "tx"
    assert f9["raw"] == "*#18*51*113##"
    cmd_query = OWNMessage.parse(f9["raw"])
    assert isinstance(cmd_query, OWNEnergyCommand)
    assert cmd_query.where == "51"

    # Frame 10: On-demand active power report (rx, 390 W)
    f10 = frames[10]
    assert f10["direction"] == "rx"
    assert f10["raw"] == "*#18*51*113*390##"
    event_query_resp = OWNMessage.parse(f10["raw"])
    assert isinstance(event_query_resp, OWNEnergyEvent)
    assert event_query_resp.active_power == 390


def test_command_factory_stream_lifecycle() -> None:
    """Verify that OWNEnergyCommand builder generates exact stream lifecycle frames."""
    # Start stream with duration 125
    cmd_125 = OWNEnergyCommand.start_sending_instant_power("51", 125)
    assert str(cmd_125) == "*#18*51*#1200#1*125##"
    assert cmd_125.where == "51"

    # Start stream with max duration 255
    cmd_255 = OWNEnergyCommand.start_sending_instant_power("51", 255)
    assert str(cmd_255) == "*#18*51*#1200#1*255##"

    # Duration clamping above 255
    cmd_clamp = OWNEnergyCommand.start_sending_instant_power("51", 300)
    assert str(cmd_clamp) == "*#18*51*#1200#1*255##"

    # Stop stream with duration 0
    cmd_stop = OWNEnergyCommand.start_sending_instant_power("51", 0)
    assert str(cmd_stop) == "*#18*51*#1200#1*0##"
