# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
import pytest
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0

async def test_cancel_automation_before_enqueue(hass, monkeypatch):
    # E.g. HA stops an automation waiting for room in the send queue.
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass)
    done = asyncio.get_running_loop().create_future()
    gw.send_status_request=AsyncMock(return_value=done)
    entered=asyncio.Event()
    async def full_queue(msg):
        entered.set()
        await asyncio.Event().wait()
    gw.send=full_queue
    _feed(light, ON_AT_2, LEVEL_0)
    assert light.is_on
    action=asyncio.create_task(light.async_turn_on())
    await entered.wait()
    action.cancel()
    with pytest.raises(asyncio.CancelledError):
        await action
    done.set_result(0.0)
    await hass.async_block_till_done()
    assert light.is_on is False, 'cancelled automation sent nothing and the check ended without ON'
