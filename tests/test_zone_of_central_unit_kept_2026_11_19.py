"""A zone of a central unit is not taken for a standalone thermostat by mistake, 2026-11-19.

Four points of an outside audit of #955, each on the frames of the F454 plant with a 99-zone
central unit of #697 (tests/fixtures/traces/issue_697) where they apply:

* The season check read a zone ten seconds after the central unit changed season. Its zones
  follow one after the other (#697: "The zones follow the central within about 14 seconds."),
  and a zone asked before its turn still answers in the old season (issue_453). Such a zone was
  held standalone, and on that F454 every setpoint written to it was then refused: the gateway
  NACKs the direct form for a zone of its central unit (row 10 of the fixture).
* Right after a restart a zone whose mode is only restored answered the write through the
  central unit with its setpoint (row 14); the check went on to the direct form, the F454
  refused it and Home Assistant raised an error for a write that had arrived.
* A valve status from behind an interface taught the zone with the same number on the
  gateway's own bus that it cools.
* A central unit's report from behind an interface made the gateway ask ``*#4*#0##`` on its
  own bus.

No capture in tests/fixtures holds a WHO 4 frame from behind an interface: those are marked
BUILT. Frames the code writes are built with OWNd, as the code does.
"""

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNEvent, OWNHeatingCommand, OWNHeatingEvent

from custom_components.myhome import climate as climate_module
from custom_components.myhome.gateway import CENTRAL_STATUS_REQUEST, MyHOMEGatewayHandler
from tests.test_climate_standalone_zone_2026_11_18 import FRAMES as _STANDALONE_ZONE_FRAMES
from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _replay, _tx, _zone, _zone2_plant
from tests.test_f455_zone_polling import _handler
from tests.test_issue_944_actuator_answer_not_central import _plant as _bus_plant
from tests.test_issue_944_actuator_answer_not_central import _receive
from tests.test_thermo_discovery_at_start import _plant as _start_plant
from tests.test_who4_four_gateways_2026_11_18 import FRAMES as _FOUR_GATEWAYS_FRAMES
from tests.test_who4_four_gateways_2026_11_18 import _found_zone

ISSUE_697 = [
    (row["direction"], row["raw"])
    for row in json.loads(
        (
            Path(__file__).parent / "fixtures" / "traces" / "issue_697" / "f454_99zone_central_subordinate_zones.json"
        ).read_text(encoding="utf-8")
    )["frames"]
]
# The season switch of the central unit and zone 3 following it, both ways (rows 3 to 8).
CENTRAL_TO_CONDITIONING, ZONE_3_IN_CONDITIONING = ISSUE_697[3][1], ISSUE_697[4][1]
CENTRAL_TO_HEATING, ZONE_3_IN_HEATING = ISSUE_697[6][1], ISSUE_697[7][1]
# The direct write the F454 refuses, the write it takes and zone 3's answer (rows 10, 13, 14).
REFUSED_DIRECT_WRITE, TAKEN_WRITE, ZONE_3_TOOK_IT = ISSUE_697[10], ISSUE_697[13], ISSUE_697[14][1]
# The central unit's report about zone 3 (row 22).
CENTRAL_REPORTS_ZONE_3 = ISSUE_697[22][1]
# Zone 3 of the live plant, running: its own mode frame (standalone_zone_2026_11_18, f461_startup_zone_status).
ZONE_3_RUNNING = "*4*1*3##"
# Zone 4 of the live plant, a fan coil on the conditioning side (who4_four_gateways_2026_11_18, f461_valve_status).
ZONE_4_VALVE_STATUS = "*#4*4*19*5*1##"
# Zone 2 of the live plant, a thermostat its 3550 is not running: the answer to `*#4*2*12##`.
ZONE_2_READ = ["*#4*2*12*0200*3##", "*4*0*2##"]
# BUILT: that valve status as zone 4 behind interface 01 would send it, and the report of a
# central unit behind that interface about its zone 3.
VALVE_STATUS_BEHIND_INTERFACE = "*#4*4#4#01*19*5*1##"
CENTRAL_REPORT_BEHIND_INTERFACE = "*4*110*#3#4#01##"


