"""Third review of #955 (S1 to S5) and an outside audit of the same head, 2026-11-18.

* S1: on a plant whose gateway is not asked for the central unit's status at start (an F455),
  the central unit becomes known from its report about one zone (``*4*110*#3##``, all its
  quarter-hour push brings). That frame is routed to zone 3 only, so zone 2, which had reported
  ``*4*0*2##`` while no central unit was known, was never compared with it.
* S2: a zone whose mode is not known yet (restored, or shown off) and that answers the direct
  setpoint write with its setpoint and no mode frame got "did not take the setpoint 21.0 °C ...
  it still reports 21.0 °C".
* S3: a zone found standalone after the central unit switched its zones off kept ``hvac_action``
  ``off`` next to a running mode.
* S4: an unanswered temperature read stayed queued next to its repeat.
* S5: the 0-10 V output sensor of a zone the central unit does not run went to 0 % with it.
* Audit: the "sweep just asked" test read a host clock under 30 s as a sweep; the temperature
  reads of valve-only zones ran on every model; the central unit's status was asked twice at
  start on an F461; the hardware table said no WHO 4 request is sent where two are.

Frames are the captures of tests/fixtures/traces/standalone_zone_2026_11_18 unless marked BUILT.
"""

import asyncio
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome import climate as climate_module
from custom_components.myhome import gateway as gateway_module
from custom_components.myhome.climate_discovery import ZoneDiscovery
from custom_components.myhome.const import CONF_STANDALONE_ZONES, DOMAIN, PLATFORMS
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.sensor import MyHOMEActuatorOutputSensor
from custom_components.myhome.sensor import async_setup_entry as sensor_setup_entry
from tests.test_climate_mode_state_2026_11_08 import _climate, _gateway
from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _replay, _rx, _zone
from tests.test_f455_zone_polling import _handler
from tests.test_issue_944_actuator_answer_not_central import _plant as _bus_plant
from tests.test_issue_944_actuator_answer_not_central import _receive
from tests.test_issue_949_fancoil_dim20_output_level import _make_gateway
from tests.test_thermo_discovery_at_start import _acknowledged, _after_the_sweep, _drain
from tests.test_thermo_discovery_at_start import _plant as _start_plant

try:
    from scripts import update_gateway_profiles as ugp
except ImportError:  # pragma: no cover - run from inside scripts/
    import update_gateway_profiles as ugp

# The answer of 2026-11-18 15:00 to `*#4*2*12##` (F461 and F455 alike).
ZONE_2_READ = ["*#4*2*12*0200*3##", "*4*0*2##"]
# The 3550's quarter-hour push seen through the F455, as the event session delivers it: no `#0`
# frame in it (a `*#14*` write seen on the bus is a command and is not dispatched).
QUARTER_HOUR_PUSH = [raw for raw in _rx("f455_quarter_hour_push") if isinstance(OWNMessage.parse(raw), OWNHeatingEvent)]


@pytest.fixture(autouse=True)
def _short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(climate_module, "SETPOINT_CONFIRM_TIMEOUT", 0.05)
    monkeypatch.setattr(climate_module, "EMPTY_REPLY_SETTLE", 0.2)
    monkeypatch.setattr(climate_module, "STANDALONE_SEASON_CONFIRM_DELAY", 0.01)
    monkeypatch.setattr(climate_module, "PACED_READ_TIMEOUT", 0.5)


@pytest.fixture
def no_entity_base() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


# ---------------------------------------------------------------- S1


def test_the_push_this_file_rests_on() -> None:
    assert "*4*110*#3##" in QUARTER_HOUR_PUSH and "*4*110*#4##" in QUARTER_HOUR_PUSH
    assert not [raw for raw in QUARTER_HOUR_PUSH if "#0" in raw.rpartition("*")[2]]  # no frame of the central unit itself


