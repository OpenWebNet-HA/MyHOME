# ruff: noqa
"""A scheduled write can fail after gateway.send has returned its receipt."""
import asyncio
import time
import pytest
from homeassistant.components.climate.const import HVACMode
from tests.test_crew_central_season_restore import _climate, _entity_base_init, _gateway, _replay, ANTIFREEZE, HEAT, RESTORE

async def test_unwritten_restore_fails_service():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    async def send(cmd):
        receipt = asyncio.get_running_loop().create_future()
        if str(cmd) == HEAT:
            receipt.set_result(time.monotonic())
        else:
            assert str(cmd) == RESTORE
            # Same as an unavailable command session: gateway_sessions.py:1540-1547
            asyncio.get_running_loop().call_soon(receipt.cancel)
        return receipt
    gw.send.side_effect = send
    with pytest.raises((Exception, asyncio.CancelledError)):
        await central.async_set_hvac_mode(HVACMode.HEAT)
    assert central._central_restore_echo is None
