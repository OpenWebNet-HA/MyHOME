"""Replay and verification tests for the 2026-10-23 live BTicino F461 capture.

Fixture:
  tests/fixtures/traces/f461_live_2026-10-23/myhome_trace_F461_live_2026-10-23.json

The capture is the physical evidence behind OWNd #136-#142 and MyHOME #791-#798. Every
test here pins a fact of the trace itself (a frame present, an ACK / NACK, a reply order,
a timing) or replays event-session frames through entities whose behaviour does not depend
on those pull requests, so the module passes on the base branch with the pinned OWNd.

What the plant said (F461 2.0.11, WHO 13 device type 200, WHO 1013 134.15.5.0, no central
unit, no WHO 16 / 22 devices):

- ``*#4*2##`` answers dimension 0, 12, the season, 14, 13 in that order and dimension 12
  (23.5, the setpoint in force) differs from dimension 14 (21.0, the last commanded value).
- ``*#4*2*20##`` is followed by two actuator frames on the event session; ``*#4*#0*20##``
  returns both on the command session in 2.1 s.
- ``*#1*0007##`` is refused in 7 ms, ``*#1*07##`` is answered, ``*#1*7##`` is area 7 (empty).
- ``*#13**0##`` sent on the command session is also delivered on the event session.
- all 18 WHO 16 requests, ``*#4*0##``, ``*#5*0##`` are refused locally (under 10 ms);
  ``*#4*#2##``, ``*#4*#2*14##``, ``*#4*#0*14##`` are refused after the bus timeout.
- ``*#13**27##`` answers ``*#13**27*70#52#54#49##`` ("F461" as ASCII codes).
- ``*2*0*0##`` was seen exactly once (one general stop from Home Assistant).
- the F411/2 shutter at 05 stops itself 5.1-5.9 s after a run starts.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from OWNd.profiles import F461Profile

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, CONF_WHERE, DOMAIN
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:10:23"
MAC_FRAME = "*#13**12*0*3*80*0*16*35##"
MODEL_NAME_FRAME = "*#13**27*70#52#54#49##"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_live_2026-10-23"
    / "myhome_trace_F461_live_2026-10-23.json"
)

RUN1 = "run1_oracle_replay_readonly"
RUN2 = "run2_reviewer_reads"
RUN3 = "run3_climate_setpoint_restore"
RUN4 = "run4_final_state"
RUN4B = "run4b_final_state_after_restore"
RUN5 = "run5_injection_flags"
RUN6 = "run6_event_session_ha_fixed_integration"
RUN7 = "run7_localha_bus_monitor"
RUN8 = "run8_flicker_repro"
RUN8B = "run8b_flicker_repro_who22"
ALL_RUNS = (RUN1, RUN2, RUN3, RUN4, RUN4B, RUN5, RUN6, RUN7, RUN8, RUN8B)


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    """Load the 2026-10-23 F461 live trace archive."""
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
    return [
        r
        for r in rows
        if r["dir"] == "result"
        and (run is None or r["run"] == run)
        and (frame is None or r["frame"] == frame)
    ]


def _result(rows: list[dict], run: str, frame: str) -> dict:
    found = _results(rows, run, frame)
    assert len(found) == 1, (run, frame, len(found))
    return found[0]


def _evt(rows: list[dict], run: str) -> list[dict]:
    return [r for r in rows if r["run"] == run and r["dir"] == "rx" and r["session"] == "evt"]


def _evt_between(rows: list[dict], run: str, frame: str) -> list[str]:
    """Event-session frames from the request ``frame`` of ``run`` until the next request of that run."""
    run_rows = [r for r in rows if r["run"] == run]
    start = next(i for i, r in enumerate(run_rows) if r["dir"] == "tx" and r["frame"] == frame)
    out: list[str] = []
    for r in run_rows[start + 1 :]:
        if r["dir"] == "tx":
            break
        if r["dir"] == "rx" and r["session"] == "evt":
            out.append(r["frame"])
    return out


def _gateway() -> MagicMock:
    gw = MagicMock()
    gw.mac = MAC
    gw.unique_id = MAC
    gw.bus_group = MAC
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.log_id = "[F461 live 2026-10-23]"
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


async def _setup_plant(hass: HomeAssistant, gw: MagicMock) -> dict[str, object]:
    from custom_components.myhome.climate import async_setup_entry as climate_setup
    from custom_components.myhome.cover import async_setup_entry as cover_setup
    from custom_components.myhome.light import async_setup_entry as light_setup

    platforms_cfg = {
        "light": {
            "light_06": {CONF_WHERE: "06", CONF_NAME: "Light 06", "model": "F411"},
            "light_07": {CONF_WHERE: "07", CONF_NAME: "Light 07", "model": "F411"},
            "light_09": {CONF_WHERE: "09", CONF_NAME: "Light 09", "model": "F411"},
        },
        "cover": {
            "cover_05": {
                CONF_WHERE: "05",
                CONF_NAME: "Cover 05",
                "advanced": False,
                "open_Best_Time": 6,
                "close_Best_Time": 6,
            },
        },
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
    entry.entry_id = "f461_live_2026_10_23"
    entry.options = {}
    attach_runtime(hass, entry, MAC, gw)

    lights: list = []
    covers: list = []
    climates: list = []
    with (
        patch(
            "custom_components.myhome.discovery.er.async_entries_for_config_entry",
            return_value=[],
        ),
        patch("custom_components.myhome.discovery.er.async_get", return_value=MagicMock()),
    ):
        await light_setup(hass, entry, lights.extend)
        await cover_setup(hass, entry, covers.extend)
        await climate_setup(hass, entry, climates.extend)

    for ent in lights + covers + climates:
        ent.hass = hass
        domain = "climate" if ent in climates else ("cover" if ent in covers else "light")
        ent.entity_id = f"{domain}.{ent._device_id}"
        ent.async_write_ha_state = MagicMock()
        ent.async_schedule_update_ha_state = MagicMock()
        attach_platform(ent, entry)

    return {ent._where: ent for ent in lights + covers + climates}


# --------------------------------------------------------------- schema, privacy, parsing


def test_schema_runs_and_synthetic_identity(trace_payload: dict, rows: list[dict], frames: list[dict]) -> None:
    """Fixture metadata, RFC 5737 / synthetic identity, run names and counts."""
    gw = trace_payload["gateway"]
    assert gw["model"] == "F461"
    assert gw["firmware"] == "2.0.11"
    assert gw["ip"] == "192.0.2.92"
    assert gw["mac"] == MAC
    assert gw["identification"] == {
        "model": "F461",
        "who13_code": "200",
        "who13_model_name": "F461",
        "who1013_code": "134",
    }
    assert trace_payload["capture"]["kind"] == "derived_trace_archive"
    assert [r["run"] for r in trace_payload["capture"]["runs"]] == list(ALL_RUNS)
    assert [r["t_offset"] for r in trace_payload["capture"]["runs"]][0] == 0.0
    ts = [f["timestamp"] for f in frames]
    assert ts == sorted(ts), "one monotonic time axis across the runs"
    assert all(a["t"] <= b["t"] for a, b in zip(rows, rows[1:], strict=False))
    assert trace_payload["capture"]["window"] == {
        "first": "2026-10-23T20:38:04+00:00",
        "last": "2026-10-23T23:03:07+00:00",
        "frames": 299,
        "rows": 939,
    }
    assert len(frames) == 299
    assert len(rows) == 939
    assert {r["run"] for r in rows} == set(ALL_RUNS)
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    # the event rows of the log are the frames array, in the same order
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [f["raw"] for f in frames]
    # the real MAC and LAN address were rewritten in both their text and their WHO 13 byte forms
    text = json.dumps(trace_payload)
    # every MAC-like string and every WHO 13 dimension 12 frame in the archive is the synthetic one
    assert set(re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)) == {MAC}
    assert {f["raw"] for f in frames if f["raw"].startswith("*#13**12*")} <= {MAC_FRAME}
    assert "192*168" not in text and "192.168" not in text
    assert MAC_FRAME in text and "*#13**10*192*0*2*92##" in text
    assert "00:03:50:00:04:97" not in text, "a different synthetic MAC than the 2026-10-15 / 10-18 fixtures"


def test_every_frame_parses_in_ownd_except_the_pending_model_name_decoder(rows: list[dict], frames: list[dict]) -> None:
    """Every rx frame parses in the pinned OWNd; the WHO 13 dimension 27 reply is the one exception."""
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
    """Each result row follows its tx row, its replies are on the command session, ACK / NACK is logged."""
    for run in ALL_RUNS:
        run_rows = [r for r in rows if r["run"] == run]
        for i, r in enumerate(run_rows):
            if r["dir"] != "result":
                continue
            tx_i = next(j for j in range(i - 1, -1, -1) if run_rows[j]["dir"] == "tx")
            tx = run_rows[tx_i]
            assert tx["frame"] == r["frame"]
            assert tx["t"] <= r["t"]
            assert abs((r["t"] - tx["t"]) - r["reply_time_s"]) < 0.002
            # the command-session replies sit right after their request, at its t, flagged as estimated
            cmd_rx = [x for x in run_rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "cmd"]
            assert [x["frame"] for x in cmd_rx] == [*r["replies"], "*#*1##" if r["ack"] else "*#*0##"]
            assert all(x["t"] == tx["t"] and x["t_estimated"] is True for x in cmd_rx)
            assert run_rows[tx_i + 1 : tx_i + 1 + len(cmd_rx)] == cmd_rx
            # events_after holds exactly the event-session frames that arrived while the request was pending
            pending = [x["frame"] for x in run_rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "evt" and x["t"] <= r["t"]]
            assert r["events_after"] == pending
    assert all(r["ack"] is False and r["replies"] == [] for r in _results(rows) if not r["ack"])


# --------------------------------------------------------------- WHO 13 / 1013 identification


def test_gateway_identification_and_model_name_dimension_27(rows: list[dict], frames: list[dict]) -> None:
    """WHO 13 device type 200, firmware 2.0.11, WHO 1013 object model 134.15.5.0, dimension 27 model name."""
    assert _result(rows, RUN1, "*#13**15##")["replies"] == ["*#13**15*200##"]
    assert _result(rows, RUN1, "*#13**16##")["replies"] == ["*#13**16*2*0*11##"]
    assert _result(rows, RUN1, "*#13**23##")["replies"] == ["*#13**23*5*10*35##"]
    assert _result(rows, RUN1, "*#13**24##")["replies"] == ["*#13**24*2*26*4##"]
    assert _result(rows, RUN1, "*#1013*0*1##")["replies"] == ["*#1013**1*134*15*5*0##"]
    assert _result(rows, RUN1, "*#1013*0##")["ack"] is False
    # the F461 answers its MAC and IP (synthetic values in the fixture)
    assert _result(rows, RUN1, "*#13**12##")["replies"] == [MAC_FRAME]
    assert _result(rows, RUN1, "*#13**10##")["replies"] == ["*#13**10*192*0*2*92##"]
    # dimension 27: the model name as ASCII codes, also delivered on the event session
    res = _result(rows, RUN1, "*#13**27##")
    assert res["ack"] is True
    assert res["replies"] == [MODEL_NAME_FRAME]
    assert res["events_after"] == [MODEL_NAME_FRAME]
    assert [f["raw"] for f in frames if f["raw"].startswith("*#13**27*")] == [MODEL_NAME_FRAME]
    codes = MODEL_NAME_FRAME[len("*#13**27*") : -2].split("#")
    assert "".join(chr(int(c)) for c in codes) == "F461"
    # dimensions 2, 3, 25, 26 are refused locally
    for dim in ("2", "3", "25", "26"):
        res = _result(rows, RUN1, f"*#13**{dim}##")
        assert res["ack"] is False and res["reply_time_s"] < 0.05, dim
    # WHO 1001 is not answered on this plant (no WHO 1 fault / diagnostic device)
    assert _result(rows, RUN1, "*#1001*74##")["ack"] is False


def test_who13_time_request_is_echoed_on_the_event_session(rows: list[dict]) -> None:
    """*#13**0## sent on the command session is delivered on the event session too (liveness probe)."""
    for run in (RUN1, RUN2):
        res = _result(rows, run, "*#13**0##")
        assert res["ack"] is True
        assert len(res["replies"]) == 1 and res["replies"][0].startswith("*#13**0*")
        assert res["events_after"] == res["replies"]
        echo = [r for r in _evt(rows, run) if r["frame"] == res["replies"][0]]
        assert len(echo) == 1 and echo[0]["ownd_class"] == "OWNGatewayEvent"
    # the spontaneous MAC and object-model frames open the event session (first two event rows of run6)
    first_two = [r["frame"] for r in _evt(rows, RUN6)[:2]]
    assert first_two == [MAC_FRAME, "*#1013**1*134*15*5*0##"]
    # every WHO 13 clock echo on the event session carries an empty time-zone field on this F461
    clock_echoes = [r["frame"] for r in _evt(rows, RUN6) if r["frame"].startswith("*#13**#0*")]
    assert clock_echoes == ["*#13**#0*01*40*00*##", "*#13**#0*01*50*00*##", "*#13**#0*02*00*00*##"]
    assert all(OWNMessage.parse(f) is not None for f in clock_echoes)


