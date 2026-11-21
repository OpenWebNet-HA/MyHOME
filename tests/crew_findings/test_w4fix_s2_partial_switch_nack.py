# ruff: noqa
# Reproduction r1s2_s2_partial_switch_nack of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNMessage
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway, MyHOMEThermoSwitch

async def test_one_refused_actuator_must_fail_whole_zone_service(hass):
    gateway = _mock_gateway()
    zone = MyHOMEThermoSwitch(hass=hass, name='Radiators', entity_name=None, icon=None, icon_on=None,
        device_id='4-3', where='3', zone='3', actuator=None, interface=None, device_class=None,
        manufacturer='BTicino', model='F430', turn_on_command=None, turn_off_command=None, gateway=gateway)
    zone.entity_id = 'switch.radiators'
    zone.async_write_ha_state = MagicMock()
    # Same reported relays as tests/test_issue_785_thermo_actuator_switch.py:2391-2394.
    for raw in ('*#4*3#2*20*0##', '*#4*3#1*20*0##'):
        zone.handle_event(OWNMessage.parse(raw))
    assert zone.extra_state_attributes['actuators'] == {'1':'off','2':'off'}
    ok = asyncio.get_running_loop().create_future()
    ok.set_result(asyncio.get_running_loop().time())
    nack = asyncio.get_running_loop().create_future()
    nack._gateway_nack = True
    nack.cancel()
    gateway.send = AsyncMock(side_effect=[ok,nack])
    with pytest.raises(HomeAssistantError):
        await zone.async_turn_on()
