# ruff: noqa
# Reproduction r1s0_preadd_zone_operation of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from homeassistant.core import State
from homeassistant.components.climate import HVACMode
from tests.test_climate_zone_operation import _zone, _feed, _operation


async def test_preadd_zone_operation(hass):
    zone = _zone(hass, '3')
    # Frame used to seed the entity before HA retrieves the saved state.
    _feed(zone, '*4*110*#3##', '*#4*#3*14*0250*1##')  # tests/test_climate_zone_operation.py:82
    assert (zone.hvac_mode, _operation(zone)) == (HVACMode.HEAT, 'manual')
    await zone.async_restore_last_state(State(zone.entity_id, 'off', {'zone_operation': 'antifreeze', 'temperature': 7.0}))
    assert (zone.hvac_mode, _operation(zone)) == (HVACMode.HEAT, 'manual')
