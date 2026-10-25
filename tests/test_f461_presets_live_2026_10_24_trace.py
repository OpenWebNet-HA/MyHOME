"""Replay and verification tests for the 2026-10-24 live BTicino F461 heating comfort / eco preset capture.

Fixture:
  tests/fixtures/traces/f461_presets_live_2026-10-24/myhome_trace_F461_presets_live_2026-10-24.json

The capture is the follow-up asked for in the review of #810: in ``f461_live_2026-10-24`` run 4 the
heating comfort template ``*#4*2*#7*1*3##`` was sent from a 21.0 baseline that already matched the
KW4691's comfort preset, and ``*#4*2*#7*2*3##`` was cooling comfort, not heating eco. Here, from a
23.5 baseline on the same Living Now KW4691 standalone thermostat behind an F461 2.0.11 (zone 2,
heating season, no central unit), the probe sent ``*#4*2*#7*1*3##`` (heating comfort) and
``*#4*2*#7*1*4##`` (heating eco) and read dimension 12 (the setpoint in force) and dimension 14
(the last commanded value) right away and again later; then Home Assistant wrote
``climate.set_temperature`` 23.5 while the two differed. Every test pins a fact of the trace itself
(a frame present, an order, an ACK, a timing) or replays event-session frames through the climate
entity as it is on the base branch with the pinned OWNd.

What the plant said:

- ``*#4*2*#7*1*3##`` from 23.5: ACK in 68 ms, echoed ``*#4*2*7*1*3##``, ``*#4*2*12*0210*3##``,
  ``*4*108*2##``, ``*#4*2*0*0238##``; dimension 12 read 21.0 and dimension 14 read 23.5 at +1 s
  and at +13 s.
- ``*#4*2*#7*1*4##``: ACK in 89 ms, echoed ``*#4*2*7*1*4##``, ``*#4*2*12*0180*3##``,
  ``*4*107*2##``, ``*#4*2*0*0238##``; dimension 12 read 18.0 and dimension 14 read 23.5 at +1 s,
  +13 s and +48 s; ``*#4*2##`` at +15.5 s answered ``0``, ``12`` = 18.0, ``*4*107*2##`` (the mode
  frame in the season frame's place), ``14`` = 23.5, ``13``.
- In this capture a heating preset moved dimension 12 and left dimension 14 at the last commanded
  value for as long as it was observed (48 s into eco); no frame realigned them. Home Assistant's
  ``*#4*2*#14*0235*3##`` 2 min later was echoed as ``*4*1*2##``, ``*#4*2*12*0235*3##``, ``*4*1*2##``,
  ``*#4*2*0*0238##`` (no dimension 14 frame, the season frame back in place of the mode frame) and
  the ``*#4*2##`` 7.4 s later read 12 = 14 = 23.5.
- The mode frames ``*4*107*2##`` / ``*4*108*2##`` parse in the pinned OWNd with no known mode
  (``mode = None``; OpenWebNet-HA/OWNd#146); ``*#4*2*7*1*4##`` parses as zone state ``eco`` in the
  ``heating`` context.
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
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCommand, OWNMessage
from OWNd.profiles import F461Profile

from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
from tests.conftest import attach_platform, attach_runtime

MAC = "00:03:50:00:10:23"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "f461_presets_live_2026-10-24"
    / "myhome_trace_F461_presets_live_2026-10-24.json"
)

RUN1 = "run1_heating_comfort_then_heating_eco"
RUN2 = "run2_home_assistant_write_realigns"
ALL_RUNS = (RUN1, RUN2)

SEASON = "*4*1*2##"
MODE_COMFORT = "*4*108*2##"
MODE_ECO = "*4*107*2##"
ZONE_STATUS = "*#4*2##"
TEMPLATE_COMFORT = "*#4*2*#7*1*3##"
TEMPLATE_ECO = "*#4*2*#7*1*4##"
ECHO_COMFORT = "*#4*2*7*1*3##"
ECHO_ECO = "*#4*2*7*1*4##"
HA_WRITE = "*#4*2*#14*0235*3##"
TEMPERATURE = "*#4*2*0*0238##"
OFFSET = "*#4*2*13*00##"


def dim(number: int, value: str) -> str:
    return f"*#4*2*{number}*{value}*3##"


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    """Load the 2026-10-24 F461 preset trace archive."""
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


def _results(rows: list[dict], run: str | None = None, frame: str | None = None, step: str | None = None) -> list[dict]:
    return [
        r
        for r in rows
        if r["dir"] == "result"
        and (run is None or r["run"] == run)
        and (frame is None or r["frame"] == frame)
        and (step is None or r["step"] == step)
    ]


def _result(rows: list[dict], run: str, frame: str, step: str | None = None) -> dict:
    found = _results(rows, run, frame, step)
    assert len(found) == 1, (run, frame, step, len(found))
    return found[0]


def _tx(rows: list[dict], run: str, frame: str, step: str | None = None) -> dict:
    found = [r for r in rows if r["dir"] == "tx" and r["run"] == run and r["frame"] == frame and (step is None or r["step"] == step)]
    assert len(found) == 1, (run, frame, step, len(found))
    return found[0]


def _evt(rows: list[dict], run: str) -> list[dict]:
    return [r for r in rows if r["run"] == run and r["dir"] == "rx" and r["session"] == "evt"]


def _evt_between(rows: list[dict], run: str, frame: str, step: str | None = None) -> list[str]:
    """Event-session frames from the request ``frame`` of ``run`` until the next request of that run."""
    run_rows = [r for r in rows if r["run"] == run]
    start = next(
        i for i, r in enumerate(run_rows) if r["dir"] == "tx" and r["frame"] == frame and (step is None or r["step"] == step)
    )
    out: list[str] = []
    for r in run_rows[start + 1 :]:
        if r["dir"] == "tx":
            break
        if r["dir"] == "rx" and r["session"] == "evt":
            out.append(r["frame"])
    return out


def _evt_before_first_request(rows: list[dict], run: str) -> list[str]:
    run_rows = [r for r in rows if r["run"] == run]
    out: list[str] = []
    for r in run_rows:
        if r["dir"] == "tx":
            break
        if r["dir"] == "rx":
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
    gw.log_id = "[F461 presets live 2026-10-24]"
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
    entry.entry_id = "f461_presets_live_2026_10_24"
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


def _replay(zone, evt_rows: list[dict]) -> list[tuple[str, float | None, str | None]]:
    """Feed the zone-2 event frames of ``evt_rows`` to the entity, return (frame, shown target, mode) after each."""
    seen: list[tuple[str, float | None, str | None]] = []
    for r in evt_rows:
        msg = OWNMessage.parse(r["frame"])
        assert msg is not None, r
        if getattr(msg, "who", None) == 4 and getattr(msg, "zone", None) == 2 and msg.__class__.__name__ == "OWNHeatingEvent":
            zone.handle_event(msg)
            seen.append((r["frame"], zone.target_temperature, zone.hvac_mode))
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
    assert [r["listener"] for r in capture["runs"]] == ["eco_evt.jsonl"] * 2
    assert capture["window"] == {"first": "2026-10-24T20:08:49+00:00", "last": "2026-10-24T20:12:04+00:00", "frames": 47, "rows": 122}
    assert len(frames) == 47 and len(rows) == 122
    assert {r["run"] for r in rows} == set(ALL_RUNS)
    assert [(r["run"], r["utc"], r["rows"], r["frames"]) for r in capture["runs"]] == [
        (RUN1, "20:08:49-20:10:22", 103, 36),
        (RUN2, "20:11:20-20:12:04", 19, 11),
    ]
    ts = [f["timestamp"] for f in frames]
    assert ts == sorted(ts), "one monotonic time axis across the runs"
    assert all(a["t"] <= b["t"] for a, b in zip(rows, rows[1:], strict=False))
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [f["raw"] for f in frames]
    # one probe invocation per run, aligned to the listener (probe t = 0 -> listener_offset_s on the archive axis)
    for run in capture["runs"]:
        assert len(run["probe_segments"]) == 1 and run["probe_segments"][0]["file"] == "eco_cmd.jsonl", run["run"]
        assert run["probe_segments"][0]["listener_offset_s"] >= run["t_offset"] - 2.0, run["run"]
    assert [s["probe_wait_s"] for r in capture["runs"] for s in r["probe_segments"]] == [1.0, 1.5]
    # Home Assistant's only request in the window is listed, not logged as a row (it went through its session)
    assert capture["home_assistant_requests"] == [
        {
            "utc": "20:11:20",
            "frame": HA_WRITE,
            "run": RUN2,
            "what": "climate.set_temperature 23.5 while dimension 12 = 18.0 (heating eco) and dimension 14 = 23.5",
        }
    ]
    assert not any(r["dir"] == "tx" and "*#14*" in r["frame"] for r in rows)
    # privacy: the only MAC-like string is the synthetic one, no LAN address, no WHO 13 identity frame
    text = json.dumps(trace_payload)
    assert set(re.findall(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text)) == {MAC}
    assert not re.search(r"192[.*]168", text) and "/Users/" not in text, "no LAN address, no local path"
    assert not any(f["raw"].startswith("*#13**12*") or f["raw"].startswith("*#13**10*") for f in frames)
    # WHO 4 zone 2 and the gateway clock are all the capture holds
    assert {re.match(r"^\*#?(\d+)\*", f["raw"]).group(1) for f in frames} == {"4", "13"}


def test_every_frame_parses_in_ownd(rows: list[dict], frames: list[dict]) -> None:
    """Every rx frame parses in the pinned OWNd; every tx frame parses as a command."""
    for item in frames:
        assert OWNMessage.parse(item["raw"]) is not None, f"Failed to parse evt frame: {item['raw']}"
    for r in rows:
        if r["dir"] == "tx":
            assert OWNCommand.parse(r["frame"]) is not None, r
        elif r["dir"] == "rx" and r["frame"] not in ("*#*1##", "*#*0##"):
            assert OWNMessage.parse(r["frame"]) is not None, r
    # WHAT 107 / 108: the pinned OWNd (<= 2.0.0b11) leaves mode = None; OpenWebNet-HA/OWNd#146 decodes
    # them as heating eco / comfort. Both are accepted so OWNd's MyHOME conformance gate stays green
    # when that lands (the dual-OWNd pattern of tests/test_message_edges.py).
    for raw in (MODE_ECO, MODE_COMFORT):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.zone == 2
        assert getattr(msg, "mode", None) in (None, "heat")
        assert getattr(msg, "season", None) in (None, "heating")
        if getattr(msg, "mode", None) is None:
            assert msg.human_readable_log == "Zone 2's mode is unknown."
    # the dimension 7 echoes: context 1 (heating), state 3 (comfort) / 4 (eco)
    for raw, state, log in ((ECHO_COMFORT, "comfort", "Zone 2 is in heating comfort."), (ECHO_ECO, "eco", "Zone 2 is in heating eco.")):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.zone == 2 and msg.zone_state == state and msg.zone_context == "heating"
        assert msg.human_readable_log == log
    season = OWNMessage.parse(SEASON)
    assert season is not None and season.human_readable_log == "Zone 2's season is heating."


def test_result_rows_are_consistent_with_their_tx_and_cmd_rx_rows(rows: list[dict]) -> None:
    """Each result row follows its tx row, its replies are on the command session, ACK / NACK is logged."""
    assert all(r["ack"] is True for r in _results(rows)), "every request of this capture was ACKed"
    assert len(_results(rows)) == 15
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
            assert [x["frame"] for x in cmd_rx] == [*r["replies"], "*#*1##"]
            assert all(x["t"] == tx["t"] and x["t_estimated"] is True for x in cmd_rx)
            assert run_rows[tx_i + 1 : tx_i + 1 + len(cmd_rx)] == cmd_rx
            pending = [x["frame"] for x in run_rows[tx_i + 1 : i] if x["dir"] == "rx" and x["session"] == "evt" and x["t"] <= r["t"]]
            assert r["events_after"] == pending
            # a status reply is delivered on both sessions: what the command session returned is
            # also in the event frames of the pending window; a template write has no reply and
            # its echo arrived after the ACK
            if tx["is_status_request"]:
                assert r["replies"] == pending, r
            else:
                assert r["replies"] == [] and pending == [], r


# --------------------------------------------------------------- run 1: heating comfort, then heating eco


def test_heating_comfort_from_23_5_moves_dimension_12_and_leaves_14(rows: list[dict]) -> None:
    """*#4*2*#7*1*3## from 12 = 14 = 23.5: 12 reads 21.0 at +1 s and +13 s, 14 reads 23.5 both times."""
    baseline = _result(rows, RUN1, ZONE_STATUS, "baseline_poll")
    assert baseline["replies"] == [TEMPERATURE, dim(12, "0235"), SEASON, dim(14, "0235"), OFFSET]
    assert 1.3 < baseline["reply_time_s"] < 1.4
    assert _evt_before_first_request(rows, RUN1) == [], "the listener's first frames are the baseline poll's replies"
    comfort = _result(rows, RUN1, TEMPLATE_COMFORT)
    assert comfort["reply_time_s"] == pytest.approx(0.068, abs=0.001) and comfort["events_after"] == [], "the ACK comes before the thermostat reports"
    assert _evt_between(rows, RUN1, TEMPLATE_COMFORT) == [ECHO_COMFORT, dim(12, "0210"), MODE_COMFORT, TEMPERATURE]
    reads12 = _results(rows, RUN1, "*#4*2*12##", "heating_comfort")
    reads14 = _results(rows, RUN1, "*#4*2*14##", "heating_comfort")
    assert [r["replies"] for r in reads12] == [[dim(12, "0210"), MODE_COMFORT]] * 2, "the mode frame rides with dimension 12, as the season frame does"
    assert [r["replies"] for r in reads14] == [[dim(14, "0235")]] * 2
    t0 = _tx(rows, RUN1, TEMPLATE_COMFORT)["t"]
    assert [round(r["t"] - r["reply_time_s"] - t0, 1) for r in reads12] == [1.1, 13.3]
    assert [round(r["t"] - r["reply_time_s"] - t0, 1) for r in reads14] == [2.2, 14.4]
    assert all(r["reply_time_s"] < 0.15 for r in [*reads12, *reads14])
    # between the template and the eco template no frame other than the read replies reached the event session
    evt = [r["frame"] for r in _evt(rows, RUN1) if comfort["t"] < r["t"] < _tx(rows, RUN1, TEMPLATE_ECO)["t"]]
    assert evt == [ECHO_COMFORT, dim(12, "0210"), MODE_COMFORT, TEMPERATURE] + [dim(12, "0210"), MODE_COMFORT, dim(14, "0235")] * 2


