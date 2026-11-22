# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
from custom_components.myhome import light as mod
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed
from tests.test_dimmer_level_0_verified_2026_11_22 import MH200N_ON, MH200N_LEVEL_0


async def test_send_when_minimum_is_only_provisional(hass, monkeypatch):
    monkeypatch.setattr(mod, '_LEVEL_ZERO_CHECK_GRACE_S', 0)
    light, gw = _light(hass, where='55')
    gw.config_entry.options = {'transition_mode': 'software_stepped'}
    closed = asyncio.get_running_loop().create_future()
    gw.send_status_request = AsyncMock(return_value=closed)
    _feed(light, MH200N_ON, MH200N_LEVEL_0)
    assert light.is_on and light._attr_brightness_pct == 1
    # User asks for 1% while verification of an actual ramp-off is outstanding.
    await light.async_turn_on(brightness_pct=1, transition=1)
    closed.set_result(0.0)
    await hass.async_block_till_done()
    assert gw.send.await_count > 0, 'provisional display is not grounds to skip requested switch-on'
