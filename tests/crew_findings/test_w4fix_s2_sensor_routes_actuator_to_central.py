# ruff: noqa
# Reproduction r1s2_s2_sensor_routes_actuator_to_central of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from custom_components.myhome.sensor import async_setup_entry as setup_sensor
from tests.test_issue_966_phantom_climate_zone import _plant, _receive
from tests.test_issue_944_actuator_answer_not_central import ANSWER_BURST

async def test_existing_central_not_an_actuator_with_all_platforms(hass):
    handler, zones, entry = await _plant(hass, model='F461')
    entry.runtime_data.platforms['sensor'] = {}
    await setup_sensor(hass, entry, lambda entities: None)
    await _receive(hass, handler, ['*4*210#0240*#0##'])  # tests/test_issue_944_actuator_answer_not_central.py:103
    assert [z._where for z in zones] == ['#0']
    central = zones[0]
    central.hass = hass
    await _receive(hass, handler, [ANSWER_BURST[2]])
    assert 'actuators' not in central.extra_state_attributes
