# ruff: noqa
# Reproduction r1s0_zone_mode_refusal_not_raised of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.components.climate import HVACMode
from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _zone, _replay, _entity_base_init


async def test_zone_mode_refusal_not_raised():
    p = _Plant()
    z = _zone(p, '3', standalone=False)
    _replay(z, ['*4*110*#3##', '*#4*#3*14*0250*1##'])  # tests/test_climate_zone_operation.py:82
    p.nack.add('*#4*#3*#14*0250*1##')  # tests/test_climate_setpoint_in_force.py:309
    with pytest.raises(HomeAssistantError):
        await z.async_set_hvac_mode(HVACMode.HEAT)