async def test_the_zones_are_told_once_when_the_central_unit_or_its_season_becomes_known(hass: HomeAssistant) -> None:
    handler, _zones = await _bus_plant(hass)
    told: list[tuple[bool, Any]] = []

    @callback
    def _told() -> None:
        told.append((handler.has_central_unit, handler.central_season))

    async_dispatcher_connect(hass, f"myhome_central_season_{handler.mac}", _told)
    await _receive(hass, handler, ZONE_2_READ)
    assert told == []  # a zone's own frames say nothing about a central unit
    await _receive(hass, handler, QUARTER_HOUR_PUSH)
    assert told == [(True, "heating")]  # once, at the first `*4*110*#3##`; not again for zone 4
    await _receive(hass, handler, ["*4*202*#0##"])  # the season changes (issue_453, row 58)
    assert told == [(True, "heating"), (True, "conditioning")]


async def test_a_zone_that_spoke_before_the_central_unit_was_known_is_checked_when_it_is(hass: HomeAssistant) -> None:
    """S1 end to end: the zone's frames, then the push with no `#0` frame, through the gateway's own routing."""
    handler, zones = await _bus_plant(hass)
    handler._initial_discovery_done.set()
    await _receive(hass, handler, ZONE_2_READ)
    zone2 = next(zone for zone in zones if zone._where == "2")
    zone2.hass = hass
    zone2.platform = MagicMock()
    zone2.entity_id = "climate.zone_2"
    setattr(zone2, "async_write_ha_state", MagicMock())
    await zone2.async_added_to_hass()
    await hass.async_block_till_done()
    assert zone2._standalone is True  # no central unit known: every zone is its own
    assert zone2._season_check_task is None

    await _receive(hass, handler, QUARTER_HOUR_PUSH)
    assert handler.has_central_unit is True and handler.central_season == "heating"
    task = zone2._season_check_task
    assert task is not None  # started by the report about zone 3, not by a frame of zone 2
    assert zone2._standalone is False
    await asyncio.sleep(0.1)  # the check has asked `*#4*2*12##`
    await _receive(hass, handler, ZONE_2_READ)
    await task
    assert zone2._standalone_detected is True
    assert zone2.hvac_mode == HVACMode.COOL
    assert ("2", None) in handler.standalone_detected_zones


async def test_a_report_collected_on_the_command_session_tells_the_zones_too(no_entity_base: None) -> None:
    plant = _Plant(has_central_unit=False)
    plant.gateway.central_season = None
    zone3 = _zone(plant, "3")
    with patch("custom_components.myhome.climate.async_dispatcher_send") as dispatch:
        _replay(zone3, ["*4*110*#3##"])
        assert [call.args[1] for call in dispatch.call_args_list] == [f"myhome_central_season_{plant.gateway.mac}"]
        _replay(zone3, ["*4*110*#3##"])  # nothing new
        assert dispatch.call_count == 1


def test_what_a_zone_offers_follows_the_central_unit_that_became_known(no_entity_base: None) -> None:
    plant = _Plant(has_central_unit=False)
    zone3 = _zone(plant, "3")
    _replay(zone3, ["*4*1*3##"])  # heating, by its own frame
    assert HVACMode.COOL in zone3.hvac_modes  # a thermostat on its own offers both seasons
    plant.gateway.has_central_unit = True
    zone3._handle_central_season_update()
    assert HVACMode.COOL not in zone3.hvac_modes  # a zone of a central unit runs in its season
    zone3.async_schedule_update_ha_state.assert_called()
    zone3.async_schedule_update_ha_state.reset_mock()
    zone3._handle_central_season_update()  # nothing changed: nothing written
    zone3.async_schedule_update_ha_state.assert_not_called()


# ---------------------------------------------------------------- the central unit's own entity


