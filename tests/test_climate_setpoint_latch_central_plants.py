"""The setpoint-in-force hold (dimension 12) applies only where ``*#4*Z##`` can refresh it.

A gateway that answers the zone status request (F461, MyHomeServer1) reports dimension 12
and 14 together on every poll and after every write, so the hold on dimension 12 is refreshed
by the next poll. A central-unit plant behind an SCS gateway (MH200N, F453AV) is never polled
per zone (#674) and updates a zone with dimension 14 (and 13) alone, so there the commanded
setpoint plus the knob offset is shown, as before #791. Replays of the captured frames:

* MH200N (``tests/fixtures/traces/issue_466/myhome_sweep_MH200N_all_2026-09-25T14-40-10.json``,
  no ``*#4*2##`` sent): zone 2 reports ``*#4*2*12*0105*3##`` spontaneously, then 9.7 s and
  20.6 s later ``*#4*2*14*0100*3##`` + ``*#4*2*13*00##`` + ``*4*110*#2##`` with no dimension 12.
* F453AV (``tests/fixtures/traces/issue_619/``, passive captures): the 10:29:57 sweep reports
  zone 14 with ``*#4*14*12*0080*3##`` then ``*#4*14*14*0100*3##``; Daily Holiday 33 minutes
  later (``11-02-55.Holiday.ON``, ``*4*115#1101*#0##``) emits 23 dimension 14 frames (22 of them
  ``*#4*Z*14*0100*3##``) and no dimension 12.
* F461 2.0.11 (``tests/fixtures/traces/f461_live_2026-10-23/``): ``*#4*2##`` is answered with
  dimension 12 (23.5) then dimension 14 (21.0); the write ``*#4*2*#14*0235*3##`` is echoed with
  dimension 12 alone and dimension 14 catches up in the next poll reply, after dimension 12.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.climate import MyHOMEClimate

TRACES = Path(__file__).resolve().parent / "fixtures" / "traces"
MH200N_SWEEP = TRACES / "issue_466" / "myhome_sweep_MH200N_all_2026-09-25T14-40-10.json"
F453AV_SWEEP = TRACES / "issue_619" / "myhome_trace_F453AV_who4_2026-10-11T10-29-57.json"
F453AV_HOLIDAY_ON = TRACES / "issue_619" / "myhome_trace_F453AV_who4_2026-10-11T11-02-55.Holiday.ON.json"
F461_LIVE = TRACES / "f461_live_2026-10-23" / "myhome_trace_F461_live_2026-10-23.json"


def _frames(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["frames"]


def _who_where(raw: str) -> tuple[str, str]:
    """WHO and WHERE of a frame: ``*WHO*WHAT*WHERE##`` or ``*#WHO*WHERE*DIM...##``."""
    parts = raw.rstrip("#").split("*")
    return (parts[1][1:], parts[2]) if raw.startswith("*#") else (parts[1], parts[3])


def _zone_frames(frames: list[dict], where: str, run: str | None = None) -> list[str]:
    """The received WHO 4 frames of one zone (``Z`` and the sub-central form ``#Z``)."""
    return [
        frame["raw"]
        for frame in frames
        if frame["direction"] == "rx"
        and _who_where(frame["raw"]) in (("4", where), ("4", f"#{where}"))
        and (run is None or frame.get("run") == run)
    ]


def _zone(hass, where: str, zone_status: bool) -> MyHOMEClimate:
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:04:66"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.has_central_unit = False
    gateway.supports_zone_status = zone_status
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=False, fan=False, standalone=None, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _feed(zone: MyHOMEClimate, frames: list[str]) -> None:
    for raw in frames:
        zone.handle_event(OWNHeatingEvent(raw))


# ------------------------------------------------------------------ central-unit plants (not pollable)

