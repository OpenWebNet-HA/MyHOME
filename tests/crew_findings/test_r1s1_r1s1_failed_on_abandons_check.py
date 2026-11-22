# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
import pytest
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed
from tests.test_dimmer_level_0_verified_2026_11_22 import MH200N_ON, MH200N_LEVEL_0

async def test_failed_turn_on_must_not_supersede_a_real_off(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass, where='55')
    closed = asyncio.get_running_loop().create_future()
    gw.send_status_request = AsyncMock(return_value=closed)
    _feed(light, MH200N_ON, MH200N_LEVEL_0)
    await asyncio.sleep(0)
    assert light.is_on
    gw.send = AsyncMock(side_effect=ConnectionError('gateway offline'))
    with pytest.raises(ConnectionError):
        await light.async_turn_on()
    # A level-only reply to the original read. No ON was sent, and no ON reported.
    _feed(light, MH200N_LEVEL_0)
    closed.set_result(0.0)
    await hass.async_block_till_done()
    assert light.is_on is False
