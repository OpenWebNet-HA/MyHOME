"""WHO 4 actuators and probes: the zone valve switch, the actuator output sensor, an actuator answer of
the central unit, and the probe's fallback read.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNHeatingEvent, OWNMessage

from custom_components.myhome.sensor import (
    MyHOMEActuatorOutputSensor,
)
from custom_components.myhome.sensor import (
    async_setup_entry as setup_sensor,
)
from custom_components.myhome.switch import MyHOMEThermoSwitch
from tests import test_probe_fallback_read
from tests.test_f455_zone_polling import _handler
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway
from tests.test_issue_944_actuator_answer_not_central import ANSWER_BURST
from tests.test_issue_966_phantom_climate_zone import _plant, _receive
from tests.test_probe_fallback_read import (
    PROBE_PUSH_FRESH,
    READ,
    _refused,
    _sensor,
    _sent,
    _settle,
)

# Fixture of another test module, asked for by name below.
gateway = test_probe_fallback_read.gateway


async def test_all_actuator_report_invents_member(hass):
    gw = _mock_gateway()
    switch = MyHOMEThermoSwitch(
        hass=hass,
        name="Radiators",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="4-3",
        where="3",
        zone="3",
        actuator=None,
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F430",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gw,
    )
    switch.handle_event(
        OWNHeatingEvent("*#4*3#0*20*1##")
    )  # tests/test_issue_949_fancoil_dim20_output_level.py:1074
    await switch.async_turn_off()
    sent = [str(c.args[0]) for c in gw.send.await_args_list]
    assert "*#4*3#1*#20*0##" not in sent  # no specific actuator has reported


async def test_partial_actuator_restore(hass):
    gw = _mock_gateway()
    switch = MyHOMEThermoSwitch(
        hass=hass,
        name="Radiators",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="4-3",
        where="3",
        zone="3",
        actuator=None,
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F430",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gw,
    )
    # Both members known at shutdown. Only one has announced itself before async_added_to_hass.
    saved = State("switch.radiators", "on", {"actuators": {"1": "on", "2": "on"}})
    switch.handle_event(
        OWNHeatingEvent("*#4*3#1*20*0##")
    )  # tests/test_issue_785_thermo_actuator_switch.py:2392
    await switch.async_restore_last_state(saved)
    await switch.async_turn_off()
    assert [str(c.args[0]) for c in gw.send.await_args_list] == [
        "*#4*3#1*#20*0##",
        "*#4*3#2*#20*0##",
    ]  # tests/test_issue_785_thermo_actuator_switch.py:2428


async def test_one_refused_actuator_must_fail_whole_zone_service(hass):
    gateway = _mock_gateway()
    zone = MyHOMEThermoSwitch(
        hass=hass,
        name="Radiators",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="4-3",
        where="3",
        zone="3",
        actuator=None,
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="F430",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gateway,
    )
    zone.entity_id = "switch.radiators"
    zone.async_write_ha_state = MagicMock()
    # Same reported relays as tests/test_issue_785_thermo_actuator_switch.py:2391-2394.
    for raw in ("*#4*3#2*20*0##", "*#4*3#1*20*0##"):
        zone.handle_event(OWNMessage.parse(raw))
    assert zone.extra_state_attributes["actuators"] == {"1": "off", "2": "off"}
    ok = asyncio.get_running_loop().create_future()
    ok.set_result(asyncio.get_running_loop().time())
    nack = asyncio.get_running_loop().create_future()
    nack._gateway_nack = True
    nack.cancel()
    gateway.send = AsyncMock(side_effect=[ok, nack])
    with pytest.raises(HomeAssistantError):
        await zone.async_turn_on()


async def test_actuator_sensor_zone_gate(hass):
    gateway = _handler(hass, "F453")
    gateway.send_status_request = AsyncMock()
    sensor = MyHOMEActuatorOutputSensor(
        hass, "Heating Zone", "2", "2#1", "2", "1", None, None, "BTicino", "Heating Zone", gateway
    )
    assert not gateway.actuator_read_refused("2#1")
    await sensor.async_update()  # HA update_entity
    assert [str(c.args[0]) for c in gateway.send_status_request.await_args_list] == [
        "*#4*2#1*20##"
    ]  # own-firmware-oracle results/F453/010100/oracle/full/thermo-zone-requests.tsv:23


async def test_existing_central_not_an_actuator_with_all_platforms(hass):
    handler, zones, entry = await _plant(hass, model="F461")
    entry.runtime_data.platforms["sensor"] = {}
    await setup_sensor(hass, entry, lambda entities: None)
    await _receive(
        hass, handler, ["*4*210#0240*#0##"]
    )  # tests/test_issue_944_actuator_answer_not_central.py:103
    assert [z._where for z in zones] == ["#0"]
    central = zones[0]
    central.hass = hass
    await _receive(hass, handler, [ANSWER_BURST[2]])
    assert "actuators" not in central.extra_state_attributes


async def test_probe_short_reconnect(hass):
    gw = _handler(hass, "F455")
    gw._on_event_connection_state_change(True)
    p = _sensor(hass, gw)
    p.async_write_ha_state = MagicMock()
    p._register_availability_listener()
    gw.send_status_request = AsyncMock(return_value=_refused(hass))
    await p.async_update()
    await _settle()
    assert p._fallback_read_refused
    gw._on_event_connection_state_change(False)
    gw._on_event_connection_state_change(True)  # before sustained unavailability, no reload
    await _settle()
    await p.async_update()
    p._call_on_remove_callbacks()
    assert _sent(gw) == [READ, READ]


async def test_routed_probe_fallback(hass, gateway):
    raw = "*#4*105#4#02*0*0215##"  # tests/test_issue_967_phantom_temperature_probe.py:435
    gateway.interface_zone_request_refused = True
    sensor = _sensor(hass, gateway, "105#4#02")
    sensor.handle_event(OWNHeatingEvent(raw))
    assert sensor.native_value == 21.5
    sensor._last_push_at -= PROBE_PUSH_FRESH + 1
    await sensor.async_update()
    assert not _sent(gateway)
