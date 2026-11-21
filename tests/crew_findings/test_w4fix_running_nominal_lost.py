# ruff: noqa
# Reproduction r1s0_running_nominal_lost of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from homeassistant.core import State
from homeassistant.components.climate import HVACMode
from tests.test_climate_setpoint_in_force import F461_POLL_REPLY, _zone, _feed


async def test_running_nominal_lost(hass):
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY)  # tests/test_climate_setpoint_in_force.py:20-25
    saved = State(live.entity_id, live.hvac_mode, {'temperature': live.target_temperature, **live.extra_state_attributes})
    assert saved.attributes['nominal_temperature'] == 21.0
    restarted = _zone(hass)
    await restarted.async_restore_last_state(saved)
    await restarted.async_set_hvac_mode(HVACMode.HEAT)
    sent = restarted._gateway_handler.send.await_args.args[0]
    assert str(sent).split('*')[-2] == '0210'
