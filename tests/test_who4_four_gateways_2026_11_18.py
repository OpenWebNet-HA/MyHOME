"""Four gateways on two live plants, 2026-11-18 (tests/fixtures/traces/who4_four_gateways_2026_11_18).

* A 3488 3.2.5 on the bus of a MyHomeServer1 3.87.13 (34 standalone thermostats, no central
  unit). The setup form suggests the model name MyHomeServer1 for a gateway typed by hand,
  and under that name the 3488 is asked what it answers: it refuses ``*#4*0##`` at once and
  ``*#4*#0##`` after 2.07 s (no central unit), and answers ``*#4*0#0*20##`` and each zone read.
* What a zone found on the bus offers. ``myhome.yaml`` says whether a zone cools (``cool:``,
  false unless stated); the bus did not, and every zone found on it offered ``cool``: on the
  29 heating-only zones of that plant Home Assistant offered a mode that stops the floor
  heating (``*4*0*49##`` after ``cool`` on zone 49). The valve status tells them apart:
  ``*#4*Z*19*CV*HV##`` has 0 on the conditioning side of a zone with no actuator there, and
  the fan coil's state on a zone that cools.
Frames are the captures of that fixture and of standalone_zone_2026_11_18 unless marked BUILT.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.core import HomeAssistant, State
from OWNd.message import OWNEvent, OWNHeatingEvent, OWNMessage

from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _replay
from tests.test_f455_zone_polling import _handler
from tests.test_issue_944_actuator_answer_not_central import _plant as _bus_plant
from tests.test_issue_944_actuator_answer_not_central import _receive
from tests.test_thermo_discovery_at_start import _plant as _start_plant

FRAMES = json.loads(
    (Path(__file__).parent / "fixtures" / "traces" / "who4_four_gateways_2026_11_18" / "frames.json").read_text(
        encoding="utf-8"
    )
)


def _answers(trace: str) -> dict[str, tuple[list[str], str, float]]:
    """request -> (reply frames, ``ack`` / ``nack``, seconds from the request to the end of its answer)."""
    out: dict[str, tuple[list[str], str, float]] = {}
    request, sent_at, replies = "", 0.0, []

    def seconds(stamp: str) -> float:
        hours, minutes, rest = stamp.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)

    for stamp, direction, raw in FRAMES[trace]:
        if direction == "tx":
            request, sent_at, replies = raw, seconds(stamp), []
        elif raw in ("*#*1##", "*#*0##"):
            out[request] = (replies, "ack" if raw == "*#*1##" else "nack", round(seconds(stamp) - sent_at, 2))
        else:
            replies.append(raw)
    return out


@pytest.fixture
def no_entity_base() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _found_zone(where: str, *, has_central_unit: bool = False, cooling: bool | None = None) -> Any:
    """A zone as ``climate_discovery`` builds it from the bus: nothing says whether it cools."""
    from custom_components.myhome.climate import MyHOMEClimate

    plant = _Plant(has_central_unit=has_central_unit)
    hass = MagicMock()
    hass.data = {}
    zone = MyHOMEClimate(
        hass=hass, name=f"Climate Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=cooling, fan=False, standalone=None, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=plant.gateway,
    )
    zone.platform = MagicMock()
    zone.entity_id = "climate.test"
    setattr(zone, "async_schedule_update_ha_state", MagicMock())
    setattr(zone, "async_write_ha_state", MagicMock())
    plant.entity = zone
    zone._plant = plant
    return zone


# ---------------------------------------------------------------- the 3488


def test_a_3488_answers_what_the_sweep_asks_under_the_name_the_setup_form_suggests(hass: HomeAssistant) -> None:
    asked = _answers("g3488_who4_requests")
    assert asked["*#4*0##"] == ([], "nack", 0.07)  # the general request: refused at once, no frame
    assert asked["*#4*#0##"] == ([], "nack", 2.07)  # no central unit on this bus: a cheap refusal
    assert asked["*#4*1*0##"][1:] == ("nack", 2.09)  # a zone that does not exist
    every = _answers("g3488_all_zone_requests")
    assert every["*#4*0#0*20##"][1] == "ack" and len(every["*#4*0#0*20##"][0]) == 8  # the first eight of the burst
    assert every["*#4*0*60##"][1] == "ack"

    handler = _handler(hass, "MyHomeServer1")
    who4 = [frame for frame in handler._discovery_frames() if frame.startswith("*#4*")]
    assert who4 == ["*#4*#0##", "*#4*0#0*20##"]  # not the refused `*#4*0##`
    assert handler.unanswered_zone_read_is_cheap is True
    assert handler.all_zone_request(20) == "*#4*0#0*20##" and handler.all_zone_request(60) == "*#4*0*60##"


def test_a_3488_zone_answers_the_reads_a_zone_sends() -> None:
    asked = _answers("g3488_who4_requests")
    assert asked["*#4*47##"][0] == [
        "*#4*47*0*0234##", "*#4*47*12*0225*3##", "*4*1*47##", "*#4*47*14*0225*3##", "*#4*47*13*00##",
    ]
    assert asked["*#4*47*0##"] == (["*#4*47*0*0234##"], "ack", 0.16)
    assert asked["*#4*47*12##"][0] == ["*#4*47*12*0225*3##", "*4*1*47##"]  # the setpoint in force with the mode frame
    assert asked["*#4*47*60##"][0] == ["*#4*47*60*56##"]
    assert asked["*#4*47*11##"][1] == "nack"  # its fan speed: refused, as on the F461 in the heating season
    for raw in (frame for replies, _result, _took in asked.values() for frame in replies):
        assert isinstance(OWNMessage.parse(raw), OWNHeatingEvent), raw


# ---------------------------------------------------------------- what a zone found on the bus offers


def test_the_valve_status_of_the_two_plants() -> None:
    bus1, bus2 = _answers("f461_valve_status"), _answers("g3488_valve_status")
    assert bus1["*#4*3*19##"][0] == ["*#4*3*19*0*1##"]  # heating only, heating
    assert bus1["*#4*4*19##"][0] == ["*#4*4*19*5*1##"]  # a cooling fan coil (off), heating valve on
    assert bus1["*#4*2*19##"][0] == ["*#4*2*19*8*5##"]  # fan coil on both sides, cooling
    for zone in ("49", "65", "59"):  # heating only
        assert bus2[f"*#4*{zone}*19##"][0] == [f"*#4*{zone}*19*0*0##"]
    for zone in ("47", "60", "48"):  # a cooling fan coil
        assert bus2[f"*#4*{zone}*19##"][0] == [f"*#4*{zone}*19*5*0##"]


@pytest.mark.parametrize(
    ("where", "frame", "cools"),
    [
        ("3", "*#4*3*19*0*1##", False),
        ("49", "*#4*49*19*0*0##", False),
        ("4", "*#4*4*19*5*1##", True),
        ("2", "*#4*2*19*8*5##", True),
        ("47", "*#4*47*19*5*0##", True),
    ],
)
def test_a_zone_found_on_the_bus_cools_once_its_valve_status_shows_a_conditioning_side(
    no_entity_base: None, where: str, frame: str, cools: bool
) -> None:
    zone = _found_zone(where)
    assert zone.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT]  # as `myhome.yaml` takes a zone by default
    _replay(zone, [frame])
    assert (HVACMode.COOL in zone.hvac_modes) is cools
    assert zone.extra_state_attributes.get("cooling_seen") is (True if cools else None)


def test_a_zone_reported_in_conditioning_cools(no_entity_base: None) -> None:
    zone2 = _found_zone("2")
    _replay(zone2, ["*#4*2*12*0200*3##", "*4*0*2##"])  # its own season frame (standalone_zone_2026_11_18)
    assert zone2.hvac_mode == HVACMode.COOL
    assert zone2.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]

    zone5 = _found_zone("5", has_central_unit=True)
    zone5.handle_event(OWNEvent.parse("*4*202*#0##"))  # the central unit itself (issue_453): says nothing about zone 5
    assert HVACMode.COOL not in zone5.hvac_modes
    zone5.handle_event(OWNEvent.parse("*4*210*#12##"))  # the central unit about another zone (issue_629)
    assert HVACMode.COOL not in zone5.hvac_modes
    zone5.handle_event(OWNEvent.parse("*4*210*#5##"))  # about this one (issue_629): manual conditioning
    assert zone5.hvac_mode == HVACMode.COOL and HVACMode.COOL in zone5.hvac_modes
    zone5.handle_event(OWNEvent.parse("*4*110*#5##"))  # BUILT from *4*110*#3##: heating again
    assert HVACMode.COOL in zone5._configured_hvac_modes()  # never unlearned


def test_what_myhome_yaml_says_is_not_changed_by_the_bus(no_entity_base: None) -> None:
    heating_only = _found_zone("47", cooling=False)
    _replay(heating_only, ["*#4*47*19*5*0##"])
    assert HVACMode.COOL not in heating_only.hvac_modes
    assert "cooling_seen" not in heating_only.extra_state_attributes
    cools = _found_zone("49", cooling=True)
    assert HVACMode.COOL in cools.hvac_modes  # at once, with no frame
    assert "cooling_seen" not in cools.extra_state_attributes


def test_frames_that_show_no_conditioning_side(no_entity_base: None) -> None:
    zone = _found_zone("47")
    # BUILT: zone 47 behind interface 01 is another zone. Its valve status (the captured
    # `*#4*47*19*5*0##` with the interface on its WHERE) and its season.
    for raw in ("*#4*47#4#01*19*5*0##", "*4*0*47#4#01##"):
        other_bus = OWNEvent.parse(raw)
        assert isinstance(other_bus, OWNHeatingEvent) and other_bus.interface == "01", raw
        zone.handle_event(other_bus)
    assert HVACMode.COOL not in zone.hvac_modes
    empty = MagicMock(spec=OWNHeatingEvent)
    empty.interface = None
    empty.dimension_value = []
    zone._note_conditioning_side(empty)  # a frame with no value: nothing learned
    assert HVACMode.COOL not in zone.hvac_modes


async def test_an_active_actuator_is_not_called_heating_before_the_zone_says_what_it_does(no_entity_base: None) -> None:
    """Zone 2's fan coil answers the sweep at speed 3 half a minute before its season frame is read."""
    zone2 = _found_zone("2")
    _replay(zone2, ["*#4*2#1*20*8##"])
    assert zone2.hvac_action != HVACAction.HEATING
    _replay(zone2, ["*4*0*2##", "*#4*2#1*20*8##"])
    assert zone2.hvac_action == HVACAction.COOLING


