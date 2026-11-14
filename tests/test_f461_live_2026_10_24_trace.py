"""Replay and verification tests for the 2026-10-24 live BTicino F461 climate capture.

Fixture:
  tests/fixtures/traces/f461_live_2026-10-24/myhome_trace_F461_live_2026-10-24.json

The capture is the before / trigger / after evidence asked for on #791: what moves WHO 4
dimension 12 (the setpoint in force) and dimension 14 (the last commanded value) on a
Living Now KW4691 standalone thermostat behind an F461 2.0.11. Every test pins a fact of the
trace itself (a frame present, an order, an ACK, a timing) or replays event-session frames
through the climate entity as it is on the base branch with the pinned OWNd.

What the plant said (zone 2, heating season, no central unit):

- A bezel turn emits dimension 12 (29.0, then 18.0 two seconds later), the season frame and
  the measured temperature; no dimension 14 and no dimension 13 frame. The first
  ``*#4*2*14##`` read, 49 s later, already answered 18.0.
- A ``*#4*2*#14*T*3##`` write echoes dimension 12, the season frame and dimension 0 (never
  dimension 14); a dimension 14 read 0.1-0.7 s later already carries T.
- ``*4*102*2##`` (antifreeze) is, among the operations exercised in this capture, the only
  one that splits the two: dimension 12 reports 7.0 with the mode frame ``*4*102*2##`` while
  dimension 14 keeps the last commanded value (23.5, 24.0, 7.5). A write of dimension 14
  brings the zone back to heating. (On this plant ``f461_live_2026-10-23`` run 1 had recorded
  12 = 23.5 != 14 = 21.0 in active heating, ``*4*1*2##``, after days without a write; that
  transition was not reproduced here.)
- The dimension 7 templates ``*#4*2*#7*1*3##`` / ``*#4*2*#7*2*3##`` are echoed as
  ``*#4*2*7*1*3##`` (heating comfort, 21.0, ``*4*108*2##``) and ``*#4*2*7*2*3##`` (cooling
  comfort, 25.0, ``*4*208*2##``); 47 s later the season flipped to conditioning and back to
  heating and the setpoint settled at 22.5 on both dimensions, without a dimension 14 write.
- A bezel press in Manual moves dimension 12 at once (23.5 -> 24.0; the valve opens); a bezel
  press in antifreeze leaves antifreeze at the protection value plus one step (7.5) on dimension
  12, with dimension 14 following; a Home Assistant write while 12 != 14 realigns both.
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
    / "f461_live_2026-10-24"
    / "myhome_trace_F461_live_2026-10-24.json"
)

RUN1 = "run1_knob_turn"
RUN2 = "run2_fast_poll_dim12_dim14"
RUN3 = "run3_antifreeze_split_and_restore"
RUN4 = "run4_preset_templates"
RUN5 = "run5_bezel_in_manual_and_in_antifreeze_then_ha_write"
ALL_RUNS = (RUN1, RUN2, RUN3, RUN4, RUN5)

SEASON = "*4*1*2##"
ANTIFREEZE = "*4*102*2##"
ZONE_STATUS = "*#4*2##"


def dim(number: int, value: str) -> str:
    return f"*#4*2*{number}*{value}*3##"


@pytest.fixture(scope="module")
def trace_payload() -> dict:
    """Load the 2026-10-24 F461 climate trace archive."""
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


def _result(rows: list[dict], run: str, frame: str, step: str | None = None) -> dict:
    found = [r for r in _results(rows, run, frame) if step is None or r["step"] == step]
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
    gw.log_id = "[F461 live 2026-10-24]"
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
    entry.entry_id = "f461_live_2026_10_24"
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


def _replay(zone, evt_rows: list[dict], until_t: float | None = None, from_t: float | None = None) -> list[tuple[str, float | None, str | None]]:
    """Feed the zone-2 event frames of ``evt_rows`` to the entity, return (frame, shown target, mode) after each."""
    seen: list[tuple[str, float | None, str | None]] = []
    for r in evt_rows:
        if (until_t is not None and r["t"] > until_t) or (from_t is not None and r["t"] < from_t):
            continue
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
    assert [r["listener"] for r in capture["runs"]] == ["evt.jsonl"] * 3 + ["transition_evt.jsonl"] * 2
    assert capture["window"] == {"first": "2026-10-24T18:07:55+00:00", "last": "2026-10-24T19:12:37+00:00", "frames": 894, "rows": 3200}
    assert len(frames) == 894 and len(rows) == 3200
    assert {r["run"] for r in rows} == set(ALL_RUNS)
    assert [(r["run"], r["rows"], r["frames"]) for r in capture["runs"]] == [
        (RUN1, 38, 21),
        (RUN2, 2902, 742),
        (RUN3, 74, 34),
        (RUN4, 93, 51),
        (RUN5, 93, 46),
    ]
    ts = [f["timestamp"] for f in frames]
    assert ts == sorted(ts), "one monotonic time axis across the runs"
    assert all(a["t"] <= b["t"] for a, b in zip(rows, rows[1:], strict=False))
    assert all(f["direction"] == "rx" and f["session"] == "evt" for f in frames)
    assert [r["frame"] for r in rows if r["dir"] == "rx" and r["session"] == "evt"] == [f["raw"] for f in frames]
    # every probe segment is aligned to its listener (probe t = 0 -> listener_offset_s on the archive axis)
    for run in capture["runs"]:
        offsets = [s["listener_offset_s"] for s in run["probe_segments"]]
        assert offsets == sorted(offsets) and all(o >= run["t_offset"] - 2.0 for o in offsets), run["run"]
        assert all(s["probe_wait_s"] in (0.6, 1.5, 2.0, 3.0) for s in run["probe_segments"]), run["run"]
    # Home Assistant's own requests are listed, not logged as rows (they went through its session)
    assert [(h["utc"], h["frame"]) for h in capture["home_assistant_requests"]] == [
        ("18:08:53", ZONE_STATUS),
        ("18:08:55", "*#4*2*60##"),
        ("18:11:46", "*#4*2*#14*0210*3##"),
        ("18:13:39", "*#4*2*#14*0235*3##"),
        ("18:58:46", ZONE_STATUS),
        ("18:58:49", "*#4*2*60##"),
        ("19:02:04", "*#4*2*#14*0210*3##"),
        ("19:05:32", ZONE_STATUS),
        ("19:05:35", "*#4*2*60##"),
        ("19:12:26", "*#4*2*#14*0235*3##"),
    ]
    assert not any(r["dir"] == "tx" and "*#14*" in r["frame"] and r["run"] in (RUN1, RUN2) for r in rows)
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
    # WHAT 108 / 208: the pinned OWNd (<= 2.0.0b11) leaves mode = None; OpenWebNet-HA/OWNd#146 decodes
    # 108 as heating comfort and 208 as cooling comfort. Both are accepted so OWNd's MyHOME
    # conformance gate stays green when that lands.
    for raw, mode, season in (("*4*108*2##", "heat", "heating"), ("*4*208*2##", "cool", "conditioning")):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.zone == 2
        assert getattr(msg, "mode", None) in (None, mode)
        assert getattr(msg, "season", None) in (None, season)
        if getattr(msg, "mode", None) is None:
            assert msg.human_readable_log == "Zone 2's mode is unknown."
    antifreeze = OWNMessage.parse(ANTIFREEZE)
    assert antifreeze is not None and antifreeze.mode == "off" and antifreeze.human_readable_log == "Zone 2's mode is set to 'off'."


def test_result_rows_are_consistent_with_their_tx_and_cmd_rx_rows(rows: list[dict]) -> None:
    """Each result row follows its tx row, its replies are on the command session, ACK / NACK is logged."""
    assert all(r["ack"] is True for r in _results(rows)), "every request of this capture was ACKed"
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
            # also in the event frames of the pending window (the only exception is a season frame
            # that reached the event session a few ms after the ACK, run 2)
            if tx["is_status_request"]:
                assert [f for f in r["replies"] if f in pending] == r["replies"] or r["replies"][-1] == SEASON, r


# --------------------------------------------------------------- run 1: the bezel turn


def test_knob_turn_emits_dimension_12_only(rows: list[dict]) -> None:
    """The bezel turn: dimension 12 twice (29.0, 18.0), the season frame, dimension 0; no 14, no 13."""
    before = _evt_before_first_request(rows, RUN1)
    assert before == [
        dim(12, "0290"),
        SEASON,
        "*#4*2*0*0239##",
        "*#4*2*0*0239##",
        dim(12, "0180"),
        SEASON,
        "*#4*2*0*0239##",
    ]
    evt = _evt(rows, RUN1)
    assert evt[4]["t"] - evt[0]["t"] == pytest.approx(2.254, abs=0.002), "the dial settled on 18.0 two seconds after 29.0"
    assert evt[0]["ownd_log"] == "Zone 2's local target temperature is set to 29.0°C."
    # 49 s later the first read of dimension 14 already answers 18.0
    read14 = _result(rows, RUN1, "*#4*2*14##")
    assert read14["t"] - evt[0]["t"] == pytest.approx(49.09, abs=0.01)
    assert read14["replies"] == [dim(14, "0180")] and read14["reply_time_s"] < 0.2
    assert read14["events_after"] == [dim(14, "0180")]
    assert _result(rows, RUN1, "*#4*2*12##")["replies"] == [dim(12, "0180"), SEASON]
    status = _result(rows, RUN1, ZONE_STATUS)
    assert status["replies"] == ["*#4*2*0*0239##", dim(12, "0180"), SEASON, dim(14, "0180"), "*#4*2*13*00##"]
    assert 1.2 < status["reply_time_s"] < 1.4
    # Home Assistant's own *#4*2## and *#4*2*60## polls (18:08:53 UTC) follow: the same burst, no probe request
    assert _evt_between(rows, RUN1, ZONE_STATUS) == [
        *status["replies"],
        "*#4*2*0*0239##",
        dim(12, "0180"),
        SEASON,
        dim(14, "0180"),
        "*#4*2*13*00##",
        "*#4*2*60*40##",
    ]


# --------------------------------------------------------------- run 2: the fast poll


def test_fast_poll_reads_both_dimensions_480_times(rows: list[dict]) -> None:
    """240 dimension 12 reads and 240 dimension 14 reads 0.6 s apart, every one ACKed in 90-330 ms."""
    res = _results(rows, RUN2)
    assert len(res) == 480
    assert [r["frame"] for r in res] == ["*#4*2*12##", "*#4*2*14##"] * 240
    assert all(0.09 <= r["reply_time_s"] <= 0.33 for r in res)
    gaps = [b["t"] - b["reply_time_s"] - a["t"] for a, b in zip(res, res[1:], strict=False)]
    assert all(0.55 < g < 0.75 for g in gaps), (min(gaps), max(gaps))
    for r in res:
        if r["frame"] == "*#4*2*12##":
            assert len(r["replies"]) == 2 and r["replies"][0].startswith("*#4*2*12*") and r["replies"][1] == SEASON
        else:
            assert len(r["replies"]) == 1 and r["replies"][0].startswith("*#4*2*14*")
    values12 = [r["replies"][0] for r in res if r["frame"] == "*#4*2*12##"]
    values14 = [r["replies"][0] for r in res if r["frame"] == "*#4*2*14##"]
    assert [values12.count(dim(12, v)) for v in ("0180", "0210", "0235")] == [84, 79, 77]
    assert [values14.count(dim(14, v)) for v in ("0180", "0210", "0235")] == [84, 78, 78]
    # the values only ever went 18.0 -> 21.0 -> 23.5 (the two Home Assistant writes)
    for values, number in ((values12, 12), (values14, 14)):
        assert [v for i, v in enumerate(values) if i == 0 or v != values[i - 1]] == [dim(number, "0180"), dim(number, "0210"), dim(number, "0235")]


def test_home_assistant_write_echo_is_dimension_12_and_both_dimensions_follow_within_a_poll_cycle(rows: list[dict]) -> None:
    """A *#4*2*#14*T*3## write echoes dimension 12, the season and dimension 0 (no dimension 14 frame)."""
    res = _results(rows, RUN2)
    evt = _evt(rows, RUN2)
    # 21.0 (18:11:46 UTC): the echo arrived between two reads, the next 12 read and the next 14 read carry it
    last_18 = [r for r in res if r["replies"][0] in (dim(12, "0180"), dim(14, "0180"))][-1]
    first_21_12 = next(r for r in res if r["replies"][0] == dim(12, "0210"))
    first_21_14 = next(r for r in res if r["replies"][0] == dim(14, "0210"))
    echo = [r["frame"] for r in evt if last_18["t"] < r["t"] < first_21_12["t"] - first_21_12["reply_time_s"]]
    assert echo == [dim(12, "0210"), SEASON, "*#4*2*0*0239##", "*#4*2*0*0239##"]
    assert first_21_14["t"] - first_21_12["t"] == pytest.approx(0.706, abs=0.002)
    # 23.5 (18:13:39 UTC): the echo arrived while a dimension 14 read was pending, whose reply already carried 23.5
    first_235_14 = next(r for r in res if r["replies"][0] == dim(14, "0235"))
    assert first_235_14["events_after"] == [dim(12, "0235"), SEASON, "*#4*2*0*0239##", dim(14, "0235")]
    first_235_12 = next(r for r in res if r["replies"][0] == dim(12, "0235"))
    assert first_235_12["t"] - first_235_14["t"] == pytest.approx(0.73, abs=0.002)
    # no dimension 14 frame was ever pushed: every dimension 14 frame of the run is a read reply
    count14 = sum(1 for r in evt if r["frame"].startswith("*#4*2*14*"))
    assert count14 == sum(1 for r in res if r["frame"] == "*#4*2*14##") == 240
    count12 = sum(1 for r in evt if r["frame"].startswith("*#4*2*12*"))
    assert count12 == 240 + 2, "240 read replies plus the two write echoes"
    # after the last read (18:15:30 UTC) the zone was silent on 12 / 14 for 42 min: humidity, temperature, clock only
    idle = [r["frame"] for r in evt if r["t"] > res[-1]["t"]]
    assert idle and not any(f.startswith("*#4*2*12*") or f.startswith("*#4*2*14*") or f == SEASON for f in idle)
    assert idle.count("*#4*2*60*40##") == 3 and idle.count("*#4*2*0*0239##") == 2
    assert [f for f in idle if f.startswith("*#13**#0*")] == [f"*#13**#0*21*{m}*00*##" for m in ("20", "30", "40", "50")]


