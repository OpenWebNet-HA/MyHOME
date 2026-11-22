# ruff: noqa
"""A physical thermostat can identify itself during the same startup-lock wait as an exposure."""
import asyncio
from unittest.mock import AsyncMock
from tests.test_thermo_discovery_at_start import _plant, _receive, _after_the_sweep
from tests.test_who4_commit4_review2_2026_11_22 import ACTUATOR_1_OFF

async def test_already_discovered_thermostat_not_read_by_actuator_only_scan(hass):
    read = AsyncMock(return_value=True)
    gw, zones = await _plant(hass, read)
    async with gw._who4_startup_poll_lock:
        await _receive(hass, gw, [ACTUATOR_1_OFF])
        gw._initial_discovery_done.set()
        for _ in range(30):
            await asyncio.sleep(0)
            if gw._who4_startup_poll_lock._waiters:
                break
        assert gw._who4_startup_poll_lock._waiters
        await _receive(hass, gw, ['*#4*3*0*0260##'])  # tests/fixtures/traces/thermo_discovery_at_start_2026_11_18/README.md:38
        assert [(zone._where,zone.current_temperature) for zone in zones] == [('3',26.0)]
    await _after_the_sweep(hass, gw)
    assert not read.await_args_list, f'already discovered before lock release: {read.await_args_list}'
