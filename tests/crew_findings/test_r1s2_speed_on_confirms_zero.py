# ruff: noqa
from types import SimpleNamespace
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light,_feed,MH202_OFF_LEVEL

async def test_on_with_speed_does_not_confirm_later_zero(hass,monkeypatch):
    # Composed order; all raw frames are from issue_466/myhome_trace_MH202_f418u2_dimmer_2026-09-27T09-43-45.json:73,109,397.
    clock=[100.]; monkeypatch.setattr(mod,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(mod,'_LEVEL_ZERO_CHECK_GRACE_S',0)
    light,gw=_light(hass,where='32',answers=('*1*1#0*32##',))
    _feed(light,'*1*5*32##',MH202_OFF_LEVEL)
    await hass.async_block_till_done()
    gw.status_answers=[]
    clock[0]+=6.
    _feed(light,MH202_OFF_LEVEL)
    await hass.async_block_till_done()
    assert light.is_on is False, 'ON with transition speed was treated as confirmation of ON at exact zero'
