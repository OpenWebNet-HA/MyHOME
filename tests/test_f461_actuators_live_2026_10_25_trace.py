"""Replay and verification tests for the 2026-10-25 live BTicino F461 actuator-switching capture.

Fixture:
  tests/fixtures/traces/f461_actuators_live_2026-10-25/myhome_trace_F461_actuators_live_2026-10-25.json

The capture is the one asked for in review 5642758778 of #792 (actuator status at startup and
``hvac_action`` from dimension 20): a local Home Assistant running the integration at the head of
#792 was restarted with an event-only listener on the bus of the F461 2.0.11 plant (Living Now
KW4691 standalone thermostat at zone 2, BT-F430/2 heating valve as actuator 1, BT-F430/4 fan coil
as actuator 2, heating season, no central unit). Every request is Home Assistant's, taken from its
debug log and placed on the listener's clock (``source: home_assistant``, ``t_estimated``); the
frames are the listener's. Every test pins a fact of the trace itself (a frame present, an order,
an ACK, a timing) or replays the event-session frames through the climate entity as it is on the
base branch with the pinned OWNd.

What the plant said:

- Startup: ``*#4*2##`` answered ``0``, ``12`` = 23.5, ``*4*1*2##``, ``14`` = 23.5, ``13`` in 1.31 s;
  ``*#4*2*60##`` 34 % in 124 ms; ``*#4*2*19##`` -> ``*#4*2*19*5*0##`` in 103 ms; then
  ``*#4*#0*20##`` was answered by the fan coil (``*#4*2#2*20*5##``, +88 ms) and the valve
  (``*#4*2#1*20*0##``, +128 ms) and ACKed 2.13 s after it was sent.
- ``climate.set_temperature`` 26.0 (``*#4*2*#14*0260*3##``, ACK 90 ms, no reply): echo
  ``*#4*2*12*0260*3##``, ``*4*1*2##``, ``*#4*2*0*0240##`` 26 ms after the ACK, no dimension 14
  frame; 2.2 s after the echo the valve reported itself on, ``*#4*2#1*#20*1##`` then 37 ms later
  ``*#4*2#1*20*1##``.
- ``climate.set_temperature`` 23.5 (``*#4*2*#14*0235*3##``, ACK 104 ms, no reply): the same echo
  with 23.5, 50 ms after the ACK; 2.1 s after the echo ``*#4*2#1*#20*0##`` then ``*#4*2#1*20*0##``.
- The fan coil (actuator 2) sent nothing but its startup reply; the valve's two switches are the
  only dimension 20 frames outside the startup reply.
- ``*#4*2#1*#20*1##`` / ``*#4*2#1*#20*0##`` (the dimension written as ``#20``) parse in the pinned
  OWNd as ``OWNHeatingCommand`` with no zone, so the integration logs them as unsupported and the
  entity only sees the status form that follows (no OWNd change decodes the ``#20`` write-form echo
  yet); the tests accept an event decoding of them as well.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from OWNd.profiles import F461Profile

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:10:25"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_actuators_live_2026-10-25"
    / "myhome_trace_F461_actuators_live_2026-10-25.json"
)

RUN1 = "run1_startup_sweep_and_actuator_request"
RUN2 = "run2_setpoint_26_0_valve_on"
RUN3 = "run3_restore_23_5_valve_off"
ALL_RUNS = (RUN1, RUN2, RUN3)

COVER_POLL = "*#2*05##"
COVER_STOPPED = "*2*0*05##"
ZONE_STATUS = "*#4*2##"
HUMIDITY_READ = "*#4*2*60##"
VALVES_READ = "*#4*2*19##"
ACTUATORS_REQUEST = "*#4*#0*20##"
WRITE_26 = "*#4*2*#14*0260*3##"
WRITE_23_5 = "*#4*2*#14*0235*3##"
SEASON = "*4*1*2##"
TEMPERATURE = "*#4*2*0*0240##"
HUMIDITY = "*#4*2*60*34##"
VALVES = "*#4*2*19*5*0##"
OFFSET = "*#4*2*13*00##"
FAN_COIL_OFF = "*#4*2#2*20*5##"
VALVE_OFF = "*#4*2#1*20*0##"
VALVE_ON = "*#4*2#1*20*1##"
VALVE_ON_CMD_FORM = "*#4*2#1*#20*1##"
VALVE_OFF_CMD_FORM = "*#4*2#1*#20*0##"
ACK = "*#*1##"


def dim(number: int, value: str) -> str:
    return f"*#4*2*{number}*{value}*3##"


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    """Load the 2026-10-25 F461 actuator trace archive."""
    assert FIXTURE.is_file(), FIXTURE
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(trace_payload: dict) -> list[dict]:
    """Return the chronological session log rows."""
    return trace_payload["rows"]


@pytest.fixture(scope="module")
def frames(trace_payload: dict) -> list[dict]:
    """Return the event-session frames."""
    return trace_payload["frames"]


def _results(rows: list[dict], run: str | None = None, frame: str | None = None) -> list[dict]:
    return [r for r in rows if r["dir"] == "result" and (run is None or r["run"] == run) and (frame is None or r["frame"] == frame)]


def _result(rows: list[dict], run: str, frame: str) -> dict:
    found = _results(rows, run, frame)
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _tx(rows: list[dict], run: str, frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "tx" and r["run"] == run and r["frame"] == frame]
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _evt(rows: list[dict], run: str | None = None) -> list[dict]:
    return [r for r in rows if (run is None or r["run"] == run) and r["dir"] == "rx" and r["session"] == "evt"]


def _evt_after(rows: list[dict], run: str, frame: str) -> list[dict]:
    """Event-session rows of ``run`` from the request ``frame`` until the next request of that run."""
    run_rows = [r for r in rows if r["run"] == run]
    start = next(i for i, r in enumerate(run_rows) if r["dir"] == "tx" and r["frame"] == frame)
    out: list[dict] = []
    for r in run_rows[start + 1 :]:
        if r["dir"] == "tx":
            break
        if r["dir"] == "rx" and r["session"] == "evt":
            out.append(r)
    return out


def _gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = MAC
    gw.unique_id = MAC
    gw.bus_group = MAC
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.log_id = "[F461 actuators live 2026-10-25]"
    gw.model = "F461"
    gw.has_central_unit = False
    gw.supports_zone_status = True
    gw.initial_discovery_pending = False
    gw.is_connected = True
    gw.profile = F461Profile()
    gw._gateway = SimpleNamespace(profile=gw.profile)
    gw.sent: list[str] = []

    async def _send(cmd, *a, **k):
        gw.sent.append(str(cmd))
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        fut.set_result(True)
        return fut

    gw.send = AsyncMock(side_effect=_send)
    gw.send_status_request = AsyncMock(side_effect=_send)
    return gw


async def _setup_zone_2(hass: HomeAssistant):
    from custom_components.myhome.climate import async_setup_entry as climate_setup

    gw = _gateway()
    platforms_cfg = {
        "climate": {
            "climate_zone_2": {
                "who": "4",
                "zone": "2",
                "name": "Climate Zone 2",
                "heat": True,
                "cool": True,
                "standalone": True,
                "central": False,
                "manufacturer": "BTicino",
                "model": "F461",
            }
        },
    }
    hass.data = {DOMAIN: {MAC: {CONF_ENTITY: gw, CONF_PLATFORMS: platforms_cfg}}}
    entry = MagicMock()
    entry.data = {"mac": MAC}
    entry.entry_id = "f461_actuators_live_2026_10_25"
    entry.options = {}
    attach_runtime(hass, entry, MAC, gw)
    climates: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await climate_setup(hass, entry, climates.extend)
    assert len(climates) == 1
    zone = climates[0]
    zone.hass = hass
    zone.entity_id = f"climate.{zone._device_id}"
    zone.async_write_ha_state = MagicMock()
    zone.async_schedule_update_ha_state = MagicMock()
    attach_platform(zone, entry)
    return zone


def _replay(zone, evt_rows: list[dict]) -> list[tuple[str, str | None, float | None, str | None]]:
    """Feed the zone-2 heating events of ``evt_rows`` to the entity as the gateway handler does.

    Returns (frame, hvac_action, shown target, hvac_mode) after each frame the entity received.
    A frame the pinned OWNd parses as a command (the ``#20`` forms) is not an event and is not
    routed to the entity; a later OWNd that decodes it as a zone-2 event is replayed like the rest.
    """
    seen: list[tuple[str, str | None, float | None, str | None]] = []
    for r in evt_rows:
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None, r
        if getattr(msg, "who", None) == 4 and getattr(msg, "zone", None) == 2 and msg.__class__.__name__ == "OWNHeatingEvent":
            zone.handle_event(msg)
            seen.append((r["frame"], zone.hvac_action, zone.target_temperature, zone.hvac_mode))
    return seen


# --------------------------------------------------------------- schema, privacy, parsing


def test_schema_runs_and_synthetic_identity(trace_payload: dict, rows: list[dict], frames: list[dict]) -> None:
    """Fixture metadata, synthetic identity, run names and counts, one monotonic time axis."""
    gw = trace_payload["gateway"]
    assert gw["model"] == "F461" and gw["firmware"] == "2.0.11"
    assert gw["ip"] == "192.0.2.92" and gw["mac"] == MAC
    assert gw["identification"] == {"model": "F461", "who13_code": "200", "who13_model_name": "F461", "who1013_code": "134"}
    capture = trace_payload["capture"]
    assert capture["kind"] == "derived_trace_archive"
    assert [r["run"] for r in capture["runs"]] == list(ALL_RUNS)
    assert capture["runs"][0]["t_offset"] == 0.0
    assert [r["listener"] for r in capture["runs"]] == ["evt.jsonl"] * 3
    assert capture["listener"]["file"] == "evt.jsonl" and capture["listener"]["duration_s"] == 3600
    assert capture["window"] == {"first": "2026-10-25T08:31:43+00:00", "last": "2026-10-25T08:35:57+00:00", "frames": 20, "rows": 51}
    assert len(frames) == 20 and len(rows) == 51
    assert {r["run"] for r in rows} == set(ALL_RUNS)
    assert [(r["run"], r["utc"], r["rows"], r["frames"], r["home_assistant_requests"]) for r in capture["runs"]] == [
        (RUN1, "08:31:43-08:31:48", 35, 10, [COVER_POLL, ZONE_STATUS, HUMIDITY_READ, VALVES_READ, ACTUATORS_REQUEST]),
        (RUN2, "08:32:59-08:33:02", 8, 5, [WRITE_26]),
        (RUN3, "08:35:55-08:35:57", 8, 5, [WRITE_23_5]),
    ]
    assert [r["t_offset"] for r in capture["runs"]] == [0.0, 76.431, 251.82]
    ts = [f["timestamp"] for f in frames]
    assert ts == sorted(ts), "one monotonic time axis across the runs"
    assert all(a["t"] <= b["t"] for a, b in zip(rows, rows[1:], strict=False))
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [f["raw"] for f in frames]
    assert [(r["t"], r["run"]) for r in _evt(rows)] == [(f["timestamp"], f["run"]) for f in frames]
    # every request is Home Assistant's, from its debug log: no probe command session in this capture
    for r in rows:
        if r["dir"] == "tx" or r["dir"] == "result" or (r["dir"] == "rx" and r["session"] == "cmd"):
            assert r["source"] == "home_assistant" and r["t_estimated"] is True, r
        else:
            assert "source" not in r and "t_estimated" not in r, r
    assert "probe_segments" not in json.dumps(capture)
    # the entity's recorded service calls and state changes (UTC wall clock), generic entity, #792 head
    states = capture["home_assistant_entity_states"]
    assert [(s["utc"], s["kind"]) for s in states] == [
        ("08:32:59.787", "service_call"),
        ("08:32:59.910", "state"),
        ("08:33:02.150", "state"),
        ("08:33:59.788", "service_call"),
    ]
    assert [s.get("temperature") for s in states] == [26.0, 26.0, 26.0, 23.5]
    assert [(s["hvac_action"], s["actuators"]) for s in states if s["kind"] == "state"] == [
        ("idle", {"1": "off", "2": "off"}),
        ("heating", {"1": "on", "2": "off"}),
    ]
    assert states[3]["note"].startswith("not delivered"), "the harness's first restore call was dropped by Home Assistant: nothing reached the bus"
    # privacy: the only MAC-like string is the synthetic one, no LAN address, no local path, no room name
    text = json.dumps(trace_payload)
    assert set(re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)) == {MAC}
    assert not re.search(r"192[.*]168", text) and "/Users/" not in text and "\\\\" not in text, "no LAN address, no local path"
    assert not re.search(r"climate\.(?!set_temperature\b)\w+", text), "no entity id in the archive (the service name is the only 'climate.' string)"
    assert not any(f["raw"].startswith("*#13**") for f in frames), "no WHO 13 identity or clock frame was exchanged"
    # WHO 4 zone 2 and the cover 05 poll's reply are all the capture holds
    assert {re.match(r"^\*#?(\d+)\*", f["raw"]).group(1) for f in frames} == {"2", "4"}
    assert [f["raw"] for f in frames if not f["raw"].startswith(("*#4*2", "*4*1*2"))] == [COVER_STOPPED]


def test_every_frame_parses_in_ownd(rows: list[dict], frames: list[dict]) -> None:
    """Every rx frame parses in the pinned OWNd; every tx frame parses as a command."""
    for item in frames:
        assert OWNMessage.parse(item["raw"]) is not None, f"Failed to parse evt frame: {item['raw']}"
    for r in rows:
        if r["dir"] == "tx":
            cmd = OWNCommand.parse(r["frame"])
            assert cmd is not None, r
            # the archive row pins what the pinned OWNd classified; a later OWNd may class a
            # dimension read as a status request, so that is accepted too
            assert bool(cmd.is_status_request) == r["is_status_request"] or bool(
                getattr(cmd, "is_dimension_request", False)
            ), r
        elif r["dir"] == "rx" and r["frame"] != ACK:
            assert OWNMessage.parse(r["frame"]) is not None, r
    # the status form of dimension 20: zone 2, actuator in the WHERE parameter, is_active from the value
    for raw, active, log in (
        (VALVE_OFF, False, "Zone 2's actuator 1 is off."),
        (VALVE_ON, True, "Zone 2's actuator 1 is on."),
        (FAN_COIL_OFF, False, "Zone 2's fan is off."),
    ):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.zone == 2 and msg.dimension == 20
        assert msg.__class__.__name__ == "OWNHeatingEvent" and msg.is_active() is active
        assert msg.human_readable_log == log
    assert OWNMessage.parse(FAN_COIL_OFF).fan_on is False, "value 5 is the fan-coil side off (OWNd's dimension 20 >= 5 rule)"
    # the '#20' command form the F461 emits 3 - 37 ms before each status form: the pinned OWNd
    # (<= 2.0.0b11) parses it as a command with no zone (the integration logs "Unsupported message
    # type" and the entity never sees it); no OWNd change decodes the '#20' write-form echo yet.
    # An event decoding is accepted too, so OWNd's MyHOME conformance gate stays green if one lands.
    for raw, active in ((VALVE_ON_CMD_FORM, True), (VALVE_OFF_CMD_FORM, False)):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.who == 4 and msg.dimension == 20
        if msg.__class__.__name__ == "OWNHeatingCommand":
            assert getattr(msg, "zone", None) is None
            assert msg.human_readable_log == raw
        else:
            assert msg.__class__.__name__ == "OWNHeatingEvent" and msg.zone == 2 and msg.is_active() is active
    assert [r["ownd_class"] for r in _evt(rows) if r["frame"].startswith("*#4*2#")] == [
        "OWNHeatingEvent", "OWNHeatingEvent", "OWNHeatingCommand", "OWNHeatingEvent", "OWNHeatingCommand", "OWNHeatingEvent",
    ]  # fmt: skip
    valves = OWNMessage.parse(VALVES)
    assert valves is not None and valves.dimension == 19 and valves.is_active() is False
    assert valves.human_readable_log == "Zone 2's cooling fan is off; heating valve is off."
    assert OWNMessage.parse(SEASON).human_readable_log == "Zone 2's season is heating."
    assert OWNMessage.parse(COVER_STOPPED).human_readable_log == "Cover 05 stopped."


def test_result_rows_are_consistent_with_their_tx_and_cmd_rx_rows(rows: list[dict]) -> None:
    """Each result row follows its tx row; the replies sit between the request and its ACK; ACK is logged."""
    assert all(r["ack"] is True for r in _results(rows)), "every request of this capture was ACKed"
    assert [r["frame"] for r in _results(rows)] == [COVER_POLL, ZONE_STATUS, HUMIDITY_READ, VALVES_READ, ACTUATORS_REQUEST, WRITE_26, WRITE_23_5]
    for run in ALL_RUNS:
        run_rows = [r for r in rows if r["run"] == run]
        for i, r in enumerate(run_rows):
            if r["dir"] != "result":
                continue
            tx_i = next(j for j in range(i - 1, -1, -1) if run_rows[j]["dir"] == "tx")
            tx = run_rows[tx_i]
            assert tx["frame"] == r["frame"] and tx["step"] == r["step"]
            assert tx["t"] <= r["t"]
            assert abs((r["t"] - tx["t"]) - r["reply_time_s"]) < 0.002
            cmd_rx = [x for x in run_rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "cmd"]
            # the F461 delivers the replies one by one: the log collected each at its own time,
            # between the request and the ACK, which closes the batch
            assert [x["frame"] for x in cmd_rx] == [*r["replies"], ACK]
            assert all(tx["t"] <= x["t"] <= r["t"] for x in cmd_rx)
            assert cmd_rx[-1]["t"] == r["t"]
            assert all(a["t"] <= b["t"] for a, b in zip(cmd_rx, cmd_rx[1:], strict=False))
            pending = [x["frame"] for x in run_rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "evt" and x["t"] <= r["t"]]
            assert r["events_after"] == pending
            # a read is delivered on both sessions: what the command session returned is also in
            # the event frames of the pending window; a dimension 14 write has no reply and its
            # echo arrived after the ACK
            if r["frame"].startswith("*#4*2*#14*"):
                assert r["replies"] == [] and pending == [], r
            else:
                assert r["replies"] == pending, r
            # every event frame reached the listener 1 - 3 ms before the log collected the reply
            for x in cmd_rx[:-1]:
                evt_t = next(y["t"] for y in run_rows[tx_i + 1 : i] if y["dir"] == "rx" and y["session"] == "evt" and y["frame"] == x["frame"])
                assert 0.0 <= x["t"] - evt_t <= 0.003, (x, evt_t)


# --------------------------------------------------------------- run 1: the startup


def test_startup_sweep_zone_status_humidity_and_valves(rows: list[dict]) -> None:
    """*#4*2## (0, 12 = 23.5, season, 14 = 23.5, 13 in 1.31 s), then *#4*2*60## and *#4*2*19## one at a time."""
    cover = _result(rows, RUN1, COVER_POLL)
    assert cover["t"] == 0.074 and cover["replies"] == [COVER_STOPPED] == cover["events_after"], "the post-discovery cover poll opens the capture"
    status = _result(rows, RUN1, ZONE_STATUS)
    assert _tx(rows, RUN1, ZONE_STATUS)["t"] == 0.126
    assert status["replies"] == [TEMPERATURE, dim(12, "0235"), SEASON, dim(14, "0235"), OFFSET]
    assert status["events_after"] == status["replies"]
    assert status["reply_time_s"] == pytest.approx(1.314, abs=0.001)
    humidity = _result(rows, RUN1, HUMIDITY_READ)
    assert humidity["replies"] == [HUMIDITY] and humidity["reply_time_s"] == pytest.approx(0.124, abs=0.001)
    valves = _result(rows, RUN1, VALVES_READ)
    assert valves["replies"] == [VALVES] and valves["reply_time_s"] == pytest.approx(0.103, abs=0.001)
    # the three requests went out back to back, each after the previous ACK (51 - 53 ms later)
    assert _tx(rows, RUN1, HUMIDITY_READ)["t"] - status["t"] == pytest.approx(0.051, abs=0.001)
    assert _tx(rows, RUN1, VALVES_READ)["t"] - humidity["t"] == pytest.approx(0.053, abs=0.001)
    assert [r["frame"] for r in _evt(rows, RUN1)][:7] == [COVER_STOPPED, TEMPERATURE, dim(12, "0235"), SEASON, dim(14, "0235"), OFFSET, HUMIDITY]
    assert [r["step"] for r in _evt(rows, RUN1)] == ["startup_sweep"] * 8 + ["startup_actuator_request"] * 2


def test_startup_actuator_request_is_answered_by_both_actuators_within_0_2_s(rows: list[dict]) -> None:
    """*#4*#0*20## 0.75 s after the sweep: fan coil (5, off) at +88 ms, valve (0, off) at +128 ms, ACK at +2.13 s."""
    tx = _tx(rows, RUN1, ACTUATORS_REQUEST)
    res = _result(rows, RUN1, ACTUATORS_REQUEST)
    assert tx["is_status_request"] is False, "sent as a command: OWNd classifies the dimension request as such"
    assert tx["t"] - _result(rows, RUN1, VALVES_READ)["t"] == pytest.approx(0.754, abs=0.001)
    assert tx["t"] - _tx(rows, RUN1, ZONE_STATUS)["t"] == pytest.approx(2.399, abs=0.001)
    assert res["replies"] == [FAN_COIL_OFF, VALVE_OFF] == res["events_after"]
    answers = _evt_after(rows, RUN1, ACTUATORS_REQUEST)
    assert [r["frame"] for r in answers] == [FAN_COIL_OFF, VALVE_OFF]
    assert [round(r["t"] - tx["t"], 3) for r in answers] == [0.088, 0.128]
    assert answers[-1]["t"] - tx["t"] < 0.2, "both actuators answered within 0.2 s"
    assert res["reply_time_s"] == pytest.approx(2.134, abs=0.001), "the ACK came 2.0 s after the last reply: the gateway closes the all-zones batch on a timeout"
    assert res["t"] == max(r["t"] for r in rows if r["run"] == RUN1), "nothing else reached the listener before the command session was closed idle"
    assert [r["ownd_log"] for r in answers] == ["Zone 2's fan is off.", "Zone 2's actuator 1 is off."]


# --------------------------------------------------------------- run 2 and 3: the writes switch the valve


def test_setpoint_26_0_switches_the_valve_on_2_2_s_after_the_echo(rows: list[dict]) -> None:
    """*#4*2*#14*0260*3##: ACK 90 ms, no reply; echo 12 / season / 0 after the ACK; *#4*2#1*#20*1## then *#4*2#1*20*1##."""
    tx = _tx(rows, RUN2, WRITE_26)
    res = _result(rows, RUN2, WRITE_26)
    assert tx["t"] == 76.431 and tx["is_status_request"] is False
    assert res["reply_time_s"] == pytest.approx(0.090, abs=0.001) and res["replies"] == [] and res["events_after"] == []
    evt = _evt_after(rows, RUN2, WRITE_26)
    assert [r["frame"] for r in evt] == [dim(12, "0260"), SEASON, TEMPERATURE, VALVE_ON_CMD_FORM, VALVE_ON]
    assert evt == _evt(rows, RUN2), "the run holds nothing else"
    assert evt[0]["t"] - res["t"] == pytest.approx(0.026, abs=0.001), "the echo reached the event session 26 ms after the ACK"
    assert not any(r["frame"].startswith("*#4*2*14*") for r in evt), "a dimension 14 write echoes dimension 12, never dimension 14"
    assert evt[3]["t"] - evt[0]["t"] == pytest.approx(2.203, abs=0.001), "the valve reported itself on 2.2 s after the echo"
    assert evt[4]["t"] - evt[3]["t"] == pytest.approx(0.037, abs=0.001), "the status form follows the '#20' command form"
    assert evt[4]["t"] - tx["t"] == pytest.approx(2.356, abs=0.001)
    assert [r["ownd_class"] for r in evt[3:]] == ["OWNHeatingCommand", "OWNHeatingEvent"]
    assert evt[4]["ownd_log"] == "Zone 2's actuator 1 is on."
    # the run starts 71.8 s after the startup's last row
    assert tx["t"] - _result(rows, RUN1, ACTUATORS_REQUEST)["t"] == pytest.approx(71.772, abs=0.001)


def test_restore_23_5_switches_the_valve_off_2_1_s_after_the_echo(rows: list[dict]) -> None:
    """*#4*2*#14*0235*3##: ACK 104 ms, no reply; echo 12 / season / 0; *#4*2#1*#20*0## then *#4*2#1*20*0## 3 ms later."""
    tx = _tx(rows, RUN3, WRITE_23_5)
    res = _result(rows, RUN3, WRITE_23_5)
    assert tx["t"] == 251.82 and tx["t"] - _tx(rows, RUN2, WRITE_26)["t"] == pytest.approx(175.389, abs=0.001)
    assert res["reply_time_s"] == pytest.approx(0.104, abs=0.001) and res["replies"] == [] and res["events_after"] == []
    evt = _evt_after(rows, RUN3, WRITE_23_5)
    assert [r["frame"] for r in evt] == [dim(12, "0235"), SEASON, TEMPERATURE, VALVE_OFF_CMD_FORM, VALVE_OFF]
    assert evt == _evt(rows, RUN3)
    assert evt[0]["t"] - res["t"] == pytest.approx(0.050, abs=0.001)
    assert evt[3]["t"] - evt[0]["t"] == pytest.approx(2.126, abs=0.001), "the valve reported itself off 2.1 s after the echo"
    assert evt[4]["t"] - evt[3]["t"] == pytest.approx(0.003, abs=0.001)
    assert evt[4]["ownd_log"] == "Zone 2's actuator 1 is off."
    assert evt[-1]["t"] == max(r["t"] for r in rows), "the valve's off report is the capture's last row"


def test_fan_coil_stays_off_and_the_valve_switches_are_the_only_other_dimension_20_frames(rows: list[dict]) -> None:
    """Actuator 2 sent *#4*2#2*20*5## once (the startup reply); actuator 1 reported off, on, off."""
    dim20 = [(r["run"], r["frame"]) for r in _evt(rows) if r["frame"].startswith("*#4*2#")]
    assert dim20 == [
        (RUN1, FAN_COIL_OFF),
        (RUN1, VALVE_OFF),
        (RUN2, VALVE_ON_CMD_FORM),
        (RUN2, VALVE_ON),
        (RUN3, VALVE_OFF_CMD_FORM),
        (RUN3, VALVE_OFF),
    ]
    assert [f for _, f in dim20 if f.startswith("*#4*2#2")] == [FAN_COIL_OFF]
    # the startup reply carries no '#20' command form; each spontaneous switch does, 3 - 37 ms before its status form
    cmd_forms = [r for r in _evt(rows) if "*#20*" in r["frame"]]
    for cmd in cmd_forms:
        status = next(r for r in _evt(rows) if r["t"] > cmd["t"] and r["frame"].startswith("*#4*2#1*20*"))
        assert status["frame"].endswith(cmd["frame"][-4:]), (cmd["frame"], status["frame"])
        assert 0.003 <= round(status["t"] - cmd["t"], 3) <= 0.037
    # the three setpoint frames of the capture are the startup's 12 / 14 pair and the two write echoes (dimension 12)
    assert [r["frame"] for r in _evt(rows) if r["frame"].startswith(("*#4*2*12*", "*#4*2*14*"))] == [
        dim(12, "0235"), dim(14, "0235"), dim(12, "0260"), dim(12, "0235"),
    ]  # fmt: skip
    assert [r["frame"] for r in _evt(rows)].count(SEASON) == 3, "a season frame rides with every dimension 12 frame"
    assert all(r["frame"] == TEMPERATURE for r in _evt(rows) if r["frame"].startswith("*#4*2*0*")), "the measured 24.0 never moved"


# --------------------------------------------------------------- entity replay


@pytest.mark.asyncio
async def test_replay_hvac_action_idle_heating_idle(hass: HomeAssistant, rows: list[dict]) -> None:
    """The three runs through the climate entity on the base branch: idle after the startup, heating on the
    valve's on report, idle on its off report; 23.5 -> 26.0 -> 23.5 from the dimension 12 echoes."""
    zone = await _setup_zone_2(hass)
    seen = _replay(zone, _evt(rows, RUN1))
    assert [frame for frame, *_ in seen] == [TEMPERATURE, dim(12, "0235"), SEASON, dim(14, "0235"), OFFSET, HUMIDITY, VALVES, FAN_COIL_OFF, VALVE_OFF]
    assert seen[1][2:] == (23.5, None) and seen[2][2:] == (23.5, HVACMode.HEAT)
    assert all(action is None for _, action, *_ in seen[:6]), "nothing before the valves / actuator replies tells the entity whether it is heating"
    assert [action for _, action, *_ in seen[6:]] == [HVACAction.IDLE] * 3, "dimension 19 then both actuators: idle"
    assert zone.hvac_action == HVACAction.IDLE and zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 23.5 and zone.current_temperature == 24.0 and zone.current_humidity == 34.0
    attrs = zone.extra_state_attributes
    assert attrs["season"] == "heating" and attrs["local_target_temperature"] == 23.5
    assert attrs["fan_coil_cooling"] is True and attrs["fan_coil_heating"] is False, "*#4*2*19*5*0##: the fan coil is the cooling side"
    assert attrs["fan_mode"] == "auto" and attrs["running_fan_speed"] == "off", "*#4*2#2*20*5##: the fan coil is off"
    assert zone._actuator_states["1"] is False and zone._actuator_states["2"] is False
    if "actuators" in attrs:  # exposed by #792; the base branch keeps the states internal
        assert attrs["actuators"] == {"1": "off", "2": "off"}

    seen = _replay(zone, _evt(rows, RUN2))
    assert [frame for frame, *_ in seen if frame != VALVE_ON_CMD_FORM] == [dim(12, "0260"), SEASON, TEMPERATURE, VALVE_ON]
    assert seen[0][1:] == (HVACAction.IDLE, 26.0, HVACMode.HEAT), "the echo moves the setpoint, not the action"
    assert all(action == HVACAction.IDLE for frame, action, *_ in seen if not frame.startswith("*#4*2#1")), "idle until the valve reports"
    assert seen[-1] == (VALVE_ON, HVACAction.HEATING, 26.0, HVACMode.HEAT)
    assert zone.hvac_action == HVACAction.HEATING
    assert zone._actuator_states["1"] is True and zone._actuator_states["2"] is False  # "valve" only on the base branch
    if "actuators" in zone.extra_state_attributes:
        assert zone.extra_state_attributes["actuators"] == {"1": "on", "2": "off"}

    seen = _replay(zone, _evt(rows, RUN3))
    assert [frame for frame, *_ in seen if frame != VALVE_OFF_CMD_FORM] == [dim(12, "0235"), SEASON, TEMPERATURE, VALVE_OFF]
    assert seen[0][1:] == (HVACAction.HEATING, 23.5, HVACMode.HEAT), "the restore echo moves the setpoint while the valve is still open"
    assert all(action == HVACAction.HEATING for frame, action, *_ in seen if not frame.startswith("*#4*2#1"))
    assert seen[-1] == (VALVE_OFF, HVACAction.IDLE, 23.5, HVACMode.HEAT)
    assert zone.hvac_action == HVACAction.IDLE and zone.target_temperature == 23.5
    assert zone._actuator_states["1"] is False and zone._actuator_states["2"] is False
    if "actuators" in zone.extra_state_attributes:
        assert zone.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}
    assert zone.extra_state_attributes["fan_mode"] == "auto" and zone.extra_state_attributes["running_fan_speed"] == "off"


