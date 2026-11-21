# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from tests.test_crew_central_season_restore import (_entity_base_init, _gateway, _climate, _sent, _replay, THERMAL_PROTECTION, HEAT, RESTORE)

async def test_discovered_before_storage_restored():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    central.entity_id = None
    central.platform = None
    _replay(central, [THERMAL_PROTECTION])
    central.entity_id = 'climate.test'
    await central.async_restore_last_state(State('climate.test', 'heat', {'season':'heating'}))
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT, RESTORE]
