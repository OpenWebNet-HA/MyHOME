# ruff: noqa
# Reproduction r1s0_nacked_central_setpoint of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import asyncio
import pytest
from homeassistant.exceptions import HomeAssistantError
from tests.test_crew_central_season_restore import _climate, _gateway, _replay, _entity_base_init


async def test_nacked_central_setpoint():
    gateway = _gateway()
    central = _climate(gateway, '#0', central=True, standalone=None)
    _replay(central, ['*4*110#0180*#0##'])  # tests/test_crew_central_season_restore.py:116
    assert central.target_temperature == 18.0
    nack = asyncio.get_running_loop().create_future()
    nack._gateway_nack = True
    nack.cancel()
    gateway.send.return_value = nack
    with pytest.raises(HomeAssistantError):
        await central.async_set_temperature(temperature=16.5)
    assert central.target_temperature == 18.0
