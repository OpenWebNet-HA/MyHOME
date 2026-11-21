# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from tests.test_crew_central_season_restore import (_entity_base_init, _gateway, _climate, _sent, HEAT)

async def test_off_without_a_report_is_not_proof_for_restore():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    await central.async_set_hvac_mode(HVACMode.OFF)
    gw.send.reset_mock()
    # No response to off, and no other report since startup (acceptance 4).
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT]
