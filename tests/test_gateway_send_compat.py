"""The gateway worker must call the real installed OWNd command API."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.connection import OWNCommandSession
from OWNd.message import OWNCommand, OWNMessage

from tests.test_gateway import gateway_handler as gateway_handler_fixture
from tests.test_gateway import mock_config_entry as config_entry_fixture

gateway_handler = gateway_handler_fixture
mock_config_entry = config_entry_fixture


@pytest.mark.parametrize("status_request", [False, True])
async def test_worker_uses_installed_ownd_send(gateway_handler, status_request):
    """Exercise real send/ACK processing, mocking only the socket and handshake."""
    session = OWNCommandSession(gateway=gateway_handler.gateway)
    session._stream_reader = object()
    session._stream_writer = writer = MagicMock(drain=AsyncMock())
    session.connect = AsyncMock(return_value={"Success": True})
    session.close = AsyncMock()
    session._read_command_response = AsyncMock(return_value=(OWNMessage.parse("*#*1##"), []))
    command = OWNCommand.parse("*#2*71##" if status_request else "*2*1*71##")
    written = await gateway_handler._enqueue(command, is_status_request=status_request)
    gateway_handler._event_session_ready.set()
    gateway_handler.send_buffer.put_nowait(None)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        await gateway_handler.sending_loop(0)
    writer.write.assert_called_once_with(str(command).encode())
    writer.drain.assert_awaited_once()
    assert not written.cancelled()
    assert isinstance(written.result(), float)
    session.close.assert_awaited_once()
    await asyncio.wait_for(gateway_handler.send_buffer.join(), 1)


@pytest.mark.parametrize("error", [None, TypeError("internal transport failure")])
async def test_worker_keeps_library_retry_default_without_retrying_typeerror(gateway_handler, error):
    """Optional retry settings stay with OWNd; an internal failure never triggers a second call."""
    calls = []

    class NewSession:
        _stream_reader = object()
        _stream_writer = object()
        connect = AsyncMock(return_value={"Success": True})
        close = AsyncMock()

        async def send(self, message, is_status_request=False, *, retry_after_lost_ack=False):
            calls.append((message, is_status_request, retry_after_lost_ack))
            if error:
                raise error
            return True

    session = NewSession()
    command = OWNCommand.parse("*2*1*71##")
    written = await gateway_handler.send(command)
    gateway_handler._event_session_ready.set()
    gateway_handler.send_buffer.put_nowait(None)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        await gateway_handler.sending_loop(0)
    assert calls == [(command, False, False)]
    assert written.cancelled() is (error is not None)
    if error is None:
        assert isinstance(written.result(), float)
    session.close.assert_awaited_once()
    await asyncio.wait_for(gateway_handler.send_buffer.join(), 1)
