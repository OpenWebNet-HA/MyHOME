"""Tests for #466: Real-World BTicino WHO 18 Dimension 1200 Energy Stream Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from physical
BTicino gateways (F454, MyHomeServer1) and BTicino F520 energy meters
can be deterministically parsed and validated against the protocol specification
and integration expectations.

Trace sources:
- tests/fixtures/traces/issue_466/myhome_trace_F454_who18_2026-10-08T19-30-16.json
  (Authentic customer capture from @xtimmy86x in PR #661 comment 6067730589, 6067865874)
- tests/fixtures/traces/issue_466/myhome_trace_MyHomeServer1_all_2026-10-01T07-59-11.json
  (Authentic production capture from @Interstellar0verdrive in issue #466 comment 5927373969)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OWNd.message import (
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNMessage,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_466"
CUSTOMER_CAPTURE_FILE = TRACES_DIR / "myhome_trace_F454_who18_2026-10-08T19-30-16.json"
MYHOMESERVER1_CAPTURE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_all_2026-10-01T07-59-11.json"


def test_f454_customer_trace_dimension1200_lifecycle() -> None:
    """Verify authentic customer trace from physical F454 and F520 energy meters."""
    assert CUSTOMER_CAPTURE_FILE.is_file(), f"Trace file missing: {CUSTOMER_CAPTURE_FILE}"
    trace: dict[str, Any] = json.loads(CUSTOMER_CAPTURE_FILE.read_text(encoding="utf-8"))

    # Assert capture metadata
    assert trace.get("capture", {}).get("window", {}).get("truncated") is False
    assert trace.get("gateway", {}).get("model") == "F454"
    assert trace.get("gateway", {}).get("firmware") == "2.0"

    frames = trace.get("frames", [])
    assert len(frames) == 66

    # All frames in this trace belong to WHO 18 (Energy Management)
    who18_frames = [f for f in frames if f.get("who") == "18"]
    assert len(who18_frames) == 66

    tx_frames = [f for f in who18_frames if f.get("direction") == "tx"]
    rx_frames = [f for f in who18_frames if f.get("direction") == "rx"]
    assert len(tx_frames) == 1
    assert len(rx_frames) == 65

    # 1. Frame 5: Auto-Update Stream Start Command on meter 56 (2 minutes)
    f5 = frames[5]
    assert f5["direction"] == "tx"
    assert f5["raw"] == "*#18*56*#1200#1*2##"
    cmd_start = OWNMessage.parse(f5["raw"])
    assert isinstance(cmd_start, OWNEnergyCommand)
    assert cmd_start.who == 18
    assert cmd_start.where == "56"
    assert getattr(cmd_start, "dimension", None) == 1200

    # 2. Frame 7: Energy meter confirms 2-minute active power stream active
    f7 = frames[7]
    assert f7["direction"] == "rx"
    assert f7["raw"] == "*#18*56*1200#1*2##"
    event_confirm = OWNMessage.parse(f7["raw"])
    assert isinstance(event_confirm, OWNEnergyEvent)
    assert event_confirm.who == 18
    assert event_confirm.where == "56"
    assert getattr(event_confirm, "dimension", None) == 1200

    # Confirmation arrived ~235 ms after start command
    delay = f7["timestamp"] - f5["timestamp"]
    assert 0.20 <= delay <= 0.30

    if getattr(event_confirm, "update_interval", None) is not None:
        assert event_confirm.update_interval == 2
        assert getattr(event_confirm, "energy_type", None) == 1
    else:
        dim_vals = getattr(
            event_confirm, "dimension_values", getattr(event_confirm, "_dimension_value", [])
        )
        assert dim_vals == ["2"]

    # 3. Active power telemetry frames (Dimension 113)
    dim113_rx = [f for f in who18_frames if f.get("direction") == "rx" and "*113*" in f["raw"]]
    assert len(dim113_rx) == 53

    # Check streamed meter 56 periodic telemetry (Frame 8)
    f8 = frames[8]
    assert f8["raw"] == "*#18*56*113*9##"
    event_p56 = OWNMessage.parse(f8["raw"])
    assert isinstance(event_p56, OWNEnergyEvent)
    assert event_p56.where == "56"
    assert event_p56.active_power == 9

    # All Dimension 113 frames parse cleanly with non-negative active power
    for f in dim113_rx:
        parsed = OWNMessage.parse(f["raw"])
        assert isinstance(parsed, OWNEnergyEvent)
        assert parsed.active_power is not None
        assert parsed.active_power >= 0

    # 4. Frame 57: Energy meter reports stream expired / stopped (interval 0)
    f57 = frames[57]
    assert f57["direction"] == "rx"
    assert f57["raw"] == "*#18*56*1200#1*0##"
    event_expired = OWNMessage.parse(f57["raw"])
    assert isinstance(event_expired, OWNEnergyEvent)
    assert event_expired.who == 18
    assert event_expired.where == "56"
    assert getattr(event_expired, "dimension", None) == 1200

    # Timer duration check: arrived ~126.18 seconds after start command (2 minutes)
    duration = f57["timestamp"] - f5["timestamp"]
    assert 120.0 <= duration <= 130.0

    if getattr(event_expired, "update_interval", None) is not None:
        assert event_expired.update_interval == 0
    else:
        dim_vals = getattr(
            event_expired, "dimension_values", getattr(event_expired, "_dimension_value", [])
        )
        assert dim_vals == ["0"]

    # 5. Subsequent interval frames (meter 56 frame 58 and meter 54 frame 30)
    f58 = frames[58]
    assert f58["raw"] == "*#18*56*1200#1*255##"
    ev58 = OWNMessage.parse(f58["raw"])
    assert isinstance(ev58, OWNEnergyEvent)
    assert ev58.where == "56"

    f30 = frames[30]
    assert f30["raw"] == "*#18*54*1200#1*255##"
    ev30 = OWNMessage.parse(f30["raw"])
    assert isinstance(ev30, OWNEnergyEvent)
    assert ev30.where == "54"

    # 6. Cumulative totalizer and partial consumption frames
    dim51_frames = [f for f in who18_frames if "*51*35098267##" in f["raw"]]
    assert len(dim51_frames) == 1
    ev_tot = OWNMessage.parse(dim51_frames[0]["raw"])
    assert isinstance(ev_tot, OWNEnergyEvent)
    assert ev_tot.where == "51"
    assert getattr(ev_tot, "dimension", None) == 51
    assert getattr(ev_tot, "total_consumption", None) == 35098267

    dim54_frames = [f for f in who18_frames if "*51*54*13165##" in f["raw"]]
    assert len(dim54_frames) == 1
    ev_partial = OWNMessage.parse(dim54_frames[0]["raw"])
    assert isinstance(ev_partial, OWNEnergyEvent)
    assert ev_partial.where == "51"
    assert getattr(ev_partial, "dimension", None) == 54
    assert getattr(ev_partial, "current_day_partial_consumption", None) == 13165


def test_myhomeserver1_production_capture_dimension1200_frames() -> None:
    """Verify authentic production trace contains valid WHO 18 Dimension 1200 and 113 frames."""
    assert MYHOMESERVER1_CAPTURE_FILE.is_file(), f"Trace file missing: {MYHOMESERVER1_CAPTURE_FILE}"
    trace: dict[str, Any] = json.loads(MYHOMESERVER1_CAPTURE_FILE.read_text(encoding="utf-8"))
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
        if getattr(parsed, "update_interval", None) is not None:
            assert parsed.update_interval == 125
            assert getattr(parsed, "energy_type", None) == 1
        else:
            dim_vals = getattr(
                parsed, "dimension_values", getattr(parsed, "_dimension_value", [])
            )
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


def test_command_factory_stream_lifecycle() -> None:
    """Verify that OWNEnergyCommand builder generates exact stream lifecycle frames."""
    # Start stream with duration 2 (as captured on meter 56)
    cmd_2 = OWNEnergyCommand.start_sending_instant_power("56", 2)
    assert str(cmd_2) == "*#18*56*#1200#1*2##"
    assert cmd_2.where == "56"

    # Start stream with duration 125
    cmd_125 = OWNEnergyCommand.start_sending_instant_power("51", 125)
    assert str(cmd_125) == "*#18*51*#1200#1*125##"
    assert cmd_125.where == "51"

    # Start stream with max duration 255
    cmd_255 = OWNEnergyCommand.start_sending_instant_power("51", 255)
    assert str(cmd_255) == "*#18*51*#1200#1*255##"

    # Stop stream with duration 0
    cmd_stop = OWNEnergyCommand.start_sending_instant_power("56", 0)
    assert str(cmd_stop) == "*#18*56*#1200#1*0##"