def _setpoint(zone: str, temperature: float, *, direct: bool) -> str:
    """The setpoint frame the integration writes, in the direct or the central unit's form."""
    return str(
        OWNHeatingCommand.set_temperature(
            where=zone, temperature=temperature, mode=climate_module.SETPOINT_WRITE_MODE, standalone=direct
        )
    )


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


def _f454_plant(*, setpoint_read: str | None = "*#4*3*14*0145*3##") -> _Plant:
    """The F454 of #697 for zone 3 at 14.5: refuses the direct form, takes the central unit's.

    ``setpoint_read`` is what the zone answers to ``*#4*3*14##`` (BUILT: the capture holds no
    read of dimension 14; the default is the written value, the other case the value before).
    """
    plant = _Plant()
    plant.gateway.model = "F454"
    plant.gateway.standalone_detected_zones = set()
    plant.nack.add(_setpoint("3", 14.5, direct=True))
    plant.answer(_setpoint("3", 14.5, direct=False), [ZONE_3_TOOK_IT], [ZONE_3_TOOK_IT])
    if setpoint_read is not None:
        plant.answer("*#4*3*14##", [setpoint_read], [setpoint_read])
    return plant


def test_the_frames_this_file_rests_on() -> None:
    assert (CENTRAL_TO_CONDITIONING, ZONE_3_IN_CONDITIONING) == ("*4*202*#0##", "*4*202*3##")
    assert (CENTRAL_TO_HEATING, ZONE_3_IN_HEATING) == ("*4*102*#0##", "*4*102*3##")
    assert REFUSED_DIRECT_WRITE == ("tx", "*#4*3*#14*0160*1##")
    assert TAKEN_WRITE == ("tx", "*#4*#3*#14*0145*1##") and ZONE_3_TOOK_IT == "*#4*3*12*0145*3##"
    assert CENTRAL_REPORTS_ZONE_3 == "*4*110*#3##"
    assert ["03:34:21.899", "rx", ZONE_3_RUNNING] in _STANDALONE_ZONE_FRAMES["f461_startup_zone_status"]
    assert ["23:02:49.421", "rx", ZONE_4_VALVE_STATUS] in _FOUR_GATEWAYS_FRAMES["f461_valve_status"]
    # What the integration writes differs from the capture in the mode digit only.
    assert _setpoint("3", 14.5, direct=False)[:-3] == TAKEN_WRITE[1][:-3]
    assert _setpoint("3", 16.0, direct=True)[:-3] == REFUSED_DIRECT_WRITE[1][:-3]
    for raw in (VALVE_STATUS_BEHIND_INTERFACE, CENTRAL_REPORT_BEHIND_INTERFACE):
        message = OWNEvent.parse(raw)
        assert isinstance(message, OWNHeatingEvent) and message.interface == "01", raw


# ------------------------------------------------- the central unit's season, and when it changed


def test_a_season_that_is_learned_is_not_a_change(hass: HomeAssistant) -> None:
    handler = _handler(hass, "F454")
    assert handler.central_season is None and handler.central_season_changed_at is None
    handler.central_season = "heating"  # learned: at start, or after a reconnect
    handler.central_season = "heating"
    assert handler.central_season == "heating" and handler.central_season_changed_at is None
    before = time.monotonic()
    handler.central_season = "conditioning"
    changed_at = handler.central_season_changed_at
    assert changed_at is not None and before <= changed_at <= time.monotonic()
    handler.central_season = None  # the event session was down: not known until it reports again
    handler.central_season = "conditioning"
    assert handler.central_season_changed_at == changed_at


def test_a_handler_built_without_init_has_no_season() -> None:
    handler = MyHOMEGatewayHandler.__new__(MyHOMEGatewayHandler)
    assert handler.central_season is None and handler.central_season_changed_at is None
    handler.central_season = "heating"
    assert handler.central_season == "heating"


async def test_the_change_is_timed_from_the_central_units_own_frame(hass: HomeAssistant) -> None:
    handler, _zones = await _bus_plant(hass)
    await _receive(hass, handler, [CENTRAL_TO_HEATING])
    assert handler.central_season == "heating" and handler.central_season_changed_at is None
    await _receive(hass, handler, [CENTRAL_TO_CONDITIONING])
    assert handler.central_season == "conditioning" and handler.central_season_changed_at is not None