def test_heating_eco_keeps_the_split_for_48_s(rows: list[dict]) -> None:
    """*#4*2*#7*1*4##: 12 reads 18.0 and 14 reads 23.5 at +1 s, +13 s and +48 s; *#4*2## carries the mode frame."""
    eco = _result(rows, RUN1, TEMPLATE_ECO)
    assert eco["reply_time_s"] == pytest.approx(0.089, abs=0.001) and eco["events_after"] == []
    assert _evt_between(rows, RUN1, TEMPLATE_ECO) == [ECHO_ECO, dim(12, "0180"), MODE_ECO, TEMPERATURE]
    t0 = _tx(rows, RUN1, TEMPLATE_ECO)["t"]
    assert t0 - _tx(rows, RUN1, TEMPLATE_COMFORT)["t"] == pytest.approx(15.53, abs=0.01)
    reads12 = _results(rows, RUN1, "*#4*2*12##", "heating_eco")
    reads14 = _results(rows, RUN1, "*#4*2*14##", "heating_eco")
    assert [r["replies"] for r in reads12] == [[dim(12, "0180"), MODE_ECO]] * 3
    assert [r["replies"] for r in reads14] == [[dim(14, "0235")]] * 3
    assert [round(r["t"] - r["reply_time_s"] - t0, 1) for r in reads12] == [1.1, 13.3, 47.8]
    assert [round(r["t"] - r["reply_time_s"] - t0, 1) for r in reads14] == [2.2, 14.4, 49.0]
    assert all(r["reply_time_s"] < 0.15 for r in [*reads12, *reads14])
    status = _result(rows, RUN1, ZONE_STATUS, "heating_eco")
    assert status["t"] - status["reply_time_s"] - t0 == pytest.approx(15.53, abs=0.01)
    assert status["replies"] == [TEMPERATURE, dim(12, "0180"), MODE_ECO, dim(14, "0235"), OFFSET], "the mode frame takes the season frame's place"
    assert 1.3 < status["reply_time_s"] < 1.4
    # after the last read: the gateway clock (20:10:00) and one humidity frame (20:10:22) with no request
    # pending in either log; no dimension 12 / 14 / 7 frame and no season frame until the Home Assistant write
    evt = _evt(rows, RUN1)
    idle = [r["frame"] for r in evt if r["t"] > reads14[-1]["t"] - reads14[-1]["reply_time_s"]]
    assert idle == [dim(14, "0235"), "*#13**#1*06*24*10*2026##", "*#13**#0*23*10*00*##", "*#4*2*60*40##"]
    assert evt[-1]["t"] - (t0 + 49.0) == pytest.approx(26.9, abs=0.1)
    # every dimension 14 frame of the run is a read reply; every dimension 12 frame is a read reply or a template echo
    frames = [r["frame"] for r in evt]
    assert sum(1 for f in frames if f.startswith("*#4*2*14*")) == len(_results(rows, RUN1, "*#4*2*14##")) + 2 == 7
    assert sum(1 for f in frames if f.startswith("*#4*2*12*")) == len(_results(rows, RUN1, "*#4*2*12##")) + 2 + 2 == 9
    assert [f for f in frames if f.startswith("*#4*2*7*")] == [ECHO_COMFORT, ECHO_ECO]
    assert frames.count(SEASON) == 1, "the season frame appears only in the baseline poll; the presets answer with their mode frame"
    assert frames.count(MODE_COMFORT) == 3 and frames.count(MODE_ECO) == 5