@pytest.mark.parametrize(
    ("saved_mode", "attributes", "restored_mode", "cools"),
    [
        # saved by this version: what the bus had shown
        ("heat", {"cooling_seen": True}, HVACMode.HEAT, True),
        ("cool", {"cooling_seen": True}, HVACMode.COOL, True),  # not `off`: the zone was cooling
        ("off", {"cooling_seen": True}, HVACMode.OFF, True),
        # saved by 2.0.0, which has no `cooling_seen`
        ("cool", {}, HVACMode.COOL, True),  # it was in conditioning
        ("heat", {"fan_coil_cooling": True, "fan_coil_heating": False}, HVACMode.HEAT, True),  # a cooling fan coil
        ("heat", {"fan_coil_cooling": False, "fan_coil_heating": False}, HVACMode.HEAT, False),
        ("heat", {}, HVACMode.HEAT, False),  # nothing says it cools: learned from the bus again
        ("off", {"season": "conditioning"}, HVACMode.OFF, False),  # a saved season alone is not taken
    ],
)
async def test_what_the_saved_state_says_about_cooling_is_applied_before_the_saved_mode(
    no_entity_base: None, saved_mode: str, attributes: dict[str, Any], restored_mode: HVACMode, cools: bool
) -> None:
    """A zone found on the bus and saved while cooling came back as ``off``; with ``cool: true`` it came back as ``cool``."""
    zone = _found_zone("47")
    await zone.async_restore_last_state(State("climate.test", saved_mode, attributes))
    assert zone.hvac_mode == restored_mode
    assert (HVACMode.COOL in zone.hvac_modes) is cools
    assert zone.extra_state_attributes.get("cooling_seen") is (True if cools else None)  # saved again from now on

    configured = _found_zone("47", cooling=True)  # the same zone with `cool: true` in myhome.yaml
    await configured.async_restore_last_state(State("climate.test", saved_mode, attributes))
    if cools:  # found on the bus and configured come back alike
        assert (zone.hvac_mode, zone.hvac_modes) == (configured.hvac_mode, configured.hvac_modes)


