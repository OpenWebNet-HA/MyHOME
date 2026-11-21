# ruff: noqa
# Reproduction r1s0_no_read_of_missing_nominal of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from tests.test_climate_zone_operation import _feed, _zone


async def test_no_read_of_missing_nominal(hass):
    zone = _zone(hass, '3')
    _feed(zone, '*4*102*3##', '*#4*3*12*0070*3##')  # tests/test_climate_zone_operation.py:189
    assert 'nominal_temperature' not in zone.extra_state_attributes
    assert '*#4*3*14##' in zone._silent_zone_reads()  # tests/test_climate_setpoint_in_force.py:369-371
