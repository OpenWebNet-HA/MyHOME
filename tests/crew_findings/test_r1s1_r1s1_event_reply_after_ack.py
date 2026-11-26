# ruff: noqa
import asyncio
from unittest.mock import AsyncMock
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0, PLAIN_ON
from tests.test_dimmer_level_0_verified_2026_11_22 import ON_AT_8, LEVEL_60, STATUS_REQUEST

async def test_event_only_on_just_after_ack_grace(hass):
    # Hypothetical timing, no claim any particular model always does this.
    # A command-session ACK precedes the status reply on the event connection.
    light, gw = _light(hass)
    _feed(light, ON_AT_8, LEVEL_60, PLAIN_ON)
    states = []
    async def read(command):
        done = asyncio.get_running_loop().create_future()
        done.set_result(0.0)
        if str(command) == STATUS_REQUEST:
            async def eventual_status():
                await asyncio.sleep(0.65)
                states.append(light.is_on)
                _feed(light, PLAIN_ON)
            hass.async_create_task(eventual_status())
        return done
    gw.send_status_request = AsyncMock(side_effect=read)
    _feed(light, LEVEL_0)
    assert light.is_on and light._attr_brightness_pct == 1
    await hass.async_block_till_done()
    assert (states, light.is_on, light._attr_brightness_pct) == ([True],True,1)