# --------------------------------------------------------------- run 3: antifreeze


def test_antifreeze_splits_dimension_12_from_14_and_the_write_restores(rows: list[dict]) -> None:
    """*4*102*2## -> 12 = 7.0 with the mode frame, 14 = 23.5; *#4*2*#14*0235*3## -> heating, 12 = 14 = 23.5."""
    cmd = _result(rows, RUN3, ANTIFREEZE)
    assert cmd["reply_time_s"] < 0.1 and cmd["events_after"] == [], "the ACK comes before the thermostat reports"
    assert _evt_between(rows, RUN3, ANTIFREEZE) == [ANTIFREEZE, dim(12, "0070"), ANTIFREEZE, "*#4*2*0*0239##"]
    assert _result(rows, RUN3, "*#4*2*12##", "antifreeze")["replies"] == [dim(12, "0070"), ANTIFREEZE]
    assert _result(rows, RUN3, "*#4*2*14##", "antifreeze")["replies"] == [dim(14, "0235")]
    split = _result(rows, RUN3, ZONE_STATUS, "antifreeze")
    assert split["replies"] == ["*#4*2*0*0239##", dim(12, "0070"), ANTIFREEZE, dim(14, "0235"), "*#4*2*13*00##"]
    # Home Assistant's own poll 26 s later (18:58:46 UTC) read the same split
    assert _evt_between(rows, RUN3, ZONE_STATUS, "antifreeze") == [*split["replies"], *split["replies"], "*#4*2*60*40##"]
    restore = _result(rows, RUN3, "*#4*2*#14*0235*3##")
    assert restore["reply_time_s"] < 0.1 and restore["events_after"] == []
    assert _evt_between(rows, RUN3, "*#4*2*#14*0235*3##") == [SEASON, dim(12, "0235"), SEASON, "*#4*2*0*0239##"]
    assert _result(rows, RUN3, "*#4*2*12##", "restore")["replies"] == [dim(12, "0235"), SEASON]
    assert _result(rows, RUN3, "*#4*2*14##", "restore")["replies"] == [dim(14, "0235")]
    back = _result(rows, RUN3, ZONE_STATUS, "restore")
    assert back["replies"] == ["*#4*2*0*0239##", dim(12, "0235"), SEASON, dim(14, "0235"), "*#4*2*13*00##"]
    assert [r["frame"] for r in _evt(rows, RUN3)][-2:] == ["*#4*2#1*#20*0##", "*#4*2#1*20*0##"], "the valve's own dimension 20 echo closes the listener"


