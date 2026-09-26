"""Standalone unit tests for gateway_sessions module (EventSessionRunner, CommandWorkerPool)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.connection import OWNGateway
from OWNd.message import OWNCommand, OWNMessage

from custom_components.myhome.bus_monitor import BusMonitor
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import (
    COMMAND_SESSION_IDLE_TIMEOUT,
    EVENT_STALL_TIMEOUT,
    CommandWorkerPool,
    EventSessionRunner,
    _cancel_written,
    _resolve_written,
    _session_is_open,
)


@pytest.fixture
def mock_handler() -> MagicMock:
    """Create a minimal mock MyHOMEGatewayHandler."""
    handler = MagicMock(spec=MyHOMEGatewayHandler)
    handler.log_id = "[F454:00:03:50:81:22:33]"
    handler.mac = "00:03:50:81:22:33"
    handler.bus_monitor = BusMonitor()
    handler.gateway = MagicMock(spec=OWNGateway)
    handler.gateway.profile = MagicMock()
    handler.gateway.profile.max_queue_size = 250
    handler.command_session_idle_timeout = COMMAND_SESSION_IDLE_TIMEOUT
    handler.hass = MagicMock()
    handler._on_event_connection_state_change = MagicMock()
    handler._process_message = AsyncMock()
    return handler


# ── Delivery Futures & Session Helpers ──────────────────────────────────────────


def test_resolve_and_cancel_written() -> None:
    """Delivery futures are resolved with timestamp or cancelled cleanly."""
    loop = asyncio.new_event_loop()
    try:
        # Resolve
        fut1: asyncio.Future[float] = loop.create_future()
        task1 = {"written": fut1}
        _resolve_written(task1, 123.45)
        assert fut1.done()
        assert fut1.result() == 123.45

        # Cancel
        fut2: asyncio.Future[float] = loop.create_future()
        task2 = {"written": fut2}
        _cancel_written(task2)
        assert fut2.cancelled()

        # Non-future values handled without error
        _resolve_written({}, 100.0)
        _cancel_written({})
    finally:
        loop.close()


def test_session_is_open() -> None:
    """_session_is_open checks presence of stream reader and writer."""
    mock_session = MagicMock()
    mock_session._stream_reader = None
    mock_session._stream_writer = None
    assert not _session_is_open(mock_session)

    mock_session._stream_reader = object()
    mock_session._stream_writer = object()
    assert _session_is_open(mock_session)


# ── EventSessionRunner ─────────────────────────────────────────────────────────


def test_event_session_runner_init_and_properties(mock_handler: MagicMock) -> None:
    """EventSessionRunner exposes required gateway and monitor properties."""
    runner = EventSessionRunner(mock_handler, stall_timeout=300.0)
    assert runner.stall_timeout == 300.0
    assert not runner._terminate_listener
    assert not runner.is_connected
    assert runner.gateway is mock_handler.gateway
    assert runner.bus_monitor is mock_handler.bus_monitor
    assert runner.log_id == mock_handler.log_id
    assert not runner.event_session_ready.is_set()


def test_event_session_runner_close_disarms_watchdog(mock_handler: MagicMock) -> None:
    """Closing runner sets flags and disarms active watchdog timeout."""
    runner = EventSessionRunner(mock_handler)
    mock_watchdog = MagicMock()
    runner._event_watchdog = mock_watchdog

    runner.close()

    assert runner._terminate_listener
    assert runner.event_session_ready.is_set()
    mock_watchdog.reschedule.assert_called_once_with(None)
    assert runner._event_watchdog is None


def test_event_session_runner_update_watchdog(mock_handler: MagicMock) -> None:
    """Watchdog is disarmed when connected and rescheduled on progress/disconnected."""
    runner = EventSessionRunner(mock_handler, stall_timeout=EVENT_STALL_TIMEOUT)
    mock_watchdog = MagicMock()
    mock_watchdog.expired.return_value = False
    mock_watchdog.when.return_value = None
    runner._event_watchdog = mock_watchdog

    # Disconnected + progress -> reschedules
    with patch("asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.time.return_value = 1000.0
        runner.is_connected = False
        runner._update_event_watchdog(progress=True)
        mock_watchdog.reschedule.assert_called_with(1000.0 + EVENT_STALL_TIMEOUT)

    # Connected -> disarms
    runner.is_connected = True
    runner._update_event_watchdog(progress=False)
    mock_watchdog.reschedule.assert_called_with(None)


async def test_event_session_runner_read_session_success_and_messages(mock_handler: MagicMock) -> None:
    """_read_event_session processes messages and records them in the bus monitor."""
    runner = EventSessionRunner(mock_handler)
    mock_session = MagicMock()
    mock_session.connect = AsyncMock(return_value={"Success": True})
    mock_session.is_connected = True

    # 1 valid message, 1 None (reconnect probe), then terminate
    msg = OWNMessage("*#1*0##")
    messages = [msg, None]

    async def get_next_side_effect():
        if messages:
            return messages.pop(0)
        runner._terminate_listener = True
        return None

    mock_session.get_next = AsyncMock(side_effect=get_next_side_effect)
    mock_session._stream_reader = object()
    mock_session._stream_writer = object()

    await runner._read_event_session(mock_session)

    mock_handler._on_event_connection_state_change.assert_any_call(True)
    mock_handler._process_message.assert_called_once_with(msg)
    recent = runner.bus_monitor.get_recent_frames()
    assert len(recent) == 1
    assert recent[0]["raw"] == "*#1*0##"


async def test_event_session_runner_read_session_auth_refused(mock_handler: MagicMock) -> None:
    """Refused event session terminates listener to prevent gateway lockout."""
    runner = EventSessionRunner(mock_handler)
    mock_session = MagicMock()
    mock_session.connect = AsyncMock(return_value={"Success": False, "Message": "password_error"})

    await runner._read_event_session(mock_session)

    mock_handler._on_event_connection_state_change.assert_called_once_with(False)
    mock_session.get_next.assert_not_called()


# ── CommandWorkerPool ──────────────────────────────────────────────────────────


def test_command_worker_pool_init(mock_handler: MagicMock) -> None:
    """CommandWorkerPool initializes buffer queue from profile and properties."""
    ready_evt = asyncio.Event()
    pool = CommandWorkerPool(mock_handler, event_session_ready=ready_evt)
    assert pool.send_buffer.maxsize == 250
    assert not pool._terminate_sender
    assert pool.mac == mock_handler.mac
    assert pool.log_id == mock_handler.log_id
    assert pool.command_session_idle_timeout == COMMAND_SESSION_IDLE_TIMEOUT


async def test_command_worker_pool_send_and_status_request(mock_handler: MagicMock) -> None:
    """Commands and status requests are queued with delivery futures."""
    pool = CommandWorkerPool(mock_handler)
    cmd = OWNCommand("*1*1*11##")
    fut = await pool.send(cmd)
    assert not fut.done()
    assert pool.send_buffer.qsize() == 1
    task = await pool.send_buffer.get()
    assert task["message"] == cmd
    assert not task["is_status_request"]
    assert task["written"] is fut

    # Status request
    req = OWNCommand("*#1*11##")
    fut_req = await pool.send_status_request(req)
    assert not fut_req.done()
    task_req = await pool.send_buffer.get()
    assert task_req["message"] == req
    assert task_req["is_status_request"]


def test_command_worker_pool_connect_refused(mock_handler: MagicMock) -> None:
    """Refused connection reasons are recognized as non-retryable."""
    pool = CommandWorkerPool(mock_handler)
    assert pool._connect_refused({"Success": False, "Message": "connection_refused"}, 1)
    assert pool._connect_refused({"Success": False, "Message": "password_error"}, 1)
    assert not pool._connect_refused({"Success": True}, 1)
    assert not pool._connect_refused(None, 1)


async def test_command_worker_pool_sending_loop_no_terminate_reset(mock_handler: MagicMock) -> None:
    """sending_loop does not reset pool-wide _terminate_sender flag on entry."""
    pool = CommandWorkerPool(mock_handler)
    pool._terminate_sender = True  # Pool was previously closed/terminated

    # Worker starts after terminate has been requested
    await pool.sending_loop(1)

    # Must stay terminated
    assert pool._terminate_sender
