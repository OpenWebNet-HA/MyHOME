# ruff: noqa
# Reproduction r1s0_all_actuator_report_invents_member of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from custom_components.myhome.switch import MyHOMEThermoSwitch
from tests.test_issue_785_thermo_actuator_switch import _mock_gateway
from OWNd.message import OWNHeatingEvent


async def test_all_actuator_report_invents_member(hass):
    gw = _mock_gateway()
    switch = MyHOMEThermoSwitch(hass=hass, name='Radiators', entity_name=None, icon=None, icon_on=None, device_id='4-3', where='3', zone='3', actuator=None, interface=None, device_class=None, manufacturer='BTicino', model='F430', turn_on_command=None, turn_off_command=None, gateway=gw)
    switch.handle_event(OWNHeatingEvent('*#4*3#0*20*1##'))  # tests/test_issue_949_fancoil_dim20_output_level.py:1074
    await switch.async_turn_off()
    sent = [str(c.args[0]) for c in gw.send.await_args_list]
    assert '*#4*3#1*#20*0##' not in sent  # no specific actuator has reported
