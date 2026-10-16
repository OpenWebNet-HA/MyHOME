"""Trace Corpus vs Firmware Oracle Validation Suite.

Empirically validates `own-firmware-oracle` performance against the entire
real-world trace and plant diagnostic corpus across `MyHOME` and
`OpenWebNet-Encyclopedia`:
1. Tier 1 — Canonical `tx` Frame Coverage: verifies that all deterministic
   real-world `tx` command/query patterns captured across 105+ trace/plant
   JSON fixtures map to canonical stimuli in `tests/golden/firmware_oracle.json`.
2. Tier 2 — Gateway-Internal Response Parity: verifies that self-contained
   gateway queries (WHO 13 device model/firmware version, WHO 1013 MAC/interface,
   WHO 5 alarm status, WHO 9 auxiliary status, WHO 1 WHAT 19 fault, WHO 1001
   diagnostic mask) emit matching `emitted_own` frames in the emulated oracle.
3. Tier 3 — Real-World Hardware Acceptance vs Emulated Gateway Verdicts:
   verifies that commands and queries accepted by physical gateways in our
   field traces (`MH200N`, `MH200`, `MyHomeServer1`, `F454`, `H4684`/`H4890`)
   are accepted/forwarded by the corresponding emulated gateway in the oracle
   (including Area 0 divergence between `MH200 020102` and `MH200N 010108`).
4. Tier 4 — Full `rx` Bus Event Corpus Parser Resilience: verifies that all
   2,770+ unique `rx` frames captured across every trace/plant file parse
   cleanly in `OWNd` without unhandled exceptions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from OWNd.message import OWNMessage

REPO_ROOT = Path(__file__).resolve().parent.parent
ORACLE_JSON_PATH = REPO_ROOT / "tests" / "golden" / "firmware_oracle.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
ENCYCLOPEDIA_EVIDENCE_DIR = REPO_ROOT.parent / "OpenWebNet-Encyclopedia" / "evidence"

# Host-clock-dependent WHO 13 queries intentionally excluded from deterministic
# oracle TSVs because bt_device emits the live host wall clock / date or calls
# settimeofday() inside the unprivileged bwrap sandbox.
CLOCK_DEPENDENT_TX_FRAMES: frozenset[str] = frozenset({
    "*#13**0##",
    "*#13**1##",
    "*#13**22##",
    "*#13**#22*12*21*24*001*04*13*04*2026##",
    "*#13**#22*17*33*54*002*05*11*09*2026##",
})


def _load_oracle() -> dict[str, Any]:
    if not ORACLE_JSON_PATH.is_file():
        pytest.skip("tests/golden/firmware_oracle.json fixture not found")
    return json.loads(ORACLE_JSON_PATH.read_text(encoding="utf-8"))


ORACLE_DATA = _load_oracle()
ALL_VERDICTS: dict[str, list[dict[str, Any]]] = ORACLE_DATA.get("verdicts", {})


def canonicalize_trace_tx_frame(frame: str) -> str:
    """Map plant-specific WHERE addresses in a trace `tx` frame to canonical oracle templates."""
    if frame in ALL_VERDICTS:
        return frame

    # WHO 1 / WHO 2 status request: *#(1|2)*<where>##
    m = re.match(r"^\*#(1|2)\*([^#*]+(?:#[^*]+)?)\#\#$", frame)
    if m:
        who, where = m.group(1), m.group(2)
        if "#4#" in where:
            return "*#1*0002#4#01##" if who == "1" else "*#2*11#4#02##"
        if len(where) == 4 and where.startswith("00"):
            return "*#1*0001##" if who == "1" else "*#2*31##"
        if len(where) == 2 and where.startswith("0") and where != "00":
            return "*#1*02##" if who == "1" else "*#2*31##"
        if where == "0":
            return f"*#{who}*0##"
        return "*#1*31##" if who == "1" else "*#2*31##"

    # WHO 1 / WHO 2 command: *(1|2)*<what>*<where>##
    m = re.match(r"^\*(1|2)\*([^*]+)\*([^#*]+(?:#[^*]+)?)\#\#$", frame)
    if m:
        who, what, where = m.group(1), m.group(2), m.group(3)
        if who == "1":
            if "#" in what:
                return "*1*0#1*31##"
            if "#4#" in where:
                return "*1*1*2#4#01##" if what == "1" else "*1*0*25#4#02##"
            if len(where) == 4 and where.startswith("00"):
                return f"*1*{what}*0001##"
            if len(where) == 2 and where.startswith("0") and where != "00":
                return f"*1*{what}*02##"
            if where == "0":
                return f"*1*{what}*0##"
            if what in ("0", "1"):
                return f"*1*{what}*31##"
            return "*1*3*31##"
        # WHO 2
        if "#" in what:
            return "*2*11#1#001*31##" if what.startswith("11#") else "*2*12#1#001*31##"
        if "#4#" in where:
            return "*2*1*11#4#02##"
        if where == "0":
            return "*2*0*31##"
        return f"*2*{what}*31##"

    # WHO 1 / WHO 2 dimension request/write: *#(1|2)*<where>*<rest>##
    m = re.match(r"^\*#(1|2)\*([^*]+)\*(.+)\#\#$", frame)
    if m:
        who, where, rest = m.group(1), m.group(2), m.group(3)
        if who == "1":
            if "#4#" in where:
                if rest.startswith("#1*"):
                    return "*#1*25#4#02*#1*101*0##"
                if rest.startswith("#12*"):
                    return "*#1*25#4#02*#12*240*87*80##"
                if rest.startswith("#14*"):
                    return "*#1*25#4#02*#14*153##"
                return f"*#1*31*{rest}##"
            if rest == "1":
                return "*#1*31*1##"
            if rest == "4":
                return "*#1*31*4##"
            if rest.startswith("#1*"):
                return "*#1*31*#1*100*0##"
            if rest.startswith("#4*"):
                return "*#1*31*#4*130*0##"
        else:
            if rest == "10":
                return "*#2*11#4#02*10##" if "#4#" in where else "*#2*31*10##"
            if rest.startswith("#11#"):
                return "*#2*31*#11#001*40##"

    # WHO 4 status request: *#4*<where>##
    m = re.match(r"^\*#4\*([^*]+)\#\#$", frame)
    if m:
        where = m.group(1)
        if where.startswith("#"):
            return "*#4*#0##"
        return "*#4*1##"

    # WHO 4 dimension request/write: *#4*<where>*<rest>##
    m = re.match(r"^\*#4\*([^*]+)\*(.+)\#\#$", frame)
    if m:
        where, rest = m.group(1), m.group(2)
        if rest in ("0", "11", "12", "13", "19", "20"):
            return f"*#4*1*{rest}##"
        if rest == "14":
            return "*#4*#0*14##" if where == "#0" else "*#4*1*14##"
        if rest.startswith("#14*"):
            return "*#4*#0*#14*0210*1##" if where.startswith("#") else "*#4*1*#14*0200*1##"
        if rest.startswith("#11*"):
            return "*#4*#1*#11*1##" if where.startswith("#") else "*#4*1*#11*1##"

    # WHO 4 command: *4*<what>*<where>##
    m = re.match(r"^\*4\*([^*]+)\*([^*]+)\#\#$", frame)
    if m:
        what, where = m.group(1), m.group(2)
        if where.startswith("#"):
            return f"*4*{what}*#0##" if f"*4*{what}*#0##" in ALL_VERDICTS else "*4*1101*#0##"
        return f"*4*{what}*1##" if f"*4*{what}*1##" in ALL_VERDICTS else "*4*1*1##"

    # WHO 5 status request: *#5*#<z>##
    if re.match(r"^\*#5\*#\d+\#\#$", frame):
        return "*#5*#1##"

    # WHO 9 status request: *#9*<where>##
    if re.match(r"^\*#9\*\d+\#\#$", frame):
        return "*#9*1##"

    # WHO 13 echoed model response logged as tx in one sound probe trace
    if re.match(r"^\*#13\*\*15\*\d+\#\#$", frame):
        return "*#13**15##"

    # WHO 14 command: *14*<what>*<where>##
    m = re.match(r"^\*14\*([^*]+)\*([^*]+)\#\#$", frame)
    if m:
        return f"*14*{m.group(1)}*32##"

    # WHO 16 status request: *#16*<where>##
    m = re.match(r"^\*#16\*(\d+)\#\#$", frame)
    if m:
        return "*#16*101##" if m.group(1).startswith("10") else "*#16*0##"

    # WHO 16 command/query
    m = re.match(r"^\*16\*([^*]+)\*([^*]+)\#\#$", frame)
    if m:
        what, where = m.group(1), m.group(2)
        if what.startswith("100"):
            return "*16*1002*21##"
        if what.startswith("600"):
            return "*16*6002*101##"
        if what.startswith("610"):
            return "*16*6102*101##"
        if what in ("100", "101", "102"):
            return f"*16*{what}*101##"
        if where.startswith("10"):
            return f"*16*{what}*101##"
        return f"*16*{what}*21##"

    m = re.match(r"^\*#16\*([^*]+)\*(.+)\#\#$", frame)
    if m:
        where, rest = m.group(1), m.group(2)
        if rest.startswith("#6*0*"):
            return "*#16*101*#6*0*96200##"
        if where.startswith("10"):
            return f"*#16*101*{rest}##"
        if where.startswith("2"):
            return f"*#16*21*{rest}##"

    # WHO 17 status request or command
    m = re.match(r"^\*#17\*(\d+)\#\#$", frame)
    if m:
        return "*#17*0##" if m.group(1) == "0" else "*#17*1##"
    m = re.match(r"^\*17\*([^*]+)\*([^*]+)\#\#$", frame)
    if m:
        return f"*17*{m.group(1)}*1##"

    # WHO 18 status/dimension request
    m = re.match(r"^\*#18\*([^*]+)\*(.+)\#\#$", frame)
    if m:
        where, rest = m.group(1), m.group(2)
        if rest.startswith("#1200#1*"):
            return "*#18*51*#1200#1*125##"
        if rest == "1200":
            return "*#18*51*1200##"
        if where.startswith("7"):
            return f"*#18*71#0*{rest}##"
        return f"*#18*51*{rest}##"

    m = re.match(r"^\*18\*([^*]+)\*([^*]+)\#\#$", frame)
    if m:
        what, where = m.group(1), m.group(2)
        if where.startswith("7"):
            return f"*18*{what}*71#0##"
        return f"*18*{what}*51##"

    # WHO 25 query/event
    if re.match(r"^\*#25\*\d+\#\#$", frame):
        return "*#25*21##"
    if re.match(r"^\*25\*1\d#0\*11\#\#$", frame):
        return "*25*11#0*11##"

    # WHO 1013 diagnostic object model query: *#1013**1## -> *#1013*0*1##
    if frame == "*#1013**1##":
        return "*#1013*0*1##"

    return frame


def _iter_all_trace_records() -> list[tuple[str, str | None, str, str]]:
    """Return (source_id, gateway_model, direction, raw_frame) across all JSON/JSONL traces."""
    records: list[tuple[str, str | None, str, str]] = []

    for json_path in sorted(FIXTURES_DIR.rglob("*.json")):
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        gw_obj = payload.get("gateway")
        gw_model = gw_obj.get("model") if isinstance(gw_obj, dict) else None
        raw_frames = payload.get("frames")
        if not isinstance(raw_frames, list):
            data_obj = payload.get("data")
            if isinstance(data_obj, dict):
                prof = data_obj.get("gateway_profile")
                if isinstance(prof, dict) and not gw_model:
                    gw_model = prof.get("model_name")
                bm = data_obj.get("bus_monitor")
                if isinstance(bm, dict):
                    raw_frames = bm.get("recent_frames")
        if not isinstance(raw_frames, list):
            continue

        for entry in raw_frames:
            if isinstance(entry, dict):
                direction = entry.get("direction")
                raw = entry.get("raw")
                if raw and direction in ("tx", "rx"):
                    records.append((json_path.stem, gw_model, str(direction), str(raw)))

    if ENCYCLOPEDIA_EVIDENCE_DIR.is_dir():
        for sub in sorted(ENCYCLOPEDIA_EVIDENCE_DIR.iterdir()):
            manifest_path = sub / "manifest.json"
            frames_path = sub / "frames.jsonl"
            if not manifest_path.is_file() or not frames_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            ev_id = str(manifest.get("evidence_id", sub.name))
            gw_model = manifest.get("environment", {}).get("gateway_model")
            for raw_line in frames_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                rec = json.loads(raw_line)
                direction = rec.get("dir")
                raw = rec.get("raw")
                if raw and direction in ("tx", "rx"):
                    records.append((ev_id, gw_model, str(direction), str(raw)))

    return records


ALL_TRACE_RECORDS = _iter_all_trace_records()


def test_tier1_trace_tx_canonical_oracle_coverage():
    """Verify 100% of deterministic `tx` frames across all traces map to oracle verdicts."""
    tx_frames = {
        raw
        for _src, _gw, direction, raw in ALL_TRACE_RECORDS
        if direction == "tx" and raw not in ("*#*1##", "*#*0##")
    }
    assert len(tx_frames) >= 435, f"Expected >= 435 unique tx frames, found {len(tx_frames)}"

    deterministic_tx = tx_frames - CLOCK_DEPENDENT_TX_FRAMES
    unmapped: list[tuple[str, str]] = []

    for frame in sorted(deterministic_tx):
        canonical = canonicalize_trace_tx_frame(frame)
        if canonical not in ALL_VERDICTS:
            unmapped.append((frame, canonical))

    assert not unmapped, (
        f"{len(unmapped)} trace tx frames did not map to any oracle verdict:\n"
        + "\n".join(f"  {raw} -> {can}" for raw, can in unmapped)
    )


@pytest.mark.parametrize(
    ("query_frame", "product", "expected_emitted_prefix"),
    [
        # WHO 13 Device Model ID (*#13**15##) across all 11 emulated gateways
        ("*#13**15##", "MH200", "*#13**15*4##"),
        ("*#13**15##", "H4684", "*#13**15*13##"),
        ("*#13**15##", "MyHomeServer1", "*#13**15*200##"),
        ("*#13**15##", "F454", "*#13**15*200##"),
        ("*#13**15##", "F459", "*#13**15*200##"),
        ("*#13**15##", "MH202", "*#13**15*200##"),
        ("*#13**15##", "F461", "*#13**15*200##"),
        ("*#13**15##", "F460", "*#13**15*200##"),
        ("*#13**15##", "F450", "*#13**15*200##"),
        ("*#13**15##", "MH200N", "*#13**15*0##"),
        ("*#13**15##", "F453AV", "*#13**15*0##"),
        # WHO 13 Firmware Version (*#13**16##) — exact compiled version across all 11 gateways
        ("*#13**16##", "MH200N", "*#13**16*1*1*8##"),
        ("*#13**16##", "MH200", "*#13**16*2*1*2##"),
        ("*#13**16##", "F454", "*#13**16*2*0*51##"),
        ("*#13**16##", "F453AV", "*#13**16*3*0*14##"),
        ("*#13**16##", "H4684", "*#13**16*2*0*54##"),
        ("*#13**16##", "MyHomeServer1", "*#13**16*2*82*6##"),
        ("*#13**16##", "F459", "*#13**16*2*1*5##"),
        ("*#13**16##", "MH202", "*#13**16*1*0*24##"),
        ("*#13**16##", "F461", "*#13**16*2*0*11##"),
        ("*#13**16##", "F460", "*#13**16*2*0*12##"),
        ("*#13**16##", "F450", "*#13**16*2*0*10##"),
        # WHO 13 Kernel & Distribution Version (*#13**23##, *#13**24##)
        ("*#13**23##", "H4684", "*#13**23*2*0*5##"),
        ("*#13**23##", "MH200", "*#13**23*0*0*9##"),
        ("*#13**23##", "F454", "*#13**23*2*6*32##"),
        ("*#13**23##", "MyHomeServer1", "*#13**23*5*10*35##"),
        ("*#13**23##", "F461", "*#13**23*4*14*78##"),
        ("*#13**24##", "H4684", "*#13**24*3*0*0##"),
        ("*#13**24##", "MH200", "*#13**24*3*0*5##"),
        ("*#13**24##", "MH200N", "*#13**24*3*0*1##"),
        ("*#13**24##", "F453AV", "*#13**24*3*0*1##"),
        ("*#13**24##", "MyHomeServer1", "*#13**24*2*23*10##"),
        # WHO 1013 Diagnostic Object Model (*#1013*0*1##) on H4684 & MH200
        ("*#1013*0*1##", "H4684", "*#1013**1*19##"),
        ("*#1013*0*1##", "MH200", "*#1013**1*4##"),
        # WHO 5 Burglar Alarm status on MH200N & MH200 (bt_alarm phantom 8-zone response)
        ("*#5*0##", "MH200N", "*5*1*##"),
        ("*#5*0##", "MH200", "*5*1*##"),
        # WHO 9 Auxiliary status on MH200N & MH200 (bt_alarm)
        ("*#9*0##", "MH200N", "*9*0*0##"),
        ("*#9*0##", "MH200", "*9*0*0##"),
        # WHO 1 WHAT 19 Lighting fault event & WHO 1001 diagnostic mask on MH200N (*#1*74##)
        ("*#1*74##", "MH200N", "*1*19*74##"),
        ("*#1*74##", "MH200N", "*#1001*74*11*111110111111111111110111##"),
    ],
)
def test_tier2_gateway_internal_response_parity(
    query_frame: str, product: str, expected_emitted_prefix: str
):
    """Verify gateway-internal queries emit byte-identical OpenWebNet frames in the oracle."""
    assert query_frame in ALL_VERDICTS, f"Missing {query_frame} in oracle index"
    entries = [e for e in ALL_VERDICTS[query_frame] if e["product"] == product]
    assert len(entries) > 0, f"No {product} entry for {query_frame}"

    emitted_all = [own for e in entries for own in e.get("emitted_own", [])]
    assert any(own.startswith(expected_emitted_prefix) for own in emitted_all), (
        f"Expected {product} on {query_frame} to emit frame starting with "
        f"{expected_emitted_prefix}, got {emitted_all}"
    )


@pytest.mark.parametrize("product", ["MH200N", "MH200"])
def test_tier2_mh200n_and_mh200_phantom_alarm_8_zones_matches_issue_466_trace(product: str):
    """Verify emulated MH200N and MH200 reproduce the exact 12-frame phantom alarm burst from issue #466."""
    entries = [e for e in ALL_VERDICTS["*#5*0##"] if e["product"] == product]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["reply"] == "ack"
    assert entry["verdict"] == "out"

    expected_phantom_frames = [
        "*5*1*##",
        "*5*9*##",
        "*5*10*##",
        "*5*6*##",
        "*5*11*#1##",
        "*5*11*#2##",
        "*5*11*#3##",
        "*5*11*#4##",
        "*5*11*#5##",
        "*5*11*#6##",
        "*5*11*#7##",
        "*5*11*#8##",
    ]
    assert entry["emitted_own"] == expected_phantom_frames