async def test_a_central_unit_known_only_from_a_zone_report_is_asked_for_its_status_once(
    hass: HomeAssistant, fast_bus_pacing: None
) -> None:
    """Live F455 alone on the bus, 2026-11-18 19:57-19:58: zones 2, 3 and 4 found, no `#0` frame, no central unit entity."""

    async def read(_where: str) -> bool | None:  # pragma: no cover - no valve-only zone here
        return False

    handler, zones = await _start_plant(hass, read, model="F455")
    handler._initial_discovery_done.set()
    await _receive(hass, handler, ZONE_2_READ)
    await hass.async_block_till_done(wait_background_tasks=True)
    handler.send_status_request.assert_not_awaited()  # nothing shows a central unit yet

    await _receive(hass, handler, QUARTER_HOUR_PUSH)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [str(call.args[0]) for call in handler.send_status_request.await_args_list] == ["*#4*#0##"]
    assert handler.central_status_just_asked is True

    # Its answer (the F461's of 2026-11-18 19:56:52) creates the entity, which does not ask again.
    await _receive(hass, handler, ["*4*102*#0##", "*4*21*#0##"])
    central = next(zone for zone in zones if zone._central)
    await central._async_send_zone_polls()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert handler.send_status_request.await_count == 1


async def test_a_central_unit_that_spoke_itself_is_not_asked(hass: HomeAssistant, fast_bus_pacing: None) -> None:
    handler, _zones = await _bus_plant(hass)
    handler._initial_discovery_done.set()
    await _receive(hass, handler, ["*4*102*#0##", *QUARTER_HOUR_PUSH])
    await hass.async_block_till_done(wait_background_tasks=True)
    handler.send_status_request.assert_not_awaited()


async def test_the_central_unit_is_not_asked_when_the_sweep_has_just_asked(
    hass: HomeAssistant, fast_bus_pacing: None
) -> None:
    """The sweep's request was lost or is still unanswered: the zone report does not double it."""
    handler, _zones = await _bus_plant(hass)
    handler._initial_discovery_done.set()
    handler._central_status_swept_at = time.monotonic()
    await _receive(hass, handler, QUARTER_HOUR_PUSH)
    await hass.async_block_till_done(wait_background_tasks=True)
    handler.send_status_request.assert_not_awaited()
    handler.request_central_status()
    handler.request_central_status()  # while the first is still waiting: one task
    await hass.async_block_till_done(wait_background_tasks=True)
    handler.send_status_request.assert_not_awaited()

    handler._initial_discovery_done.clear()
    handler.request_central_status()  # waiting for the sweep when the gateway is unloaded
    task = handler._central_status_task
    await handler.close_listener()
    assert handler._central_status_task is None
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


def test_a_hass_double_that_schedules_nothing_leaves_no_pending_request(hass: HomeAssistant) -> None:
    handler = _handler(hass, "F455")
    handler.hass = MagicMock()  # async_create_background_task returns no task
    handler.request_central_status()  # no "never awaited" warning: the run was closed
    assert handler._central_status_task is None


def test_a_zone_report_collected_on_the_command_session_asks_for_the_central_unit_too(no_entity_base: None) -> None:
    plant = _Plant(has_central_unit=False)
    plant.gateway.central_season = None
    zone3 = _zone(plant, "3")
    _replay(zone3, ["*4*110*#3##", "*4*110*#3##"])
    plant.gateway.request_central_status.assert_called_once_with()
    plant.gateway.request_central_status = None  # a gateway double without it
    plant.gateway.has_central_unit = False
    _replay(zone3, ["*4*110*#3##"])


async def test_the_gateway_model_is_read_when_the_sweep_is_over_not_when_the_zones_are_set_up(hass: HomeAssistant) -> None:
    """The in-band identity may correct the model after the platform was set up."""
    asked: list[str] = []

    async def read(where: str) -> bool | None:
        asked.append(where)
        return False

    handler, _zones = await _start_plant(hass, read, model="MH200N")
    assert handler.asks_every_actuator_at_start is False
    with patch.object(type(handler), "asks_every_actuator_at_start", new=True):  # corrected to an F455, say
        await _receive(hass, handler, ["*#4*7#1*20*0##"])  # a valve frame of the two-misses test
        await _after_the_sweep(hass, handler)
    assert asked == ["7"]


# ---------------------------------------------------------------- S2