# --------------------------------------------------------------- run 4: the dimension 7 templates


def test_dimension_7_templates_and_the_season_flips(rows: list[dict]) -> None:
    """*#4*2*#7*1*3## / *#4*2*#7*2*3## are echoed as heating / cooling comfort with the mode frames 108 / 208."""
    # the listener opened on the echo of Home Assistant's *#4*2*#14*0210*3## (19:02:04 UTC): no dimension 14 frame
    assert _evt_before_first_request(rows, RUN4) == [dim(12, "0210"), SEASON, "*#4*2*0*0239##"]
    baseline = _result(rows, RUN4, ZONE_STATUS, "baseline_poll")
    assert baseline["replies"] == ["*#4*2*0*0239##", dim(12, "0210"), SEASON, dim(14, "0210"), "*#4*2*13*00##"]
    comfort = _result(rows, RUN4, "*#4*2*#7*1*3##")
    assert comfort["reply_time_s"] < 0.1 and comfort["events_after"] == []
    assert _evt_between(rows, RUN4, "*#4*2*#7*1*3##") == ["*#4*2*7*1*3##", dim(12, "0210"), "*4*108*2##", "*#4*2*0*0239##"]
    # *#4*2*#7*2*3## is context 2 (cooling), state 3 (comfort): Cooling Comfort, not Heating Eco (*#4*2*#7*1*4##).
    # The archive keeps the probe step name "eco_template_and_reads" because the request was sent under that
    # mistaken label during the capture; the fixture was not regenerated for it.
    cooling_comfort = _result(rows, RUN4, "*#4*2*#7*2*3##")
    assert cooling_comfort["reply_time_s"] < 0.1 and cooling_comfort["events_after"] == []
    after_cooling_comfort = _evt_between(rows, RUN4, "*#4*2*#7*2*3##")
    assert after_cooling_comfort[:4] == ["*#4*2*7*2*3##", dim(12, "0250"), "*4*208*2##", "*#4*2*0*0239##"]
    # 47 s later the zone flipped to conditioning, 3 s later back to heating, 2 s later the setpoint became 22.5
    assert after_cooling_comfort[4:] == [
        "*4*0*2##",
        dim(12, "0250"),
        "*4*0*2##",
        "*#4*2*0*0238##",
        SEASON,
        dim(12, "0250"),
        SEASON,
        "*#4*2*0*0238##",
        dim(12, "0225"),
        SEASON,
        "*#4*2*0*0238##",
    ]
    evt = _evt(rows, RUN4)
    t_cooling_comfort = next(r["t"] for r in evt if r["frame"] == "*#4*2*7*2*3##")
    t_cool = next(r["t"] for r in evt if r["frame"] == "*4*0*2##")
    t_heat = next(r["t"] for r in evt if r["frame"] == SEASON and r["t"] > t_cool)
    t_225 = next(r["t"] for r in evt if r["frame"] == dim(12, "0225"))
    assert t_cool - t_cooling_comfort == pytest.approx(47.57, abs=0.01)
    assert t_heat - t_cool == pytest.approx(3.16, abs=0.01)
    assert t_225 - t_heat == pytest.approx(1.99, abs=0.01)
    # both dimensions read 22.5 without any dimension 14 write
    assert _result(rows, RUN4, "*#4*2*12##")["replies"] == [dim(12, "0225"), SEASON]
    assert _result(rows, RUN4, "*#4*2*14##")["replies"] == [dim(14, "0225")]
    assert _result(rows, RUN4, ZONE_STATUS, "eco_template_and_reads")["replies"][1:4] == [dim(12, "0225"), SEASON, dim(14, "0225")]
    for msg_raw, log in (("*#4*2*7*1*3##", "Zone 2 is in heating comfort."), ("*#4*2*7*2*3##", "Zone 2 is in cooling comfort.")):
        msg = OWNMessage.parse(msg_raw)
        assert msg is not None and msg.zone_state == "comfort" and msg.human_readable_log == log
    assert OWNMessage.parse("*#4*2*7*1*3##").zone_context == "heating"
    assert OWNMessage.parse("*#4*2*7*2*3##").zone_context == "cooling"
    # the restore: the write echo (12, season, 0), then the poll shows 23.5 on both; Home Assistant's poll 23 s later
    restore = _result(rows, RUN4, "*#4*2*#14*0235*3##")
    assert restore["reply_time_s"] < 0.1 and restore["events_after"] == []
    assert _evt_between(rows, RUN4, "*#4*2*#14*0235*3##") == [dim(12, "0235"), SEASON, "*#4*2*0*0238##"]
    final = _result(rows, RUN4, ZONE_STATUS, "restore")
    assert final["replies"] == ["*#4*2*0*0238##", dim(12, "0235"), SEASON, dim(14, "0235"), "*#4*2*13*00##"]
    assert _evt_between(rows, RUN4, ZONE_STATUS, "restore")[:11] == [
        *final["replies"],
        "*#4*2*0*0239##",
        dim(12, "0235"),
        SEASON,
        dim(14, "0235"),
        "*#4*2*13*00##",
        "*#4*2*60*40##",
    ]


