# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
import pytest
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0


async def test_timer_failure_does_not_undo_acknowledged_brightness(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass)
    closed = asyncio.get_running_loop().create_future()
    gw.send_status_request = AsyncMock(return_value=closed)
    _feed(light, ON_AT_2, LEVEL_0)
    # First write delivered, only enqueue of the timer operation fails.
    delivered = asyncio.get_running_loop().create_future()
    delivered.set_result(0.0)
    gw.send = AsyncMock(side_effect=[delivered, asyncio.QueueShutDown()])
    with pytest.raises(asyncio.QueueShutDown):
        await light.async_turn_on_timed(duration=60, brightness_pct=60)
    assert gw.send.await_count == 2 and light._attr_brightness_pct == 1
    closed.set_result(0.0)  # earlier status request finally settles without undoing ON
    await hass.async_block_till_done()
    assert light.is_on is True
    _feed(light, "*#1*0010*1*160*10##")
    assert (light.is_on, light._attr_brightness_pct) == (True, 60)
