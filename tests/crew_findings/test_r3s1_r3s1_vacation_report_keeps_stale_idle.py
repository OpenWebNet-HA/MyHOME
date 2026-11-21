# ruff: noqa
"""The published vacation status, unlike a generic command echo, proves a mode change."""
from homeassistant.components.climate.const import HVACMode
from tests.test_crew_central_season_restore import _entity_base_init, _gateway, _climate, _replay, _sent, ANTIFREEZE, HEAT

async def test_active_holiday_is_not_an_old_protection_state():
    g=_gateway(); c=_climate(g,'#0',central=True,standalone=None)
    # Source example, not a plant capture: generic activation has a return-program
    # parameter (kb/openwebnet-who-4.txt:2450), but its monitor report has none (:2461).
    _replay(c,[ANTIFREEZE,'*4*33003*#0##'])
    assert c.hvac_mode == HVACMode.AUTO and c.extra_state_attributes.get('holiday_days') == 3
    await c.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(g) == [HEAT]
