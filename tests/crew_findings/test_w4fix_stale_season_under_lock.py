# ruff: noqa
# Reproduction r1s0_stale_season_under_lock of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import asyncio
import time
from homeassistant.components.climate.const import HVACMode
from tests.test_central_season_pair_delivery import _pending_sends, _until
from tests.test_crew_central_season_restore import (ANTIFREEZE, THERMAL_PROTECTION, HEATING_PROGRAM, _climate, _gateway, _replay, _sent, _entity_base_init)


async def test_stale_season_under_lock():
    gateway = _gateway()
    central = _climate(gateway, '#0', central=True, standalone=None)
    _replay(central, [THERMAL_PROTECTION])
    futures = await _pending_sends(gateway)
    heat = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await _until(lambda: len(futures) == 1)
    slider = asyncio.create_task(central.async_set_temperature(temperature=16.5))
    await asyncio.sleep(0)
    _replay(central, [ANTIFREEZE])
    futures[0].set_result(time.monotonic())
    await _until(lambda: len(futures) == 2)
    _replay(central, [HEATING_PROGRAM])
    futures[1].set_result(time.monotonic())
    await _until(lambda: len(futures) == 3)
    futures[2].set_result(time.monotonic())
    await asyncio.gather(heat, slider)
    assert _sent(gateway)[-1] == '*#4*#0*#14*0165*1##'  # issue_697/f454_99zone_central_subordinate_zones.json:38
