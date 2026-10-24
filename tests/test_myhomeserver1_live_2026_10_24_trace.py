"""Verification tests for the 2026-10-24 live MyHomeServer1 (firmware 3.87.13) status-read capture.

Fixture:
  tests/fixtures/traces/myhomeserver1_live_2026-10-24/myhome_trace_MyHomeServer1_live_2026-10-24.json

A second gateway family for the facts behind OWNd #137, #139, #140, #141, #142 and MyHOME
#791-#794, #798: a large home plant (WHO 4 zones 35-70, about 100 WHO 1 points including
four-digit area-0 points 0010-0015). Only status reads were sent. Every test pins a fact of the
trace itself, so the module passes with the pinned OWNd. The firmware oracle emulates
MyHomeServer1 2.82.06; this unit runs 3.87.13, and *#13**12## answers a real MAC here (synthetic
in the fixture) where the emulated 2.82.06 returns zeros (oracle PR #59).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from OWNd.message import OWNCommand, OWNMessage

MAC = "00:03:50:00:20:24"
MAC_FRAME = "*#13**12*0*3*80*0*32*36##"
MODEL_NAME_FRAME = "*#13**27*77#121#72#111#109#101#83#101#114#118#101#114#49##"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "myhomeserver1_live_2026-10-24"
    / "myhome_trace_MyHomeServer1_live_2026-10-24.json"
)

RUN1 = "run1_identify"
RUN2 = "run2_reads_thermo_sound_alarm_lights"
RUN3 = "run3_reads_lights_addresses_humidity"


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


def _result(rows: list[dict], run: str, frame: str) -> dict:
    found = [r for r in rows if r["dir"] == "result" and r["run"] == run and r["frame"] == frame]
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _evt(rows: list[dict], run: str) -> list[dict]:
    return [r for r in rows if r["run"] == run and r["dir"] == "rx" and r["session"] == "evt"]


# --------------------------------------------------------------- schema, privacy, parsing


def test_schema_runs_and_synthetic_identity(trace_payload: dict, rows: list[dict], frames: list[dict]) -> None:
    gw = trace_payload["gateway"]
    assert gw["model"] == "MyHomeServer1"
    assert gw["firmware"] == "3.87.13"
    assert gw["ip"] == "192.0.2.9"
    assert gw["mac"] == MAC
    assert gw["identification"] == {
        "model": "MyHomeServer1",
        "who13_code": "200",
        "who13_model_name": "MyHomeServer1",
        "who1013_code": "67",
    }
    assert "2.82.06" in gw["oracle_note"]
    assert trace_payload["capture"]["kind"] == "derived_trace_archive"
    assert [r["run"] for r in trace_payload["capture"]["runs"]] == [RUN1, RUN2, RUN3]
    assert trace_payload["capture"]["window"]["frames"] == len(frames) == 376
    assert trace_payload["capture"]["window"]["rows"] == len(rows) == 613
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [f["raw"] for f in frames]
    # only status reads were sent
    assert all(r["frame"].startswith("*#") for r in rows if r["dir"] == "tx")
    text = json.dumps(trace_payload)
    # every MAC-like string and every WHO 13 dimension 12 frame in the archive is the synthetic one
    assert set(re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)) == {MAC}
    assert {f["raw"] for f in frames if f["raw"].startswith("*#13**12*")} <= {MAC_FRAME}
    assert "192*168" not in text and "192.168" not in text
    assert MAC_FRAME in text
    assert "00:03:50:00:10:23" not in text, "a different synthetic MAC than the F461 fixtures"


def test_every_frame_parses_in_ownd_except_the_pending_model_name_decoder(rows: list[dict], frames: list[dict]) -> None:
    for item in frames:
        parsed = OWNMessage.parse(item["raw"])
        if item["raw"] == MODEL_NAME_FRAME:
            continue  # no decoder in the pinned OWNd (OWNd#139); it must not raise
        assert parsed is not None, f"Failed to parse evt frame: {item['raw']}"
    for r in rows:
        if r["dir"] == "tx":
            assert OWNCommand.parse(r["frame"]) is not None or OWNMessage.parse(r["frame"]) is not None, r
        elif r["dir"] == "rx" and r["frame"] not in ("*#*1##", "*#*0##", MODEL_NAME_FRAME):
            assert OWNMessage.parse(r["frame"]) is not None, r


def test_result_rows_are_consistent_with_their_tx_and_cmd_rx_rows(rows: list[dict]) -> None:
    for run in (RUN1, RUN2, RUN3):
        run_rows = [r for r in rows if r["run"] == run]
        for i, r in enumerate(run_rows):
            if r["dir"] != "result":
                continue
            tx = next(x for x in reversed(run_rows[:i]) if x["dir"] == "tx")
            assert tx["frame"] == r["frame"] and tx["t"] <= r["t"]
            assert abs((r["t"] - tx["t"]) - r["reply_time_s"]) < 0.002
            cmd_rx = [x["frame"] for x in run_rows[:i] if x["dir"] == "rx" and x["session"] == "cmd" and x["t"] == r["t"]]
            assert cmd_rx == [*r["replies"], "*#*1##" if r["ack"] else "*#*0##"]


# --------------------------------------------------------------- WHO 13 / 1013 identification


def test_gateway_identification_firmware_3_87_13_and_model_name(rows: list[dict], frames: list[dict]) -> None:
    assert _result(rows, RUN1, "*#13**15##")["replies"] == ["*#13**15*200##"]
    assert _result(rows, RUN1, "*#13**16##")["replies"] == ["*#13**16*3*87*13##"]
    assert _result(rows, RUN1, "*#1013**1##")["replies"] == ["*#1013**1*67*15*5*0##"]
    assert _result(rows, RUN1, "*#13**1##")["replies"] == ["*#13**1*06*24*10*2026##"]
    datetime_reply = _result(rows, RUN1, "*#13**22##")["replies"]
    assert len(datetime_reply) == 1 and re.fullmatch(r"\*#13\*\*22\*\d\d\*\d\d\*\d\d\*999\*06\*24\*10\*2026##", datetime_reply[0])
    # the MAC is answered (a real one on 3.87.13, synthetic here; the emulated 2.82.06 answers zeros)
    mac = _result(rows, RUN1, "*#13**12##")
    assert mac["ack"] is True and mac["replies"] == [MAC_FRAME] and MAC_FRAME in mac["events_after"]
    msg = OWNMessage.parse(MAC_FRAME)
    assert msg is not None and msg.human_readable_log == f"Gateway's MAC address is: {MAC}."
    # dimension 27: the model name as ASCII codes, no decoder in the pinned OWNd
    res = _result(rows, RUN1, "*#13**27##")
    assert res["ack"] is True and res["replies"] == [MODEL_NAME_FRAME] and MODEL_NAME_FRAME in res["events_after"]
    codes = MODEL_NAME_FRAME[len("*#13**27*") : -2].split("#")
    assert "".join(chr(int(c)) for c in codes) == "MyHomeServer1"
    assert [f["raw"] for f in frames if f["raw"].startswith("*#13**27*")] == [MODEL_NAME_FRAME]
    # every reply of run1 is also delivered on the event session; the plant's own WHO 4 traffic runs in between
    for r in [x for x in rows if x["run"] == RUN1 and x["dir"] == "result"]:
        assert r["ack"] is True and r["reply_time_s"] < 0.2 and r["replies"][0] in r["events_after"], r["frame"]
    own_traffic = [r["frame"] for r in _evt(rows, RUN1) if r["frame"].startswith("*#4*")]
    assert len(own_traffic) == 106


def test_who13_time_request_is_echoed_on_the_event_session(rows: list[dict]) -> None:
    for run, reply in ((RUN1, "*#13**0*16*19*49*999##"), (RUN3, "*#13**0*16*22*38*999##")):
        res = _result(rows, run, "*#13**0##")
        assert res["ack"] is True and res["reply_time_s"] < 0.1
        assert res["replies"] == [reply] and reply in res["events_after"]
        echo = [r for r in _evt(rows, run) if r["frame"] == reply]
        assert len(echo) == 1 and echo[0]["ownd_class"] == "OWNGatewayEvent"


# --------------------------------------------------------------- WHO 4 thermoregulation


def test_general_and_central_unit_forms(rows: list[dict]) -> None:
    """*#4*0## is refused at once; *#4*#0## and *#4*#0*14## are forwarded, unanswered and retried once."""
    general = _result(rows, RUN3, "*#4*0##")
    assert general["ack"] is False and general["reply_time_s"] < 0.1 and general["replies"] == []
    for frame in ("*#4*#0##", "*#4*#0*14##"):
        res = _result(rows, RUN2, frame)
        assert res["ack"] is False and res["replies"] == [] and res["events_after"] == []
        assert 4.0 < res["reply_time_s"] < 4.5, frame
        assert [r for r in rows if r["run"] == RUN2 and r["dir"] == "tx" and r["frame"] == frame][0]["is_status_request"] is False