# --------------------------------------------------------------- WHO 1 address spellings


def test_area0_point_spellings_0007_nack_07_ack_7_is_area_7(rows: list[dict]) -> None:
    """Four-digit area-0 points are refused locally; two digits answer; one digit is an area."""
    four = _result(rows, RUN2, "*#1*0007##")
    assert four["ack"] is False and four["reply_time_s"] < 0.05 and four["replies"] == []
    two = _result(rows, RUN2, "*#1*07##")
    assert two["ack"] is True and two["replies"] == ["*1*0*07##"] and two["reply_time_s"] < 0.2
    one = _result(rows, RUN2, "*#1*7##")
    assert one["ack"] is True and one["replies"] == [] and 0.9 < one["reply_time_s"] < 1.2
    for frame in ("*#1*0001##", "*#1*0002##", "*#1*0002#4#01##"):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is False and res["reply_time_s"] < 0.05, frame
    # 0010 / 0015 (area 1) are forwarded to the bus and time out there instead
    for frame in ("*#1*0010##", "*#1*0015##", "*#1*02##", "*#1*31##"):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is False and 1.8 < res["reply_time_s"] < 2.5, frame
    # the plant's own points answer with the two-digit WHERE
    assert _result(rows, RUN1, "*#1*06##")["replies"] == ["*1*0*06##"]
    assert _result(rows, RUN1, "*#1*07##")["replies"] == ["*1*0*07##"]


