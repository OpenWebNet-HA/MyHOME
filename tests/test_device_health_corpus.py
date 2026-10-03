"""Device faults against every frame we hold: real captures and the Encyclopedia corpus.

* Every capture under ``tests/fixtures/traces`` is replayed through the tracker.
  Only the captures of the MH200 actuator in a fault state (EVID-MH200-WHAT19-FAULT)
  may raise a fault; every other plant is healthy and must raise nothing.
* The golden corpus (``tests/golden``, vendored from the OpenWebNet Encyclopedia)
  holds documented frames only: none of them may raise a fault.
* The published WHO 1 WHAT table (WHO_1.pdf, as the Encyclopedia gives it:
  0..18, 20..29, 30/31, 1000) decides what is "unmapped"; OWNd's decoder is pinned
  to it here, value by value. The ZigBee variant spec adds three events (32 Toggle,
  34 movement, 39 end of movement); they are not faults. No capture holds any of them.

The healthy MyHomeServer1 interview in the cover-diagnostic captures answers a WHO
1001 DIMENSION 7 mask with zero bits (``*#1001*0*7*111111111111111101101111##``): a
zero bit is not a fault, which is why masks are evidence and never a trigger.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from OWNd.message import OWNEvent

from custom_components.myhome import device_health as dh
from custom_components.myhome.device_health import DeviceHealth

TESTS = Path(__file__).resolve().parent
TRACES = TESTS / "fixtures" / "traces"
GOLDEN = TESTS / "golden" / "corpus.json"
FRAME = re.compile(r"\*#?\d+\*[0-9*#]*##")
LIGHTING_FRAME = re.compile(r"\*#?(?:1|1001)\*")

MASK_74 = "*#1001*74*11*111110111111111111110111##"
# Captures of the faulty actuator: the faults each must raise (with the evidence the fault
# ends up carrying), and nothing else.
EXPECTED = {
    "myhome_trace_MH200_all_2026-09-26T21-00-00.json": {("74", "19", MASK_74)},
    "config_entry_mh200_sound_f441m.json": {("74", "19", MASK_74)},
}
PUBLISHED_WHO1_WHATS = [*range(0, 19), *range(20, 30), 30, 31]


def _tracker() -> DeviceHealth:
    # No hass: faults are tracked but not filed, which is all a replay needs.
    return DeviceHealth(SimpleNamespace(hass=None, config_entry=None, name="", log_id=""))


def _frames(path: Path) -> list[tuple[float | None, str]]:
    """Received frames of a capture in order, with their capture time when it has one."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        payload = json.loads(text)
        records = payload.get("frames") if isinstance(payload, dict) else None
        if isinstance(records, list) and records and isinstance(records[0], dict) and "raw" in records[0]:
            return [
                (r.get("timestamp"), r["raw"]) for r in records if r.get("direction", "rx") == "rx" and r.get("raw")
            ]
    return [(None, frame) for frame in FRAME.findall(text)]


def _replay(path: Path) -> tuple[set[tuple[str, str, str]], int]:
    """Every fault the capture raised with its last evidence, and how many WHO 1/1001 frames it fed."""
    health = _tracker()
    raised: dict[tuple[str, str], str] = {}
    fed = 0
    clock = 0.0
    for stamp, raw in _frames(path):
        clock = float(stamp) if stamp is not None else clock
        # Parse only what the tracker reads: other WHOs are not this test's business, and
        # their decoders (OWNd rejects a malformed WHO 13 frame) must not break the replay.
        if not LIGHTING_FRAME.match(raw):
            continue
        message = OWNEvent.parse(raw)
        if message is None or getattr(message, "who", None) not in (1, 1001):
            continue
        fed += 1
        with patch.object(dh.time, "monotonic", return_value=clock):
            health.observe(message)
        raised.update({(f["where"], f["code"]): f["evidence"] for f in health.faults})
    return {(where, code, evidence) for (where, code), evidence in raised.items()}, fed


CAPTURES = sorted(p for p in TRACES.rglob("*") if p.suffix in (".json", ".txt"))


def test_the_corpus_is_not_empty():
    fed = sum(_replay(path)[1] for path in CAPTURES)
    assert len(CAPTURES) >= 50 and fed >= 1000, (len(CAPTURES), fed)
    assert set(EXPECTED) <= {p.name for p in CAPTURES}


@pytest.mark.parametrize("path", CAPTURES, ids=lambda p: p.name)
def test_capture_raises_exactly_its_known_faults(path: Path):
    raised, _ = _replay(path)
    assert raised == EXPECTED.get(path.name, set())


def test_no_documented_frame_of_the_golden_corpus_is_a_fault():
    records = json.loads(GOLDEN.read_text(encoding="utf-8"))
    records = records.get("frames", records) if isinstance(records, dict) else records
    health = _tracker()
    fed = 0
    for record in records:
        if record.get("who") not in (1, 1001) or not record.get("frame"):
            continue
        message = OWNEvent.parse(record["frame"])
        if message is not None:
            fed += 1
            health.observe(message)
    assert fed >= 20
    assert health.faults == []


@pytest.mark.parametrize("what", PUBLISHED_WHO1_WHATS)
def test_a_published_lighting_status_is_not_a_fault(what: int):
    health = _tracker()
    health.observe(OWNEvent.parse(f"*1*{what}*51##"))
    assert health.faults == []


# 33 is in no source we hold (not SCS WHO 1, not the ZigBee variant): it stays undocumented.
@pytest.mark.parametrize("what", [19, 33, 50, 99])
def test_a_status_outside_the_published_table_is_a_fault(what: int):
    health = _tracker()
    health.observe(OWNEvent.parse(f"*1*{what}*51##"))
    assert [(f["where"], f["code"]) for f in health.faults] == [("51", str(what))]


@pytest.mark.parametrize("what", sorted(dh.DOCUMENTED_EVENTS))
def test_a_documented_lighting_event_is_neither_a_fault_nor_a_recovery(what: int):
    """ZigBee spec 4.0: 32 Toggle, 34 movement, 39 end of movement say nothing about the state."""
    health = _tracker()
    health.observe(OWNEvent.parse(f"*1*{what}*51##"))
    assert health.faults == []

    health.observe(OWNEvent.parse("*1*19*51##"))
    health.observe(OWNEvent.parse(f"*1*{what}*51##"))
    assert [(f["where"], f["code"]) for f in health.faults] == [("51", "19")]
