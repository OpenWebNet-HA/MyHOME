# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive, _sent
from tests.test_crew_central_season_restore import COOL, HEATING_PROGRAM

async def test_first_new_season_after_reconnect_supersedes_old_heating_report(hass):
    handler, zones = await _plant(hass)
    handler._on_event_connection_state_change(True)
    await _receive(hass, handler, [HEATING_PROGRAM])
    central = next(z for z in zones if z._central)
    handler._on_event_connection_state_change(False)
    handler._on_event_connection_state_change(True)
    # First report in the new session: a central-controlled zone, manual cooling.
    # tests/test_issue_944_actuator_answer_not_central.py:107
    await _receive(hass, handler, ['*4*210*#4##'])
    assert handler.central_season == 'conditioning'
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(handler) == [COOL]
