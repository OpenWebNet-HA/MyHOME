# ruff: noqa
from homeassistant.core import State
from tests.test_crew_central_season_restore import _climate, _gateway, _entity_base_init, _sent, _replay, HEAT, HVACMode

async def test_shared_bus_seasonless_frame_does_not_validate_saved_season():
    gw = _gateway()
    c = _climate(gw,'#0',central=True,standalone=None)
    await c.async_restore_last_state(State(c.entity_id,'cool',{'season':'conditioning'}))
    # F455 sees another gateway's command. 3550 did not answer it in the capture.
    # tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:84-87
    _replay(c,['*4*311*#0##'])
    assert not c._season_reported
    await c.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT]
