# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
import pytest
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0


async def test_failed_enqueue_does_not_spend_confirmed_on(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on
    gw.send = AsyncMock(side_effect=asyncio.QueueShutDown())
    with pytest.raises(asyncio.QueueShutDown):
        await light.async_turn_off()
    # Nothing sent or changed; same dimmer still reports its captured minimum.
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on, 'failed OFF erased the verified state'