# --------------------------------------------------------------- run 5: the bezel in Manual and in antifreeze


def test_bezel_in_manual_then_antifreeze_then_bezel_in_antifreeze_then_home_assistant_write(rows: list[dict]) -> None:
    """What moves 12 and 14 in both directions, on the wire."""
    # bezel +0.5 in Manual (19:10:42 UTC): dimension 12 at once, the valve opens 3 s later; no dimension 14
    assert _evt_before_first_request(rows, RUN5) == [dim(12, "0240"), SEASON, "*#4*2*0*0239##", "*#4*2#1*#20*1##", "*#4*2#1*20*1##"]
    # antifreeze: 12 = 7.0, the valve closes, the poll shows 14 = 24.0 (the bezel value had reached 14 by then)
    assert _evt_between(rows, RUN5, ANTIFREEZE, "antifreeze_1") == [
        ANTIFREEZE,
        dim(12, "0070"),
        ANTIFREEZE,
        "*#4*2*0*0239##",
        "*#4*2#1*#20*0##",
        "*#4*2#1*20*0##",
    ]
    split = _result(rows, RUN5, ZONE_STATUS, "antifreeze_1")
    assert split["replies"] == ["*#4*2*0*0239##", dim(12, "0070"), ANTIFREEZE, dim(14, "0240"), "*#4*2*13*00##"]
    # bezel +0.5 in antifreeze (19:11:10 UTC): heating again at the protection value plus one step, dimension 12 first
    assert _evt_between(rows, RUN5, ZONE_STATUS, "antifreeze_1") == [*split["replies"], SEASON, dim(12, "0075"), SEASON, "*#4*2*0*0239##"]
    assert _result(rows, RUN5, "*#4*2*12##")["replies"] == [dim(12, "0075"), SEASON]
    assert _result(rows, RUN5, "*#4*2*14##")["replies"] == [dim(14, "0075")]
    assert _result(rows, RUN5, ZONE_STATUS, "reads_after_bezel")["replies"] == ["*#4*2*0*0239##", dim(12, "0075"), SEASON, dim(14, "0075"), "*#4*2*13*00##"]
    # antifreeze again: 12 = 7.0, 14 keeps 7.5
    assert _evt_between(rows, RUN5, ANTIFREEZE, "antifreeze_2") == [ANTIFREEZE, dim(12, "0070"), ANTIFREEZE, "*#4*2*0*0238##"]
    split2 = _result(rows, RUN5, ZONE_STATUS, "antifreeze_2")
    assert split2["replies"] == ["*#4*2*0*0238##", dim(12, "0070"), ANTIFREEZE, dim(14, "0075"), "*#4*2*13*00##"]
    # Home Assistant's climate.set_temperature 23.5 (19:12:26 UTC) while 12 != 14: heating, 12 = 23.5, then 14 = 23.5
    assert _evt_between(rows, RUN5, ZONE_STATUS, "antifreeze_2") == [*split2["replies"], SEASON, dim(12, "0235"), SEASON, "*#4*2*0*0238##"]
    final = _result(rows, RUN5, ZONE_STATUS, "poll_after_ha_write")
    assert final["replies"] == ["*#4*2*0*0238##", dim(12, "0235"), SEASON, dim(14, "0235"), "*#4*2*13*00##"]
    # the dimension 20 echoes: a '#'-prefixed command form followed by the status form, as on 2026-10-23
    for raw, log in (("*#4*2#1*20*1##", "Zone 2's actuator 1 is on."), ("*#4*2#1*20*0##", "Zone 2's actuator 1 is off.")):
        msg = OWNMessage.parse(raw)
        assert msg is not None and msg.zone == 2 and msg.human_readable_log == log


