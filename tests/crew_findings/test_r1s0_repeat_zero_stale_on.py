# ruff: noqa
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _feed, _light
from tests.test_dimmer_level_0_verified_2026_11_22 import MH200N_ON, MH200N_LEVEL_0


async def test_new_zero_uses_its_own_age(hass, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mod, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    light, gateway = _light(hass, where='55')
    closed = asyncio.get_running_loop().create_future()
    gateway.send_status_request = AsyncMock(return_value=closed)
    _feed(light, MH200N_ON, MH200N_LEVEL_0)
    assert light.is_on
    now[0] += 5
    try:
        _feed(light, MH200N_LEVEL_0)
        assert gateway.send_status_request.await_count == 1
        assert light.is_on is False, 'second zero is not within 2 s of the ON status'
    finally:
        closed.cancel()
        await hass.async_block_till_done()