def test_tier3_mh200_vs_mh200n_area0_hardware_trace_reproduction():
    """Verify emulated MH200 (020102) vs MH200N (010108) & F454 (020051) matches issue #700 trace.

    In `tests/fixtures/traces/issue_700_area0_mh200_f454.json`, physical MH200 2.1.2
    rejected 2-digit Area 0 addressing (`*1*1*02##`, `*1*0*02##`, `*#1*02##`) with NACK
    and transmitted nothing on the bus, while accepting 1-digit (`*1*1*2##`), whereas
    MH200N 1.1.8 and F454 2.0.51 translated and transmitted both onto the SCS bus.
    """
    for two_digit_cmd in ("*1*1*02##", "*1*0*02##"):
        by_prod = {e["product"]: e for e in ALL_VERDICTS[two_digit_cmd]}
        # Legacy MH200 020102 rejects 2-digit Area 0 commands with NACK/silent
        assert by_prod["MH200"]["reply"] == "nack"
        assert by_prod["MH200"]["verdict"] == "silent"
        # MH200N 010108 and F454 020051 ACK and forward to SCS bus
        assert by_prod["MH200N"]["reply"] == "ack"
        assert by_prod["MH200N"]["verdict"] == "out"
        assert by_prod["F454"]["reply"] == "ack"
        assert by_prod["F454"]["verdict"] == "out"

    # For status request *#1*02##, MH200 rejects immediately (silent), whereas
    # MH200N and F454 translate and put `b3001200010000` onto the SCS bus (out).
    query_by_prod = {e["product"]: e for e in ALL_VERDICTS["*#1*02##"]}
    assert query_by_prod["MH200"]["verdict"] == "silent"
    assert query_by_prod["MH200N"]["verdict"] == "out"
    assert query_by_prod["F454"]["verdict"] == "out"

    # 1-digit Area 0 (*1*1*2##) is accepted and forwarded by all three gateways
    one_digit_by_prod = {e["product"]: e for e in ALL_VERDICTS["*1*1*2##"]}
    for prod in ("MH200", "MH200N", "F454"):
        assert one_digit_by_prod[prod]["reply"] == "ack"
        assert one_digit_by_prod[prod]["verdict"] == "out"


