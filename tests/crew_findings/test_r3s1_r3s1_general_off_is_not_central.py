# ruff: noqa
"""A general-probes operation is not an OFF report from the 3550."""
from homeassistant.components.climate.const import HVACMode
from tests.test_issue_944_actuator_answer_not_central import _plant, _receive, _sent
from tests.test_crew_central_season_restore import HEAT

async def test_general_probe_off_does_not_replace_manual_setpoint(hass):
    handler, zones = await _plant(hass)
    await _receive(hass, handler, ['*4*110#0180*#0##'])  # tests/fixtures/traces/issue_466/README.md:30
    central = next(c for c in zones if c._central)
    # Composed routing case, not a capture. General OFF verbatim from tests/test_issue_256_257.py:235-236.
    await _receive(hass, handler, ['*4*303*0##'])
    handler.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(handler) == [HEAT]
