# ruff: noqa
import asyncio
import time

from homeassistant.components.climate.const import HVACMode

from custom_components.myhome.gateway_sessions import (
    CommandWorkerPool,
    _cancel_written,
    _resolve_written,
)
from tests.test_crew_central_season_restore import ANTIFREEZE, RESTORE, _climate, _gateway, _replay
from tests.test_crew_central_season_restore import _entity_base_init as _entity_base_init


async def test_unwritten_restore():
    gw = _gateway()
    # Use the real send() queue/future contract with a controlled consumer.
    gw.gateway.profile.max_queue_size = 250
    pool = CommandWorkerPool(gw)

    async def send(cmd):
        return await CommandWorkerPool.send(pool, cmd)

    gw.send.side_effect = send
    cu = _climate(gw, "#0", standalone=None, central=True)
    _replay(cu, [ANTIFREEZE])
    service = asyncio.create_task(cu.async_set_hvac_mode(HVACMode.HEAT))
    first = await asyncio.wait_for(pool.send_buffer.get(), 1)
    _resolve_written(first, time.monotonic())
    second = await asyncio.wait_for(pool.send_buffer.get(), 1)
    assert str(second["message"]) == RESTORE
    _cancel_written(second)
    await asyncio.sleep(0)
    assert cu._central_restore_echo is None
    (result,) = await asyncio.gather(service, return_exceptions=True)
    assert isinstance(result, BaseException), (
        "service reported success for a restore that was never written"
    )