async def test_mh200n_zone_2_follows_the_dimension_14_update_reported_without_dimension_12(hass):
    """Frames 159-191 of the sweep: 12 = 10.5 at 14:39:27, then 14 = 10.0 at :37 and :48, no dimension 12."""
    frames = _frames(MH200N_SWEEP)
    assert not any(f["direction"] == "tx" and f["raw"] == "*#4*2##" for f in frames)
    raws = _zone_frames(frames, "2")
    assert raws.count("*#4*2*12*0105*3##") == 1 and raws.count("*#4*2*14*0100*3##") == 2
    first_12 = raws.index("*#4*2*12*0105*3##")
    assert not any(raw.startswith("*#4*2*12*") for raw in raws[first_12 + 1 :])

    zone = _zone(hass, "2", zone_status=False)
    assert zone._gateway_supports_zone_status is False
    _feed(zone, raws[: first_12 + 1])
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 10.5  # the spontaneous report, until the zone says more
    _feed(zone, raws[first_12 + 1 :])
    assert zone.target_temperature == 10.0  # the later dimension 14 is the zone's setpoint now
    assert zone.extra_state_attributes["local_target_temperature"] == 10.0
    assert zone._target_temperature == 10.0


async def test_f453av_zone_14_holiday_on_updates_the_zone_without_dimension_12(hass):
    """Sweep: 12 = 8.0 then 14 = 10.0 for zone 14; Daily Holiday: ``*#4*14*14*0100*3##``, no dimension 12."""
    holiday = _frames(F453AV_HOLIDAY_ON)
    assert sum(f["raw"] == "*4*115#1101*#0##" for f in holiday) == 1
    holiday_who4 = [f["raw"] for f in holiday if _who_where(f["raw"])[0] == "4"]
    assert sum(raw.startswith("*#4*") and raw.split("*")[3] == "14" for raw in holiday_who4) == 23
    assert sum("*14*0100*3##" in raw for raw in holiday_who4) == 22  # zone 17 reports 26.0
    assert not any(raw.startswith("*#4*") and raw.split("*")[3] == "12" for raw in holiday_who4)

    zone = _zone(hass, "14", zone_status=False)
    sweep = _zone_frames(_frames(F453AV_SWEEP), "14")
    assert sweep.index("*#4*14*12*0080*3##") < sweep.index("*#4*14*14*0100*3##")
    _feed(zone, sweep[: sweep.index("*#4*14*12*0080*3##") + 1])
    assert zone.target_temperature == 8.0
    _feed(zone, sweep[sweep.index("*#4*14*12*0080*3##") + 1 :])
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 10.0  # the central unit's setpoint for the zone, as before #791

    on_holiday = _zone_frames(holiday, "14")
    assert on_holiday == ["*4*111*#14##", "*4*111*#14##", "*#4*14*0*0280##", "*#4*14*14*0100*3##"]
    _feed(zone, on_holiday)
    assert zone.target_temperature == 10.0
    assert zone.extra_state_attributes["local_target_temperature"] == 10.0


async def test_a_knob_turn_reported_by_dimension_13_alone_moves_the_slider(hass):
    """MH200N dial turns emit ``*#4*Z*13*OO##`` only (frames 106-130 of the sweep, zone 4)."""
    zone = _zone(hass, "4", zone_status=False)
    _feed(zone, ["*4*110*#4##", "*#4*4*12*0100*3##", "*#4*4*14*0100*3##", "*#4*4*13*00##"])
    assert zone.target_temperature == 10.0
    _feed(zone, ["*#4*4*13*03##"])
    assert zone.target_temperature == 13.0
    assert zone.extra_state_attributes["local_offset"] == 3
    _feed(zone, ["*#4*4*13*13##"])  # -3
    assert zone.target_temperature == 7.0