@pytest.mark.parametrize(
    ("frame", "product", "expected_reply", "expected_verdict"),
    [
        # WHO 16 Sound Diffusion commands/queries that succeeded on physical MH200N/MH200 in traces
        ("*#16*0*5##", "MH200N", "ack", "out"),
        ("*#16*101*5##", "MH200N", "ack", "out"),
        ("*#16*101*6##", "MH200N", "ack", "out"),
        ("*#16*101*#6*0*96200##", "MH200N", "ack", "out"),
        ("*#16*21*1##", "MH200N", "ack", "out"),
        ("*16*3*101##", "MH200N", "ack", "out"),
        ("*16*13*101##", "MH200N", "ack", "out"),
        ("*16*3*21##", "MH200N", "ack", "out"),
        ("*16*13*21##", "MH200N", "ack", "out"),
        # WHO 16 WHAT=0 / WHAT=10 on source 101 rejected by both physical MH200/MH200N and emulated bt_difson
        ("*16*0*101##", "MH200N", "nack", "silent"),
        ("*16*10*101##", "MH200N", "nack", "silent"),
        ("*16*0*21##", "MH200N", "nack", "silent"),
        ("*16*0*21##", "MH200", "nack", "silent"),
        # WHO 5 / WHO 9 Alarm & Aux queries/commands on MH200N & MH200
        ("*#5*0##", "MH200N", "ack", "out"),
        ("*#5*#1##", "MH200N", "ack", "out"),
        ("*#9*0##", "MH200N", "ack", "out"),
        ("*9*0*1##", "MH200N", "ack", "out"),
        ("*9*1*1##", "MH200N", "ack", "out"),
        # WHO 22 Multimedia sound diffusion: supported on MH200N/F454 (bt_multi), absent on legacy MH200
        ("*22*3#1*3#1#1##", "MH200N", "ack", "out"),
        ("*22*3#1*3#1#1##", "F454", "ack", "out"),
        ("*22*3#1*3#1#1##", "MH200", "nack", "silent"),
        # WHO 2 Automation step commands on F454
        ("*2*11#1#001*31##", "F454", "ack", "out"),
        ("*2*12#1#001*31##", "F454", "ack", "out"),
    ],
)
def test_tier3_physical_trace_commands_accepted_by_emulated_gateways(
    frame: str, product: str, expected_reply: str, expected_verdict: str
):
    """Verify commands/probes from physical hardware traces match emulated gateway verdicts."""
    assert frame in ALL_VERDICTS, f"Missing {frame} in oracle index"
    entries = [e for e in ALL_VERDICTS[frame] if e["product"] == product]
    assert len(entries) > 0
    for entry in entries:
        assert entry["reply"] == expected_reply, f"Expected {expected_reply} for {frame} on {product}, got {entry}"
        assert entry["verdict"] == expected_verdict, f"Expected {expected_verdict} for {frame} on {product}, got {entry}"


