# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
import pytest
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0
from tests.test_dimmer_level_0_verified_2026_11_22 import LEVEL_60


@pytest.mark.parametrize('order', ['closed', 'newer_level'])
async def test_enqueue_failure_restores_only_current_check(hass, monkeypatch, order):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass)
    closed = asyncio.get_running_loop().create_future()
    gw.send_status_request = AsyncMock(return_value=closed)
    _feed(light, ON_AT_2, LEVEL_0)
    task = light._level_zero_check_task
    release = asyncio.Event()
    async def failed_send(cmd):
        await release.wait()
        raise asyncio.QueueShutDown()
    gw.send = failed_send
    command = asyncio.create_task(light.async_turn_on())
    await asyncio.sleep(0)
    if order == 'closed':
        closed.set_result(0.0)
        await task
    else:
        _feed(light, LEVEL_60)
    release.set()
    with pytest.raises(asyncio.QueueShutDown):
        await command
    if not closed.done():
        closed.set_result(0.0)
    await hass.async_block_till_done()
    expected = (False, 0) if order == 'closed' else (True, 60)
    assert (light.is_on, light._attr_brightness_pct) == expected