async def test_a_zone_configured_as_heating_only_stays_so_whatever_was_saved(no_entity_base: None) -> None:
    configured = _found_zone("47", cooling=False)
    await configured.async_restore_last_state(State("climate.test", "cool", {"cooling_seen": True, "fan_coil_cooling": True}))
    assert HVACMode.COOL not in configured.hvac_modes
    assert configured.hvac_mode == HVACMode.OFF  # as before this change: a mode the zone is not configured for


async def test_the_zones_and_the_central_unit_built_from_the_bus(hass: HomeAssistant) -> None:
    handler, zones = await _bus_plant(hass)
    await _receive(hass, handler, ["*4*102*#0##", "*#4*3*0*0260##", "*#4*4*0*0258##"])
    by_where = {zone._where: zone for zone in zones}
    assert by_where["#0"]._cooling is True and by_where["#0"]._cooling_configured is True  # a central unit runs both seasons
    assert by_where["3"]._cooling is False and by_where["3"]._cooling_configured is False
    await _receive(hass, handler, ["*#4*3*19*0*1##", "*#4*4*19*5*1##"])
    assert by_where["3"]._cooling is False
    assert by_where["4"]._cooling is True


# ---------------------------------------------------------------- the starts, replayed


def _events(trace: str) -> list[str]:
    """What the instance's event session received, as the gateway dispatches it."""
    return [raw for _stamp, direction, raw in FRAMES[trace] if direction == "rx" and isinstance(OWNMessage.parse(raw), OWNHeatingEvent)]