@pytest.mark.parametrize("answer_follows_the_write", [True, False])
async def test_a_setpoint_that_moved_after_the_direct_write_is_taken_whatever_the_mode(
    no_entity_base: None, answer_follows_the_write: bool
) -> None:
    """The zone's mode is a restored one and no mode frame comes: 20.0 read, 21.0 written, 21.0 reported."""
    plant = _Plant()
    moved = ["*#4*2*14*0210*3##"]  # captured as the answer to a read; BUILT as the only frame after the write
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"], moved)
    plant.answer("*#4*2*#14*0210*3##", moved if answer_follows_the_write else [])
    zone2 = _zone(plant, "2")
    zone2._hvac_mode_restored = True
    assert zone2._own_off is None and zone2._asleep() is True
    await zone2.async_set_temperature(temperature=21.0)  # no "did not take the setpoint" error
    expected = ["*#4*#2*#14*0210*3##", "*#4*2*14##", "*#4*2*#14*0210*3##"]
    assert plant.sent == expected + ([] if answer_follows_the_write else ["*#4*2*14##"])
    assert zone2._standalone_detected is True
    assert zone2._setpoint_check_before is None  # the check is over


async def test_a_zone_that_is_off_and_keeps_the_requested_setpoint_is_no_error(no_entity_base: None) -> None:
    """Off at 20.0, 20.0 asked, and it stays off: nothing was refused, and nothing shows which form it takes."""
    plant = _Plant()
    plant.answer("*#4*2*14##", ["*#4*2*14*0200*3##"], ["*#4*2*14*0200*3##"])
    zone2 = _zone(plant, "2")
    _replay(zone2, ["*#4*2*14*0200*3##", "*4*303*2##"])  # as in the F7 test of #951: zone 2 off
    await zone2.async_set_temperature(temperature=20.0)
    assert plant.sent == ["*#4*#2*#14*0200*3##", "*#4*2*14##", "*#4*2*#14*0200*3##", "*#4*2*14##"]
    assert zone2._standalone_detected is False
    assert zone2.hvac_mode == HVACMode.OFF


# ---------------------------------------------------------------- S3


def test_a_zone_found_standalone_after_the_central_unit_s_off_is_idle_not_off(no_entity_base: None) -> None:
    plant = _Plant()
    plant.gateway.central_season = None  # no season check runs here: the finding is made by hand below
    zone2 = _zone(plant, "2")
    _replay(zone2, ZONE_2_READ)  # running, in conditioning, by its own frame
    zone2._handle_central_mode_update(HVACMode.OFF)  # the central unit switches its zones off
    assert (zone2.hvac_mode, zone2.hvac_action) == (HVACMode.OFF, HVACAction.OFF)
    zone2._mark_standalone_detected(by_season=True)
    assert (zone2.hvac_mode, zone2.hvac_action) == (HVACMode.COOL, HVACAction.IDLE)


# ---------------------------------------------------------------- S5: the finding is shared


def test_the_finding_is_kept_on_the_gateway_and_dropped_with_it(no_entity_base: None) -> None:
    plant = _Plant()
    plant.gateway.standalone_detected_zones = set()
    zone2 = _zone(plant, "2")
    zone2._mark_standalone_detected()
    assert plant.gateway.standalone_detected_zones == {("2", None)}
    zone2._set_standalone_detected(False)  # the central unit reported it again
    assert plant.gateway.standalone_detected_zones == set()
    zone2._mark_standalone_detected()
    zone2._forget_standalone_detected()  # the entity is removed
    assert plant.gateway.standalone_detected_zones == set()
    plant.gateway.standalone_detected_zones = None  # a gateway double without the set
    zone2._forget_standalone_detected()


async def test_the_central_unit_s_off_does_not_zero_the_output_of_a_zone_it_does_not_run(hass: HomeAssistant) -> None:
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    gw.has_central_unit = True
    gw.standalone_detected_zones = {("1", None)}
    sensor = MyHOMEActuatorOutputSensor(
        hass=hass, name="Climate Zone 1", device_id="1", where="1#3", zone="1", actuator="3", interface=None,
        standalone=None, manufacturer="BTicino", model="Heating Zone", gateway=gw,
    )
    sensor.hass = hass
    sensor.entity_id = "sensor.zone_1_act_3"
    setattr(sensor, "async_write_ha_state", MagicMock())
    await sensor.async_added_to_hass()
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*180##"))  # the frame of the #949 tests
    assert sensor._standalone is True
    async_dispatcher_send(hass, f"myhome_central_mode_{gw.mac}", HVACMode.OFF)
    await hass.async_block_till_done()
    assert sensor.native_value == 80

    gw.standalone_detected_zones.clear()  # the central unit reported the zone: it runs it again
    assert sensor._standalone is False
    async_dispatcher_send(hass, f"myhome_central_mode_{gw.mac}", HVACMode.OFF)
    await hass.async_block_till_done()
    assert sensor.native_value == 0