# ------------------------------------------------- the season check waits for the zones to follow


def _watch_the_check(zone: Any, plant: _Plant, answer: list[str]) -> list[float]:
    """Run the zone's season check on the test's loop; ``answer`` is what follows ``*#4*Z*12##``."""
    loop = asyncio.get_running_loop()
    asked: list[float] = []

    def _request(where: str, dimension: int, **_kwargs: Any) -> bool:
        assert (where, dimension) == (zone._where, 12)
        asked.append(time.monotonic())
        for raw in answer:
            loop.call_soon(zone.handle_event, OWNEvent.parse(raw))
        return True

    plant.gateway.request_zone_dimension = MagicMock(side_effect=_request)
    plant.gateway.wait_for_initial_discovery = AsyncMock()
    zone.hass.async_create_background_task = lambda coro, name=None: loop.create_task(coro)
    return asked


async def test_a_zone_that_follows_the_central_unit_late_stays_its_zone(no_entity_base: None) -> None:
    """The season switch of #697: the zone is not asked while the plant is still following."""
    plant = _Plant()
    plant.gateway.central_season = "heating"
    zone3 = _zone(plant, "3")
    asked = _watch_the_check(zone3, plant, [ZONE_3_IN_HEATING])  # asked too early, it would still say heating
    _replay(zone3, [ZONE_3_IN_HEATING])
    assert zone3._season_check_task is None  # the central unit's season

    plant.gateway.central_season = "conditioning"  # CENTRAL_TO_CONDITIONING, a moment ago
    plant.gateway.central_season_changed_at = time.monotonic()
    zone3._handle_central_mode_update(HVACMode.COOL)
    task = zone3._season_check_task
    assert task is not None
    await asyncio.sleep(0.1)  # ten times the delay of the check: before, the zone was read here
    assert asked == [] and not task.done()
    _replay(zone3, [ZONE_3_IN_CONDITIONING])  # its turn has come
    await task
    assert asked == []  # in the central unit's season now: nothing to ask
    assert zone3._standalone_detected is False and zone3._standalone is False


async def test_a_zone_still_in_the_other_season_after_that_is_found_standalone(no_entity_base: None) -> None:
    """The live plant's zone 2: its 3550 goes back to heating and the zone keeps cooling."""
    plant = _Plant()
    plant.gateway.central_season = "conditioning"
    zone2 = _zone(plant, "2")
    asked = _watch_the_check(zone2, plant, ZONE_2_READ)
    _replay(zone2, ZONE_2_READ)
    assert zone2._season_check_task is None

    plant.gateway.central_season = "heating"
    changed_at = plant.gateway.central_season_changed_at = time.monotonic()
    zone2._handle_central_mode_update(HVACMode.HEAT)
    task = zone2._season_check_task
    assert task is not None
    await task
    assert len(asked) == 1 and asked[0] - changed_at >= 0.3  # not before the plant had the time to follow
    assert zone2._standalone_detected is True


async def test_a_second_change_starts_the_wait_again(no_entity_base: None) -> None:
    """#697: Estate at 20:07:11, Inverno at 20:07:19."""
    plant = _Plant()
    plant.gateway.central_season = "conditioning"
    zone2 = _zone(plant, "2")
    asked = _watch_the_check(zone2, plant, ZONE_2_READ)
    _replay(zone2, ZONE_2_READ)
    plant.gateway.central_season = "heating"
    plant.gateway.central_season_changed_at = time.monotonic()
    zone2._handle_central_mode_update(HVACMode.HEAT)
    task = zone2._season_check_task
    assert task is not None
    await asyncio.sleep(0.2)
    again = plant.gateway.central_season_changed_at = time.monotonic()  # changed once more meanwhile
    await task
    assert len(asked) == 1 and asked[0] - again >= 0.3


async def test_a_season_that_was_only_learned_is_checked_after_the_short_delay(no_entity_base: None) -> None:
    """At start nothing is moving: zone 2 is found as before, without the longer wait."""
    plant = _Plant()
    plant.gateway.central_season = "heating"
    plant.gateway.central_season_changed_at = None
    zone2 = _zone(plant, "2")
    asked = _watch_the_check(zone2, plant, ZONE_2_READ)
    started = time.monotonic()
    _replay(zone2, ZONE_2_READ)
    task = zone2._season_check_task
    assert task is not None
    await task
    assert len(asked) == 1 and asked[0] - started < 0.3
    assert zone2._standalone_detected is True