def _written(trace: str, direction: str = "tx") -> list[str]:
    return [raw for _stamp, way, raw in FRAMES[trace] if way == direction]


BUS_2_ZONES = [str(zone) for zone in (*range(35, 54), 55, *range(57, 71))]  # the 34 thermostats of bus 2
BUS_2_COOLING = {"47", "48", "57", "60", "70"}  # the five with a cooling fan coil (`cool: true` in the plant's myhome.yaml)


@pytest.mark.parametrize("trace", ["g3488_blank_start", "myhomeserver1_blank_start"])
async def test_a_start_on_bus_2_finds_the_34_zones_and_the_five_that_cool(hass: HomeAssistant, trace: str) -> None:
    async def read(_where: str) -> bool | None:  # the reads' answers are in the trace
        return None

    handler, zones = await _start_plant(hass, read, model="MyHomeServer1")
    await _receive(hass, handler, _events(trace))
    by_where = {zone._where: zone for zone in zones}
    assert sorted(by_where, key=int) == BUS_2_ZONES
    assert handler.has_central_unit is False
    assert {where for where, zone in by_where.items() if zone._cooling} == BUS_2_COOLING
    for where, zone in by_where.items():
        cools = where in BUS_2_COOLING
        assert zone.hvac_mode == HVACMode.HEAT, where
        assert zone.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, *([HVACMode.COOL] if cools else [])], where

    # What was asked: every actuator once, the central unit once (refused: there is none).
    assert _written(trace).count("*#4*0#0*20##") == 1
    assert _written(trace, "nack").count("*#4*#0##") == 1 and "*#4*#0##" not in _written(trace)
    assert "*#4*0##" not in _written(trace) + _written(trace, "nack")


async def test_the_start_on_bus_1_finds_what_each_zone_is(hass: HomeAssistant) -> None:
    handler, zones = await _bus_plant(hass)  # an F461
    await _receive(hass, handler, _events("f461_blank_start"))
    by_where = {zone._where: zone for zone in zones}
    assert sorted(by_where) == ["#0", "2", "3", "4"]
    assert by_where["2"]._cooling is True and by_where["2"].hvac_mode == HVACMode.COOL
    assert by_where["3"]._cooling is False  # two heating valves
    assert by_where["4"]._cooling is True  # a heating valve and a cooling fan coil
    assert _written("f461_blank_start").count("*#4*#0##") == 1  # the sweep, not the new entity as well
    assert _written("f461_blank_start").count("*#4*0#0*20##") == 1


async def test_an_f455_alone_asks_the_central_unit_once_it_is_known_from_a_zone_report(
    hass: HomeAssistant, fast_bus_pacing: None
) -> None:
    async def read(_where: str) -> bool | None:
        return None

    handler, zones = await _start_plant(hass, read, model="F455")
    handler._initial_discovery_done.set()
    events = _events("f455_alone_blank_start")
    known_from = events.index("*4*110*#3##")
    assert not [raw for raw in events[:known_from] if raw.endswith("*#0##")]  # no frame of the central unit before it
    await _receive(hass, handler, events[: known_from + 1])
    await hass.async_block_till_done(wait_background_tasks=True)
    asked = [str(call.args[0]) for call in handler.send_status_request.await_args_list]
    assert asked.count("*#4*#0##") == 1  # next to the zones' own fan coil requests
    await _receive(hass, handler, events[known_from + 1 :])  # its answer is in the trace: *4*102*#0##
    await hass.async_block_till_done(wait_background_tasks=True)
    asked = [str(call.args[0]) for call in handler.send_status_request.await_args_list]
    assert asked.count("*#4*#0##") == 1  # the entity created from the answer does not ask again
    assert sorted(zone._where for zone in zones) == ["#0", "2", "3", "4"]
    assert _written("f455_alone_blank_start").count("*#4*#0##") == 1  # as the live instance did


def test_the_start_with_the_central_unit_off_asked_zone_2_s_actuators_again() -> None:
    rows = FRAMES["f461_central_off_start"]
    asked = [index for index, (_stamp, direction, raw) in enumerate(rows) if direction == "tx" and raw == "*#4*0#0*20##"]
    off = [index for index, (_stamp, direction, raw) in enumerate(rows) if direction == "rx" and raw == "*4*103*#0##"]
    assert len(asked) == 2 and asked[0] < off[-1] < asked[1]  # the sweep, the central unit's off report, then again
    after = [raw for _stamp, direction, raw in rows[off[-1] : asked[1]] if direction == "rx"]
    assert "*4*0*2##" in after and "*#4*2#1*20*8##" in after  # zone 2 in conditioning, its fan coil at speed 3
