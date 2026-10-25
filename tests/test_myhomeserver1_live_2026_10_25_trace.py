"""Replay and verification tests for the 2026-10-25 live MyHomeServer1 (firmware 3.87.13) per-zone status-read capture.

Fixture:
  tests/fixtures/traces/myhomeserver1_live_2026-10-25/myhome_trace_MyHomeServer1_live_2026-10-25.json

The second gateway family next to ``f461_actuators_live_2026-10-25`` for the reads behind #792
(actuator status at startup, ``hvac_action`` from dimension 20) and #793 (humidity gate): on the
large home plant of ``myhomeserver1_live_2026-10-24`` (WHO 4 zones 35-70) the probe sent
``*#4*#0*20##`` and then, zone by zone, ``*#4*Z*19##``, ``*#4*Z*20##``, ``*#4*Z*11##`` and
``*#4*Z*60##``, then ``*#4*0*60##``, and listened for 600 s. Status reads only. Every test pins a
fact of the trace itself or replays event-session frames through the climate entity as it is on
the base branch with the pinned OWNd.

What the plant said:

- ``*#4*#0*20##`` was ACKed in 0.50 s with 8 frames on the command session; the event session
  received the whole burst, 47 frames from +0.18 s to +1.91 s (39 of them after the ACK), 43
  distinct: 34 ``Z#1`` (zones 35-70 but 54 and 56), 5 ``Z#2`` fan coils (47, 48, 57, 60, 70) and
  the pumps ``0#1``-``0#4``; zones 35-38 answered twice. Every actuator was off (0 / fan 5).
- ``*#4*Z*19##`` and ``*#4*Z*20##``: ACK on 34 zones in 0.16-0.25 s, NACK after 2.1 s on 54 and
  56 (no zone); the five fan-coil zones read ``*#4*Z*19*5*0##`` and answer ``Z#2`` and ``Z#1``.
- ``*#4*Z*11##``: NACK on all 36 zones (the fan-speed dimension is refused on a valve zone, the
  split the F461 showed in its heating season in ``f461_live_2026-10-15``).
- ``*#4*Z*60##``: ACK on the 17 zones with a humidity probe in 0.19-0.29 s, NACK after 2.1 s on
  the other 19; ``*#4*0*60##`` answers the same 17 zones in 0.66 s.
- The listen: the house's periodic zone sweep (season, 12 / 14 / 13, dimension 7 setpoints,
  temperatures, dimension 5, humidity), four valve off reports (each ``*#4*Z#1*#20*0##`` then
  ``*#4*Z#1*20*0##``), WHO 1 light traffic and the 13:00 clock broadcast; no actuator switched on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from OWNd.profiles import MyHomeServer1Profile

from custom_components.myhome.climate import _calling_zones, _zone_address, _zone_route_keys
from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from tests.conftest import attach_platform, attach_runtime
from tests.test_f461_actuators_live_2026_10_25_trace import _gateway

MAC = "00:03:50:00:10:26"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "myhomeserver1_live_2026-10-25"
    / "myhome_trace_MyHomeServer1_live_2026-10-25.json"
)

RUN1 = "run1_identify_and_all_zones_actuators"
RUN2 = "run2_per_zone_reads_19_20_11_60"
RUN3 = "run3_general_humidity"
RUN4 = "run4_listen_600_s"
ALL_RUNS = (RUN1, RUN2, RUN3, RUN4)

ZONES = tuple(range(35, 71))
NO_ZONE = (54, 56)
FAN_COIL_ZONES = (47, 48, 57, 60, 70)
HUMIDITY_ZONES = (39, 40, 41, 42, 43, 47, 48, 53, 55, 57, 58, 59, 60, 61, 68, 69, 70)
HUMIDITY = {39: 47, 40: 48, 41: 43, 42: 51, 43: 55, 47: 58, 48: 53, 53: 60, 55: 49, 57: 51, 58: 57, 59: 66, 60: 54, 61: 52, 68: 55, 69: 52, 70: 68}
PUMPS = ("*#4*0#1*20*0##", "*#4*0#2*20*0##", "*#4*0#3*20*0##", "*#4*0#4*20*0##")
ALL_ZONES_REQUEST = "*#4*#0*20##"
GENERAL_HUMIDITY = "*#4*0*60##"


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    assert FIXTURE.is_file(), FIXTURE
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(trace_payload: dict) -> list[dict]:
    return trace_payload["rows"]


@pytest.fixture(scope="module")
def frames(trace_payload: dict) -> list[dict]:
    return trace_payload["frames"]


def _results(rows: list[dict], pattern: str) -> list[dict]:
    return [r for r in rows if r["dir"] == "result" and re.fullmatch(pattern, r["frame"])]


def _result(rows: list[dict], frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "result" and r["frame"] == frame]
    assert len(found) == 1, (frame, len(found))
    return found[0]


def _tx(rows: list[dict], frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "tx" and r["frame"] == frame]
    assert len(found) == 1, (frame, len(found))
    return found[0]


def _evt(rows: list[dict], run: str | None = None) -> list[dict]:
    return [r for r in rows if (run is None or r["run"] == run) and r["dir"] == "rx" and r["session"] == "evt"]


def _evt_between(rows: list[dict], frame: str) -> list[dict]:
    """Event-session rows from the request ``frame`` until the next request."""
    start = next(i for i, r in enumerate(rows) if r["dir"] == "tx" and r["frame"] == frame)
    out: list[dict] = []
    for r in rows[start + 1 :]:
        if r["dir"] == "tx":
            break
        if r["dir"] == "rx" and r["session"] == "evt":
            out.append(r)
    return out


def _zone(frame: str) -> int:
    return int(re.match(r"^\*#?4\*(\d+)", frame).group(1))


async def _setup_zones(hass: HomeAssistant, zones: tuple[int, ...]):
    from custom_components.myhome.climate import async_setup_entry as climate_setup

    gw = _gateway()
    gw.mac = gw.unique_id = gw.bus_group = MAC
    gw.model = "MyHomeServer1"
    gw.log_id = "[MyHomeServer1 live 2026-10-25]"
    gw.profile = MyHomeServer1Profile()
    gw._gateway.profile = gw.profile
    platforms_cfg = {
        "climate": {
            f"climate_zone_{z}": {
                "who": "4",
                "zone": str(z),
                "name": f"Climate Zone {z}",
                "heat": True,
                "cool": False,
                "standalone": False,
                "central": False,
                "manufacturer": "Legrand / BTicino",
                "model": "MyHomeServer1",
            }
            for z in zones
        },
    }
    hass.data = {DOMAIN: {MAC: {CONF_ENTITY: gw, CONF_PLATFORMS: platforms_cfg}}}
    entry = MagicMock()
    entry.data = {"mac": MAC}
    entry.entry_id = "myhomeserver1_live_2026_10_25"
    entry.options = {}
    attach_runtime(hass, entry, MAC, gw)
    climates: list = []
    with (
        patch("custom_components.myhome.discovery.er.async_entries_for_config_entry", return_value=[]),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await climate_setup(hass, entry, climates.extend)
    assert [c._where for c in climates] == [str(z) for z in zones]
    for zone in climates:
        zone.hass = hass
        zone.entity_id = f"climate.{zone._device_id}"
        zone.async_write_ha_state = MagicMock()
        zone.async_schedule_update_ha_state = MagicMock()
        attach_platform(zone, entry)
    return {str(z): c for z, c in zip(zones, climates, strict=True)}


def _route(entities: dict, evt_rows: list[dict]) -> list[tuple[str, str]]:
    """Deliver the WHO 4 events of ``evt_rows`` the way the platform routes them (``_zone_route_keys``).

    Returns (frame, entity WHERE) for every delivery. A pump frame ``0#N`` has no route key and
    reaches no zone; a frame the pinned OWNd parses as a command is not an event and is skipped.
    """
    delivered: list[tuple[str, str]] = []
    for r in evt_rows:
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None, r
        if getattr(msg, "who", None) != 4 or msg.__class__.__name__ != "OWNHeatingEvent":
            continue
        for key in dict.fromkeys(_zone_route_keys(msg, _zone_address(msg))):
            if key in entities:
                entities[key].handle_event(msg)
                delivered.append((r["frame"], key))
    return delivered


# --------------------------------------------------------------- schema, privacy, parsing


def test_schema_runs_and_synthetic_identity(trace_payload: dict, rows: list[dict], frames: list[dict]) -> None:
    gw = trace_payload["gateway"]
    assert gw["model"] == "MyHomeServer1" and gw["firmware"] == "3.87.13"
    assert gw["ip"] == "192.0.2.9" and gw["mac"] == MAC
    assert gw["identification"] == {"model": "MyHomeServer1", "who13_code": "200", "who13_model_name": "MyHomeServer1", "who1013_code": "67"}
    assert "myhomeserver1_live_2026-10-24" in gw["identification_note"] and "2.82.06" in gw["oracle_note"]
    capture = trace_payload["capture"]
    assert capture["kind"] == "derived_trace_archive"
    assert [r["run"] for r in capture["runs"]] == list(ALL_RUNS)
    assert capture["window"] == {"first": "2026-10-25T10:46:46+00:00", "last": "2026-10-25T11:02:48+00:00", "frames": 677, "rows": 1224}
    assert len(frames) == 677 and len(rows) == 1224
    assert [(r["t_offset"], r["rows"], r["frames"], r["utc"]) for r in capture["runs"]] == [
        (0.0, 68, 49, "10:46:46-10:46:50"),
        (6.148, 643, 126, "10:46:52-10:52:53"),
        (369.609, 28, 17, "10:52:55-10:52:56"),
        (436.573, 485, 485, "10:54:02-11:02:48"),
    ]
    assert capture["probe"] == {"file": "mhs1_actuators_reads.jsonl", "probe_wait_s": 1.2, "pause_between_zones_s": 1.0, "listen_s": 600}
    ts = [f["timestamp"] for f in frames]
    assert ts == sorted(ts) and all(a["t"] <= b["t"] for a, b in zip(rows, rows[1:], strict=False))
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [f["raw"] for f in frames]
    # status reads only: 2 WHO 13, the all-zones request, 36 x 4 per-zone reads, the general humidity read
    tx = [r["frame"] for r in rows if r["dir"] == "tx"]
    assert len(tx) == 148 and all(f.startswith("*#") for f in tx)
    assert all(r["is_status_request"] is True for r in rows if r["dir"] == "tx")
    assert tx[:3] == ["*#13**16##", "*#13**0##", ALL_ZONES_REQUEST] and tx[-1] == GENERAL_HUMIDITY
    assert tx[3:-1] == [f"*#4*{z}*{d}##" for z in ZONES for d in (19, 20, 11, 60)]
    assert not any(r["dir"] == "tx" for r in rows if r["run"] == RUN4), "the listen had no request pending"
    # privacy: the only MAC-like string is the synthetic one, no WHO 13 dimension 12 frame, no LAN address, no local path
    text = json.dumps(trace_payload)
    assert set(re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)) == {MAC}
    assert not any(f["raw"].startswith("*#13**12*") for f in frames)
    assert not re.search(r"192[.*]168", text) and "/Users/" not in text
    assert {re.match(r"^\*#?(\d+)\*", f["raw"]).group(1) for f in frames} == {"1", "4", "13"}


def test_every_frame_parses_in_ownd(rows: list[dict], frames: list[dict]) -> None:
    for item in frames:
        assert OWNMessage.parse(item["raw"]) is not None, f"Failed to parse evt frame: {item['raw']}"
    for r in rows:
        if r["dir"] == "tx":
            assert OWNCommand.parse(r["frame"]) is not None, r
        elif r["dir"] == "rx" and r["frame"] not in ("*#*1##", "*#*0##"):
            assert OWNMessage.parse(r["frame"]) is not None, r
    # a zone's actuator frame routes to that zone; a pump frame 0#N has no zone and no route key
    for raw, zone in (("*#4*35#1*20*0##", 35), ("*#4*47#2*20*5##", 47), ("*#4*70#1*20*0##", 70)):
        msg = OWNMessage.parse(raw)
        assert msg.zone == zone and msg.is_active() is False
        assert _calling_zones(msg) == ([str(zone)], None) and _zone_address(msg) is not None
        assert set(_zone_route_keys(msg, _zone_address(msg))) == {str(zone)}
    for raw in PUMPS:
        msg = OWNMessage.parse(raw)
        assert msg.__class__.__name__ == "OWNHeatingEvent" and msg.zone == 0 and msg.is_active() is False
        assert _calling_zones(msg) == ([], None) and _zone_address(msg) is None
        assert _zone_route_keys(msg, _zone_address(msg)) == [], "a pump frame creates no zone and reaches none"
    # a pump call names the calling zone, not zone 0
    msg = OWNMessage.parse("*4*4002#35*0#1##")
    assert msg is not None and _calling_zones(msg) == (["35"], None) and _zone_route_keys(msg, _zone_address(msg)) == ["35"]
    # the '#20' command form of the spontaneous valve reports: a command with no zone in the pinned
    # OWNd (OpenWebNet-HA/OWNd#138 decodes it); an event decoding is accepted as well
    for raw in sorted({r["frame"] for r in _evt(rows) if "*#20*" in r["frame"]}):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.who == 4 and msg.dimension == 20
        if msg.__class__.__name__ == "OWNHeatingCommand":
            assert getattr(msg, "zone", None) is None
        else:
            assert msg.__class__.__name__ == "OWNHeatingEvent" and msg.zone == _zone(raw) and msg.is_active() is False


def test_result_rows_are_consistent_with_their_tx_and_cmd_rx_rows(rows: list[dict]) -> None:
    results = [r for r in rows if r["dir"] == "result"]
    assert len(results) == 148
    for i, r in enumerate(rows):
        if r["dir"] != "result":
            continue
        tx_i = next(j for j in range(i - 1, -1, -1) if rows[j]["dir"] == "tx")
        tx = rows[tx_i]
        assert tx["frame"] == r["frame"] and tx["step"] == r["step"] and tx["run"] == r["run"]
        assert tx["t"] <= r["t"]
        assert abs((r["t"] - tx["t"]) - r["reply_time_s"]) < 0.002
        # the command-session replies sit right after their request, at its t, flagged as estimated
        cmd_rx = [x for x in rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "cmd"]
        assert [x["frame"] for x in cmd_rx] == [*r["replies"], "*#*1##" if r["ack"] else "*#*0##"]
        assert all(x["t"] == tx["t"] and x["t_estimated"] is True for x in cmd_rx)
        assert rows[tx_i + 1 : tx_i + 1 + len(cmd_rx)] == cmd_rx
        # events_after holds exactly the event-session frames that arrived while the request was pending
        pending = [x["frame"] for x in rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "evt" and x["t"] <= r["t"]]
        assert r["events_after"] == pending
        assert (r["replies"] == []) == (not r["ack"]), r
        if r["ack"] and r["frame"] not in (ALL_ZONES_REQUEST, GENERAL_HUMIDITY):
            assert set(r["replies"]) <= set(pending), r


# --------------------------------------------------------------- identification and the all-zones request


def test_identify_firmware_and_clock_echoed_on_the_event_session(rows: list[dict]) -> None:
    fw = _result(rows, "*#13**16##")
    assert fw["replies"] == ["*#13**16*3*87*13##"] == fw["events_after"] and fw["reply_time_s"] == pytest.approx(0.027, abs=0.001)
    clock = _result(rows, "*#13**0##")
    assert clock["replies"] == ["*#13**0*12*46*47*999##"] == clock["events_after"] and clock["reply_time_s"] == pytest.approx(0.020, abs=0.001)
    assert [r["frame"] for r in _evt(rows, RUN1) if r["frame"].startswith("*#13")] == ["*#13**16*3*87*13##", "*#13**0*12*46*47*999##"]


def test_all_zones_actuator_request_43_distinct_frames_all_off_pumps_included(rows: list[dict]) -> None:
    """*#4*#0*20##: ACK in 0.50 s with 8 replies; the event session gets 47 frames (43 distinct) over 1.9 s."""
    tx = _tx(rows, ALL_ZONES_REQUEST)
    res = _result(rows, ALL_ZONES_REQUEST)
    assert res["ack"] is True and res["reply_time_s"] == pytest.approx(0.496, abs=0.001)
    assert len(res["replies"]) == 8 and res["events_after"] == res["replies"]
    burst = _evt_between(rows, ALL_ZONES_REQUEST)
    assert burst == [r for r in _evt(rows, RUN1) if r["frame"].startswith("*#4*")]
    raws = [r["frame"] for r in burst]
    assert len(raws) == 47 and len(set(raws)) == 43
    assert raws[:8] == res["replies"], "the command session collected the first 8 frames of the burst"
    assert round(burst[0]["t"] - tx["t"], 3) == 0.184 and round(burst[-1]["t"] - tx["t"], 3) == 1.909
    assert sum(1 for r in burst if r["t"] > res["t"]) == 39, "most of the burst arrived after the ACK"
    assert all(re.fullmatch(r"\*#4\*\d+#[1-4]\*20\*[05]##", f) for f in raws), "every actuator off (0) or fan coil off (5)"
    valves = sorted(_zone(f) for f in set(raws) if re.fullmatch(r"\*#4\*[1-9]\d*#1\*20\*0##", f))
    assert valves == [z for z in ZONES if z not in NO_ZONE] and len(valves) == 34
    assert sorted(_zone(f) for f in set(raws) if re.fullmatch(r"\*#4\*[1-9]\d*#2\*20\*5##", f)) == list(FAN_COIL_ZONES)
    assert sorted(f for f in set(raws) if f.startswith("*#4*0#")) == list(PUMPS)
    assert sorted(_zone(f) for f in raws if raws.count(f) == 2) == [35, 35, 36, 36, 37, 37, 38, 38], "zones 35-38 answered twice"
    assert res["t"] == tx["t"] + res["reply_time_s"] and max(r["t"] for r in rows if r["run"] == RUN1) == burst[-1]["t"]