def test_area0_sweep_reports_09_but_the_point_behind_the_f422_does_not_answer(rows: list[dict]) -> None:
    """*#1*0## lists 06, 07 and 09; *#1*09## times out on the bus (actuator without configurator 2)."""
    for frame in ("*#1*0##", "*#1*00##"):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is True
        assert res["replies"] == ["*1*0*06##", "*1*0*07##", "*1*0*09##"], frame
    nine = _result(rows, RUN1, "*#1*09##")
    assert nine["ack"] is False and 1.8 < nine["reply_time_s"] < 2.5
    # dimension 15 is group-only: refused on a point, answered (and echoed) on group 1
    assert _result(rows, RUN1, "*#1*06*15##")["ack"] is False
    group = _result(rows, RUN1, "*#1*#1*15##")
    assert group["ack"] is True and group["events_after"] == ["*#1*#1*15##"]


# --------------------------------------------------------------- WHO 4 climate


def test_zone_status_reply_order_and_dimension_12_differs_from_14(rows: list[dict]) -> None:
    """*#4*2## answers 0, 12, season, 14, 13; dimension 12 (in force) is not dimension 14 (commanded)."""
    res = _result(rows, RUN1, "*#4*2##")
    assert res["ack"] is True
    assert res["replies"] == [
        "*#4*2*0*0239##",
        "*#4*2*12*0235*3##",
        "*4*1*2##",
        "*#4*2*14*0210*3##",
        "*#4*2*13*00##",
    ]
    assert res["events_after"] == res["replies"]
    assert 1.2 < res["reply_time_s"] < 1.5
    assert _result(rows, RUN1, "*#4*2*12##")["replies"] == ["*#4*2*12*0235*3##", "*4*1*2##"]
    assert _result(rows, RUN1, "*#4*2*14##")["replies"] == ["*#4*2*14*0210*3##"]
    assert _result(rows, RUN1, "*#4*2*0##")["replies"] == ["*#4*2*0*0239##"]
    assert _result(rows, RUN1, "*#4*2*13##")["replies"] == ["*#4*2*13*00##"]
    assert _result(rows, RUN1, "*#4*2*19##")["replies"] == ["*#4*2*19*5*0##"]
    # the fan-speed read is refused on the valve side in heating season (after the bus timeout)
    fan = _result(rows, RUN1, "*#4*2*11##")
    assert fan["ack"] is False and fan["reply_time_s"] > 1.8
    # run4: Home Assistant had written 21.0, so both dimensions read 21.0
    assert _result(rows, RUN4, "*#4*2##")["replies"][1] == "*#4*2*12*0210*3##"
    assert _result(rows, RUN4, "*#4*2##")["replies"][3] == "*#4*2*14*0210*3##"


