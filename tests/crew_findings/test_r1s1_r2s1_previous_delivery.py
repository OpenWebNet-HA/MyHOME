# ruff: noqa
"""The pool owns the first delivery future after the light gives up; it can still dispatch that result."""
import asyncio
from unittest.mock import AsyncMock
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed
from tests.test_dimmer_level_0_verified_2026_11_22 import MH200N_ON, MH200N_PLAIN_ON, MH200N_LEVEL_0, STATUS_REQUEST

async def test_first_request_completes_during_second_verification(hass, monkeypatch):
    # Built delays/replies, not a claimed MH200N capture. Command results follow the timestamp
    # completion as in custom_components/myhome/gateway_sessions.py:1625-1667.
    monkeypatch.setattr(mod,'_LEVEL_ZERO_CHECK_TIMEOUT_S',0.01)
    monkeypatch.setattr(mod,'_LEVEL_ZERO_CHECK_GRACE_S',0)
    light, gw = _light(hass, where='55')
    first, second = (asyncio.get_running_loop().create_future() for _ in range(2))
    gw.send_status_request=AsyncMock(side_effect=(first,second))
    _feed(light,MH200N_ON,MH200N_LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False and not first.done()
    # Own status, then a real new off with a ramp. Its verification is a NEW request.
    # A preset here differs from the old read's plain ON: the pool has not previously
    # recorded a copy of that reply, so deduplication won't hide the late result.
    _feed(light,MH200N_ON,MH200N_LEVEL_0)
    await asyncio.sleep(0)
    assert [str(c.args[0]) for c in gw.send_status_request.await_args_list]==['*#1*55##']*2  # tests/fixtures/traces/issue_612/myhome_trace_MH200N_startup_cen_scene_2026-10-07T20-38-24.json:1929
    # First transaction finally finishes: the old sampled ON is dispatched. It is not
    # the result of the second transaction. That one returns only its current exact zero.
    first.set_result(0.0)
    _feed(light,MH200N_PLAIN_ON)
    second.set_result(0.0)
    _feed(light,MH200N_LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False, 'the closed first read confirmed a different, level-only read'