async def test_the_season_switch_through_the_gateways_own_routing(hass: HomeAssistant) -> None:
    """Zone 2 cooling next to a central unit that goes from conditioning to heating, end to end."""
    handler, zones = await _bus_plant(hass)
    handler._initial_discovery_done.set()
    await _receive(hass, handler, [CENTRAL_TO_CONDITIONING, *ZONE_2_READ])
    zone2 = next(zone for zone in zones if zone._where == "2")
    zone2.hass = hass
    zone2.platform = MagicMock()
    zone2.entity_id = "climate.zone_2"
    setattr(zone2, "async_write_ha_state", MagicMock())
    await zone2.async_added_to_hass()
    await hass.async_block_till_done()
    assert zone2._season_check_task is None and handler.central_season_changed_at is None

    asked: list[float] = []
    request = handler.request_zone_dimension

    def _request(where: str, dimension: int, **kwargs: Any) -> Any:
        if dimension == 12:
            asked.append(time.monotonic())
        return request(where, dimension, **kwargs)

    handler.request_zone_dimension = _request
    await _receive(hass, handler, [CENTRAL_TO_HEATING])
    changed_at = handler.central_season_changed_at
    task = zone2._season_check_task
    assert changed_at is not None and task is not None
    await asyncio.sleep(0.1)
    assert asked == []  # the plant is given the time to follow
    while not asked:
        await asyncio.sleep(0.02)
    assert asked[0] - changed_at >= 0.3
    await _receive(hass, handler, ZONE_2_READ)
    await task
    assert zone2._standalone_detected is True and zone2.hvac_mode == HVACMode.COOL


# ------------------------------------------------- a refused direct write ends a wrong finding


async def test_a_zone_held_standalone_by_mistake_is_written_through_the_central_unit_again(
    no_entity_base: None,
) -> None:
    plant = _f454_plant()
    zone3 = _zone(plant, "3")
    _replay(zone3, [ZONE_3_RUNNING])
    zone3._mark_standalone_detected(by_season=True)  # read before its turn, before this fix
    assert zone3._standalone is True and ("3", None) in plant.gateway.standalone_detected_zones
    echo_before = zone3._written_target_temperature

    await zone3.async_set_temperature(temperature=14.5)  # no error

    assert plant.sent == [_setpoint("3", 14.5, direct=True), _setpoint("3", 14.5, direct=False)]
    assert zone3._standalone_detected is False and zone3._standalone is False
    assert "standalone_detected" not in zone3.extra_state_attributes
    assert ("3", None) not in plant.gateway.standalone_detected_zones
    assert zone3._written_target_temperature == echo_before  # as for any write in the central unit's form

    plant.sent.clear()
    await zone3.async_set_temperature(temperature=14.5)
    assert plant.sent == [_setpoint("3", 14.5, direct=False)]  # the refused form is not tried again


async def test_the_same_for_a_zone_left_in_protection_by_the_season_switch(no_entity_base: None) -> None:
    """Its report of a setpoint shows what it holds, so the check reads it; the refused form is not sent again."""
    plant = _f454_plant()
    zone3 = _zone(plant, "3")
    _replay(zone3, [ZONE_3_IN_CONDITIONING])  # `*4*202*3##`, where the switch to Estate left it
    zone3._mark_standalone_detected(by_season=True)

    await zone3.async_set_temperature(temperature=14.5)  # no error

    direct, via_central = _setpoint("3", 14.5, direct=True), _setpoint("3", 14.5, direct=False)
    assert plant.sent == [direct, via_central, "*#4*3*14##"]
    assert zone3._standalone_detected is False and ("3", None) not in plant.gateway.standalone_detected_zones