def test_antifreeze_is_the_only_operation_that_left_12_and_14_apart(rows: list[dict]) -> None:
    """In this capture every *#4*2## reply with 12 != 14 carries the antifreeze mode frame; every heating-season reply has 12 == 14."""
    polls = _results(rows, frame=ZONE_STATUS)
    assert len(polls) == 10
    for r in polls:
        value12 = r["replies"][1][len("*#4*2*12*") : -len("*3##")]
        value14 = r["replies"][3][len("*#4*2*14*") : -len("*3##")]
        mode = r["replies"][2]
        if value12 != value14:
            assert mode == ANTIFREEZE and value12 == "0070", r
        else:
            assert mode == SEASON, r
    assert sum(1 for r in polls if r["replies"][2] == ANTIFREEZE) == 3
    # the same holds for the bursts Home Assistant's own polls produced on the event session
    for run, step in ((RUN1, "reads_after_knob"), (RUN3, "antifreeze"), (RUN4, "restore")):
        burst = _evt_between(rows, run, ZONE_STATUS, step)[5:10]
        assert burst[1].startswith("*#4*2*12*") and burst[3].startswith("*#4*2*14*"), (run, burst)
        same = burst[1][len("*#4*2*12*") :] == burst[3][len("*#4*2*14*") :]
        assert same == (burst[2] == SEASON), (run, burst)


