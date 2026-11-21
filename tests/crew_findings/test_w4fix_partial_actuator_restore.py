# ruff: noqa
# Reproduction r1s0_partial_actuator_restore of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent
from custom_components.myhome.switch import MyHOMEThermoSwitch
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway


async def test_partial_actuator_restore(hass):
    gw = _mock_gateway()
    switch = MyHOMEThermoSwitch(hass=hass, name='Radiators', entity_name=None, icon=None, icon_on=None, device_id='4-3', where='3', zone='3', actuator=None, interface=None, device_class=None, manufacturer='BTicino', model='F430', turn_on_command=None, turn_off_command=None, gateway=gw)
    # Both members known at shutdown. Only one has announced itself before async_added_to_hass.
    saved = State('switch.radiators', 'on', {'actuators': {'1': 'on', '2': 'on'}})
    switch.handle_event(OWNHeatingEvent('*#4*3#1*20*0##'))  # tests/test_issue_785_thermo_actuator_switch.py:2392
    await switch.async_restore_last_state(saved)
    await switch.async_turn_off()
    assert [str(c.args[0]) for c in gw.send.await_args_list] == ['*#4*3#1*#20*0##', '*#4*3#2*#20*0##']  # tests/test_issue_785_thermo_actuator_switch.py:2428
