"""Fourth review of #955 (T1 to T7), 2026-11-19.

* T1: a central unit's report from behind an interface marked the gateway's own bus as having
  a central unit; frames of a zone behind an interface were delivered to the local zone with
  the same number, and a zone behind an interface seen first kept the local zone from being
  created.
* T2: the modes a zone offers were not recomputed when a mode frame changed its season, nor
  after a restore from a state that is no mode.
* T3: what an open valve is doing was decided when the valve reported and not again; a zone
  that was off, restored in the other season, or whose other valve had said "off" first kept
  a wrong action. A zone not known to cool was shown cooling in ``auto``.
* T4: a zone created by the reads of the valve-only zones polled outside the start-up poll
  lock; an address exposed as a virtual probe was read.
* T5: a central unit first seen in a reply on the command session did not tell the zones; the
  season check concluded from an answer that crossed a season change.
* T6: after a refused direct write the same frame was sent again; a refused mode command was
  not caught.

Frames are captures (tests/fixtures/traces: standalone_zone_2026_11_18,
who4_four_gateways_2026_11_18, issue_697) unless marked BUILT; ``test_the_frames_this_file_rests_on``
checks that. No capture holds a WHO 4 frame from behind an interface.
"""

import asyncio
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import CLIMATE_MODE_OFF, OWNHeatingCommand, OWNHeatingEvent, OWNMessage

from custom_components.myhome import climate as climate_module
from custom_components.myhome.climate_discovery import _zone_route_keys
from custom_components.myhome.where_grammar import (
    central_unit_season,
    is_central_unit_actuator_status,
    is_central_unit_report,
)
from tests.test_climate_standalone_zone_2026_11_18 import FRAMES as STANDALONE_ZONE
from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _replay, _zone
from tests.test_issue_944_actuator_answer_not_central import _plant as _bus_plant
from tests.test_issue_944_actuator_answer_not_central import _receive
from tests.test_thermo_discovery_at_start import _after_the_sweep
from tests.test_thermo_discovery_at_start import _plant as _start_plant
from tests.test_who4_four_gateways_2026_11_18 import FRAMES as FOUR_GATEWAYS
from tests.test_who4_four_gateways_2026_11_18 import _found_zone
from tests.test_zone_of_central_unit_kept_2026_11_19 import ISSUE_697, _setpoint

try:
    from scripts import update_roadmap_tables as roadmap
except ImportError:  # pragma: no cover - run from inside scripts/
    import update_roadmap_tables as roadmap

_TRACES = Path(__file__).parent / "fixtures" / "traces"
CAPTURED = (
    {row[2] for trace in (STANDALONE_ZONE, FOUR_GATEWAYS) for rows in trace.values() if isinstance(rows, list) for row in rows}
    | {raw for _direction, raw in ISSUE_697}
    # The F461 monitor of 2026-11-08 (the 3550 in conditioning, zone 4 in manual conditioning).
    | set(re.findall(r"\*#?4\*[^\"\s]*##", (_TRACES / "climate_mode_2026_11_08" / "frames.json").read_text(encoding="utf-8")))
)
# BUILT, all of them: no gateway has shown a WHO 4 frame with an interface number.
CENTRAL_REPORT_BEHIND_INTERFACE = "*4*110*#3#4#01##"
CENTRAL_ACTUATOR_STATUS_BEHIND_INTERFACE = "*#4*#0#4#01*20*1##"
ZONE_4_BEHIND_INTERFACE = ["*#4*4#4#01*0*0275##", "*4*0*4#4#01##", "*#4*4#4#01*19*5*1##"]
# BUILT: zone 3's second valve open (in every capture it is the first, ``*#4*3#1*20*1##``).
ZONE_3_SECOND_VALVE_ON = "*#4*3#2*20*1##"


@pytest.fixture(autouse=True)
def _short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(climate_module, "SETPOINT_CONFIRM_TIMEOUT", 0.05)
    monkeypatch.setattr(climate_module, "EMPTY_REPLY_SETTLE", 0.2)
    monkeypatch.setattr(climate_module, "STANDALONE_SEASON_CONFIRM_DELAY", 0.01)
    monkeypatch.setattr(climate_module, "PACED_READ_TIMEOUT", 0.5)
    monkeypatch.setattr(climate_module, "CENTRAL_SEASON_SETTLE", 0.3)