# --------------------------------------------------------------- entity replays


@pytest.mark.asyncio
async def test_replay_knob_turn_shows_dimension_12(hass: HomeAssistant, rows: list[dict]) -> None:
    """Run 1 through the climate entity: 29.0 then 18.0 shown from dimension 12.

    Every dimension 14 frame of run 1 carries 18.0 as well, so the values alone cannot tell which
    dimension drives the display: the dimension 12 hold flag says it (#812's presets replay, 14 = 23.5
    while 12 = 26.0, is the one where the values differ).
    """
    zone = await _setup_zone_2(hass)
    seen = _replay(zone, _evt(rows, RUN1))
    assert seen[0] == (dim(12, "0290"), 29.0, None)
    assert seen[1] == (SEASON, 29.0, HVACMode.HEAT)
    assert seen[4] == (dim(12, "0180"), 18.0, HVACMode.HEAT)
    assert all(target == 18.0 for _, target, _ in seen[4:])
    assert zone.target_temperature == 18.0
    assert zone._target_temperature == 18.0  # the commanded value coincides with the one in force here
    assert zone._local_target_reported is True  # only a dimension 12 report sets it: 18.0 is shown from dimension 12
    assert zone.current_temperature == 23.9
    assert zone.extra_state_attributes["local_offset"] == 0
    assert zone.extra_state_attributes["knob_pos"] == "0"
    assert zone.extra_state_attributes["season"] == "heating"