def test_setpoint_write_mode_3_restores_the_setpoint_in_force(rows: list[dict]) -> None:
    """*#4*2*#14*0235*3## is ACKed, dimension 12 follows at once, dimension 14 catches up, the season stays."""
    write = _result(rows, RUN3, "*#4*2*#14*0235*3##")
    assert write["ack"] is True and write["reply_time_s"] < 0.2
    assert write["events_after"] == [], "the ACK comes before the thermostat reports"
    assert _evt_between(rows, RUN3, "*#4*2*#14*0235*3##") == ["*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*0*0239##"]
    evt = [r["frame"] for r in _evt(rows, RUN3)]
    assert evt.index("*#4*2*12*0235*3##") < evt.index("*#4*2*14*0235*3##")
    after = _result(rows, RUN3, "*#4*2##")
    assert after["replies"] == [
        "*#4*2*0*0239##",
        "*#4*2*12*0235*3##",
        "*4*1*2##",
        "*#4*2*14*0235*3##",
        "*#4*2*13*00##",
    ]
    # two hours later the plant still reports 23.5 on both dimensions
    later = _result(rows, RUN4B, "*#4*2##")
    assert later["replies"][1] == "*#4*2*12*0235*3##" and later["replies"][3] == "*#4*2*14*0235*3##"