@pytest.fixture
def no_entity_base() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _modes(zone: Any) -> list[str]:
    return [str(mode) for mode in zone.hvac_modes]


def test_the_frames_this_file_rests_on() -> None:
    for raw in (
        "*4*1*4##", "*4*210*#4##", "*4*303*4##", "*#4*4#1*20*1##", "*#4*3#1*20*0##", "*#4*3#1*20*1##", "*#4*3#2*20*0##",
        "*4*1*3##", "*4*110*#3##", "*4*102*3##", "*4*202*3##", "*4*0*2##", "*#4*2#1*20*8##", "*4*303*3##", "*4*303*#3##",
        "*4*103*#3##", "*#4*4*0*0256##", "*4*21*#0##", "*4*110#0170*#0##",
    ):
        assert raw in CAPTURED, raw
    for raw in (CENTRAL_REPORT_BEHIND_INTERFACE, CENTRAL_ACTUATOR_STATUS_BEHIND_INTERFACE, *ZONE_4_BEHIND_INTERFACE):
        assert raw not in CAPTURED
        message = OWNMessage.parse(raw)
        assert isinstance(message, OWNHeatingEvent) and message.interface == "01", raw
    assert ZONE_3_SECOND_VALVE_ON not in CAPTURED


# ------------------------------------------------- T1: another bus's central unit and zones


async def test_a_central_unit_behind_an_interface_says_nothing_about_this_bus(hass: HomeAssistant) -> None:
    handler, zones = await _bus_plant(hass)
    told: list[bool] = []
    async_dispatcher_connect(hass, f"myhome_central_season_{handler.mac}", callback(lambda: told.append(True)))
    await _receive(hass, handler, ["*#4*2*0*0238##", "*4*0*2##"])
    zone2 = next(zone for zone in zones if zone._where == "2")
    assert zone2._standalone is True and HVACMode.AUTO in zone2.hvac_modes

    await _receive(hass, handler, [CENTRAL_REPORT_BEHIND_INTERFACE])
    assert is_central_unit_report(OWNMessage.parse(CENTRAL_REPORT_BEHIND_INTERFACE)) is True  # a central unit's, not this bus's
    assert handler.has_central_unit is False and handler.central_season is None
    assert central_unit_season(OWNMessage.parse(CENTRAL_REPORT_BEHIND_INTERFACE)) is None  # nor is its season this bus's
    assert central_unit_season(OWNMessage.parse("*4*0*2##")) is None  # a zone's own frame is no central unit's
    assert zone2._standalone is True and told == []  # still a standalone thermostat, written directly

    await _receive(hass, handler, ["*4*110*#3##"])  # this bus's central unit
    assert handler.has_central_unit is True and handler.central_season == "heating" and told == [True]
    assert zone2._standalone is False


async def test_an_actuator_status_of_a_central_unit_behind_an_interface_reaches_nobody(hass: HomeAssistant) -> None:
    message = OWNMessage.parse(CENTRAL_ACTUATOR_STATUS_BEHIND_INTERFACE)
    assert is_central_unit_actuator_status(message) is True
    assert is_central_unit_actuator_status(OWNMessage.parse("*#4*#0*20*1##")) is True  # the local one (#944)
    assert _zone_route_keys(message, None) == []
    handler, zones = await _bus_plant(hass)
    await _receive(hass, handler, ["*4*110#0170*#0##"])  # the local central unit (issue 697)
    central = next(zone for zone in zones if zone._central)
    before = dict(central.extra_state_attributes)
    await _receive(hass, handler, [CENTRAL_ACTUATOR_STATUS_BEHIND_INTERFACE])
    assert len(zones) == 1 and dict(central.extra_state_attributes) == before


