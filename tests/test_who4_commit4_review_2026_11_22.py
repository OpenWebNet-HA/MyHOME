"""What a second review of the WHO 4 fixes (commit ``e7d7f08c``) found, 2026-11-22.

Each test reproduces one point of that review on the code before this commit. The frames are
those of the tests and captures named next to them; no frame is new.

Thermo actuator switch
- a whole-zone switch queued its writes one by one, waiting for each answer before the next:
  a newer OFF could be overtaken by the rest of an older ON, and three silent actuators cost
  three delivery timeouts;
- a write the gateway refused left the "reverted after a write" detector armed;
- an aggregate report (``Z#0``), saved and restored, became a proven actuator 1;
- a configured ``turn_on_command`` hid a refusal by the gateway.

Climate
- ``auto`` written through the central unit, a fan speed and the central unit's ``off`` hid a
  refusal by the gateway; the refused ``off`` was also shown on every zone of the plant;
- ``auto`` on a standalone zone with no known setpoint returned without sending anything;
- the silent-zone poll asked for the commanded setpoint although it had arrived meanwhile;
- a candidate zone exposed on the bus while its read waited for the startup lock was still read.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNHeatingEvent, OWNMessage

from custom_components.myhome import climate_discovery
from custom_components.myhome import switch as switch_module
from custom_components.myhome.switch import MyHOMEThermoSwitch
from tests.test_climate_setpoint_in_force import F461_POLL_REPLY
from tests.test_climate_setpoint_in_force import _feed as _feed_zone
from tests.test_climate_setpoint_in_force import _zone as _setpoint_zone
from tests.test_crew_central_season_restore import HEATING_PROGRAM, _climate, _gateway, _replay
from tests.test_f455_zone_polling import _handler, _queued, _ready, _spin
from tests.test_f455_zone_polling import _zone as _polled_zone
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway
from tests.test_issue_966_phantom_climate_zone import _plant, _receive, _settle
from tests.test_probe_fallback_read import _refused
from tests.test_who4_candidate_kept_2026_11_21 import ZONE_1_TEMPERATURE
from tests.test_zone_silent_poll_reads import _fan_zone


def _switch(hass, gateway, *, where="3", actuator=None, turn_on_command=None):
    return MyHOMEThermoSwitch(
        hass=hass, name="Radiators", entity_name=None, icon=None, icon_on=None, device_id=f"4-{where}",
        where=where, zone="3", actuator=actuator, interface=None, device_class=None, manufacturer="BTicino",
        model="F430", turn_on_command=turn_on_command, turn_off_command=None, gateway=gateway,
    )


def _sent(gateway) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


# --- thermo actuator switch -------------------------------------------------------------------


async def test_a_newer_off_is_not_overtaken_by_the_rest_of_an_older_on(hass):
    gateway = _mock_gateway()
    switch = _switch(hass, gateway)
    for frame in ("*#4*3#2*20*0##", "*#4*3#1*20*0##"):  # tests/test_issue_785_thermo_actuator_switch.py:2419-2420
        switch.handle_event(OWNHeatingEvent(frame))
    slow = hass.loop.create_future()
    done = hass.loop.create_future()
    done.set_result(0.0)
    gateway.send.side_effect = [slow, done, done, done]
    turn_on = asyncio.create_task(switch.async_turn_on())
    await asyncio.sleep(0)
    turn_off = asyncio.create_task(switch.async_turn_off())
    for _ in range(8):
        await asyncio.sleep(0)
    slow.set_result(0.0)
    await asyncio.gather(turn_on, turn_off)
    sent = _sent(gateway)
    assert sent == ["*#4*3#1*#20*1##", "*#4*3#2*#20*1##", "*#4*3#1*#20*0##", "*#4*3#2*#20*0##"], sent


async def test_a_whole_zone_switch_waits_one_delivery_timeout_not_one_per_actuator(hass, monkeypatch):
    gateway = _mock_gateway()
    switch = _switch(hass, gateway)
    # tests/test_issue_785_thermo_actuator_switch.py:2419-2420; tests/test_issue_908_fancoil_dim20_100.py:855
    for frame in ("*#4*3#2*20*0##", "*#4*3#1*20*0##", "*#4*3#3*20*5##"):
        switch.handle_event(OWNHeatingEvent(frame))
    pending = [hass.loop.create_future() for _ in range(3)]
    gateway.send.side_effect = pending
    limit = 0.08
    monkeypatch.setattr(switch_module, "DIRECT_WRITE_DELIVERY_TIMEOUT", limit)
    start = hass.loop.time()
    try:
        await switch.async_turn_on()
        elapsed = hass.loop.time() - start
        assert gateway.send.await_count == 3
        assert elapsed < 2 * limit, f"one service took {elapsed / limit:.1f} delivery timeouts"
    finally:
        for future in pending:
            future.cancel()


async def test_a_partly_refused_zone_write_still_names_the_refused_frame(hass):
    gateway = _mock_gateway()
    switch = _switch(hass, gateway)
    for frame in ("*#4*3#2*20*0##", "*#4*3#1*20*0##"):
        switch.handle_event(OWNHeatingEvent(frame))
    done = hass.loop.create_future()
    done.set_result(0.0)
    gateway.send.side_effect = [done, _refused(hass)]
    with pytest.raises(HomeAssistantError, match=r"\*#4\*3#2\*#20\*1##"):
        await switch.async_turn_on()
    assert switch._pending_direct_write is not None  # actuator 1 took the write: a revert of it is still told


async def test_a_refused_write_does_not_arm_the_revert_warning(hass, caplog):
    gateway = _mock_gateway()
    switch = _switch(hass, gateway)
    off = OWNHeatingEvent("*#4*3#1*20*0##")  # tests/test_issue_785_thermo_actuator_switch.py:2420
    on = OWNHeatingEvent("*#4*3#1*20*1##")  # tests/fixtures/traces/zone_short_off_2026_11_19/frames.json:433
    switch.handle_event(off)
    gateway.send.return_value = _refused(hass)
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()
    switch.handle_event(on)  # somebody else's on and off: Home Assistant's only write was refused
    switch.handle_event(off)
    assert not switch._revert_warned, caplog.text


async def test_an_aggregate_report_is_not_a_proven_actuator_after_a_restart(hass):
    gateway = _mock_gateway()
    before = _switch(hass, gateway)
    before.handle_event(OWNHeatingEvent("*#4*3#0*20*1##"))  # tests/test_issue_949_fancoil_dim20_output_level.py:1074
    assert before.extra_state_attributes["actuators"] == {"1": "on"}  # shown as before
    saved = State("switch.radiators", "on", before.extra_state_attributes)
    after = _switch(hass, gateway)
    await after.async_restore_last_state(saved)
    assert after.extra_state_attributes["actuators"] == {"1": "on"}
    await after.async_turn_off()
    # The same case before a restart: tests/test_thermo_actuators_and_probes.py, test_all_actuator_report_invents_member
    assert _sent(gateway) == ["*#4*3*#20*0##"]

    # An actuator that reported by its number is a proven one, before and after a restart.
    after.handle_event(OWNHeatingEvent("*#4*3#1*20*0##"))
    again = _switch(hass, gateway)
    await again.async_restore_last_state(State("switch.radiators", "off", after.extra_state_attributes))
    gateway.send.reset_mock()
    await again.async_turn_on()
    assert _sent(gateway) == ["*#4*3#1*#20*1##"]


async def test_a_configured_command_the_gateway_refuses_is_an_error(hass):
    gateway = _mock_gateway()
    # The write the switch builds itself for this address: tests/test_issue_785_thermo_actuator_switch.py:2449.
    switch = _switch(hass, gateway, where="3#1", actuator="1", turn_on_command="*#4*3#1*#20*1##")
    gateway.send.return_value = _refused(hass)
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()


async def test_a_configured_command_nothing_answers_is_not_an_error(hass, monkeypatch):
    monkeypatch.setattr(switch_module, "DIRECT_WRITE_DELIVERY_TIMEOUT", 0.02)
    gateway = _mock_gateway()
    switch = _switch(hass, gateway, where="3#1", actuator="1", turn_on_command="*#4*3#1*#20*1##")
    silent = hass.loop.create_future()
    gateway.send.return_value = silent
    try:
        await switch.async_turn_on()
    finally:
        silent.cancel()


# --- climate --------------------------------------------------------------------------------------


async def test_auto_through_the_central_unit_refused_by_the_gateway_is_an_error(hass):
    zone = _setpoint_zone(hass, standalone=False)
    zone._gateway_handler.has_central_unit = True
    _feed_zone(zone, *F461_POLL_REPLY)
    zone._gateway_handler.send.return_value = _refused(hass)
    with pytest.raises(HomeAssistantError):
        await zone.async_set_hvac_mode(HVACMode.AUTO)


async def test_auto_on_a_standalone_zone_without_a_setpoint_is_an_error(hass):
    zone = _setpoint_zone(hass, "3", standalone=True)
    _feed_zone(zone, "*4*102*3##", "*#4*3*12*0070*3##")  # tests/test_climate_zone_operation.py:189
    assert HVACMode.AUTO in zone.hvac_modes
    with pytest.raises(ServiceValidationError):
        await zone.async_set_hvac_mode(HVACMode.AUTO)
    zone._gateway_handler.send.assert_not_awaited()


async def test_a_refused_fan_speed_is_an_error_and_is_not_shown(hass):
    gateway = _handler(hass, "F461")
    zone = _fan_zone(hass, gateway, "4-2")
    zone._standalone_config = False
    gateway.send = AsyncMock(return_value=_refused(hass))
    before = zone.fan_mode
    with pytest.raises(HomeAssistantError):
        await zone.async_set_fan_mode("low")
    assert zone.fan_mode == before


async def test_a_refused_off_of_the_central_unit_is_an_error_and_no_zone_goes_off(hass):
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    central.hass = hass
    _replay(central, [HEATING_PROGRAM])
    mode_before = central.hvac_mode
    zone = _climate(gateway, "3", standalone=False)
    _replay(zone, ["*4*110*#3##"])  # tests/test_climate_zone_operation.py:82
    unsubscribe = async_dispatcher_connect(hass, f"myhome_central_mode_{gateway.mac}", zone._handle_central_mode_update)
    try:
        gateway.send.return_value = _refused(hass)
        with pytest.raises(HomeAssistantError):
            await central.async_set_hvac_mode(HVACMode.OFF)
        await hass.async_block_till_done()
        assert zone.hvac_mode == HVACMode.HEAT
        assert central.hvac_mode == mode_before
    finally:
        unsubscribe()


async def test_the_commanded_setpoint_that_arrived_meanwhile_is_not_asked(hass, fast_bus_pacing):
    gateway = _handler(hass, "MyHomeServer1")
    waiting = hass.loop.create_future()
    _ready(gateway, side_effect=lambda *args: waiting)
    zone = _polled_zone(hass, gateway, "3")
    # tests/fixtures/traces/issue_429/myhome_trace_MyHomeServer1_who4_2026-09-23T19-32-29.json:469,505
    for raw in ("*4*102*3##", "*#4*3*12*0070*3##"):
        zone.handle_event(OWNMessage.parse(raw))
    poll = asyncio.create_task(zone._poll_if_silent(grace=0))
    await _spin()
    assert _queued(gateway) == ["*#4*3*0##"]  # tests/test_f455_zone_polling.py:147
    zone.handle_event(OWNMessage.parse("*#4*3*0*0270##"))  # the same issue_429 capture:541
    # tests/fixtures/traces/issue_466/myhome_sweep_MH200N_all_2026-09-25T14-40-10.json:1189
    zone.handle_event(OWNMessage.parse("*#4*3*14*0180*3##"))
    assert zone.extra_state_attributes["nominal_temperature"] == 18.0
    waiting.set_result(0.0)
    await poll
    assert "*#4*3*14##" not in _queued(gateway)


@pytest.mark.usefixtures("fast_bus_pacing")
async def test_a_candidate_exposed_while_its_read_waits_for_the_lock_is_not_read(hass, monkeypatch):
    exposed: set[str] = set()
    monkeypatch.setattr(climate_discovery, "is_exposed_on_bus", lambda _hass, _entry, _who, address: address in exposed)
    read = AsyncMock(return_value=False)
    gateway, _zones, _ = await _plant(hass, read=read)
    past_quiet = asyncio.Event()

    async def quiet(_since):
        past_quiet.set()

    gateway._wait_for_bus_quiet = quiet
    async with gateway._who4_startup_poll_lock:
        await _receive(hass, gateway, [ZONE_1_TEMPERATURE])
        gateway._initial_discovery_done.set()
        await asyncio.wait_for(past_quiet.wait(), 2)
        exposed.add("1")
    await _settle(hass, gateway)
    assert read.await_count == 0