def test_zone_35_status_reply_order_and_dimensions(rows: list[dict]) -> None:
    """*#4*35## answers dimension 0, 12, the season, 14, 13 in that order, like the F461."""
    res = _result(rows, RUN2, "*#4*35##")
    assert res["ack"] is True and 1.2 < res["reply_time_s"] < 1.5
    assert res["replies"] == [
        "*#4*35*0*0231##",
        "*#4*35*12*0200*3##",
        "*4*1*35##",
        "*#4*35*14*0200*3##",
        "*#4*35*13*00##",
    ]
    assert res["events_after"] == res["replies"]
    assert _result(rows, RUN2, "*#4*35*12##")["replies"] == ["*#4*35*12*0200*3##", "*4*1*35##"]
    assert _result(rows, RUN2, "*#4*35*14##")["replies"] == ["*#4*35*14*0200*3##"]
    season = OWNMessage.parse("*4*1*35##")
    assert season is not None and season.who == 4 and season.zone == 35


def test_actuator_status_per_zone_and_all_zones(rows: list[dict]) -> None:
    """*#4*35*20## -> *#4*35#1*20*0##; *#4*#0*20## is ACKed with 44 actuator frames, zone 0 included."""
    zone = _result(rows, RUN2, "*#4*35*20##")
    assert zone["ack"] is True and zone["reply_time_s"] < 0.3
    assert zone["replies"] == ["*#4*35#1*20*0##"] and zone["events_after"] == ["*#4*35#1*20*0##"]
    all_zones = _result(rows, RUN2, "*#4*#0*20##")
    assert all_zones["ack"] is True and all_zones["reply_time_s"] < 0.5
    assert len(all_zones["replies"]) == 8 and set(all_zones["replies"]) <= set(all_zones["events_after"])
    actuators = all_zones["events_after"]
    assert len(actuators) == 43 and len(set(actuators)) == 43
    assert all(re.fullmatch(r"\*#4\*\d+#[1-4]\*20\*[05]##", f) for f in actuators)
    assert {f for f in actuators if f.startswith("*#4*0#")} == {
        "*#4*0#1*20*0##",
        "*#4*0#2*20*0##",
        "*#4*0#3*20*0##",
        "*#4*0#4*20*0##",
    }
    fan_coils = sorted(f for f in actuators if "#2*20*5##" in f)
    assert fan_coils == ["*#4*47#2*20*5##", "*#4*48#2*20*5##", "*#4*57#2*20*5##", "*#4*60#2*20*5##", "*#4*70#2*20*5##"]
    assert len({f for f in actuators if "#1*20*0##" in f and not f.startswith("*#4*0#")}) == 34
    for raw in ("*#4*0#1*20*0##", "*#4*35#1*20*0##", "*#4*47#2*20*5##"):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.who == 4 and msg.is_active() is False


