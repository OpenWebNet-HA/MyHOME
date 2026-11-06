"""#466: Legrand F455 (firmware 1.0.76) on the F461 test plant, live drive and direct requests (2026-11-06).

Two captures from one F455 on a shared SCS bus (lights 06 / 07 / 09#4#01, F418 dimmer 0010, shutter
05, standalone zone 2, 3550 central unit #0, zone 4, 4611 IR detector 344):

- ``F455_full_debug_log_2026-11-06T16-24-46.log``: the Home Assistant core log of the F455 entry at
  DEBUG during a reload, ``myhome.sweep_bus`` and a drive of every configured device, MyHOME 2.0.0b17
  on OWNd 2.0.0b13.
- ``F455_direct_requests_2026-11-06.jsonl``: status requests sent directly to the F455 with OWNd, one
  record per request (ACK, elapsed seconds, collected replies) and per event-session frame.

The F455's address and MAC and the primary F461's MAC are synthetic (``192.0.2.55``,
``00:03:50:00:11:06``, ``00:03:50:00:11:07``). Every assertion holds on OWNd 2.0.0b13 and on dev.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

from OWNd.message import (
    OWNAutomationEvent,
    OWNGatewayDiagnosticEvent,
    OWNGatewayEvent,
    OWNHeatingEvent,
    OWNLightingDiagnosticEvent,
    OWNLightingEvent,
    OWNMessage,
)

TRACES = Path(__file__).resolve().parent / "fixtures/traces/issue_466"
LOG = TRACES / "F455_full_debug_log_2026-11-06T16-24-46.log"
DIRECT = TRACES / "F455_direct_requests_2026-11-06.jsonl"
LOG_SHA256 = "a8c3d76cf6d753f35a33e261bead9b067a50f50b14c47493b24fad19f83c1a93"
DIRECT_SHA256 = "c3250cffb7bfc5bf46693f83ab2fa9605085d63a64230917eb586e9250a0431d"

SYNTHETIC_ADDRESS = "192.0.2.55"
SYNTHETIC_MAC = "00:03:50:00:11:06"
SYNTHETIC_PRIMARY_MAC = "00:03:50:00:11:07"

ANY_LINE = re.compile(
    r"^(\S+ \S+) (DEBUG|INFO|WARNING|ERROR) \(MainThread\) \[custom_components\.myhome(?:\.\w+)?\] "
    r"(?:\[([^\]]*)\] )?(.*)$"
)


def _lines() -> list[str]:
    return LOG.read_text(encoding="utf-8").splitlines()


def _texts() -> list[str]:
    return [m.group(4) for line in _lines() for m in [ANY_LINE.match(line)] if m]


def _received() -> list[str]:
    return [
        m.group(1) for t in _texts() for m in [re.match(r"^Message received: `([^`]+)`$", t)] if m
    ]


def _queued() -> list[str]:
    return [
        m.group(1)
        for t in _texts()
        for m in [re.match(r"^Message `([^`]+)` was successfully queued\.$", t)]
        if m
    ]


def _records() -> list[dict]:
    return [
        json.loads(line) for line in DIRECT.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _sent() -> dict[str, dict]:
    """Last record per request frame (``*#13**15##`` was sent in both runs)."""
    return {r["frame"]: r for r in _records() if r["kind"] == "sent"}


def test_fixture_provenance_is_pinned() -> None:
    assert hashlib.sha256(LOG.read_bytes()).hexdigest() == LOG_SHA256
    assert hashlib.sha256(DIRECT.read_bytes()).hexdigest() == DIRECT_SHA256


def test_no_gateway_network_identity_is_committed() -> None:
    for path in (LOG, DIRECT):
        text = path.read_text(encoding="utf-8")
        ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))
        macs = set(re.findall(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b", text))
        assert ips <= {SYNTHETIC_ADDRESS}
        assert macs <= {SYNTHETIC_MAC, SYNTHETIC_PRIMARY_MAC}
    assert all(mac.startswith("00:03:50:") for mac in (SYNTHETIC_MAC, SYNTHETIC_PRIMARY_MAC))


def test_capture_environment_is_the_f455_1_0_76_on_b17() -> None:
    texts = _texts()
    assert "Setting up MyHOME gateway 'F455 Gateway' (v2.0.0b17, OWNd v2.0.0b13)" in texts
    assert "Auto-detected gateway firmware `1.0.76` via WHO=13 Dimension 16." in texts
    assert {
        m.group(3) for line in _lines() for m in [ANY_LINE.match(line)] if m and m.group(3)
    } == {f"F455 gateway - {SYNTHETIC_ADDRESS}"}
    sent = _sent()
    assert sent["*#13**16##"]["replies"] == ["*#13**16*1*0*76##"]
    assert sent["*#13**15##"]["replies"] == ["*#13**15*200##"]
    assert sent["*#1013**1##"]["replies"] == ["*#1013**1*8*15*0*5##"]
    model = OWNMessage.parse("*#1013**1*8*15*0*5##")
    assert isinstance(model, OWNGatewayDiagnosticEvent) and model.device_type == "F455"


def test_every_frame_parses_identically_on_b13_and_dev() -> None:
    received = _received()
    assert len(received) == 44
    parsed = [OWNMessage.parse(raw) for raw in received]
    assert None not in parsed
    assert collections.Counter(type(m).__name__ for m in parsed) == {
        "OWNLightingEvent": 16,
        "OWNHeatingEvent": 13,
        "OWNAutomationEvent": 8,
        "OWNLightingDiagnosticEvent": 3,
        "OWNGatewayEvent": 3,
        "OWNGatewayDiagnosticEvent": 1,
    }
    events = [r["raw"] for r in _records() if r["kind"] == "event"]
    assert len(events) == 52
    kinds = collections.Counter(type(OWNMessage.parse(raw)).__name__ for raw in events)
    assert kinds == {
        "OWNHeatingEvent": 26,
        "OWNLightingEvent": 12,
        "OWNGatewayEvent": 6,
        "OWNAutomationEvent": 4,
        "OWNGatewayDiagnosticEvent": 2,
        "OWNLightingDiagnosticEvent": 2,
    }
    assert isinstance(OWNMessage.parse("*#1001*01*1*151*5*0*0##"), OWNLightingDiagnosticEvent)


def test_standby_entry_skips_every_startup_general_request() -> None:
    texts = _texts()
    skipped = [
        m.group(1)
        for t in texts
        for m in [re.match(r"^Skipping WHO=(\d+) discovery: follower gateway on shared bus\.$", t)]
        if m
    ]
    assert sorted(skipped) == ["1", "16", "18", "18", "2", "4", "5"]
    assert sum(t.startswith("light: Pruned duplicate secondary entity") for t in texts) == 4
    assert sum("Pruned duplicate secondary entity" in t for t in texts) == 9
    assert any(t.endswith(f"in favor of primary gateway {SYNTHETIC_PRIMARY_MAC}") for t in texts)
    queued = set(_queued())
    for general in ("*#1*0##", "*#2*0##", "*#4*0##", "*#5*0##", "*#16*0*5##", "*#18*51*51##"):
        assert general not in queued
    # The sweep service on a standby entry sends WHO 13 / WHO 1013 reads only.
    assert "Executing diagnostic bus sweep on gateway 00:03:50:00:11:06 (force=False)" in texts
    assert {
        "*#13**0##",
        "*#13**15##",
        "*#13**16##",
        "*#13**23##",
        "*#13**24##",
        "*#1013*0*1##",
    } <= queued


def test_climate_zones_never_ask_for_temperature_setpoint_or_status() -> None:
    queued = _queued()
    who4 = [frame for frame in queued if frame.startswith("*#4*") and "*#14*" not in frame]
    assert who4 == ["*#4*2*11##", "*#4*#0##", "*#4*4*11##", "*#4*2*20##", "*#4*4*20##"]
    texts = _texts()
    assert (
        "Gateway rejected status request *#4*2*20## (NACK, 0 response(s)). Subsystem or device may not be present."
        in texts
    )
    assert (
        "Gateway rejected status request *#4*4*20## (NACK, 0 response(s)). Subsystem or device may not be present."
        in texts
    )
    # The central unit read is answered with its mode and flags.
    received = _received()
    for frame in ("*4*202*#0##", "*4*21*#0##", "*4*31*#0##", "*4*24*#0##"):
        assert frame in received


def test_f455_answers_single_dimension_zone_reads_at_once() -> None:
    sent = _sent()
    fast = {
        "*#4*2*0##": ["*#4*2*0*0239##"],
        "*#4*4*0##": ["*#4*4*0*0255##"],
        "*#4*2*14##": ["*#4*2*14*0220*3##"],
        "*#4*4*14##": ["*#4*4*14*0215*3##"],
        "*#4*2*12##": ["*#4*2*12*0220*3##", "*4*0*2##"],
        "*#4*2*13##": ["*#4*2*13*00##"],
        "*#4*2*19##": ["*#4*2*19*7*5##"],
        "*#4*2#1*20##": ["*#4*2#1*20*7##"],
        "*#4*4#1*20##": ["*#4*4#1*20*0##"],
    }
    for frame, replies in fast.items():
        assert sent[frame]["ack"] is True, frame
        assert sent[frame]["replies"] == replies, frame
        assert sent[frame]["dt"] < 0.2, frame
    temp = OWNMessage.parse("*#4*2*0*0239##")
    assert isinstance(temp, OWNHeatingEvent) and temp.zone == 2 and temp.main_temperature == 23.9


def test_zone_status_and_general_heating_request_take_nine_seconds() -> None:
    sent = _sent()
    for frame in ("*#4*2##", "*#4*4##", "*#4*0##"):
        assert sent[frame]["ack"] is True, frame
        assert sent[frame]["replies"] is True, frame  # nothing collected on the command session
        assert 8.5 < sent[frame]["dt"] < 9.5, frame
    # *#4*Z## still delivers its five frames, on the event session.
    events = [r["raw"] for r in _records() if r["kind"] == "event"]
    for frame in ("*#4*2*12*0220*3##", "*#4*2*13*00##", "*#4*4*12*0225*3##", "*#4*4*13*01##"):
        assert frame in events


def test_actuator_zone_form_and_humidity_are_nacked() -> None:
    sent = _sent()
    for frame in ("*#4*2*20##", "*#4*4*20##", "*#4*0*20##", "*#4*2*60##"):
        assert sent[frame]["ack"] is False, frame
        assert sent[frame]["dt"] < 0.05, frame


def test_requests_without_an_ack_inside_ownd_s_ten_second_window() -> None:
    sent = _sent()
    for frame in ("*#2*05*10##", "*#18*51*51##", "*#18*51*113##"):
        assert sent[frame]["ack"] is False, frame
        assert sent[frame]["replies"] is None, frame
        assert sent[frame]["dt"] > 9.9, frame


def test_general_lighting_and_automation_requests_terminate() -> None:
    sent = _sent()
    assert sent["*#1*0##"]["replies"] == ["*1*0*06##", "*1*0*07##", "*1*0*0010##", "*1*0*09#4#01##"]
    assert sent["*#1*0##"]["dt"] < 1.5
    assert sent["*#2*0##"]["replies"] == ["*2*0*05##"]
    assert sent["*#2*0##"]["dt"] < 1.5
    assert sent["*#4*#0##"]["replies"] == ["*4*202*#0##", "*4*21*#0##", "*4*31*#0##", "*4*24*#0##"]
    assert sent["*#5*0##"]["ack"] is True and sent["*#5*0##"]["replies"] is True
    assert sent["*#25*344##"]["ack"] is True and sent["*#25*344##"]["replies"] is True


def test_off_dimmer_answers_dimension_1_with_dimension_4_and_plain_on_restores_memory() -> None:
    sent = _sent()
    assert sent["*#1*0010*1##"]["replies"] == ["*#1*0010*4*100*2##"]
    assert sent["*#1*0010*4##"]["replies"] == ["*#1*0010*4*100*2##"]
    off = OWNMessage.parse("*#1*0010*4*100*2##")
    assert isinstance(off, OWNLightingEvent) and off.is_on is False
    received = _received()
    assert "*1*10*0010##" in received and "*#1*0010*1*200*4##" in received
    queued = _queued()
    assert "*#1*0010*#1*150*0##" in queued and "*#1*0010*#1*200*0##" in queued
    assert "*#1*0010*1*150*4##" in received


def test_shutter_and_zone_2_setpoint_writes_echo() -> None:
    queued, received = _queued(), _received()
    assert [f for f in queued if f.startswith("*2*")] == ["*2*1*05##", "*2*0*05##", "*2*2*05##"]
    assert "*2*1*05##" in received and "*2*2*05##" in received
    assert [f for f in queued if "*#14*" in f] == ["*#4*2*#14*0225*3##", "*#4*2*#14*0220*3##"]
    assert "*#4*2*12*0225*3##" in received and "*#4*2*12*0220*3##" in received
    assert isinstance(OWNMessage.parse("*2*2*05##"), OWNAutomationEvent)
    assert isinstance(OWNMessage.parse("*#13**15*200##"), OWNGatewayEvent)
