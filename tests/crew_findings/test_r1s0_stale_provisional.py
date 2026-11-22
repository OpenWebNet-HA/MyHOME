# ruff: noqa
import asyncio
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0
from tests.test_dimmer_level_0_verified_2026_11_22 import ON_AT_8

async def test_new_level_after_settling_status(hass):
    light, gateway = _light(hass)
    _feed(light, ON_AT_2, LEVEL_0)
    # Higher preset settles the first check, then a fresh ramp-to-off report
    # arrives before its grace timer has expired. No further bus traffic.
    _feed(light, ON_AT_8, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False, 'latest level was neither checked nor settled; still on at 1%'