async def test_an_scs_gateway_shows_the_commanded_setpoint_plus_offset_as_before(hass):
    """The F461 reply order replayed on a gateway that cannot be polled per zone: 21.0 + offset (status quo)."""
    zone = _zone(hass, "2", zone_status=False)
    _feed(zone, ["*#4*2*0*0239##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*00##"])
    assert zone.target_temperature == 21.0
    assert zone._local_target_reported is False
    _feed(zone, ["*#4*2*12*0235*3##"])
    assert zone.target_temperature == 23.5  # the last report is shown, nothing is held
    _feed(zone, ["*#4*2*13*02##"])
    assert zone.target_temperature == 25.5  # nominal re-derived from the report (23.5) plus the offset


# ------------------------------------------------------------------ pollable gateways keep the hold

async def test_f461_live_poll_replies_show_the_setpoint_in_force(hass):
    """run1: ``*#4*2##`` → 0, 12 = 23.5, season, 14 = 21.0, 13; a second poll 27 s later, the same."""
    frames = _frames(F461_LIVE)
    run1 = [
        (f["timestamp"], f["raw"])
        for f in frames
        if f.get("run") == "run1_oracle_replay_readonly" and f["raw"] in _zone_frames(frames, "2", "run1_oracle_replay_readonly")
    ]
    polls = [raw for t, raw in run1 if 325.0 <= t <= 353.0]
    assert polls == [
        "*#4*2*0*0239##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*00##",
        "*#4*2*60*39##",
        "*#4*2*0*0239##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*00##",
    ]
    zone = _zone(hass, "2", zone_status=True)
    _feed(zone, polls[:5])
    assert zone.target_temperature == 23.5
    assert zone._target_temperature == 21.0
    assert zone._local_target_reported is True
    _feed(zone, polls[5:])
    assert zone.target_temperature == 23.5
    assert zone.hvac_mode == HVACMode.HEAT


async def test_f461_live_write_echo_then_poll_keeps_the_written_setpoint(hass):
    """run3: the write is echoed by dimension 12 alone; dimension 14 catches up in the next poll, after 12."""
    frames = _frames(F461_LIVE)
    run3 = _zone_frames(frames, "2", run="run3_climate_setpoint_restore")
    assert run3 == [
        "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*0*0239##",
        "*#4*2*0*0239##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0235*3##", "*#4*2*13*00##",
    ]
    zone = _zone(hass, "2", zone_status=True)
    _feed(zone, _zone_frames(frames, "2", run="run1_oracle_replay_readonly")[:5])
    assert zone.target_temperature == 23.5
    await zone.async_set_temperature(temperature=23.5)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*2*#14*0235*3##"
    _feed(zone, run3)
    assert zone.target_temperature == 23.5
    assert zone._target_temperature == 23.5
    assert zone._local_target_reported is True


async def test_antifreeze_split_seen_live_on_2026_10_24(hass):
    """``*4*102*2##`` → 12 = 7.0 while 14 = 23.5 stays; the poll reply shows 7.0; the restore shows 23.5."""
    zone = _zone(hass, "2", zone_status=True)
    _feed(zone, ["*#4*2*0*0241##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0235*3##", "*#4*2*13*00##"])
    _feed(zone, ["*#4*2*12*0070*3##", "*4*102*2##"])
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.target_temperature == 7.0
    _feed(zone, ["*#4*2*0*0241##", "*#4*2*12*0070*3##", "*4*102*2##", "*#4*2*14*0235*3##", "*#4*2*13*00##"])
    assert zone.target_temperature == 7.0
    assert zone._target_temperature == 23.5
    _feed(zone, ["*4*1*2##", "*#4*2*12*0235*3##", "*#4*2*14*0235*3##"])
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 23.5


@pytest.mark.parametrize("zone_status", [True, False])
async def test_a_poll_or_report_with_dimension_12_last_shows_it_on_both_kinds_of_gateway(hass, zone_status):
    zone = _zone(hass, "2", zone_status=zone_status)
    _feed(zone, ["*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*00##", "*#4*2*12*0235*3##"])
    assert zone.target_temperature == 23.5
    assert zone._local_target_reported is zone_status
