# ruff: noqa
# Reproduction r1s0_central_alias_general_off of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from homeassistant.components.climate import HVACMode
from tests.test_crew_central_season_restore import _entity_base_init, _gateway, _climate, _replay, HEATING_PROGRAM


async def test_central_alias_general_off():
    central = _climate(_gateway(), '0', central=True, standalone=None)
    _replay(central, [HEATING_PROGRAM])
    assert central.hvac_mode == HVACMode.HEAT
    _replay(central, ['*4*303*0##'])  # tests/test_climate_central_discovery.py:1202
    assert central.hvac_mode == HVACMode.HEAT
