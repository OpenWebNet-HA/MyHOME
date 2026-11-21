# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive, _sent
from tests.test_crew_central_season_restore import HEAT, COOL

async def test_last_confirmed_manual_season_survives_a_local_echo(hass):
    handler, entities = await _plant(hass)
    # Composed scenario, no central answer after COOL; real monitor echo format in
    # tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:88.
    await _receive(hass, handler, ['*4*110#0180*#0##'])  # tests/fixtures/traces/issue_466/README.md:30
    central = next(c for c in entities if c._central)
    await central.async_set_hvac_mode(HVACMode.COOL)
    await _receive(hass, handler, [COOL])
    assert central.hvac_mode == HVACMode.HEAT
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(handler) == [HEAT]