def test_no_frame_realigned_the_dimensions_while_a_preset_was_active(rows: list[dict]) -> None:
    """From the comfort echo to the end of run 1 every 12 / 14 pair read differed: 21.0 / 23.5, then 18.0 / 23.5."""
    res = _results(rows, RUN1)
    assert [r["frame"] for r in res] == [
        ZONE_STATUS,
        TEMPLATE_COMFORT, "*#4*2*12##", "*#4*2*14##", "*#4*2*12##", "*#4*2*14##",
        TEMPLATE_ECO, "*#4*2*12##", "*#4*2*14##", "*#4*2*12##", "*#4*2*14##", ZONE_STATUS, "*#4*2*12##", "*#4*2*14##",
    ]  # fmt: skip
    pairs = []
    for a, b in zip(res, res[1:], strict=False):
        if a["frame"] == "*#4*2*12##" and b["frame"] == "*#4*2*14##":
            pairs.append((a["replies"][0][len("*#4*2*12*") : -len("*3##")], b["replies"][0][len("*#4*2*14*") : -len("*3##")]))
    assert pairs == [("0210", "0235")] * 2 + [("0180", "0235")] * 3
    for poll in _results(rows, RUN1, ZONE_STATUS):
        value12 = poll["replies"][1][len("*#4*2*12*") : -len("*3##")]
        value14 = poll["replies"][3][len("*#4*2*14*") : -len("*3##")]
        assert (value12 == value14) == (poll["replies"][2] == SEASON), poll
    assert [p["replies"][2] for p in _results(rows, RUN1, ZONE_STATUS)] == [SEASON, MODE_ECO]


