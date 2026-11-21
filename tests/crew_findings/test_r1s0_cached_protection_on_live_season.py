# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from tests.test_crew_central_season_restore import (_entity_base_init, _gateway, _climate, _sent, _replay, HEAT)

async def test_live_season_does_not_confirm_saved_protection():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    await central.async_restore_last_state(State(central.entity_id, 'heat', {'central_operation': 'antifreeze'}))
    _replay(central, [HEAT])  # only the season, no protection reported this boot
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT]