def test_a_frame_from_behind_an_interface_is_routed_under_its_interface_only() -> None:
    assert _zone_route_keys(OWNMessage.parse(ZONE_4_BEHIND_INTERFACE[0]), None) == ["4#4#01"]
    assert set(_zone_route_keys(OWNMessage.parse(CENTRAL_REPORT_BEHIND_INTERFACE), None)) == {"3#4#01", "#3#4#01"}
    assert set(_zone_route_keys(OWNMessage.parse("*#4*4*0*0256##"), None)) == {"4"}  # the local zone: as before
    assert set(_zone_route_keys(OWNMessage.parse("*4*110*#3##"), None)) == {"3", "#3"}


async def test_the_zone_behind_an_interface_and_the_local_one_do_not_mix(hass: HomeAssistant) -> None:
    handler, zones = await _bus_plant(hass)
    await _receive(hass, handler, ["*#4*4*0*0256##", "*4*1*4##"])  # the local zone 4, heating at 25.6
    local = next(zone for zone in zones if zone._where == "4" and zone._interface is None)
    for raw in ZONE_4_BEHIND_INTERFACE:
        await _receive(hass, handler, [raw])
    routed = next(zone for zone in zones if zone._where == "4" and zone._interface == "01")
    assert (local.current_temperature, local.hvac_mode) == (25.6, HVACMode.HEAT)
    assert HVACMode.COOL not in local.hvac_modes
    assert (routed.current_temperature, routed.hvac_mode) == (27.5, HVACMode.COOL)


async def test_a_zone_behind_an_interface_seen_first_does_not_hide_the_local_one(hass: HomeAssistant) -> None:
    handler, zones = await _bus_plant(hass)
    await _receive(hass, handler, [ZONE_4_BEHIND_INTERFACE[0]])
    await _receive(hass, handler, ["*#4*4*0*0256##"])
    assert {(zone._where, zone._interface): zone.current_temperature for zone in zones} == {
        ("4", "01"): 27.5,
        ("4", None): 25.6,
    }


# ------------------------------------------------- T2: the offered modes follow the season


def test_a_zone_of_the_central_unit_takes_the_mode_of_the_new_season(no_entity_base: None) -> None:
    """``cool: true`` in ``myhome.yaml``: last seen in heating, then reported in manual conditioning."""
    zone4 = _zone(_Plant(), "4")
    _replay(zone4, ["*4*1*4##"])
    assert (zone4.hvac_mode, _modes(zone4)) == (HVACMode.HEAT, ["off", "auto", "heat"])
    zone4.handle_event(OWNMessage.parse("*4*210*#4##"))
    assert (zone4.hvac_mode, _modes(zone4)) == (HVACMode.COOL, ["off", "auto", "cool"])


@pytest.mark.parametrize("cooling", [None, False, True])  # found on the bus, `cool: false`, `cool: true`
def test_the_modes_follow_the_central_units_season_both_ways(no_entity_base: None, cooling: bool | None) -> None:
    """The season switch of #697 and of the live 3550 (2026-11-19): the zone follows with ``*4*202*3##``, then ``*4*102*3##``."""
    zone3 = _found_zone("3", has_central_unit=True, cooling=cooling)
    _replay(zone3, ["*4*1*3##"])
    assert _modes(zone3) == ["off", "auto", "heat"]
    _replay(zone3, ["*4*202*3##"])  # conditioning: `heat` is the off-season mode
    assert "heat" not in _modes(zone3)
    assert ("cool" in _modes(zone3)) is (cooling is not False)  # a found zone has learned it; `cool: false` has not
    _replay(zone3, ["*4*102*3##"])  # back to heating
    assert _modes(zone3) == ["off", "auto", "heat"]


async def test_cool_learned_in_summer_is_not_offered_after_a_restart_in_winter(no_entity_base: None) -> None:
    """Live 2026-11-19, zone 3 (two heating valves): ``cool`` was offered until the zone's next mode frame."""
    zone3 = _found_zone("3", has_central_unit=True)
    await zone3.async_restore_last_state(State("climate.test", "off", {"temperature": 7.0, "cooling_seen": True}))
    assert "cool" in _modes(zone3)  # nothing says the season yet
    _replay(zone3, ["*4*102*3##"])  # antifreeze: the zone's first frame after the restart
    assert _modes(zone3) == ["off", "auto", "heat"]