# --------------------------------------------------------------- run 2: the Home Assistant write


def test_home_assistant_write_while_split_realigns_both(rows: list[dict]) -> None:
    """climate.set_temperature 23.5 (20:11:20 UTC) in heating eco: echo 12 + season, the poll reads 12 = 14 = 23.5."""
    assert _evt_before_first_request(rows, RUN2) == [SEASON, dim(12, "0235"), SEASON, TEMPERATURE], "the write echo: no dimension 14 frame, the season frame back"
    evt = _evt(rows, RUN2)
    poll = _result(rows, RUN2, ZONE_STATUS, "poll_after_ha_write")
    assert poll["t"] - poll["reply_time_s"] - evt[0]["t"] == pytest.approx(7.4, abs=0.05)
    assert poll["reply_time_s"] == pytest.approx(1.441, abs=0.001)
    assert poll["replies"] == [TEMPERATURE, dim(12, "0235"), SEASON, dim(14, "0235"), OFFSET]
    assert poll["events_after"] == poll["replies"]
    # 43 s after the write the valve reported itself off: a '#'-prefixed command form, then the status form
    assert _evt_between(rows, RUN2, ZONE_STATUS) == [*poll["replies"], "*#4*2#1*#20*0##", "*#4*2#1*20*0##"]
    assert evt[-1]["t"] - evt[0]["t"] == pytest.approx(43.35, abs=0.01)
    for raw, log in (("*#4*2#1*20*0##", "Zone 2's actuator 1 is off."),):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.zone == 2 and msg.human_readable_log == log
    # the run starts 1 s after run 1 ended, on the archive's monotonic axis
    run1_end = max(r["t"] for r in rows if r["run"] == RUN1)
    assert evt[0]["t"] == pytest.approx(run1_end + 1.0, abs=0.001)