# --------------------------------------------------------------- per-zone reads


def test_dimension_19_and_20_per_zone_ack_on_34_zones_nack_on_54_and_56(rows: list[dict]) -> None:
    d19 = _results(rows, r"\*#4\*\d+\*19##")
    d20 = _results(rows, r"\*#4\*[1-9]\d*\*20##")
    assert [_zone(r["frame"]) for r in d19] == list(ZONES) and [_zone(r["frame"]) for r in d20] == list(ZONES)
    for res in (*d19, *d20):
        zone = _zone(res["frame"])
        if zone in NO_ZONE:
            assert res["ack"] is False and 2.1 < res["reply_time_s"] < 2.13, res
            assert not any(f.startswith(f"*#4*{zone}") for f in res["events_after"]), "nothing from a zone that does not exist"
        else:
            assert res["ack"] is True and 0.15 < res["reply_time_s"] < 0.25, res
    # dimension 19: valve zones read 0 / 0, the five fan-coil zones 5 / 0 (fan coil off, valve off)
    for res in d19:
        if res["ack"]:
            zone = _zone(res["frame"])
            expected = f"*#4*{zone}*19*{'5' if zone in FAN_COIL_ZONES else '0'}*0##"
            assert res["replies"] == [expected], res
            msg = OWNMessage.parse(expected)
            assert msg.is_active() is False and msg.is_heating() is False
    # dimension 20: a valve zone answers Z#1 once; a fan-coil zone answers Z#2 and Z#1 on the event session
    for res in d20:
        if not res["ack"]:
            continue
        zone = _zone(res["frame"])
        evt = [f for f in (r["frame"] for r in _evt_between(rows, res["frame"])) if f.startswith(f"*#4*{zone}#")]
        if zone in FAN_COIL_ZONES:
            assert set(evt) == {f"*#4*{zone}#2*20*5##", f"*#4*{zone}#1*20*0##"}, res
            assert set(res["replies"]) <= set(evt) and len(res["replies"]) == 1
        else:
            assert res["replies"] == [f"*#4*{zone}#1*20*0##"] and set(evt) == {f"*#4*{zone}#1*20*0##"}, res
    assert sum(1 for r in d20 if r["ack"] and len(r["events_after"]) == 2 and len(set(r["events_after"])) == 1) == 2, "zones 51 and 64 echoed their reply twice before the ACK"
    doubled = sorted(_zone(r["frame"]) for r in d20 if r["ack"] and _zone(r["frame"]) not in FAN_COIL_ZONES and [f for f in (x["frame"] for x in _evt_between(rows, r["frame"])) if f.startswith(f"*#4*{_zone(r['frame'])}#")].count(r["replies"][0]) == 2)
    assert doubled == [35, 40, 42, 51, 64], "five valve zones echoed their dimension 20 reply twice on the event session"