@pytest.mark.parametrize("state", ["unavailable", "unknown"])
async def test_a_restore_from_a_state_that_is_no_mode_still_offers_what_was_learned(no_entity_base: None, state: str) -> None:
    zone = _found_zone("47")
    await zone.async_restore_last_state(State("climate.test", state, {"cooling_seen": True}))
    assert zone.hvac_mode == HVACMode.OFF and "cool" in _modes(zone)


# ------------------------------------------------- T3: the action of an open valve


def _both(zone: str, **kwargs: Any) -> tuple[Any, Any]:
    """The zone found on the bus, and the same zone as ``myhome.yaml`` has it (heating only)."""
    return _found_zone(zone, **kwargs), _found_zone(zone, cooling=False, **kwargs)


def test_a_valve_that_opens_after_the_other_said_off(no_entity_base: None) -> None:
    for zone3 in _both("3"):
        _replay(zone3, ["*#4*3#1*20*0##", ZONE_3_SECOND_VALVE_ON])
        _replay(zone3, ["*4*1*3##"])
        assert (zone3.hvac_mode, zone3.hvac_action) == (HVACMode.HEAT, HVACAction.HEATING)
        _replay(zone3, ["*#4*3#2*20*0##"])  # both closed
        assert zone3.hvac_action == HVACAction.IDLE


def test_a_zone_that_was_off_when_its_valve_reported(no_entity_base: None) -> None:
    for zone4 in _both("4"):
        _replay(zone4, ["*4*303*4##", "*#4*4#1*20*1##"])
        _replay(zone4, ["*4*1*4##"])
        assert (zone4.hvac_mode, zone4.hvac_action) == (HVACMode.HEAT, HVACAction.HEATING)
        _replay(zone4, ["*4*303*4##"])  # switched off: its actuators are off with it
        assert (zone4.hvac_mode, zone4.hvac_action) == (HVACMode.OFF, HVACAction.OFF)


async def test_a_zone_restored_in_one_season_that_runs_in_the_other(no_entity_base: None) -> None:
    zone4 = _found_zone("4", cooling=True)
    await zone4.async_restore_last_state(State("climate.test", "heat", {"temperature": 22.0}))
    _replay(zone4, ["*#4*4#1*20*1##"])
    assert zone4.hvac_action == HVACAction.HEATING  # by the restored mode: nothing better is known
    _replay(zone4, ["*4*0*4##"])  # BUILT for zone 4 from the captured `*4*0*2##`: it is in conditioning
    assert (zone4.hvac_mode, zone4.hvac_action) == (HVACMode.COOL, HVACAction.COOLING)


def test_a_side_the_valve_status_named_is_kept(no_entity_base: None) -> None:
    """Dimension 19 says which side runs: the season frames that follow do not change that."""
    zone4 = _found_zone("4", cooling=True)
    _replay(zone4, ["*#4*4*19*0*1##"])  # BUILT from `*#4*3*19*0*1##`: the heating side on
    assert zone4.hvac_action == HVACAction.HEATING
    _replay(zone4, ["*4*0*4##"])  # BUILT, as above
    assert zone4.hvac_action == HVACAction.HEATING


def test_a_zone_not_known_to_cool_is_not_shown_cooling_in_auto(no_entity_base: None) -> None:
    for cooling, above_setpoint in ((None, None), (False, HVACAction.HEATING), (True, HVACAction.COOLING)):
        zone = _found_zone("4", cooling=cooling)
        zone._attr_hvac_mode = HVACMode.AUTO
        zone._target_temperature, zone._attr_native_current_temperature = 20.0, 22.0
        _replay(zone, ["*#4*4#1*20*1##"])
        assert zone.hvac_action is above_setpoint, cooling
        zone._attr_native_current_temperature = 18.0
        _replay(zone, ["*#4*4#1*20*1##"])
        assert zone.hvac_action == HVACAction.HEATING


# ------------------------------------------------- T4: the reads of the valve-only zones