@pytest.mark.asyncio
async def test_replay_antifreeze_and_restore(hass: HomeAssistant, rows: list[dict]) -> None:
    """Run 3: *4*102*2## turns the zone off; the restore write brings heating and 23.5 back."""
    zone = await _setup_zone_2(hass)
    evt = _evt(rows, RUN3)
    t_restore = next(r["t"] for r in evt if r["frame"] == SEASON)  # the write echo opens with the season frame
    seen = _replay(zone, evt, until_t=t_restore - 0.001)
    assert seen[0] == (ANTIFREEZE, None, HVACMode.OFF)
    assert all(mode == HVACMode.OFF for _, _, mode in seen)
    assert zone.hvac_mode == HVACMode.OFF
    # the split poll in antifreeze: dimension 14 (23.5) is kept as the nominal setpoint while the zone is off
    assert zone._target_temperature == 23.5
    seen = _replay(zone, evt, from_t=t_restore - 0.001)
    assert seen[0] == (SEASON, 23.5, HVACMode.HEAT), "heating again, the nominal setpoint is shown"
    assert seen[1] == (dim(12, "0235"), 23.5, HVACMode.HEAT)
    assert all(target == 23.5 and mode == HVACMode.HEAT for _, target, mode in seen)
    assert zone.target_temperature == 23.5


