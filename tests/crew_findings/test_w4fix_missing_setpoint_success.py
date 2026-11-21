# ruff: noqa
# Reproduction r1s0_missing_setpoint_success of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.exceptions import HomeAssistantError
from tests.test_climate_zone_operation import _feed, _zone


async def test_missing_setpoint_success(hass):
    zone = _zone(hass, '3')
    _feed(zone, '*4*102*3##', '*#4*3*12*0070*3##')  # tests/test_climate_zone_operation.py:189
    assert 'nominal_temperature' not in zone.extra_state_attributes
    with pytest.raises(HomeAssistantError):
        await zone.async_set_hvac_mode(HVACMode.HEAT)
