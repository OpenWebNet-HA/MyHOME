# ruff: noqa
import asyncio
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed
from tests.test_dimmer_level_0_verified_2026_11_22 import MH200N_ON, MH200N_LEVEL_0, MH200N_PLAIN_ON


async def test_on_while_waiting_for_queue_space_is_not_an_answer(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_TIMEOUT_S', .02)
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass, where='55')
    recovered = gw.send_status_request
    async def full(cmd):
        await asyncio.Event().wait()  # send_buffer.put never returns; no write
    gw.send_status_request = full
    _feed(light, MH200N_ON, MH200N_LEVEL_0)
    await asyncio.sleep(0)
    _feed(light, MH200N_PLAIN_ON)  # a separate switch-on, not a response
    await asyncio.sleep(.05)
    assert light.is_on
    gw.send_status_request = recovered
    _feed(light, MH200N_LEVEL_0)
    await hass.async_block_till_done()
    assert not light.is_on, 'queues recovered, but next level zero neither checked nor settled off'