@pytest.mark.asyncio
async def test_replay_bezel_in_manual_and_in_antifreeze_then_home_assistant_write(hass: HomeAssistant, rows: list[dict]) -> None:
    """Run 5: 24.0 from the bezel, off in antifreeze, 7.5 after the bezel press in antifreeze, 23.5 after the write."""
    zone = await _setup_zone_2(hass)
    evt = _evt(rows, RUN5)
    seen = _replay(zone, evt)
    by_frame = {}
    for frame, target, mode in seen:
        by_frame.setdefault(frame, []).append((target, mode))
    assert seen[0] == (dim(12, "0240"), 24.0, None)
    assert seen[1] == (SEASON, 24.0, HVACMode.HEAT)
    assert by_frame[ANTIFREEZE][0] == (24.0, HVACMode.OFF)
    assert by_frame[dim(12, "0075")][0] == (7.5, HVACMode.HEAT), "the bezel press in antifreeze: heating at 7.5"
    assert by_frame[dim(14, "0075")][0] == (7.5, HVACMode.HEAT)
    assert by_frame[dim(12, "0235")][0] == (23.5, HVACMode.HEAT), "the Home Assistant write: 23.5 at once"
    assert zone.target_temperature == 23.5 and zone.hvac_mode == HVACMode.HEAT
    assert zone.current_temperature == 23.8


@pytest.mark.asyncio
async def test_replay_templates_setpoints_follow_dimension_12(hass: HomeAssistant, rows: list[dict]) -> None:
    """Run 4: the entity shows 21.0, 25.0 (cooling comfort), 22.5 and 23.5 as dimension 12 reports them.

    The mode derived from ``*#4*2*7*2*3##`` (cooling comfort) is not pinned here: the pinned OWNd
    reads it as a cooling context and the entity keeps that mode through the later season frames;
    the fixture README notes the observation.
    """
    zone = await _setup_zone_2(hass)
    seen = _replay(zone, _evt(rows, RUN4))
    targets = [target for frame, target, _ in seen if frame.startswith("*#4*2*12*")]
    assert [v for i, v in enumerate(targets) if i == 0 or v != targets[i - 1]] == [21.0, 25.0, 22.5, 23.5]
    assert zone.target_temperature == 23.5
    assert zone.current_temperature == 23.9
