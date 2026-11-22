# ruff: noqa
import asyncio
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0, OFF
from tests.test_dimmer_level_0_verified_2026_11_22 import STATUS_REQUEST


async def test_off_answer_then_new_on_and_zero_during_grace(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', .05)
    light, gw = _light(hass, answers=(OFF,))
    _feed(light, ON_AT_2, LEVEL_0)
    await asyncio.sleep(.005)
    assert light.is_on is False  # first request already answered OFF and closed
    gw.status_answers = [ON_AT_2]
    _feed(light, ON_AT_2, LEVEL_0)  # distinct physical switch-on after OFF
    await hass.async_block_till_done()
    assert (gw.requests.count(STATUS_REQUEST), light.is_on) == (2, True), 'new cycle settled by closed request, never asked'