def test_dimension_11_is_refused_on_every_zone(rows: list[dict]) -> None:
    """*#4*Z*11## NACKs on all 36 zones: the fan-speed dimension is refused on a valve zone (and on the fan-coil zones too)."""
    d11 = _results(rows, r"\*#4\*\d+\*11##")
    assert [_zone(r["frame"]) for r in d11] == list(ZONES)
    assert all(r["ack"] is False and r["replies"] == [] for r in d11)
    assert all(r["reply_time_s"] > 2.09 for r in d11)
    late = sorted(r["reply_time_s"] for r in d11 if r["reply_time_s"] > 2.3)
    assert len(late) == 5 and late[0] == pytest.approx(2.851, abs=0.001) and late[-1] == pytest.approx(5.812, abs=0.001), "31 NACKs in 2.09-2.19 s, five between 2.85 and 5.81 s"
    assert max(r["reply_time_s"] for r in d11 if r["reply_time_s"] <= 2.3) < 2.2
    assert not any(f"*{_zone(r['frame'])}*11*" in f for r in d11 for f in r["events_after"]), "no dimension 11 frame on the event session"
    assert not any(re.search(r"\*11\*", r["frame"]) for r in _evt(rows)), "the plant never emits dimension 11"


def test_humidity_only_where_a_probe_exists_and_the_general_sweep(rows: list[dict]) -> None:
    d60 = _results(rows, r"\*#4\*[1-9]\d*\*60##")
    assert [_zone(r["frame"]) for r in d60] == list(ZONES)
    assert [_zone(r["frame"]) for r in d60 if r["ack"]] == list(HUMIDITY_ZONES) and len(HUMIDITY_ZONES) == 17
    for res in d60:
        zone = _zone(res["frame"])
        if zone in HUMIDITY_ZONES:
            assert res["replies"] == [f"*#4*{zone}*60*{HUMIDITY[zone]}##"] and res["reply_time_s"] < 0.3, res
            assert OWNMessage.parse(res["replies"][0]).human_readable_log == f"Zone {zone}'s main sensor is reporting a humidity of {HUMIDITY[zone]}.0%."
        else:
            assert res["ack"] is False and 2.1 < res["reply_time_s"] < 3.1, res
    sweep = _result(rows, GENERAL_HUMIDITY)
    assert sweep["ack"] is True and sweep["reply_time_s"] == pytest.approx(0.658, abs=0.001)
    assert len(sweep["replies"]) == 8 and sweep["events_after"] == sweep["replies"]
    answered = [r["frame"] for r in _evt(rows, RUN3)]
    assert sorted(_zone(f) for f in answered) == list(HUMIDITY_ZONES), "the same 17 zones answer the general read"
    moved = {_zone(f): int(f.split("*")[4].rstrip("#")) - HUMIDITY[_zone(f)] for f in answered if int(f.split("*")[4].rstrip("#")) != HUMIDITY[_zone(f)]}
    assert moved == {39: 1, 42: 1, 61: 1}, "the same values as the per-zone reads 6 min earlier, zones 39, 42 and 61 one point up"
    assert answered[-1] == "*#4*39*60*48##" and max(r["t"] for r in _evt(rows, RUN3)) - sweep["t"] == pytest.approx(0.324, abs=0.001), "the last zone answered 0.3 s after the ACK"