def test_actuator_status_dimension_20_per_zone_and_all_zones(rows: list[dict]) -> None:
    """*#4*2*20## is followed by two actuator frames on evt; *#4*#0*20## returns both on cmd in 2.1 s."""
    zone = _result(rows, RUN1, "*#4*2*20##")
    assert zone["ack"] is True and zone["reply_time_s"] < 0.2
    assert zone["replies"] == ["*#4*2#1*20*0##"]
    assert zone["events_after"] == ["*#4*2#1*20*0##"], "actuator 1 arrives before the ACK"
    assert _evt_between(rows, RUN1, "*#4*2*20##") == ["*#4*2#1*20*0##", "*#4*2#2*20*5##"], "actuator 2 right after it"
    all_zones = _result(rows, RUN1, "*#4*#0*20##")
    assert all_zones["ack"] is True and 2.0 < all_zones["reply_time_s"] < 2.3
    assert all_zones["replies"] == ["*#4*2#1*20*0##", "*#4*2#2*20*5##"]
    for raw, log in (("*#4*2#1*20*0##", "Zone 2's actuator 1 is off."), ("*#4*2#2*20*5##", "Zone 2's fan is off.")):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.who == 4 and msg.zone == 2
        assert msg.is_active() is False and msg.human_readable_log == log
    # the per-zone 1 / 23 / 40 / 41 forms are forwarded to the bus and time out (no such zones)
    for frame in ("*#4*1*20##", "*#4*1#1*20##", "*#4*1*0##", "*#4*40*19##"):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is False and res["reply_time_s"] > 1.8, frame


