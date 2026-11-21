# ruff: noqa
# Reproduction r1s0_off_rolls_back_new_nominal of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from tests.test_climate_zone_operation import _zone, _feed


async def test_off_rolls_back_new_nominal(hass):
    z = _zone(hass, '2')
    _feed(z, '*4*1*2##', '*#4*2*14*0210*3##')  # tests/test_climate_setpoint_in_force.py:20-25
    _feed(z, '*#4*2*12*0070*3##', '*#4*2*14*0170*3##', '*4*102*2##')  # tests/test_climate_zone_operation.py:106
    assert z.extra_state_attributes['nominal_temperature'] == 17.0
