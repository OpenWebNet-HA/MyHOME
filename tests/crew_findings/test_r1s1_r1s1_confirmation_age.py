# ruff: noqa
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, LEVEL_0, ON_AT_2
from tests.test_dimmer_level_0_verified_2026_11_22 import STATUS_REQUEST

async def test_background_interval_starts_with_the_confirmation(hass, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(mod, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass)
    reply = asyncio.get_running_loop().create_future()
    gw.send_status_request = AsyncMock(return_value=reply)
    _feed(light, ON_AT_2, LEVEL_0)
    await asyncio.sleep(0)
    now[0] += 16
    reply.set_result(now[0])
    _feed(light, ON_AT_2)
    await hass.async_block_till_done()
    assert light.is_on and light._attr_brightness_pct == 1
    now[0] += 15
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    requests = [str(call.args[0]) for call in gw.send_status_request.await_args_list]
    assert requests.count(STATUS_REQUEST) == 1, requests
    assert light.is_on