async def test_a_zone_created_by_those_reads_polls_after_them_not_across(hass: HomeAssistant, fast_bus_pacing: None) -> None:
    reading: list[str] = []
    crossed: list[str] = []

    async def read(where: str) -> bool | None:
        reading.append(where)
        try:
            if where == "8":
                await _receive(hass, handler, ["*#4*8*0*0255##"])  # BUILT from `*#4*3*0*0255##`: zone 8's thermostat
                await asyncio.sleep(0.05)  # the zone is added while its read is still out
                return True
            return False
        finally:
            reading.remove(where)

    handler, zones = await _start_plant(hass, read, model="F455")
    polled = handler.send_status_request

    async def _poll(message: Any) -> Any:
        if reading:
            crossed.append(f"{message} during the read of zone {reading[0]}")
        return await polled(message)

    handler.send_status_request = _poll
    handler.request_zone_dimension = MagicMock(side_effect=lambda where, dim, **_k: crossed.extend([f"{where}*{dim}"] if reading else []) or True)
    await _receive(hass, handler, ["*#4*8#1*20*1##", "*#4*9#1*20*0##"])  # BUILT: two addresses with a plain valve
    await _after_the_sweep(hass, handler)
    assert [zone._where for zone in zones] == ["8"]
    assert handler.actuator_only_reads_running is False
    assert crossed == []


async def test_an_address_exposed_as_a_virtual_probe_is_not_read(hass: HomeAssistant) -> None:
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        return False

    handler, zones = await _start_plant(hass, read, model="F461")
    with patch(
        "custom_components.myhome.climate_discovery.is_exposed_on_bus",
        side_effect=lambda _hass, _entry, _who, where: str(where) == "7",
    ):
        await _receive(hass, handler, ["*#4*7#1*20*0##", "*#4*8#1*20*1##"])  # BUILT: two addresses with a plain valve
        await _after_the_sweep(hass, handler)
    assert asked == ["8"] and zones == []


# ------------------------------------------------- T5: both sessions tell the zones; the season check


async def test_a_central_unit_first_seen_on_the_command_session_tells_the_zones(hass: HomeAssistant) -> None:
    """Its status asked at start is answered on the command session: ``*4*21*#0##`` carries no season."""
    handler, zones = await _bus_plant(hass)
    told: list[bool] = []
    async_dispatcher_connect(hass, f"myhome_central_season_{handler.mac}", callback(lambda: told.append(handler.has_central_unit)))
    await _receive(hass, handler, ["*#4*2*0*0238##", "*4*0*2##"])
    events = handler._event_dispatcher
    reply = OWNMessage.parse("*4*21*#0##")
    news = events.note_central_unit(reply)
    assert news == (True, False) and handler.has_central_unit is True
    events.tell_central_unit(reply, *news)
    await hass.async_block_till_done()
    assert told == [True]
    assert events.note_central_unit(reply) == (False, False)  # once
    assert events.note_central_unit(OWNMessage.parse("*#4*2*0*0238##")) == (False, False)  # not a central unit's frame


