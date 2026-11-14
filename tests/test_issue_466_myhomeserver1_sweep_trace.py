"""#466: MyHomeServer1 sweep with a setpoint experiment on zone 55 (contributed by @gdluck)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from OWNd.message import OWNHeatingEvent, OWNLightingEvent, OWNMessage

TRACE = (
    Path(__file__).resolve().parent
    / "fixtures/traces/issue_466/myhome_sweep_MyHomeServer1_all_2026-09-29T18-20-22.json"
)


def _frames() -> list[dict]:
    return json.loads(TRACE.read_text(encoding="utf-8"))["frames"]


def test_every_frame_parses():
    messages = [OWNMessage.parse(f["raw"]) for f in _frames()]
    assert len(messages) == 200
    assert all(m is not None for m in messages)
    assert sum(isinstance(m, OWNLightingEvent) for m in messages) >= 40
    assert sum(isinstance(m, OWNHeatingEvent) for m in messages) >= 100


def test_zone_55_pump_stops_before_the_valve_closes():
    """Pump call/stop and the actuator status arrive in this order on the wire."""
    order = [
        f["raw"]
        for f in _frames()
        if f["raw"]
        in (
            "*#4*55#1*20*1##",
            "*4*4001#55*0#3##",
            "*4*4002#55*0#3##",
            "*#4*55#1*20*0##",
        )
    ]
    assert order == ["*#4*55#1*20*1##", "*4*4001#55*0#3##", "*4*4002#55*0#3##", "*#4*55#1*20*0##"]


def test_setpoint_write_is_echoed_as_dimension_12_mode_3():
    frames = [f["raw"] for f in _frames()]
    write = frames.index("*#4*60*#14*0235*1##")
    assert frames[write + 1] == "*#4*60*12*0235*3##"
    assert frames[write + 2] == "*4*1*60##"


def _first(frames, raw, after=0.0):
    return next(f for f in frames if f["raw"] == raw and f["timestamp"] >= after)


def test_zone_55_pump_and_valve_timing():
    """Valve opens, the pump is called ~2.3 s later; the pump stops ~2 s before the valve closes."""
    frames = _frames()
    opened = _first(frames, "*#4*55#1*20*1##")
    called = _first(frames, "*4*4001#55*0#3##", opened["timestamp"])
    pump_on = _first(frames, "*#4*0#3*20*1##", called["timestamp"])
    stopped = _first(frames, "*4*4002#55*0#3##", called["timestamp"])
    pump_off = _first(frames, "*#4*0#3*20*0##", stopped["timestamp"])
    _first(frames, "*4*4002*55##", stopped["timestamp"])
    closed = _first(frames, "*#4*55#1*20*0##", stopped["timestamp"])
    assert 2.0 <= called["timestamp"] - opened["timestamp"] <= 2.5
    assert 1.8 <= closed["timestamp"] - stopped["timestamp"] <= 2.3
    assert pump_on["timestamp"] - called["timestamp"] < 0.2
    assert pump_off["timestamp"] - stopped["timestamp"] < 0.2
    assert _first(frames, "*#4*55*0*0246##")  # the room was at 24.6 C throughout


def test_setpoint_write_has_no_dimension_7_or_14_echo():
    frames = _frames()
    write = _first(frames, "*#4*60*#14*0235*1##")
    window = [f["raw"] for f in frames if 0 < f["timestamp"] - write["timestamp"] < 3 and f["direction"] == "rx"]
    assert "*#4*60*12*0235*3##" in window and "*4*1*60##" in window
    assert not [raw for raw in window if raw.startswith(("*#4*60*7*", "*#4*60*14*"))]


def test_unchanged_repeat_write_is_answered_with_the_temperature_only():
    frames = _frames()
    repeat = [f for f in frames if f["raw"] == "*#4*55*#14*0220*1##"]
    assert len(repeat) == 2
    second = repeat[1]["timestamp"]
    answers = [f["raw"] for f in frames if 0 < f["timestamp"] - second < 1 and f["raw"].startswith("*#4*55*")]
    assert answers == ["*#4*55*0*0246##"]


def test_dimension_4_report_on_point_66():
    assert _first(_frames(), "*#1*66*4*100*4##")["direction"] == "rx"


DIAGNOSTICS = TRACE.with_name("diagnostics_MyHomeServer1_startup_poll_2026-09-29T18-31.json")
DEAD_ZONES = {0, 1, 2, 3, 4, 6, 32, 33, 71, 75, 76}


def _poll():
    return json.loads(DIAGNOSTICS.read_text(encoding="utf-8"))["data"]["bus_monitor"]["recent_frames"]


def test_diagnostics_dead_zones_are_polled_and_never_answer():
    """The 11 restored zones: polled ~6.4 s apart, no frame of theirs on the bus, no NACK recorded."""
    frames = _poll()
    polled = {int(f["raw"][4:-2]): f for f in frames if f["direction"] == "tx" and f["raw"].startswith("*#4*") and f["raw"][4:-2].isdigit()}
    assert DEAD_ZONES <= set(polled)
    answered = {
        int(m.group(1))
        for f in frames
        if f["direction"] == "rx" and (m := re.match(r"\*#4\*(\d+)#?\d*\*", f["raw"]))
    }
    assert not DEAD_ZONES & answered
    assert not any(f["is_nack"] for f in frames)  # the bus card does not record the refusal (the debug log does, below)
    ordered = sorted((polled[z]["timestamp"] for z in DEAD_ZONES - {0}))
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    assert all(6.3 <= gap <= 6.6 for gap in gaps[1:]), gaps


def test_diagnostics_live_zones_and_the_ones_without_dimension_7():
    frames = _poll()
    dims: dict[int, set[int]] = {}
    for f in frames:
        if f["direction"] == "rx" and (m := re.match(r"\*#4\*(\d+)\*(\d+)\*", f["raw"])):
            dims.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
    live = {z: d for z, d in dims.items() if z >= 35}
    assert len(live) == 34
    assert {z for z, d in live.items() if 7 not in d} == {36, 40, 42, 55, 60, 68}
    assert all({0, 12, 13, 14} <= d for d in live.values())


NACK_LOG = TRACE.with_name("MyHomeServer1_dead_zone_nacks_2026-09-29T22-45.txt")
LOG_LINE = re.compile(r"^(\S+ \S+) DEBUG \(MainThread\) \[custom_components\.myhome\] (?:\[[^\]]*\] )?(.*)$")


def _debug_log() -> list[tuple[datetime, str]]:
    lines = (LOG_LINE.match(line) for line in NACK_LOG.read_text(encoding="utf-8").splitlines())
    return [(datetime.fromisoformat(m.group(1)), m.group(2)) for m in lines if m]


def _dead_zone_polls() -> dict[str, dict[str, datetime]]:
    """Per status request: when the worker took it, when it was retried, when the gateway refused it."""
    polls: dict[str, dict[str, datetime]] = {}
    for when, text in _debug_log():
        if m := re.match(r"Message `(\*#4\*\d+##)` was successfully unqueued", text):
            polls[m.group(1)] = {"taken": when}
        elif (m := re.match(r"Could not send message `(\*#4\*\d+##)`\. Retrying", text)) and m.group(1) in polls:
            polls[m.group(1)]["retried"] = when
        elif m := re.match(r"Gateway rejected status request (\*#4\*\d+##) \(NACK", text):
            polls[m.group(1)]["nack"] = when
    return polls


def test_debug_log_dead_zones_are_refused_with_a_nack_after_one_retry():
    """The gateway does answer a silent zone: a NACK, 3.2 s after a retry that is 3.2 s after the request (#553)."""
    polls = _dead_zone_polls()
    assert {int(k[4:-2]) for k in polls} == DEAD_ZONES
    for request, poll in polls.items():
        assert "nack" in poll, request
        if request == "*#4*0##":  # the general request is refused at once
            assert (poll["nack"] - poll["taken"]).total_seconds() < 0.1
            continue
        assert 3.1 <= (poll["retried"] - poll["taken"]).total_seconds() <= 3.3, request
        assert 3.1 <= (poll["nack"] - poll["retried"]).total_seconds() <= 3.3, request
    started = min(p["taken"] for p in polls.values())
    assert 65 <= (max(p["nack"] for p in polls.values()) - started).total_seconds() <= 70  # the ~70 s startup wait


def test_debug_log_counts_the_dead_zones_among_the_44_climate_entities():
    """The reporter's myhome.yaml configures 33 zones (35-70); the other 11 entities are restored from the registry."""
    restored = [text for _, text in _debug_log() if "climate restored/configured" in text]
    assert len(restored) == 1 and "44 entities" in restored[0]
