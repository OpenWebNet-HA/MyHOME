# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0

async def test_timeout_covers_wait_for_queue_space(hass, monkeypatch):
    light, gw = _light(hass)
    space = asyncio.Event()
    gw.send_status_request = AsyncMock(side_effect=lambda cmd: None)
    async def blocked(cmd):
        await space.wait()  # FrameScheduler.put with a full send buffer
        return None
    gw.send_status_request = blocked
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_TIMEOUT_S', .01)
    _feed(light, ON_AT_2, LEVEL_0)
    try:
        await asyncio.sleep(.05)
        assert light.is_on is False, 'never got queue space; provisional ON still displayed after timeout'
    finally:
        await light.async_will_remove_from_hass()
        await hass.async_block_till_done()