def test_tier4_all_trace_rx_frames_parse_cleanly_in_ownd():
    """Verify all 2,770+ unique `rx` frames across all 105+ trace files parse without exceptions in OWNd."""
    rx_frames = {
        raw
        for _src, _gw, direction, raw in ALL_TRACE_RECORDS
        if direction == "rx"
    }
    assert len(rx_frames) >= 2700, f"Expected >= 2700 unique rx frames, found {len(rx_frames)}"

    # Known malformed synthetic frame in f500_tuner fixture (missing leading '#' on WHO 22 dim 6);
    # OWNMessage.parse must return None cleanly without raising.
    known_unparseable_rx = {"*22*2#1*6*2##"}

    failures: list[str] = []
    for frame in sorted(rx_frames):
        try:
            parsed = OWNMessage.parse(frame)
            if frame in known_unparseable_rx:
                if parsed is not None:
                    failures.append(f"Expected None for malformed frame {frame}, got {parsed!r}")
            elif parsed is None:
                failures.append(f"OWNMessage.parse returned None for trace rx frame: {frame}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"Exception parsing trace rx frame {frame}: {type(exc).__name__}: {exc}")

    assert not failures, (
        f"OWNd failed to parse {len(failures)} trace rx frames:\n" + "\n".join(failures[:50])
    )
