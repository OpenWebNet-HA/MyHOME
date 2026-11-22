# ruff: noqa
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, MH202_OFF_LEVEL

async def test_on_with_speed_does_not_confirm_a_zero(hass, monkeypatch):
    # Frames from issue_466/myhome_trace_MH202_f418u2_dimmer_2026-09-27T09-43-45.json:73,133,397.
    # Interleave the speed command's event with a check that otherwise only sees the level.
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gateway = _light(hass, where='32', answers=('*1*1#0*32##', MH202_OFF_LEVEL))
    _feed(light, '*1*5*32##', MH202_OFF_LEVEL)
    await hass.async_block_till_done()
    assert not light.is_on, 'ON at a speed is not the plain ON status answer; only level zero followed it'
