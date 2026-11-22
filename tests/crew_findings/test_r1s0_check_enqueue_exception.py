# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0

async def test_queue_shutdown_during_status_check_allows_recovery(hass):
    light, gw = _light(hass, answers=(ON_AT_2,))
    recovered = gw.send_status_request
    gw.send_status_request = AsyncMock(side_effect=asyncio.QueueShutDown())
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    gw.send_status_request = recovered
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    assert gw.requests, 'done task retained in the in-flight slot after an immediate enqueue exception'