async def test_the_command_session_notes_a_central_units_reply() -> None:
    """A central unit first seen in a reply on the command session, through the real session.

    Worker -> OWNCommandSession -> TCP -> mock gateway, which answers ``*#4*#3##`` as the live
    F461 did (thermo_discovery_at_start_2026_11_18/user_actions_discovered_F461.log:76). The
    central unit is noted before the entities are handed the frame, the zones are told and
    its status is asked once, after the frame was handed over: the order of the event session.
    """
    from tests.mock_gateway_harness import MockGatewayHarness
    from tests.test_command_session_contract import _handler, _run_one_frame

    replies = ["*4*110*#3##", "*#4*#3*14*0265*1##"]
    actions = (_TRACES / "thermo_discovery_at_start_2026_11_18" / "user_actions_discovered_F461.log").read_text(encoding="utf-8")
    assert f"READ via F461 *#4*#3## -> ('ack', {replies!r})" in actions

    def _gateway(frame: str, _writer: Any) -> str | None:
        if frame == "*99*0##":
            return "*#*1##"
        if frame == "*#4*#3##":
            return "".join(replies) + "*#*1##"
        return None

    harness = MockGatewayHarness()
    harness.set_custom_handler(_gateway)
    port = await harness.start()
    steps: list[tuple[Any, ...]] = []
    try:
        handler = _handler(port)
        mac = handler.mac
        handler.request_central_status = lambda: steps.append(("central status asked",))  # type: ignore[method-assign]
        assert handler.has_central_unit is False and handler.central_season is None

        def _dispatched(_hass: Any, signal: str, *args: Any) -> None:
            steps.append((signal, *(str(arg) for arg in args), handler.has_central_unit, handler.central_season))

        with patch("custom_components.myhome.gateway.async_dispatcher_send", _dispatched):
            await _run_one_frame(handler, "*#4*#3##")
    finally:
        await harness.stop()

    assert "*#4*#3##" in harness.received_messages
    assert handler.has_central_unit is True and handler.central_season == "heating"
    # Already known when the entities are handed the frame; told and asked once, after it.
    assert steps == [
        (f"myhome_message_{mac}", "*4*110*#3##", True, "heating"),
        (f"myhome_central_season_{mac}", True, "heating"),
        ("central status asked",),
        (f"myhome_message_{mac}", "*#4*#3*14*0265*1##", True, "heating"),
    ]


async def test_a_season_change_while_the_zone_is_read_cannot_mark_it(no_entity_base: None) -> None:
    """The zone is read because it is in the season the central unit is not in; a change of the
    central unit while that read is out makes the two agree, whatever the zone answers."""
    plant = _Plant()
    plant.gateway.central_season = "heating"
    plant.gateway.central_season_changed_at = None
    zone3 = _zone(plant, "3")
    loop = asyncio.get_running_loop()
    asked: list[float] = []

    def _request(where: str, dimension: int, **_kwargs: Any) -> bool:
        asked.append(time.monotonic())
        # The central unit goes to conditioning while the read is out; the zone answers as it was.
        plant.gateway.central_season = "conditioning"
        plant.gateway.central_season_changed_at = time.monotonic()
        loop.call_soon(zone3.handle_event, OWNMessage.parse("*4*202*3##"))
        return True

    plant.gateway.request_zone_dimension = MagicMock(side_effect=_request)
    plant.gateway.wait_for_initial_discovery = AsyncMock()
    zone3.hass.async_create_background_task = lambda coro, name=None: loop.create_task(coro)
    _replay(zone3, ["*4*202*3##"])  # in conditioning next to a central unit in heating: the check starts
    task = zone3._season_check_task
    assert task is not None
    await task
    assert len(asked) == 1 and zone3._standalone_detected is False


# ------------------------------------------------- T6: a refused direct form is not sent twice


def _f454(zone: str = "3") -> _Plant:
    plant = _Plant()
    plant.gateway.model = "F454"
    plant.gateway.standalone_detected_zones = set()
    return plant


async def test_after_the_refusal_the_direct_form_is_not_tried_again(no_entity_base: None) -> None:
    """A zone in protection, whose own report of the setpoint proves nothing: read, and no second direct frame."""
    plant = _f454()
    direct, via_central = _setpoint("3", 14.5, direct=True), _setpoint("3", 14.5, direct=False)
    plant.nack.add(direct)
    plant.answer(via_central, ["*#4*3*12*0145*3##"])
    plant.answer("*#4*3*14##", ["*#4*3*14*0145*3##"])  # BUILT: the written value read back
    zone3 = _zone(plant, "3")
    _replay(zone3, ["*4*202*3##"])
    zone3._mark_standalone_detected(by_season=True)
    await zone3.async_set_temperature(temperature=14.5)  # no error
    assert plant.sent == [direct, via_central, "*#4*3*14##"]
    assert zone3._standalone_detected is False