@pytest.mark.asyncio
async def test_replay_action_sequence_matches_the_recorded_entity_states(hass: HomeAssistant, rows: list[dict], trace_payload: dict) -> None:
    """Over the whole capture the entity's action changes exactly as Home Assistant recorded it live: idle, heating, idle."""
    zone = await _setup_zone_2(hass)
    seen = _replay(zone, _evt(rows))
    actions = [action for _, action, *_ in seen if action is not None]
    assert [a for i, a in enumerate(actions) if i == 0 or a != actions[i - 1]] == [HVACAction.IDLE, HVACAction.HEATING, HVACAction.IDLE]
    targets = [target for _, _, target, _ in seen if target is not None]
    assert [t for i, t in enumerate(targets) if i == 0 or t != targets[i - 1]] == [23.5, 26.0, 23.5]
    recorded = [s for s in trace_payload["capture"]["home_assistant_entity_states"] if s["kind"] == "state"]
    assert [s["hvac_action"] for s in recorded] == ["idle", "heating"], "the live entity went idle -> heating on the first write (the restore's idle was read back, not recorded)"
    assert all(s["hvac_mode"] == "heat" and s["fan"] == "off" for s in recorded)
    assert zone.hvac_mode == HVACMode.HEAT and zone.hvac_action == HVACAction.IDLE
