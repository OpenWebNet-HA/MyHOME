# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive, _sent
from tests.test_crew_central_season_restore import HEAT, RESTORE, HEATING_PROGRAM

async def test_plant_has_newer_live_season_from_central_about_its_zone(hass):
    handler,zones = await _plant(hass)
    await _receive(hass,handler,[HEATING_PROGRAM])
    central = next(c for c in zones if c._central)
    # Central now reports zone 4 in manual conditioning. No new #0 burst:
    # tests/fixtures/traces/climate_mode_2026_11_08/frames.json (f461 zone 4).
    await _receive(hass,handler,['*4*210*#4##'])
    assert handler.central_season == 'conditioning'
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(handler) == [HEAT, RESTORE]
