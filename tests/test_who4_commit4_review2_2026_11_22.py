"""What the review of the fixes for the second review of commit ``e7d7f08c`` found, 2026-11-22.

Four points, each reproduced on the code before this commit. The frames are those of
``tests/test_who4_commit4_review_2026_11_22.py`` and of the captures named there; no frame is new.
The scenarios are composed (an order of events built for the test), not recorded transactions.

- The central unit's ``off`` / program raised for a refusal only when the gateway handed the
  delivery back already refused. A real gateway refuses after the frame was queued: the call had
  returned, ``off`` was shown and passed on to every zone.
- The silent-zone poll checked "has the commanded setpoint arrived" before it let the zone's own
  requests through; a setpoint that arrived during that wait was still asked.
- An address known only from its actuators, exposed on the bus while its read waited for the
  startup lock, was still read (the candidate path had the check, this one did not).
- A thermo switch call cancelled while it waited for the gateway (an automation restarted) left
  its "reverted after a write" check armed for a write that was then refused.
"""

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNHeatingEvent, OWNMessage

from custom_components.myhome import climate_discovery
from tests.test_crew_central_season_restore import HEATING_PROGRAM, _climate, _gateway, _replay
from tests.test_f455_zone_polling import _handler, _queued, _ready
from tests.test_f455_zone_polling import _zone as _polled_zone
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway
from tests.test_thermo_discovery_at_start import _after_the_sweep, _plant, _receive
from tests.test_who4_commit4_review_2026_11_22 import _switch

ACTUATOR_1_OFF = "*#4*3#1*20*0##"  # tests/test_issue_785_thermo_actuator_switch.py:2420
ACTUATOR_1_ON = "*#4*3#1*20*1##"  # tests/fixtures/traces/zone_short_off_2026_11_19/frames.json:433


async def test_an_off_of_the_central_unit_refused_after_it_was_queued_is_an_error(hass):
    """The NACK comes after ``send`` returned, as on a real gateway (the delivery future is pending)."""
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    central.hass = hass
    _replay(central, [HEATING_PROGRAM])
    zone = _climate(gateway, "3", standalone=False)
    _replay(zone, ["*4*110*#3##"])  # tests/fixtures/traces/zone_short_off_2026_11_19/frames.json:198
    unsubscribe = async_dispatcher_connect(hass, f"myhome_central_mode_{gateway.mac}", zone._handle_central_mode_update)
    try:
        delivery = hass.loop.create_future()
        gateway.send.return_value = delivery
        call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.OFF))
        await asyncio.sleep(0)
        assert not call.done()  # nothing is shown before the gateway answered
        assert central.hvac_mode == HVACMode.HEAT
        delivery._gateway_nack = True  # how the session marks a NACK: tests/test_probe_fallback_read.py (_refused)
        delivery.cancel()
        with pytest.raises(HomeAssistantError):
            await call
        await hass.async_block_till_done()
        assert central.hvac_mode == HVACMode.HEAT
        assert zone.hvac_mode == HVACMode.HEAT
    finally:
        unsubscribe()


async def test_an_off_of_the_central_unit_the_gateway_took_is_shown(hass):
    """The same call with the delivery confirmed: `off` is shown once the gateway has answered."""
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [HEATING_PROGRAM])
    delivery = hass.loop.create_future()
    gateway.send.return_value = delivery
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    assert central.hvac_mode == HVACMode.HEAT
    delivery.set_result(0.0)
    await call
    assert central.hvac_mode == HVACMode.OFF


@pytest.mark.usefixtures("fast_bus_pacing")
async def test_a_setpoint_that_arrives_while_the_zones_own_requests_go_first_is_not_asked(hass):
    gateway = _handler(hass, "MyHomeServer1")
    zone = _polled_zone(hass, gateway, "3")
    # tests/fixtures/traces/issue_429/myhome_trace_MyHomeServer1_who4_2026-09-23T19-32-29.json:469,505,541
    for raw in ("*4*102*3##", "*#4*3*12*0070*3##", "*#4*3*0*0270##"):
        zone.handle_event(OWNHeatingEvent(raw))
    zone._local_offset_read_sent = True  # as after the startup sweep
    _ready(gateway)

    async def own_requests_first() -> None:
        # tests/fixtures/traces/issue_466/myhome_sweep_MH200N_all_2026-09-25T14-40-10.json:1189
        zone.handle_event(OWNHeatingEvent("*#4*3*14*0180*3##"))

    gateway.wait_for_zone_requests = own_requests_first
    await zone._poll_if_silent(grace=0)
    assert "*#4*3*14##" not in _queued(gateway)


async def test_an_actuator_only_address_exposed_while_its_read_waits_is_not_read(hass, monkeypatch):
    exposed: set[str] = set()
    monkeypatch.setattr(climate_discovery, "is_exposed_on_bus", lambda _hass, _entry, _who, where: where in exposed)
    read = AsyncMock(return_value=False)
    gateway, _zones = await _plant(hass, read)
    async with gateway._who4_startup_poll_lock:
        await _receive(hass, gateway, [ACTUATOR_1_OFF])
        gateway._initial_discovery_done.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if gateway._who4_startup_poll_lock._waiters:
                break
        assert gateway._who4_startup_poll_lock._waiters  # the read waits for the lock
        exposed.add("3")
    await _after_the_sweep(hass, gateway)
    assert read.await_count == 0


async def test_a_cancelled_switch_call_does_not_leave_its_revert_check_armed(hass, caplog):
    gateway = _mock_gateway()
    switch = _switch(hass, gateway)
    off = OWNMessage.parse(ACTUATOR_1_OFF)
    on = OWNMessage.parse(ACTUATOR_1_ON)
    switch.handle_event(off)
    written = hass.loop.create_future()
    gateway.send.return_value = written
    call = asyncio.create_task(switch.async_turn_on())
    await asyncio.sleep(0)
    gateway.send.assert_awaited_once()
    call.cancel()
    with suppress(asyncio.CancelledError):
        await call
    assert not written.done()  # the frame itself is not withdrawn
    written._gateway_nack = True
    written.cancel()
    await asyncio.sleep(0)
    switch.handle_event(on)
    switch.handle_event(off)
    assert "reverted to OFF" not in caplog.text


async def test_an_aggregate_actuator_saved_off_is_still_no_write_target_after_a_restart(hass):
    """The saved state of a switch last seen off through an aggregate report (built attributes, no frame)."""
    gateway = _mock_gateway()
    switch = _switch(hass, gateway)
    saved = State("switch.radiators", "off", {"actuators": {"1": "off"}, "aggregate_actuators": ["1"]})
    await switch.async_restore_last_state(saved)
    assert switch.extra_state_attributes["actuators"] == {"1": "off"}
    assert switch.extra_state_attributes["aggregate_actuators"] == ["1"]
    await switch.async_turn_on()
    # The zone form, not actuator 1: tests/test_issue_785_thermo_actuator_switch.py:2416
    assert [str(call.args[0]) for call in gateway.send.await_args_list] == ["*#4*3*#20*1##"]
