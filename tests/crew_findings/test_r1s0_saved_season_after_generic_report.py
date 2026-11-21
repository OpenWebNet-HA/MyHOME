# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import State
from tests.test_crew_central_season_restore import _climate, _gateway, _entity_base_init, _replay, _sent, HEAT

async def test_generic_operating_mode_does_not_confirm_saved_season():
    gw = _gateway(); c = _climate(gw,'#0',standalone=None,central=True)
    await c.async_restore_last_state(State(c.entity_id,'cool',{'season':'conditioning'}))
    # F455 monitor; the 3550 did not act on it. README.md:84-87 in central_restore_3100_live_2026-11-12.
    _replay(c,['*4*311*#0##'])
    assert not c._season_reported
    await c.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw)==[HEAT]