async def test_a_write_neither_form_delivers_is_reported_as_not_taken_through_the_central_unit(no_entity_base: None) -> None:
    plant = _f454()
    direct, via_central = _setpoint("3", 14.5, direct=True), _setpoint("3", 14.5, direct=False)
    plant.nack.add(direct)
    plant.answer("*#4*3*14##", ["*#4*3*14*0170*3##"])  # BUILT: 17.0, what it held
    zone3 = _zone(plant, "3")
    _replay(zone3, ["*4*1*3##"])
    zone3._mark_standalone_detected(by_season=True)
    with pytest.raises(HomeAssistantError) as raised:
        await zone3.async_set_temperature(temperature=14.5)
    assert raised.value.translation_key == "setpoint_not_taken_via_central"
    assert plant.sent == [direct, via_central, "*#4*3*14##"]


def _off(zone: str, *, direct: bool) -> str:
    return str(OWNHeatingCommand.set_mode(where=zone, mode=CLIMATE_MODE_OFF, standalone=direct))


async def test_a_refused_mode_command_goes_through_the_central_unit(no_entity_base: None) -> None:
    """#697: ``*4*303*3##`` "Could not send message", ``*4*303*#3##`` answered ``*4*303*3##``, ``*4*103*#3##``."""
    assert (_off("3", direct=True), _off("3", direct=False)) == ("*4*303*3##", "*4*303*#3##")
    plant = _f454()
    plant.nack.add("*4*303*3##")
    plant.answer("*4*303*#3##", ["*4*303*3##", "*4*103*#3##"])
    zone3 = _zone(plant, "3")
    _replay(zone3, ["*4*1*3##"])
    zone3._mark_standalone_detected(by_season=True)
    await zone3.async_set_hvac_mode(HVACMode.OFF)
    await asyncio.sleep(0)
    assert plant.sent == ["*4*303*3##", "*4*303*#3##"]
    assert zone3._standalone_detected is False and zone3.hvac_mode == HVACMode.OFF

    plant.sent.clear()
    await zone3.async_set_hvac_mode(HVACMode.OFF)
    assert plant.sent == ["*4*303*#3##"]  # a zone of the central unit: one frame, not waited for


async def test_the_same_for_heat_and_a_mode_command_that_is_taken_changes_nothing(no_entity_base: None) -> None:
    plant = _f454()
    zone3 = _zone(plant, "3")
    _replay(zone3, ["*4*1*3##", "*#4*3*14*0255*3##"])
    zone3._mark_standalone_detected(by_season=True)
    await zone3.async_set_hvac_mode(HVACMode.OFF)  # acknowledged: it is standalone as found
    assert plant.sent == ["*4*303*3##"] and zone3._standalone_detected is True

    plant.sent.clear()
    plant.nack.update(str(frame) for frame in (_setpoint_in("3", 25.5, "heat", direct=True),))
    await zone3.async_set_hvac_mode(HVACMode.HEAT)
    assert plant.sent == [_setpoint_in("3", 25.5, "heat", direct=True), _setpoint_in("3", 25.5, "heat", direct=False)]
    assert zone3._standalone_detected is False


def _setpoint_in(zone: str, temperature: float, mode: str, *, direct: bool) -> str:
    from OWNd.message import CLIMATE_MODE_COOL, CLIMATE_MODE_HEAT

    season = CLIMATE_MODE_HEAT if mode == "heat" else CLIMATE_MODE_COOL
    return str(OWNHeatingCommand.set_temperature(where=zone, temperature=temperature, mode=season, standalone=direct))


async def test_a_command_that_cannot_be_built_sends_nothing(no_entity_base: None) -> None:
    plant = _f454()
    zone3 = _zone(plant, "3")
    await zone3._send_in_zone_form(lambda _direct: None)
    assert plant.sent == []


async def test_a_configured_standalone_zone_is_not_waited_for(no_entity_base: None) -> None:
    plant = _f454()
    plant.nack.add("*4*303*3##")
    zone3 = _zone(plant, "3", standalone=True)
    await zone3.async_set_hvac_mode(HVACMode.OFF)
    assert plant.sent == ["*4*303*3##"]


# ------------------------------------------------- T7


def test_the_3488_is_counted_with_the_model_it_is_set_up_as() -> None:
    assert roadmap.normalize_gateway_model("g3488") == "MyHomeServer1"
    assert roadmap.normalize_gateway_model("3488") == "MyHomeServer1"
    assert roadmap.normalize_gateway_model("f461") == "F461"