def test_humidity_per_zone_only_where_a_probe_exists_and_general_sweep(rows: list[dict]) -> None:
    """*#4*35*60## times out (no probe); *#4*0*60## answers 17 zones; *#4*39*60## answers directly."""
    none = _result(rows, RUN2, "*#4*35*60##")
    assert none["ack"] is False and 2.0 < none["reply_time_s"] < 2.3 and none["events_after"] == []
    sweep = _result(rows, RUN2, "*#4*0*60##")
    assert sweep["ack"] is True and sweep["reply_time_s"] < 0.6
    humid = sweep["events_after"]
    assert len(humid) == 17 and len(set(humid)) == 17
    assert all(re.fullmatch(r"\*#4\*\d+\*60\*\d+##", f) for f in humid)
    assert sorted(int(f.split("*")[2]) for f in humid) == [39, 40, 41, 42, 43, 47, 48, 53, 55, 57, 58, 59, 60, 61, 68, 69, 70]
    assert set(sweep["replies"]) <= set(humid) and len(sweep["replies"]) == 8
    zone = _result(rows, RUN3, "*#4*39*60##")
    assert zone["ack"] is True and zone["reply_time_s"] < 0.3 and zone["replies"] == ["*#4*39*60*51##"]
    msg = OWNMessage.parse("*#4*39*60*51##")
    assert msg is not None and msg.zone == 39 and msg.main_humidity == 51.0


