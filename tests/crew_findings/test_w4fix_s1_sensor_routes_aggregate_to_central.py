# ruff: noqa
# Reproduction r1s1_s1_sensor_routes_aggregate_to_central of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from unittest.mock import MagicMock
from custom_components.myhome.sensor import async_setup_entry as sensors
from tests.test_issue_966_phantom_climate_zone import _plant, _receive

async def test_actuator_answer_through_both_platforms(hass):
    handler, zones, entry = await _plant(hass, model='F461')
    entry.runtime_data.platforms['sensor'] = {}
    await sensors(hass, entry, lambda es: None)
    await _receive(hass, handler, ['*4*210#0240*#0##'])  # tests/test_issue_944_actuator_answer_not_central.py:103
    central, = zones
    central.async_write_ha_state = MagicMock()
    await _receive(hass, handler, ['*#4*#0*20*1##'])  # tests/test_issue_944_actuator_answer_not_central.py:45
    assert 'actuators' not in central.extra_state_attributes
