# ruff: noqa
import asyncio
import time
import pytest
from homeassistant.components.climate.const import HVACMode
from tests.test_crew_central_season_restore import (_entity_base_init, _climate, _gateway, _replay, _sent, HEAT, RESTORE, ANTIFREEZE)

async def test_restore_withdrawn_before_socket_write():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    loop = asyncio.get_running_loop()
    def enqueue(command):
        written = loop.create_future()
        if str(command) == HEAT:
            written.set_result(time.monotonic())
        else:
            loop.call_soon(written.cancel)  # worker drops envelope without writing
        return written
    gw.send.side_effect = enqueue
    try:
        await central.async_set_hvac_mode(HVACMode.HEAT)
    except (Exception, asyncio.CancelledError):
        return
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert _sent(gw) == [HEAT, RESTORE] and central._central_restore_echo is None
    pytest.fail('Service reported success although the restore was never written')


from unittest.mock import AsyncMock, MagicMock, patch
from custom_components.myhome.gateway_sessions import CommandWorkerPool
from tests.test_gateway_sessions import mock_handler

async def test_worker_nack_of_restore_reaches_caller(mock_handler):
    ready = asyncio.Event(); ready.set()
    pool = CommandWorkerPool(mock_handler, event_session_ready=ready)
    central = _climate(mock_handler, '#0', standalone=None, central=True)
    mock_handler.send = pool.send
    _replay(central,[ANTIFREEZE])
    session = MagicMock(); session.is_connected=True; session._stream_reader=object(); session._stream_writer=object()
    session.connect = AsyncMock(return_value={'Success':True}); session.close=AsyncMock()
    session.send=AsyncMock(side_effect=[True,None]); session.last_error.reason='nack'
    mock_handler.config_entry = None  # ordinary target, not an expose binding
    with patch('custom_components.myhome.gateway.OWNCommandSession', return_value=session):
        call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
        worker = asyncio.create_task(pool.sending_loop(0))
        failure = None
        try:
            await asyncio.wait_for(call,1)
        except (Exception, asyncio.CancelledError) as exc:
            failure = exc
        await pool.send_buffer.put(None); await asyncio.wait_for(worker,1)
    assert [str(c.kwargs['message']) for c in session.send.await_args_list] == [HEAT,RESTORE]
    assert failure is not None, 'worker marked restore NACK; service nonetheless returned normally'

