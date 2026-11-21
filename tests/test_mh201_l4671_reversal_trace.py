"""Trace provenance, protocol, and timing verification for MH201 + L4671/2 reversal (#959).

Fixture contributed by @ermanno00:
  tests/fixtures/traces/mh201_l4671_reversal_2026-11-18/myhome_trace_MH201_where41_reversal_2026-11-18.json

Verifies:
1. Exact SHA-256 digest, privacy scrubbing (host/port null, findings == []), and
   BTicino MH201 firmware 3.3.6 metadata (WHO=13 code 200, WHO=1013 10.15.0.5,
   MH201Profile 0.1 s queue pacing, 1 worker) + L4671/2 timed shutter metadata.
2. Full 10-frame (6 rx, 4 tx) OWNd parsing coverage via OWNMessage, OWNEvent,
   OWNCommand, OWNAutomationEvent, and OWNAutomationCommand, plus microsecond
   agreement between float timestamps and ISO-8601 UTC timestamps.
3. Hardware timing & interlock semantics on WHERE 41 (Refs #959):
   - frames[0..2]: prior UP run, *#2*41## poll while moving, and stop.
   - frames[3..4]: *2*2*41## starts DOWN in 63.02 ms with no pre-move *2*0*41##.
   - frames[5..6]: *2*1*41## sent 5.09 s into the 15 s close is answered in
     55.26 ms (inside ECHO_WINDOW = 1.5 s) by *2*0*41## (STOP), with no
     subsequent *2*1*41## over the next 3.95 s.
   - frames[7..8]: a second *2*1*41## starts UP in 67.21 ms.
   - frames[8..9]: the L4671/2 internal ~60 s relay cutoff emits *2*0*41##
     61.38 s after motor start (matching _actuator_cutoff == 60.0), 46.38 s
     after the 15 s curtain reached its mechanical limit switch.
4. Firmware Oracle cross-check (tests/golden/firmware_oracle.json): MH201 030644
   is catalogued with 0 verdicts, MH202 010024 emits a single direction SCS
   frame without a 120A pre-STOP, whereas 10 emulated Linux gateways in
   firmware_oracle.json emit 120A before 1208/1209.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from OWNd.message import (
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNCommand,
    OWNEvent,
    OWNMessage,
)
from OWNd.profiles import MH201Profile, get_gateway_profile

from custom_components.myhome.cover import (
    _actuator_cutoff,
    _is_standard_timed_shutter_model,
)
from custom_components.myhome.cover_motion import ECHO_WINDOW
from scripts.anonymize_plant_fixture import findings

FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "mh201_l4671_reversal_2026-11-18"
)
TRACE_PATH = FIXTURE_DIR / "myhome_trace_MH201_where41_reversal_2026-11-18.json"
README_PATH = FIXTURE_DIR / "README.md"
ORACLE_PATH = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"

TRACE_SHA256 = "8a6dfcaa82e36011a47ab867f1d35c9548ede5a02642e8c1a49de4f163821c98"

STOP = "*2*0*41##"
UP = "*2*1*41##"
DOWN = "*2*2*41##"
STATUS_REQ = "*#2*41##"


def _load_trace() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(TRACE_PATH.read_text(encoding="utf-8")))


def test_trace_provenance_sha256_and_privacy() -> None:
    """Verify exact SHA-256 pin, non-synthetic provenance, and zero privacy leaks."""
    assert TRACE_PATH.is_file(), f"Missing trace fixture: {TRACE_PATH}"
    assert README_PATH.is_file(), f"Missing companion README: {README_PATH}"

    raw_text = TRACE_PATH.read_text(encoding="utf-8")
    assert hashlib.sha256(raw_text.encode("utf-8")).hexdigest() == TRACE_SHA256
    assert findings(raw_text, data_file=True) == []

    readme_text = README_PATH.read_text(encoding="utf-8")
    assert findings(readme_text, data_file=False) == []
    assert TRACE_SHA256 in readme_text
    assert "#959" in readme_text

    doc = _load_trace()
    assert doc["synthetic"] is False
    assert doc["capture"]["kind"] == "trace"
    assert doc["capture"]["filters"] == {
        "who": "2",
        "where": "41",
        "direction": None,
    }
    window = doc["capture"]["window"]
    assert window["frames"] == 10
    assert window["truncated"] is False
    assert doc["environment"] == {
        "integration_version": "2.0.0",
        "ownd_version": "2.0.1",
    }


def test_mh201_gateway_and_l4671_actuator_metadata() -> None:
    """Verify MH201 3.3.6 WHO=13 / WHO=1013 (10.15.0.5) and L4671/2 metadata."""
    doc = _load_trace()
    gw = doc["gateway"]
    assert gw["model"] == "MH201"
    assert gw["manufacturer"] == "BTicino S.p.A."
    assert gw["firmware"] == "3.3.6"
    assert gw["host"] is None
    assert gw["port"] is None
    assert gw["mac_prefix"] == "00:03:50"
    assert gw["queue_pacing"] == 0.1
    assert gw["worker_count"] == 1

    ident = gw["identification"]
    assert ident["model"] == "MH201"
    assert ident["source"] == "ssdp"
    assert ident["ssdp_model"] == "MH201"
    assert ident["who13_code"] == "200"
    assert ident["who13_firmware"] == "3.3.6"
    assert ident["who1013_code"] == "10"
    assert ident["who1013_model"] == "MH201"
    assert ident["who1013_n_conf"] == "15"
    assert ident["who1013_brand"] == "0"
    assert ident["who1013_line"] == "5"
    assert ident["profile"] == "MH201Profile"
    assert ident["conflict"] is None

    profile = get_gateway_profile("MH201", "3.3.6")
    assert isinstance(profile, MH201Profile)
    assert profile.command_queue_delay == 0.10

    actuator = doc["actuator"]
    assert actuator == {
        "model": "L4671/2",
        "where": "41",
        "strategy": "timed",
        "travel_time": 15,
    }
    assert _is_standard_timed_shutter_model(actuator["model"]) is True


def test_all_ten_frames_parse_and_timestamps_align() -> None:
    """Verify OWNd parser coverage and BusFrame timestamp integrity across all 10 frames."""
    doc = _load_trace()
    frames = doc["frames"]
    assert len(frames) == 10
    assert sum(1 for f in frames if f["direction"] == "rx") == 6
    assert sum(1 for f in frames if f["direction"] == "tx") == 4

    assert doc["capture"]["window"]["first"] == frames[0]["iso_time"]
    assert doc["capture"]["window"]["last"] == frames[-1]["iso_time"]

    prev_ts: float | None = None
    for idx, entry in enumerate(frames):
        assert entry["who"] == "2"
        assert entry["where"] == "41"
        assert entry["dimension"] is None
        assert entry["is_ack"] is False
        assert entry["is_nack"] is False
        assert entry["is_duplicate"] is False

        ts = float(entry["timestamp"])
        iso_ts = datetime.fromisoformat(entry["iso_time"]).timestamp()
        assert abs(ts - iso_ts) < 1e-6, f"Frame {idx} timestamp/iso_time drift"
        if prev_ts is not None:
            assert ts > prev_ts, f"Frame {idx} not strictly monotonic"
        prev_ts = ts

        parsed = OWNMessage.parse(entry["raw"])
        assert parsed is not None
        assert parsed.is_valid is True

        if entry["direction"] == "rx":
            evt = OWNEvent.parse(entry["raw"])
            assert isinstance(evt, OWNAutomationEvent)
            assert evt.where == "41"
            assert evt.is_translation is False
            assert evt.current_position is None
            what = entry["what"]
            assert evt.is_opening is (what == "1")
            assert evt.is_closing is (what == "2")
        else:
            cmd = OWNCommand.parse(entry["raw"])
            assert isinstance(cmd, OWNAutomationCommand)
            assert cmd.where == "41"

    assert str(OWNAutomationCommand.status("41")) == STATUS_REQ
    assert str(OWNAutomationCommand.stop_shutter("41")) == STOP
    assert str(OWNAutomationCommand.raise_shutter("41")) == UP
    assert str(OWNAutomationCommand.lower_shutter("41")) == DOWN


def test_reversal_sequence_latencies_and_61s_relay_cutoff() -> None:
    """Verify frames[0..2] prior run and frames[3..9] reversal + 61.38 s relay cutoff."""
    doc = _load_trace()
    frames = doc["frames"]
    travel_time = float(doc["actuator"]["travel_time"])
    assert travel_time == 15.0

    # Earlier buffered run (frames[0..2]): UP start, *#2*41## status poll, and STOP.
    assert [(f["direction"], f["raw"]) for f in frames[:3]] == [
        ("rx", UP),
        ("tx", STATUS_REQ),
        ("rx", STOP),
    ]
    assert 11.1 < frames[1]["timestamp"] - frames[0]["timestamp"] < 11.2
    assert 28.6 < frames[2]["timestamp"] - frames[0]["timestamp"] < 28.7

    # Reversal reproduction (frames[3..9]) after 105.63 s idle gap.
    assert frames[3]["timestamp"] - frames[2]["timestamp"] > 105.0
    repro = frames[3:]
    assert [(f["direction"], f["raw"]) for f in repro] == [
        ("tx", DOWN),  # [3] 21:08:56.008613 down
        ("rx", DOWN),  # [4] 21:08:56.071633 moving down (+63.02 ms; no pre-move STOP)
        ("tx", UP),    # [5] 21:09:01.096229 up while closing (+5.09 s into 15 s run)
        ("rx", STOP),  # [6] 21:09:01.151487 actuator STOPPED (+55.26 ms < ECHO_WINDOW)
        ("tx", UP),    # [7] 21:09:05.104210 second up (+3.95 s later)
        ("rx", UP),    # [8] 21:09:05.171416 moving up (+67.21 ms; no pre-move STOP)
        ("rx", STOP),  # [9] 21:10:06.550313 actuator ~60 s relay cutoff (+61.38 s)
    ]

    t = [float(f["timestamp"]) for f in repro]
    down_rtt = t[1] - t[0]
    stop_on_reverse_rtt = t[3] - t[2]
    second_up_rtt = t[5] - t[4]
    reversal_into_close = t[2] - t[0]
    quiet_after_stop = t[4] - t[3]
    relay_cutoff_elapsed = t[6] - t[5]

    assert 0.060 < down_rtt < 0.070
    assert 5.0 < reversal_into_close < 5.2
    assert 0.050 < stop_on_reverse_rtt < 0.060
    assert stop_on_reverse_rtt < ECHO_WINDOW
    assert quiet_after_stop > ECHO_WINDOW
    assert 3.9 < quiet_after_stop < 4.0
    assert 0.060 < second_up_rtt < 0.075

    # Internal M=0 (~60 s) relay hold outlasts the 15 s physical travel by ~46.38 s.
    assert 61.3 < relay_cutoff_elapsed < 61.5
    assert relay_cutoff_elapsed - travel_time > 46.0
    assert _actuator_cutoff(relay_cutoff_elapsed) == 60.0


def test_firmware_oracle_pre_move_stop_gateway_split() -> None:
    """Pin the firmware_oracle.json split behind Issue #959 (MH201/MH202 vs pre-STOP gateways)."""
    oracle = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    gateways = {g["product"]: g for g in oracle["gateways"]}

    # Bare-metal MH201 (STM32F217 / CMX-RTX) is catalogued with 0 emulated verdicts.
    assert gateways["MH201"]["status"] == "catalogued"
    assert gateways["MH201"]["suites"] == []

    # trace-corpus *2*1*31## / *2*2*31## verdicts across emulated gateways:
    for frame, dir_scs in (("*2*1*31##", "1208"), ("*2*2*31##", "1209")):
        by_prod = {entry["product"]: entry for entry in oracle["verdicts"][frame]}

        # MH202 (010024) emits ONLY the direction SCS telegram (no 120A pre-STOP).
        mh202_bus = [
            bytes.fromhex(bf).decode("ascii").strip()
            for bf in by_prod["MH202"]["bus_frames"]
        ]
        assert len(mh202_bus) == 1
        assert mh202_bus[0].endswith(dir_scs)
        assert by_prod["MH202"]["emitted_own"] == [
            "*2*1000#1*31##" if dir_scs == "1208" else "*2*1000#2*31##"
        ]

        # All 10 other WHO=2-capable Linux gateways in firmware_oracle.json emit
        # a preceding 120A STOP telegram before 1208/1209.
        pre_stop_gateways = (
            "F453",
            "F453AV",
            "F454",
            "F459",
            "F460",
            "F461",
            "H4684",
            "MH200",
            "MH200N",
            "MyHomeServer1",
        )
        for prod in pre_stop_gateways:
            bus_frames = [
                bytes.fromhex(bf).decode("ascii").strip()
                for bf in by_prod[prod]["bus_frames"]
            ]
            assert len(bus_frames) == 2, prod
            assert bus_frames[0].endswith("120A"), prod
            assert bus_frames[1].endswith(dir_scs), prod
