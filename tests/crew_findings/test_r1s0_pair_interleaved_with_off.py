# ruff: noqa
import asyncio
import time
from homeassistant.components.climate.const import HVACMode
from custom_components.myhome.gateway_sessions import CommandWorkerPool
from OWNd.message import OWNHeatingCommand
from tests.test_gateway_sessions import mock_handler
from tests.test_crew_central_season_restore import _climate, _replay, _entity_base_init, ANTIFREEZE, HEAT, RESTORE

async def test_newer_off_is_not_undone_by_older_restore(mock_handler):
    # Equivalent to a nearly full production scheduler with one free place per dequeue.
    mock_handler.gateway.profile.max_queue_size = 1
    pool = CommandWorkerPool(mock_handler)
    central = _climate(mock_handler, '#0', standalone=None, central=True)
    mock_handler.send = pool.send
    _replay(central, [ANTIFREEZE])
    off = OWNHeatingCommand.set_central_mode(where='#0', mode='off')
    await pool.send(off)  # fill the queue
    heat_task = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await asyncio.sleep(0)
    off_task = asyncio.create_task(central.async_set_hvac_mode(HVACMode.OFF))
    await asyncio.sleep(0)
    sent = []
    for _ in range(20):
        if not pool.send_buffer.empty():
            item = pool.send_buffer.get_nowait()
            sent.append(str(item['message']))
            item['written'].set_result(time.monotonic())
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(heat_task, off_task), 1)
    assert sent[1] == HEAT
    assert sent[-1] == str(off), f'The program command undoes the later OFF request: {sent[1:]}'
