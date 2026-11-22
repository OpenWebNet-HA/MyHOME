# ruff: noqa
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, OFF, LEVEL_0, PLAIN_ON
from tests.test_dimmer_level_0_verified_2026_11_22 import ON_AT_8, LEVEL_60


async def test_off_answer_spends_provisional_level(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass, answers=(OFF,))
    _feed(light, ON_AT_8, LEVEL_60, LEVEL_0)
    assert light.is_on and light._attr_brightness_pct == 1
    await hass.async_block_till_done()
    assert not light.is_on
    _feed(light, PLAIN_ON)
    await hass.async_block_till_done()
    assert light.is_on and light._attr_brightness_pct == 60, 'plain ON reused provisional minimum rather than last known level'