async def test_a_zone_listed_as_standalone_in_the_options_has_a_standalone_output_sensor(hass: HomeAssistant) -> None:
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    gw.has_central_unit = True
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: gw.mac}, options={CONF_STANDALONE_ZONES: ["2"]}, unique_id=gw.mac)
    entry.add_to_hass(hass)
    entry.runtime_data = MyHOMERuntimeData(gateway=gw, platforms={platform: {} for platform in PLATFORMS})
    added: list[Any] = []

    def _add(entities: list[Any], update_before_add: bool = False) -> None:
        for entity in entities:
            entity.hass = hass
            entity.entity_id = f"sensor.mock_{len(added)}"
            entity.async_write_ha_state = MagicMock()
            added.append(entity)

    await sensor_setup_entry(hass, entry, _add)
    for raw in ("*#4*2#3*20*140##", "*#4*4#1*20*130##"):  # frames of the #949 tests
        async_dispatcher_send(hass, f"myhome_message_{gw.mac}", OWNMessage.parse(raw))
    await hass.async_block_till_done()
    by_where = {entity._where: entity for entity in added if isinstance(entity, MyHOMEActuatorOutputSensor)}
    assert by_where["2#3"]._standalone is True  # listed
    assert by_where["4#1"]._standalone is False  # a zone of the central unit


# ---------------------------------------------------------------- the audit's four