async def test_a_thermostat_that_is_standalone_is_found_again(no_entity_base: None) -> None:
    """One refusal on the live plant's zone 2: that write fails, and the next one finds the zone again."""
    plant = _zone2_plant()
    plant.gateway.standalone_detected_zones = set()
    direct, via_central = _tx("f455_view_zone2_setpoint_direct")[0], _setpoint("2", 21.0, direct=False)
    assert direct == _setpoint("2", 21.0, direct=True)
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"])  # a second read while it still holds 20.0
    plant.nack.add(direct)
    zone2 = _zone(plant, "2")
    _replay(zone2, ZONE_2_READ)
    zone2._mark_standalone_detected(by_season=True)

    with pytest.raises(HomeAssistantError) as raised:
        await zone2.async_set_temperature(temperature=21.0)
    assert raised.value.translation_key == "setpoint_not_taken_via_central"
    assert plant.sent == [direct, via_central, "*#4*2*14##"]  # the refused form is not sent a second time
    assert zone2._standalone_detected is False

    plant.nack.clear()
    plant.sent.clear()
    await zone2.async_set_temperature(temperature=21.0)
    assert plant.sent == [via_central, "*#4*2*14##", direct]  # ignored by the central unit, taken directly
    assert zone2._standalone_detected is True and ("2", None) in plant.gateway.standalone_detected_zones


async def test_a_configured_standalone_zone_keeps_its_form_and_its_error(no_entity_base: None) -> None:
    plant = _f454_plant()
    zone3 = _zone(plant, "3", standalone=True)
    with pytest.raises(HomeAssistantError, match="the gateway refused the setpoint write"):
        await zone3.async_set_temperature(temperature=14.5)
    assert plant.sent == [_setpoint("3", 14.5, direct=True)]
    assert zone3._standalone is True


async def test_a_write_that_was_only_not_acknowledged_in_time_changes_nothing(no_entity_base: None) -> None:
    plant = _f454_plant()
    plant.nack.clear()
    plant.lost.add(_setpoint("3", 14.5, direct=True))  # no reply in time: it may have arrived
    zone3 = _zone(plant, "3")
    zone3._mark_standalone_detected(by_season=True)
    await zone3.async_set_temperature(temperature=14.5)  # a warning, as before
    assert plant.sent == [_setpoint("3", 14.5, direct=True)]
    assert zone3._standalone_detected is True


# ------------------------------------------------- right after a restart


async def _restored(plant: _Plant, **zone: Any) -> Any:
    zone3 = _zone(plant, "3", **zone)
    await zone3.async_restore_last_state(State("climate.test", "heat", {"temperature": 17.0}))
    assert zone3._hvac_mode_restored is True  # nothing says yet whether the thermostat is on
    return zone3


async def test_a_write_that_arrived_raises_no_error_when_the_direct_form_is_refused(no_entity_base: None) -> None:
    plant = _f454_plant()
    zone3 = await _restored(plant)

    await zone3.async_set_temperature(temperature=14.5)  # no error

    assert plant.sent == [_setpoint("3", 14.5, direct=False), "*#4*3*14##", _setpoint("3", 14.5, direct=True)]
    assert zone3._standalone_detected is False


async def test_the_same_when_the_direct_form_is_only_not_acknowledged(no_entity_base: None) -> None:
    plant = _f454_plant()
    plant.nack.clear()
    plant.lost.add(_setpoint("3", 14.5, direct=True))
    zone3 = await _restored(plant)
    await zone3.async_set_temperature(temperature=14.5)
    assert plant.sent[-1] == _setpoint("3", 14.5, direct=True) and zone3._standalone_detected is False


async def test_a_zone_known_to_be_running_needs_one_frame(no_entity_base: None) -> None:
    plant = _f454_plant()
    zone3 = await _restored(plant)
    _replay(zone3, [ZONE_3_RUNNING])
    await zone3.async_set_temperature(temperature=14.5)
    assert plant.sent == [_setpoint("3", 14.5, direct=False)]


async def test_a_zone_in_antifreeze_raises_no_error_either(no_entity_base: None) -> None:
    """#697 after the switch to Inverno: zone 3 in ``*4*102*3##``, then written through the central unit."""
    plant = _f454_plant()
    zone3 = _zone(plant, "3")
    _replay(zone3, [ZONE_3_IN_HEATING])  # protection: its report of a setpoint shows what it holds
    await zone3.async_set_temperature(temperature=14.5)  # no error
    assert plant.sent == [_setpoint("3", 14.5, direct=False), "*#4*3*14##", _setpoint("3", 14.5, direct=True)]
    assert zone3._standalone_detected is False


