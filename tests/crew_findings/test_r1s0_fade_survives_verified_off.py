# ruff: noqa
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0
from custom_components.myhome import light as mod

async def test_settled_off_stops_ongoing_software_fade(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass, answers=(LEVEL_0,))
    _feed(light, ON_AT_2)
    light._fade_engine.start_fade(1, 7, .3)
    _feed(light, LEVEL_0)
    assert light._fade_engine.is_fading
    await hass.async_block_till_done()
    assert not light.is_on, 'ongoing fade sent more brightness commands after level-only answer settled OFF'
