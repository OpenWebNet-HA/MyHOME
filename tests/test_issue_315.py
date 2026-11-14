"""Tests for Issue #315: F454 event socket keepalive, absence of 120s timeout, and transparent recovery.

Issue #315 report:
  `No heartbeat received for 120 seconds. Event socket likely hung. Severing connection.`
  Commands stop working; user had to reload config entry twice a day.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from OWNd.connection import (
    EVENT_INACTIVITY_TIMEOUT,
    EVENT_KEEPALIVE_FRAME,
    OWNEventSession,
    OWNGateway,
)
from OWNd.message import OWNCommand, OWNLightingCommand
from OWNd.profiles import F454Profile, get_gateway_profile

from custom_components.myhome.const import CONF_DEVICE_TYPE
from custom_components.myhome.gateway import MyHOMEGatewayHandler


@pytest.fixture
def f454_gateway_info():
    """Build info for an F454 gateway."""
    return {
        CONF_HOST: "192.168.1.50",
        CONF_PORT: 20000,
        CONF_PASSWORD: "12345",
        CONF_NAME: "F454",
        CONF_DEVICE_TYPE: "F454",
        CONF_MAC: "00:03:50:AA:BB:CC",
        "address": "192.168.1.50",
        "port": 20000,
        "password": "12345",
        "modelName": "F454",
        "deviceType": "F454",
        "serialNumber": "00:03:50:AA:BB:CC",
    }



def test_f454_profile_has_event_keepalive():
    """Verify F454 profile specifies a 90-second event keepalive and timeout is 3900s."""
    profile = get_gateway_profile("F454")
    assert isinstance(profile, F454Profile)
    assert profile.event_keepalive_interval == 90
    # In modern OWNd, inactivity timeout is 3900s (65 minutes), NOT 120s
    assert EVENT_INACTIVITY_TIMEOUT == 3900
    assert EVENT_KEEPALIVE_FRAME == b"*#*1##"


@pytest.mark.asyncio
async def test_f454_event_session_starts_and_stops_keepalive_task(f454_gateway_info):
    """Verify OWNEventSession launches the 90s keepalive task for F454 upon connect."""
    gw = OWNGateway(f454_gateway_info)
    assert gw.profile.event_keepalive_interval == 90

    session = OWNEventSession(gateway=gw, logger=logging.getLogger("test"))
    assert session._keepalive_interval == 90
    assert session._keepalive_task is None

    # Mock stream connect & negotiation
    mock_reader = AsyncMock()
    mock_writer = MagicMock()
    mock_writer.drain = AsyncMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)), \
         patch.object(session, "_negotiate", return_value={"Success": True}):
        result = await session.connect()
        assert result.get("Success") is True
        assert session._keepalive_task is not None
        assert not session._keepalive_task.done()

        # Closing the session cancels and cleans up the keepalive task
        await session.close()
        assert session._keepalive_task is None


@pytest.mark.asyncio
async def test_f454_event_session_keepalive_sends_periodic_ack(f454_gateway_info):
    """Verify the keepalive loop writes *#*1## on the event socket."""
    gw = OWNGateway(f454_gateway_info)
    session = OWNEventSession(gateway=gw, logger=logging.getLogger("test"))
    session._keepalive_interval = 0.05  # accelerated for test

    mock_writer = MagicMock()
    mock_writer.drain = AsyncMock()
    session._stream_writer = mock_writer

    session._start_keepalive()
    await asyncio.sleep(0.12)
    await session._stop_keepalive()

    # Should have sent *#*1## at least twice
    assert mock_writer.write.call_count >= 2
    mock_writer.write.assert_called_with(EVENT_KEEPALIVE_FRAME)


@pytest.mark.asyncio
async def test_f454_silent_bus_does_not_timeout_at_120s(f454_gateway_info):
    """Verify silence on the event bus does NOT raise TimeoutError at 120s."""
    gw = OWNGateway(f454_gateway_info)
    session = OWNEventSession(gateway=gw, logger=logging.getLogger("test"))

    # Verify inactivity timeout is set above 120s
    assert session._inactivity_timeout == EVENT_INACTIVITY_TIMEOUT
    assert session._inactivity_timeout >= 3600


@pytest.mark.asyncio
async def test_f454_event_socket_drop_and_recovery_without_reload(hass, f454_gateway_info):
    """Verify that if the event socket disconnects, it recovers transparently without reload."""
    entry = MagicMock()
    entry.data = f454_gateway_info
    entry.entry_id = "test_f454_entry"

    handler = MyHOMEGatewayHandler(hass=hass, config_entry=entry)
    assert handler.gateway.model_name == "F454"

    # Initially connect
    handler._on_event_connection_state_change(True)
    assert handler.is_connected is True
    assert handler.available is True
    assert handler._event_session_ready.is_set()

    # 1. Simulate socket disconnect (e.g. gateway dropped event socket)
    handler._on_event_connection_state_change(False)
    assert handler.is_connected is False
    # Gateway remains available during AVAILABILITY_GRACE (60s)
    assert handler.available is True
    assert handler._unavailable_timer is not None
    assert not handler._event_session_ready.is_set()

    # 2. Simulate successful auto-reconnect before grace period expires
    handler._on_event_connection_state_change(True)
    assert handler.is_connected is True
    assert handler.available is True
    assert handler._unavailable_timer is None
    assert handler._event_session_ready.is_set()

    # 3. Clean up
    await handler.close_listener()


@pytest.mark.asyncio
async def test_commands_continue_after_event_reconnect(hass, f454_gateway_info):
    """Verify commands continue to be sent after an event socket reconnect cycle."""
    entry = MagicMock()
    entry.data = f454_gateway_info
    entry.entry_id = "test_f454_entry"

    handler = MyHOMEGatewayHandler(hass=hass, config_entry=entry)

    with patch("custom_components.myhome.gateway.OWNCommandSession") as mock_cmd_class:
        mock_cmd = MagicMock()
        mock_cmd.connect = AsyncMock(return_value={"Success": True})
        mock_cmd.close = AsyncMock()
        mock_cmd.is_connected = True
        mock_cmd.send = AsyncMock(return_value=True)
        mock_cmd_class.return_value = mock_cmd

        handler._event_session_ready.set()
        worker = asyncio.create_task(handler.sending_loop(0))

        # Command 1 sent successfully
        cmd1 = OWNLightingCommand.status("12")
        await handler.send_status_request(cmd1)
        await asyncio.sleep(0.05)
        mock_cmd.send.assert_called_with(
            message=cmd1,
            is_status_request=True,
        )

        # Event connection flaps (disconnect -> reconnect)
        handler._on_event_connection_state_change(False)
        handler._on_event_connection_state_change(True)

        # Command 2 sent successfully without restarting worker or reloading entry
        mock_cmd.send.reset_mock()
        cmd2 = OWNCommand.parse("*1*1*12##")
        await handler.send(cmd2)
        await asyncio.sleep(0.05)
        mock_cmd.send.assert_called_with(
            message=cmd2,
            is_status_request=False,
        )

        # Clean shutdown
        await handler.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=1)
        await handler.close_listener()