def test_actuator_frames_follow_every_zone_poll_while_the_fixed_integration_ran(rows: list[dict]) -> None:
    """run6: after each *#4*2## reply burst the two actuator frames arrive within 3 s (one *#4*#0*20##)."""
    evt = _evt(rows, RUN6)
    polls = [r["t"] for r in evt if r["frame"] == "*#4*2*13*00##"]
    assert len(polls) == 7
    for t in polls:
        following = [r["frame"] for r in evt if t < r["t"] <= t + 3.5]
        assert {"*#4*2#1*20*0##", "*#4*2#2*20*5##"} <= set(following), t
    # the manual *#4*2*20## of the local Home Assistant session (run7)
    manual = [r["frame"] for r in _evt(rows, RUN7) if r["frame"].startswith("*#4*2#")]
    assert manual == ["*#4*2#1*20*0##", "*#4*2#2*20*5##", "*#4*2#2*20*5##"]
    # a '#'-prefixed dimension 20 write echo was seen once in run6 and parses as a command
    hashed = [r["frame"] for r in evt if r["frame"] == "*#4*2#1*#20*0##"]
    assert len(hashed) == 1 and OWNMessage.parse(hashed[0]) is not None


def test_humidity_general_sweep_and_per_zone(rows: list[dict]) -> None:
    """*#4*0*60## is answered by zone 2 after 2.1 s; *#4*2*60## in 0.14 s; the thermostat pushes it too."""
    sweep = _result(rows, RUN2, "*#4*0*60##")
    assert sweep["ack"] is True and 2.0 < sweep["reply_time_s"] < 2.3
    assert sweep["replies"] == ["*#4*2*60*39##"]
    zone = _result(rows, RUN2, "*#4*2*60##")
    assert zone["ack"] is True and zone["reply_time_s"] < 0.2
    assert zone["replies"] == ["*#4*2*60*39##"]
    spontaneous = [r for r in _evt(rows, RUN6) if r["frame"].startswith("*#4*2*60*")]
    assert len(spontaneous) == 3 and all(r["frame"] == "*#4*2*60*38##" for r in spontaneous)
    msg = OWNMessage.parse("*#4*2*60*39##")
    assert msg is not None and msg.zone == 2 and msg.main_humidity == 39.0


def test_central_unit_forms_are_refused_on_a_plant_without_one(rows: list[dict]) -> None:
    """*#4*#2##, *#4*#2*14##, *#4*#0##, *#4*#0*14## time out on the bus; *#4*0## is refused locally."""
    for frame in ("*#4*#2##", "*#4*#2*14##", "*#4*#0##", "*#4*#0*14##"):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is False and res["replies"] == [] and 2.0 < res["reply_time_s"] < 2.3, frame
    general = _result(rows, RUN1, "*#4*0##")
    assert general["ack"] is False and general["reply_time_s"] < 0.05
    report = _result(rows, RUN5, "*#4*#0*0*0215##")
    assert report["ack"] is False and report["reply_time_s"] > 2.0 and report["events_after"] == []


# --------------------------------------------------------------- refused WHOs and WHO 18


def test_every_who16_request_is_refused_locally(rows: list[dict]) -> None:
    """All 18 WHO 16 forms NACK in under 10 ms: the F461 has no sound-diffusion handler."""
    who16 = [r for r in _results(rows, RUN1) if r["frame"].startswith("*#16*")]
    assert len(who16) == 18
    assert {r["frame"] for r in who16} >= {"*#16*0##", "*#16*0*5##", "*#16*101##", "*#16*101*5##", "*#16*21*1##"}
    assert all(r["ack"] is False and r["replies"] == [] and r["reply_time_s"] <= 0.01 for r in who16)
    for frame in ("*#5*0##", "*#5*#1##", "*#9*0##", "*#9*1##", "*#17*0##", "*#17*1##", "*#14*06##", "*#8*0##", "*#3*0##"):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is False and res["reply_time_s"] < 0.05, frame
    # WHO 22 is forwarded to the bus and times out (no sound devices): 1.8-2.9 s
    who22 = [r for r in _results(rows, RUN1) if r["frame"].startswith("*#22*")]
    assert len(who22) == 13 and all(not r["ack"] and 1.8 < r["reply_time_s"] < 2.9 for r in who22)
    assert 1.9 < _result(rows, RUN2, "*#22*3#1#1*1##")["reply_time_s"] < 2.1