async def test_a_write_that_did_not_arrive_still_raises(no_entity_base: None) -> None:
    """The zone reports the setpoint it had: neither form changed it, and the user is told.

    The error names the write that was asked for (through the central unit), as when the
    direct form is refused first; not the direct frame the check tried on its own.
    """
    plant = _f454_plant(setpoint_read="*#4*3*14*0170*3##")  # BUILT: 17.0, what it held
    plant.answers[_setpoint("3", 14.5, direct=False)] = []  # and nothing answers the write
    zone3 = await _restored(plant)
    with pytest.raises(HomeAssistantError) as raised:
        await zone3.async_set_temperature(temperature=14.5)
    assert raised.value.translation_key == "setpoint_not_taken_via_central"
    assert raised.value.translation_placeholders is not None
    assert raised.value.translation_placeholders["frame"] == _setpoint("3", 14.5, direct=False)
    assert raised.value.translation_placeholders["reported"] == "17.0"
    assert plant.sent == [_setpoint("3", 14.5, direct=False), "*#4*3*14##", _setpoint("3", 14.5, direct=True)]
    assert zone3._standalone_detected is False


async def test_a_direct_form_without_a_reply_after_an_untaken_write_is_a_warning(
    no_entity_base: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Not refused, only unanswered: the direct write may still have arrived, so no error."""
    plant = _f454_plant(setpoint_read="*#4*3*14*0170*3##")  # BUILT: 17.0, what it held
    plant.answers[_setpoint("3", 14.5, direct=False)] = []
    plant.nack.discard(_setpoint("3", 14.5, direct=True))
    plant.lost.add(_setpoint("3", 14.5, direct=True))
    zone3 = await _restored(plant)
    await zone3.async_set_temperature(temperature=14.5)
    assert "was not acknowledged by the gateway" in caplog.text
    assert zone3._standalone_detected is False


# ------------------------------------------------- frames from behind an interface


def test_a_valve_status_from_behind_an_interface_teaches_the_local_zone_nothing(no_entity_base: None) -> None:
    zone4 = _found_zone("4")
    zone4.handle_event(OWNEvent.parse(VALVE_STATUS_BEHIND_INTERFACE))
    assert HVACMode.COOL not in zone4.hvac_modes
    assert "cooling_seen" not in zone4.extra_state_attributes
    zone4.handle_event(OWNEvent.parse(ZONE_4_VALVE_STATUS))  # its own
    assert HVACMode.COOL in zone4.hvac_modes


async def test_the_zone_behind_the_interface_learns_it_through_the_gateways_routing(hass: HomeAssistant) -> None:
    handler, zones = await _bus_plant(hass)
    await _receive(hass, handler, ["*#4*4*19*0*1##"])  # BUILT: a local zone 4 that only heats
    await _receive(hass, handler, [VALVE_STATUS_BEHIND_INTERFACE])
    await _receive(hass, handler, [VALVE_STATUS_BEHIND_INTERFACE])  # again, now that its zone exists
    offers_cool = {zone._interface: HVACMode.COOL in zone.hvac_modes for zone in zones if zone._where == "4"}
    assert offers_cool == {None: False, "01": True}


async def test_a_central_unit_behind_an_interface_is_not_asked_on_the_local_bus(
    hass: HomeAssistant, fast_bus_pacing: None
) -> None:
    async def _read(_where: str) -> None:
        return None

    handler, _zones = await _start_plant(hass, _read, model="F455")
    handler._initial_discovery_done.set()
    await _receive(hass, handler, [CENTRAL_REPORT_BEHIND_INTERFACE])
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == []
    assert handler.has_central_unit is False  # another bus's central unit is not this bus's
    assert handler.central_season is None  # nor is its season

    await _receive(hass, handler, [CENTRAL_REPORTS_ZONE_3])  # the local central unit: asked once
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == [CENTRAL_STATUS_REQUEST]


def test_the_same_for_a_report_that_reaches_the_entity_directly(no_entity_base: None) -> None:
    """A reply collected on the command session does not pass the event session's bookkeeping."""
    plant = _Plant(has_central_unit=False)
    plant.gateway.central_season = None
    plant.gateway.request_central_status = MagicMock()
    zone3 = _zone(plant, "3")
    with patch("custom_components.myhome.climate.async_dispatcher_send"):
        zone3.handle_event(OWNEvent.parse(CENTRAL_REPORT_BEHIND_INTERFACE))
        plant.gateway.request_central_status.assert_not_called()
        assert plant.gateway.has_central_unit is False
        zone3.handle_event(OWNEvent.parse(CENTRAL_REPORTS_ZONE_3))  # the local one is still found
        plant.gateway.request_central_status.assert_called_once_with()
        assert plant.gateway.has_central_unit is True
        zone3.handle_event(OWNEvent.parse(CENTRAL_REPORTS_ZONE_3))
        zone3.handle_event(OWNEvent.parse(CENTRAL_REPORT_BEHIND_INTERFACE))
        plant.gateway.request_central_status.assert_called_once_with()  # once


# ------------------------------------------------- a valve that was open before the zone said its mode


def _blank_start(trace: str) -> list[str]:
    """What the gateway sent during a start with no zone known (who4_four_gateways_2026_11_18), in order."""
    return [raw for _stamp, direction, raw in _FOUR_GATEWAYS_FRAMES[trace] if direction == "rx"]


def test_an_open_valve_is_called_heating_once_the_zone_says_it_heats(no_entity_base: None) -> None:
    """A MyHomeServer1 and a 3488 on one bus, zones found on the bus (the captures of 2026-11-18; seen again live on 2026-11-19).

    Zone 40's valve answers the sweep (``*#4*40#1*20*1##``) before the zone's status is read
    (``*4*1*40##``). A plain valve does not say which side it is on and does not report again
    until it changes, so the zone showed ``heat`` with no action, while the same zone in
    ``myhome.yaml`` (heating only) showed ``heating`` at once.
    """
    capture = _blank_start("myhomeserver1_blank_start")
    assert capture.index("*#4*40#1*20*1##") < capture.index("*4*1*40##")
    zone40 = _found_zone("40")
    _replay(zone40, ["*#4*40#1*20*1##"])
    assert zone40.hvac_action is None  # it may also cool: nothing is claimed yet
    _replay(zone40, [raw for raw in capture if raw.startswith(("*#4*40*0*", "*#4*40*12*"))][:2])
    assert zone40.hvac_action is None  # a temperature and a setpoint do not say it either
    _replay(zone40, ["*4*1*40##"])
    assert zone40.hvac_mode == HVACMode.HEAT and zone40.hvac_action == HVACAction.HEATING

    configured = _found_zone("40", cooling=False)  # `cool: false` in myhome.yaml
    _replay(configured, ["*#4*40#1*20*1##"])
    assert configured.hvac_action == HVACAction.HEATING
    _replay(configured, ["*4*1*40##"])
    assert configured.hvac_mode == HVACMode.HEAT and configured.hvac_action == HVACAction.HEATING


@pytest.mark.parametrize("trace", ["myhomeserver1_blank_start", "g3488_blank_start"])
@pytest.mark.parametrize(("zone", "action"), [("36", "heating"), ("40", "heating"), ("53", "heating"), ("49", "idle")])
def test_the_whole_blank_start_ends_the_same_for_a_found_and_a_configured_zone(
    no_entity_base: None, trace: str, zone: str, action: str
) -> None:
    """Zones 36, 40 and 53 had their valve open in that capture, zone 49 had it closed; all four only heat."""
    found, configured = _found_zone(zone), _found_zone(zone, cooling=False)
    for entity in (found, configured):
        _replay(entity, _blank_start(trace))
    assert found.hvac_mode == configured.hvac_mode == HVACMode.HEAT
    assert found.hvac_action == configured.hvac_action == action


def test_an_action_that_is_known_is_not_touched(no_entity_base: None) -> None:
    zone2 = _found_zone("2")
    _replay(zone2, ["*#4*2#1*20*8##", "*4*0*2##"])  # the live plant's zone 2: a fan coil at speed 3, then conditioning
    assert zone2.hvac_action == HVACAction.COOLING
    _replay(zone2, ["*#4*2*0*0235##"])
    assert zone2.hvac_action == HVACAction.COOLING