# --------------------------------------------------------------- entity replays


@pytest.mark.asyncio
async def test_replay_presets_show_dimension_12_and_keep_heating(hass: HomeAssistant, rows: list[dict]) -> None:
    """Run 1 through the climate entity: 23.5, then 21.0 (comfort), then 18.0 (eco), heating throughout.

    The dimension 14 read replies (23.5) do not move the shown setpoint while dimension 12 says
    otherwise; the mode frames 107 / 108 (``mode = None`` on the pinned OWNd, heating eco / comfort once OpenWebNet-HA/OWNd#146 lands) leave the mode alone; ``*#4*2*7*1*4##``
    (eco, heating context) keeps ``heat``.
    """
    zone = await _setup_zone_2(hass)
    seen = _replay(zone, _evt(rows, RUN1))
    assert seen[1] == (dim(12, "0235"), 23.5, None)
    assert seen[2] == (SEASON, 23.5, HVACMode.HEAT)
    targets = [target for frame, target, _ in seen]
    assert [v for i, v in enumerate(targets) if i == 0 or v != targets[i - 1]] == [None, 23.5, 21.0, 18.0]
    by_frame: dict[str, list] = {}
    for frame, target, mode in seen:
        by_frame.setdefault(frame, []).append((target, mode))
    assert by_frame[ECHO_COMFORT] == [(23.5, HVACMode.HEAT)]
    assert by_frame[dim(12, "0210")] == [(21.0, HVACMode.HEAT)] * 3
    assert by_frame[MODE_COMFORT] == [(21.0, HVACMode.HEAT)] * 3
    assert by_frame[ECHO_ECO] == [(21.0, HVACMode.HEAT)]
    assert by_frame[dim(12, "0180")] == [(18.0, HVACMode.HEAT)] * 5
    assert by_frame[MODE_ECO] == [(18.0, HVACMode.HEAT)] * 5
    assert by_frame[dim(14, "0235")] == [(23.5, HVACMode.HEAT)] + [(21.0, HVACMode.HEAT)] * 2 + [(18.0, HVACMode.HEAT)] * 4
    assert all(mode == HVACMode.HEAT for _, _, mode in seen[2:])
    assert zone.target_temperature == 18.0 and zone.hvac_mode == HVACMode.HEAT
    assert zone.current_temperature == 23.8
    assert zone.extra_state_attributes["season"] == "heating"


@pytest.mark.asyncio
async def test_replay_home_assistant_write_shows_23_5(hass: HomeAssistant, rows: list[dict]) -> None:
    """Run 1 then run 2 through the entity: the write echo moves the shown setpoint from 18.0 to 23.5 at once."""
    zone = await _setup_zone_2(hass)
    _replay(zone, _evt(rows, RUN1))
    assert zone.target_temperature == 18.0
    seen = _replay(zone, _evt(rows, RUN2))
    assert seen[0] == (SEASON, 18.0, HVACMode.HEAT)
    assert seen[1] == (dim(12, "0235"), 23.5, HVACMode.HEAT)
    assert all(target == 23.5 and mode == HVACMode.HEAT for _, target, mode in seen[1:])
    assert zone.target_temperature == 23.5 and zone.hvac_mode == HVACMode.HEAT
    assert zone.extra_state_attributes["season"] == "heating"