def test_who18_energy_requests_on_a_plant_without_a_meter(rows: list[dict]) -> None:
    """Meter reads are ACKed with no data after the bus timeout; the dimension 1200 read is refused."""
    for frame, lo, hi in (("*#18*51*51##", 0.5, 0.7), ("*#18*51*113##", 0.7, 0.9), ("*#18*51*54##", 0.7, 0.9)):
        res = _result(rows, RUN1, frame)
        assert res["ack"] is True and res["replies"] == [] and lo < res["reply_time_s"] < hi, frame
    stream = _result(rows, RUN1, "*#18*51*1200##")
    assert stream["ack"] is False and stream["reply_time_s"] < 0.05
    report = _result(rows, RUN5, "*#18*51*113*100##")
    assert report["ack"] is True and report["events_after"] == [], "the report form is accepted but not echoed"


# --------------------------------------------------------------- WHO 2 cover 05 and the general stop


def test_general_stop_was_seen_exactly_once(frames: list[dict]) -> None:
    """*2*0*0## (one Home Assistant general stop) is the only general WHO 2 frame of the capture."""
    general = [f for f in frames if f["raw"] == "*2*0*0##"]
    assert len(general) == 1 and general[0]["run"] == RUN6
    msg = OWNMessage.parse("*2*0*0##")
    assert msg is not None and msg.who == 2 and msg.where == "0"
    assert not any(f["raw"].startswith("*2*") and f["raw"].endswith("*0##") and f["raw"] != "*2*0*0##" for f in frames)


def test_f411_2_shutter_05_stops_itself_after_each_run(frames: list[dict]) -> None:
    """Every run starts with the gateway's pre-move STOP burst; a full run ends 5.1-5.9 s later on the actuator's own stop."""
    starts = []
    for run in (RUN6, RUN7):
        evt = [f for f in frames if f["run"] == run]
        for i, f in enumerate(evt):
            if f["raw"] in ("*2*1*05##", "*2*2*05##"):
                translation = f"*2*1000#{f['raw'][3]}*05##"
                # the three frames before the move status: stop translation, stop status, move translation
                # (the gateway delivers the translation echoes and the actuator statuses in varying order)
                assert sorted(x["raw"] for x in evt[i - 3 : i]) == sorted(["*2*1000#0*05##", "*2*0*05##", translation]), (run, f["timestamp"])
                nxt = next(x for x in evt[i + 1 :] if x["raw"].startswith("*2*"))
                starts.append((run, f["raw"], round(nxt["timestamp"] - f["timestamp"], 3), nxt["raw"]))
    assert len(starts) == 11
    full_runs = [s for s in starts if s[3] == "*2*0*05##"]
    assert len(full_runs) == 9
    assert all(5.1 <= s[2] <= 5.9 for s in full_runs), full_runs
    # two runs (set_cover_position) were stopped by Home Assistant: after 2.45 s and 3.65 s
    partial = [s for s in starts if s[3] != "*2*0*05##"]
    assert partial == [
        (RUN6, "*2*2*05##", 3.654, "*2*1000#0*05##"),
        (RUN7, "*2*1*05##", 2.446, "*2*1000#0*05##"),
    ]


def test_cover_05_dimension_reads(rows: list[dict]) -> None:
    """The F411/2 reports no position: dimension 10 ACKs with nothing, dimension 11 is refused."""
    assert _result(rows, RUN1, "*#2*05##")["replies"] == ["*2*0*05##"]
    dim10 = _result(rows, RUN1, "*#2*05*10##")
    assert dim10["ack"] is True and dim10["replies"] == [] and dim10["reply_time_s"] > 1.0
    assert _result(rows, RUN1, "*#2*05*11##")["ack"] is False
    assert _result(rows, RUN1, "*#2*0##")["replies"] == ["*2*0*05##"]


