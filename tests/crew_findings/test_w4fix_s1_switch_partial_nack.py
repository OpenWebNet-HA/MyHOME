# ruff: noqa
# Reproduction r1s1_s1_switch_partial_nack of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from unittest.mock import MagicMock
import pytest
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNHeatingEvent
from custom_components.myhome.switch import MyHOMEThermoSwitch
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway

async def test_multi_actuator_write_surfaces_partial_refusal(hass):
    gw = _mock_gateway()
    switch = MyHOMEThermoSwitch(hass=hass, name='Radiators', entity_name=None, icon=None, icon_on=None, device_id='4-3', where='3',zone='3',actuator=None,interface=None,device_class=None,manufacturer='BTicino',model='F430',turn_on_command=None,turn_off_command=None,gateway=gw)
    switch.async_write_ha_state = MagicMock()
    # tests/test_issue_785_thermo_actuator_switch.py:2419-2420.
    for raw in ('*#4*3#2*20*0##','*#4*3#1*20*0##'):
        switch.handle_event(OWNHeatingEvent(raw))
    ack, nack = (hass.loop.create_future() for _ in range(2))
    ack.set_result(0.0)
    nack._gateway_nack = True
    nack.cancel()
    gw.send.side_effect = [ack, nack]
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()