def test_the_plant_pushes_setpoints_as_dimension_7_and_temperatures(frames: list[dict]) -> None:
    """The home plant broadcasts *#4*Z*7*1*1*T## setpoints and *#4*Z*0*T## temperatures on its own."""
    raws = [f["raw"] for f in frames]
    dim7 = {r for r in raws if re.fullmatch(r"\*#4\*\d+\*7\*1\*1\*\d{4}##", r)}
    assert len(dim7) >= 30 and "*#4*35*7*1*1*0200##" in dim7
    dim0 = {r for r in raws if re.fullmatch(r"\*#4\*\d+\*0\*\d{4}##", r)}
    assert len(dim0) == 33
    zones = sorted({int(r.split("*")[2]) for r in dim7 | dim0})
    assert zones[0] == 35 and zones[-1] == 70
    for raw in ("*#4*35*7*1*1*0200##", "*#4*35*0*0231##", "*#4*53*5*0##"):
        assert OWNMessage.parse(raw) is not None, raw


# --------------------------------------------------------------- WHO 16 / WHO 5 refused


def test_who16_and_who5_requests_are_refused_locally(rows: list[dict]) -> None:
    for frame in ("*#16*0*5##", "*#16*101*5##"):
        res = _result(rows, RUN2, frame)
        assert res["ack"] is False and res["reply_time_s"] <= 0.005 and res["replies"] == [], frame
    alarm = _result(rows, RUN2, "*#5*0##")
    assert alarm["ack"] is False and alarm["reply_time_s"] <= 0.01 and alarm["events_after"] == []


# --------------------------------------------------------------- WHO 1 / WHO 2 general status and address spellings


def test_general_light_status_lists_four_digit_and_two_digit_points(rows: list[dict]) -> None:
    """*#1*0## answers about 100 point statuses (74 distinct points) in 4.7 s; *#2*0## ACKs with no data."""
    res = _result(rows, RUN2, "*#1*0##")
    assert res["ack"] is True and 4.5 < res["reply_time_s"] < 5.0
    assert len(res["replies"]) == 100
    points = {r.split("*")[3][:-2] for r in res["replies"]}
    assert len(points) == 74
    assert all(re.fullmatch(r"\*1\*[01]\*\d{2,4}##", r) for r in res["replies"])
    assert {"0010", "0011", "0012", "0013", "0014", "0015"} <= points, "four-digit area-0 points"
    assert {"0111", "0215", "0515"} <= points, "four-digit area 1-5 points"
    assert {"11", "14", "69"} <= points, "two-digit points"
    assert "0001" not in points and "0004" not in points and "01" not in points and "04" not in points
    cover = _result(rows, RUN2, "*#2*0##")
    assert cover["ack"] is True and cover["replies"] == [] and 4.0 < cover["reply_time_s"] < 4.6
    assert cover["events_after"] and all(f.startswith("*1*") for f in cover["events_after"])


def test_four_digit_area0_points_are_devices_distinct_from_the_two_digit_ones(rows: list[dict]) -> None:
    """0014 and 14 are two devices; 0010 exists and 10 does not; 0001 / 0004 are refused at once."""
    assert _result(rows, RUN3, "*#1*0014##")["replies"] == ["*1*0*0014##"]
    assert _result(rows, RUN3, "*#1*14##")["replies"] == ["*1*0*14##"]
    assert _result(rows, RUN3, "*#1*0010##")["replies"] == ["*1*0*0010##"]
    ten = _result(rows, RUN3, "*#1*10##")
    assert ten["ack"] is False and 1.9 < ten["reply_time_s"] < 2.2
    for frame, limit in (("*#1*0001##", 0.01), ("*#1*0004##", 0.02)):
        res = _result(rows, RUN3, frame)
        assert res["ack"] is False and res["reply_time_s"] <= limit and res["events_after"] == [], frame
    for frame in ("*#1*01##", "*#1*04##"):
        res = _result(rows, RUN3, frame)
        assert res["ack"] is False and 1.9 < res["reply_time_s"] < 2.2, frame
    for raw, where in (("*1*0*0014##", "0014"), ("*1*0*14##", "14"), ("*1*0*0010##", "0010")):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.who == 1 and msg.where == where