# --------------------------------------------------------------- the house's own traffic


def test_spontaneous_valve_reports_pump_calls_and_the_listen(rows: list[dict]) -> None:
    evt = _evt(rows)
    assert not any(re.fullmatch(r"\*#4\*\d+#\d\*20\*[1-4]##", r["frame"]) for r in evt), "no actuator switched on during the capture"
    # every spontaneous valve report: the '#20' command form, then the status form within 50 ms
    cmd_forms = [r for r in evt if "*#20*" in r["frame"]]
    assert [(r["run"], _zone(r["frame"])) for r in cmd_forms] == [(RUN2, 35), (RUN2, 53), (RUN2, 64), (RUN2, 55), (RUN2, 68), (RUN4, 70), (RUN4, 51), (RUN4, 46), (RUN4, 43)]
    for cmd in cmd_forms:
        status = next(r for r in evt if r["t"] > cmd["t"] and r["frame"].startswith(f"*#4*{_zone(cmd['frame'])}#1*20*"))
        assert status["frame"] == cmd["frame"].replace("*#20*", "*20*") and 0 < status["t"] - cmd["t"] <= 0.05
    # every valve report is preceded by the zone's pump call (*4*4002#Z*0#N## then *4*4002*Z##), about 2 s earlier
    pump_calls = [r for r in evt if "4002#" in r["frame"]]
    assert [r["frame"] for r in pump_calls] == [
        "*4*4002#35*0#1##", "*4*4002#53*0#3##", "*4*4002#64*0#4##", "*4*4002#55*0#3##", "*4*4002#68*0#4##",
        "*4*4002#70*0#3##", "*4*4002#51*0#3##", "*4*4002#46*0#2##", "*4*4002#43*0#2##",
    ]  # fmt: skip
    assert [_zone(r["frame"]) for r in cmd_forms] == [int(r["frame"].split("#")[1].split("*")[0]) for r in pump_calls]
    for call, cmd in zip(pump_calls, cmd_forms, strict=True):
        zone = _zone(cmd["frame"])
        assert any(r["frame"] == f"*4*4002*{zone}##" and call["t"] < r["t"] < cmd["t"] for r in evt), call
        assert 1.9 < cmd["t"] - call["t"] < 2.1, (call["frame"], cmd["t"] - call["t"])
    # the listen: 485 frames in 600 s with no request pending
    listen = _evt(rows, RUN4)
    assert len(listen) == 485 and listen[0]["step"] == "listen"
    assert [r["frame"] for r in listen if r["frame"].startswith("*#13")] == ["*#13**#1*00*25*10*2026##", "*#13**#0*13*00*00*##"]
    who4 = [r["frame"] for r in listen if r["frame"].startswith(("*#4*", "*4*"))]
    assert sorted(int(f.split("*")[3].rstrip("#")) for f in who4 if re.fullmatch(r"\*4\*1\*\d+##", f)) == [z for z in ZONES if z not in NO_ZONE], "the sweep's heating season frame for every zone"
    for dim in (12, 14, 13):
        assert sorted(_zone(f) for f in who4 if re.fullmatch(rf"\*#4\*\d+\*{dim}\*.*", f)) == [z for z in ZONES if z not in NO_ZONE]
    assert sum(1 for f in who4 if re.fullmatch(r"\*#4\*\d+\*7\*1\*1\*\d{4}##", f)) == 47, "setpoints pushed as dimension 7"
    assert sum(1 for f in who4 if re.fullmatch(r"\*#4\*\d+\*0\*\d{4}##", f)) == 82
    assert sorted(_zone(f) for f in who4 if re.fullmatch(r"\*#4\*\d+\*60\*\d+##", f)) == list(HUMIDITY_ZONES), "humidity pushed by the 17 zones with a probe"
    assert sum(1 for f in who4 if re.fullmatch(r"\*#4\*\d+\*5\*0##", f)) == 17
    assert sum(1 for r in listen if r["frame"].startswith("*1*")) == 167 and sum(1 for r in listen if r["frame"].startswith("*#1*")) == 1
    assert [f for f in who4 if re.search(r"\*#?20\*", f)] == ["*#4*70#1*#20*0##", "*#4*70#1*20*0##", "*#4*51#1*#20*0##", "*#4*51#1*20*0##", "*#4*46#1*#20*0##", "*#4*46#1*20*0##", "*#4*43#1*#20*0##", "*#4*43#1*20*0##"]