async def test_no_sweep_is_not_a_fresh_sweep_on_a_host_that_has_just_booted(
    hass: HomeAssistant, fast_bus_pacing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`0 < monotonic() - 0.0 < 30` held for the first 30 s of the host's clock."""
    started = time.monotonic()
    young_clock = SimpleNamespace(monotonic=lambda: 10.0 + (time.monotonic() - started))
    monkeypatch.setattr(gateway_module, "time", young_clock)
    handler = _acknowledged(_handler(hass, "F461"))
    handler._initial_discovery_done.set()  # the sweep is over and did not ask every actuator
    assert handler._all_actuators_swept_at == 0.0
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*0#0*20##"]


@pytest.mark.parametrize("model", ["MH200N", "F453", "F453AV", "H4890", "H4684", "MH200", "MH201", "MH202"])
async def test_valve_only_zones_are_read_only_where_the_sweep_asks_every_actuator(hass: HomeAssistant, model: str) -> None:
    """Where the general request is forwarded every thermostat has answered it; elsewhere nothing was measured."""
    asked: list[str] = []

    async def read(where: str) -> bool | None:  # pragma: no cover - must not run
        asked.append(where)
        return False

    handler, zones = await _start_plant(hass, read, model=model)
    assert handler.asks_every_actuator_at_start is False
    await _receive(hass, handler, ["*#4*7#1*20*0##", "*#4*8#1*20*1##"])  # the valve frames of the two-misses test (BUILT)
    await _after_the_sweep(hass, handler)
    assert asked == []
    assert zones == []


def test_a_config_entry_double_that_schedules_nothing_leaves_no_pending_read(hass: HomeAssistant) -> None:
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:00:01"
    gateway.asks_every_actuator_at_start = True
    gateway.read_zone_temperature = AsyncMock(return_value=False)
    runtime = SimpleNamespace(gateway=gateway, platforms={})
    entry = MagicMock()  # async_create_background_task returns no task
    ZoneDiscovery(hass, entry, runtime, MagicMock()).start_reads()
    entry.async_create_background_task.assert_called_once()
    gateway.read_zone_temperature.assert_not_awaited()  # and no "never awaited" warning: the run was closed


async def test_the_zones_created_from_the_kept_answers_do_not_ask_every_actuator_again(
    hass: HomeAssistant, fast_bus_pacing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live MyHomeServer1, 34 zones, 2026-11-18 21:02-21:04: the sweep's request, then 15 more in 40 s."""
    handler = _acknowledged(_handler(hass, "MyHomeServer1"))
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler).count("*#4*0#0*20##") == 1

    monkeypatch.setattr(gateway_module, "ALL_ACTUATORS_SWEEP_FRESH", 0.0)  # the run outlasts the fresh window
    handler.actuator_only_reads(True)
    handler.watch_zone_actuators("47")  # a zone the run has just created
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []
    handler.actuator_only_reads(False)
    handler.watch_zone_actuators("48")  # later: a zone that spoke by itself
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*0#0*20##"]

    monkeypatch.setattr(gateway_module, "ALL_ACTUATORS_SWEEP_FRESH", 30.0)
    handler.watch_zone_actuators("49")  # right after that request: served by it
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []


async def test_the_run_of_reads_is_marked_on_the_gateway_and_cleared_when_it_ends(hass: HomeAssistant) -> None:
    seen: list[bool] = []
    handler: Any = None

    async def read(_where: str) -> bool | None:
        seen.append(handler._actuator_only_reads_running)
        return False

    handler, _zones = await _start_plant(hass, read)  # an F461: every valve-only address is asked
    handler._all_actuators_swept_at = 1.0  # the sweep asked every actuator long ago
    await _receive(hass, handler, ["*#4*7#1*20*0##", "*#4*8#1*20*1##"])  # BUILT
    await _after_the_sweep(hass, handler)
    assert seen == [True, True]
    assert handler._actuator_only_reads_running is False
    assert handler._all_actuators_swept_at > 1.0  # fresh once more for the zones the last reads created


@pytest.mark.parametrize("model", sorted(gateway_module.STARTUP_ALL_ACTUATORS_REQUEST))
def test_the_models_whose_valve_only_zones_are_read(hass: HomeAssistant, model: str) -> None:
    assert _handler(hass, model).asks_every_actuator_at_start is True


async def test_the_central_unit_s_status_is_not_asked_again_right_after_the_sweep(
    hass: HomeAssistant, fast_bus_pacing: None, no_entity_base: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live F461, 2026-11-18: `*#4*#0##` at 16:00:58.889 (the sweep) and at 16:01:08.227 (the new entity's poll)."""
    handler = _acknowledged(_handler(hass, "F461"))
    assert handler.central_status_just_asked is False
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler).count("*#4*#0##") == 1
    assert handler.central_status_just_asked is True

    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    gateway.central_status_just_asked = handler.central_status_just_asked
    await central._async_send_zone_polls()
    assert gateway.send_status_request.await_count == 0
    gateway.central_status_just_asked = False  # later, or on a gateway whose sweep does not ask (an F455)
    await central._async_send_zone_polls()
    assert [str(call.args[0]) for call in gateway.send_status_request.await_args_list] == ["*#4*#0##"]

    monkeypatch.setattr(gateway_module, "ALL_ACTUATORS_SWEEP_FRESH", 0.0)
    assert handler.central_status_just_asked is False  # no longer fresh


async def test_the_f455_sweep_does_not_ask_the_central_unit(hass: HomeAssistant, fast_bus_pacing: None) -> None:
    handler = _acknowledged(_handler(hass, "F455"))
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "*#4*#0##" not in _drain(handler)
    assert handler.central_status_just_asked is False


async def test_a_temperature_read_that_was_not_written_in_time_is_withdrawn(
    hass: HomeAssistant, fast_bus_pacing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S4: the caller repeats the read, so the first one must not go out next to it."""
    monkeypatch.setattr(gateway_module, "PACED_WRITE_TIMEOUT", 0.01)
    handler = _handler(hass, "MyHomeServer1")
    queued: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    handler.send_status_request = AsyncMock(return_value=queued)
    assert await handler.read_zone_temperature("47") is None
    assert queued.cancelled()


def test_the_hardware_table_names_what_the_sweep_sends_in_place_of_the_general_request(hass: HomeAssistant) -> None:
    """Audit: "WHO 1, 2, 18 (firmware drops `*#4*0##`)" next to notes that list two WHO 4 requests."""
    for row in ugp.GATEWAY_DISCOVERY_METADATA:
        handler = _handler(hass, str(row["profile_name"]))
        general = ugp.GENERAL_ZONE_STATUS in ugp.cast_list(row.get("sweep_omits"))
        sent = [frame for frame in handler._discovery_frames() if frame.startswith("*#4*")]
        assert sent == (ugp.cast_list(row.get("sweep_replaces")) if general else [ugp.GENERAL_ZONE_STATUS]), row["model"]
    table = ugp.generate_gateway_discovery_table()
    assert "WHO 1, 2, 18 (firmware drops `*#4*0##`; `*#4*#0##`, `*#4*0#0*20##` sent in place of `*#4*0##`) |" in table
    assert "WHO 1, 2, 16 (firmware drops `*#4*0##`) |" in table  # an MH200: nothing is sent in its place


# ---------------------------------------------------------------- an outside audit of faed68d3


async def test_the_marks_of_this_gateway_count_when_another_one_serves_who_4(
    hass: HomeAssistant, fast_bus_pacing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared bus: WHO 4 goes through a delegated secondary or the standby, the zones are this entry's."""
    handler = _acknowledged(_handler(hass, "F461"))
    other = _handler(hass, "MyHomeServer1")
    monkeypatch.setattr(type(handler), "_who4_target", lambda self: other if self is handler else self)
    handler._initial_discovery_done.set()

    handler._all_actuators_swept_at = time.monotonic()  # this gateway's sweep asked every actuator
    handler.watch_zone_actuators("2")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []

    monkeypatch.setattr(gateway_module, "ALL_ACTUATORS_SWEEP_FRESH", 0.0)
    handler.actuator_only_reads(True)  # its read run is on: the mark is on this gateway, not on the other
    handler.watch_zone_actuators("3")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []
    handler.actuator_only_reads(False)

    handler._all_actuators_swept_at = 0.0
    other._all_actuators_swept_at = time.monotonic()  # the other gateway's sweep asked
    monkeypatch.setattr(gateway_module, "ALL_ACTUATORS_SWEEP_FRESH", 30.0)
    handler.watch_zone_actuators("4")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []


async def test_a_zone_that_lost_its_actuators_is_asked_again_although_every_actuator_was_just_asked(
    hass: HomeAssistant, fast_bus_pacing: None
) -> None:
    handler = _acknowledged(_handler(hass, "F461"))
    await handler.initial_discovery()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler).count("*#4*0#0*20##") == 1

    handler.request_zone_dimension("2", 20)  # served by the sweep
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []
    handler.request_zone_dimension("2", 20, again=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == ["*#4*0#0*20##"]
    handler.request_zone_dimension("2", 20)  # and the mark does not stay
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == []


def test_a_zone_found_standalone_after_the_central_unit_s_off_asks_its_actuators_again(no_entity_base: None) -> None:
    """The off marked its actuators off and set its 0-10 V output sensor to 0 %: neither was the plant's state."""
    zone2 = _zone(_Plant(), "2")
    _replay(zone2, ["*#4*2#1*20*8##", "*4*0*2##"])
    assert zone2.extra_state_attributes["actuators"] == {"1": "on"}
    zone2._handle_central_mode_update(HVACMode.OFF)
    assert zone2.extra_state_attributes["actuators"] == {"1": "off"}
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    zone2._mark_standalone_detected()
    ask.assert_called_once_with("2", 20, again=True)
    ask.reset_mock()
    zone2._mark_standalone_detected()  # once: nothing to ask the second time
    ask.assert_not_called()

    untouched = _zone(_Plant(), "2")
    untouched._mark_standalone_detected()  # the central unit never switched it off: nothing to ask
    untouched._gateway_handler.request_zone_dimension.assert_not_called()