# --------------------------------------------------------------- the light pulse of 20:42:07 UTC


def test_light_pulse_during_run1_was_not_reproduced(rows: list[dict]) -> None:
    """A 100 ms pulse of 07 and 06 arrived during a locally refused read; re-sending the same reads showed nothing."""
    pulse = _result(rows, RUN1, "*#3*#1##")
    assert pulse["ack"] is False and pulse["reply_time_s"] < 0.01 and pulse["events_after"] == []
    assert _evt_between(rows, RUN1, "*#3*#1##") == ["*1*1*07##", "*1*0*07##", "*1*1*06##"]
    for run in (RUN8, RUN8B):
        for res in _results(rows, run):
            assert res["ack"] is False and res["events_after"] == [], res
    assert [r["frame"] for r in _results(rows, RUN8)] == ["*#25*21##", "*#3*#1##", "*#25*21##"]
    assert not _evt(rows, RUN8) and not _evt(rows, RUN8B)


# --------------------------------------------------------------- entity replays


@pytest.mark.asyncio
async def test_replay_run7_lights_and_shutter_through_the_entities(hass: HomeAssistant, rows: list[dict]) -> None:
    """The local Home Assistant session: 06 on / off, 07 on / off, three shutter runs with hardware stops."""
    entities = await _setup_plant(hass, _gateway())
    light_06, light_07, shutter = entities["06"], entities["07"], entities["05"]
    seen: list[tuple[str, bool | None, bool, bool]] = []
    for r in _evt(rows, RUN7):
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None
        if getattr(msg, "is_translation", False):
            continue
        who, where = getattr(msg, "who", None), getattr(msg, "where", None)
        if who == 1 and where in ("06", "07"):
            entities[where].handle_event(msg)
            seen.append((r["frame"], light_06.is_on, light_07.is_on is True, False))
        elif who == 2 and where == "05":
            shutter.handle_event(msg)
            seen.append((r["frame"], None, shutter.is_closing is True, shutter.is_opening is True))
    light_states = [(f, on06, on07) for f, on06, on07, _ in seen if f.startswith("*1*")]
    assert light_states == [
        ("*1*0*06##", False, False),
        ("*1*1*06##", True, False),
        ("*1*0*06##", False, False),
        ("*1*1*07##", False, True),
        ("*1*0*07##", False, False),
        ("*1*0*07##", False, False),
    ]
    cover_states = [(f, closing, opening) for f, _, closing, opening in seen if f.startswith("*2*")]
    assert cover_states == [
        ("*2*0*05##", False, False),
        ("*2*2*05##", True, False),
        ("*2*0*05##", False, False),
        ("*2*0*05##", False, False),
        ("*2*1*05##", False, True),
        ("*2*0*05##", False, False),
        ("*2*0*05##", False, False),
        ("*2*2*05##", True, False),
        ("*2*0*05##", False, False),
    ]
    assert light_06.is_on is False and light_07.is_on is False
    assert shutter.is_closing is False and shutter.is_opening is False


@pytest.mark.asyncio
async def test_replay_run4b_zone_2_through_the_climate_entity(hass: HomeAssistant, rows: list[dict]) -> None:
    """The final reads: 24.1 degrees measured, 23.5 in force and commanded, heating season, valve off."""
    entities = await _setup_plant(hass, _gateway())
    zone2 = entities["2"]
    for r in _evt(rows, RUN4B):
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None
        if getattr(msg, "who", None) == 4 and getattr(msg, "zone", None) == 2:
            zone2.handle_event(msg)
    assert zone2.current_temperature == 24.1
    assert zone2.target_temperature == 23.5
    assert zone2.hvac_mode == HVACMode.HEAT
    assert zone2.extra_state_attributes["season"] == "heating"
    assert zone2.extra_state_attributes["local_offset"] == 0