# --------------------------------------------------------------- entity replay


@pytest.mark.asyncio
async def test_replay_actuator_burst_gives_idle_and_pumps_reach_no_zone(hass: HomeAssistant, rows: list[dict]) -> None:
    """Zones 35 (valve) and 47 (valve + fan coil) through the climate entities on the base branch: the burst
    leaves both idle with every actuator off; the pump frames 0#1-0#4 are delivered to no entity."""
    entities = await _setup_zones(hass, (35, 47))
    delivered = _route(entities, _evt(rows, RUN1))
    assert [f for f, _ in delivered] == ["*#4*47#2*20*5##", "*#4*35#1*20*0##", "*#4*47#1*20*0##", "*#4*35#1*20*0##"]
    assert not any(f.startswith("*#4*0#") for f, _ in delivered)
    assert len(entities) == 2
    valve = entities["35"]
    assert valve.hvac_action == HVACAction.IDLE and valve._actuator_states == {"1": False}
    assert "fan_mode" not in valve.extra_state_attributes
    fan_coil = entities["47"]
    assert fan_coil.hvac_action == HVACAction.IDLE and fan_coil._actuator_states == {"2": False, "1": False}
    assert fan_coil.extra_state_attributes["fan_mode"] == "auto" and fan_coil.extra_state_attributes["running_fan_speed"] == "off"
    for zone, expected in ((valve, {"1": "off"}), (fan_coil, {"1": "off", "2": "off"})):
        if "actuators" in zone.extra_state_attributes:  # exposed by #792; the base branch keeps the states internal
            assert zone.extra_state_attributes["actuators"] == expected
    # the per-zone reads of run 2: dimension 19 and 20 keep idle, the dimension 11 NACK brings nothing,
    # humidity only on zone 47
    _route(entities, [r for r in _evt(rows, RUN2) if r["step"] in ("zone_35", "zone_47")])
    assert valve.hvac_action == HVACAction.IDLE and valve.current_humidity is None
    assert valve.extra_state_attributes["fan_coil_heating"] is False and valve.extra_state_attributes["fan_coil_cooling"] is False
    assert fan_coil.hvac_action == HVACAction.IDLE and fan_coil.current_humidity == 58.0
    assert fan_coil.extra_state_attributes["fan_coil_cooling"] is True and fan_coil.extra_state_attributes["fan_coil_heating"] is False
    assert fan_coil._actuator_states == {"2": False, "1": False, "valve": False}, "dimension 19 adds the valve key"
    # the listen: the house's sweep gives both zones their season, setpoint and last temperature, nothing switches on
    _route(entities, _evt(rows, RUN4))
    for zone, temperature in ((valve, 22.4), (fan_coil, 23.6)):
        assert zone.hvac_mode == HVACMode.HEAT and zone.hvac_action == HVACAction.IDLE
        assert zone.extra_state_attributes["season"] == "heating"
        assert zone.current_temperature == temperature
    assert valve.target_temperature == 20.0 and fan_coil.target_temperature == 23.0, "dimension 12 of the sweep"
