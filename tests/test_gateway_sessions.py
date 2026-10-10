"""Standalone unit tests for gateway_sessions module (EventSessionRunner, CommandWorkerPool)."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.connection import OWNGateway
from OWNd.message import OWNCommand, OWNMessage

from custom_components.myhome.bus_monitor import BusMonitor
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import (
    COMMAND_SESSION_IDLE_TIMEOUT,
    EVENT_STALL_TIMEOUT,
    CommandPriorityQueue,
    CommandWorkerPool,
    EventSessionRunner,
    _cancel_written,
    _PriorityQueueStorage,
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
    handler.gateway.profile.command_queue_delay = 0
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
    mock_watchdog.expired.return_value = False
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


# ── CommandPriorityQueue & Storage ───────────────────────────────────────────


async def test_command_priority_queue_ordering() -> None:
    """User commands are dequeued strictly ahead of status polls, preserving FIFO within tiers."""
    q: CommandPriorityQueue = CommandPriorityQueue()

    s1 = {"message": "S1", "is_status_request": True}
    s2 = {"message": "S2", "is_status_request": True}
    c1 = {"message": "C1", "is_status_request": False}
    s3 = {"message": "S3", "is_status_request": True}
    c2 = {"message": "C2", "is_status_request": False}

    for item in [s1, s2, c1, s3, c2]:
        q.put_nowait(item)

    assert q.qsize() == 5
    assert q.command_qsize == 2
    assert q.status_qsize == 3

    # Inspection of internal sequence matches priority ordering:
    assert [item["message"] for item in list(q._queue)] == ["C1", "C2", "S1", "S2", "S3"]

    # Dequeue order strictly yields commands first (FIFO), then status polls (FIFO)
    assert (await q.get())["message"] == "C1"
    assert q.command_qsize == 1
    assert q.status_qsize == 3

    assert (await q.get())["message"] == "C2"
    assert q.command_qsize == 0
    assert q.status_qsize == 3

    assert (await q.get())["message"] == "S1"
    assert q.command_qsize == 0
    assert q.status_qsize == 2

    assert (await q.get())["message"] == "S2"
    assert (await q.get())["message"] == "S3"
    assert q.empty()
    assert q.command_qsize == 0
    assert q.status_qsize == 0


async def test_command_priority_queue_read_back_after_write() -> None:
    """Read-back status query queued after a command cannot overtake the command."""
    q: CommandPriorityQueue = CommandPriorityQueue()

    c1 = {"message": "*1*1*21##", "is_status_request": False}
    s1 = {"message": "*#1*21##", "is_status_request": True}

    await q.put(c1)
    await q.put(s1)

    first = await q.get()
    second = await q.get()

    assert first["message"] == "*1*1*21##"
    assert first["is_status_request"] is False
    assert second["message"] == "*#1*21##"
    assert second["is_status_request"] is True


async def test_command_priority_queue_sentinels() -> None:
    """Worker shutdown sentinels (None) are dequeued only after draining all commands and status polls."""
    q: CommandPriorityQueue = CommandPriorityQueue()

    s1 = {"message": "S1", "is_status_request": True}
    c1 = {"message": "C1", "is_status_request": False}
    sentinel = None
    c2 = {"message": "C2", "is_status_request": False}

    await q.put(s1)
    await q.put(c1)
    await q.put(sentinel)
    await q.put(c2)

    assert [item if item is None else item["message"] for item in list(q._queue)] == [
        "C1",
        "C2",
        "S1",
        None,
    ]

    assert (await q.get())["message"] == "C1"
    assert (await q.get())["message"] == "C2"
    assert (await q.get())["message"] == "S1"
    assert await q.get() is None
    assert q.empty()


async def test_command_priority_queue_full_backpressure() -> None:
    """When full, waiting command putters take precedence over waiting status putters."""
    q: CommandPriorityQueue = CommandPriorityQueue(maxsize=1)

    s0 = {"message": "S0", "is_status_request": True}
    s1 = {"message": "S1", "is_status_request": True}
    c1 = {"message": "C1", "is_status_request": False}
    c2 = {"message": "C2", "is_status_request": False}

    # Queue full
    await q.put(s0)
    assert q.full()

    admitted: list[str] = []

    async def put_item(item: dict[str, Any]) -> None:
        await q.put(item)
        admitted.append(item["message"])

    # Launch background puts: status first, then two commands
    t_s1 = asyncio.create_task(put_item(s1))
    await asyncio.sleep(0)

    t_c1 = asyncio.create_task(put_item(c1))
    await asyncio.sleep(0)

    t_c2 = asyncio.create_task(put_item(c2))
    await asyncio.sleep(0)

    # Pop s0; c1 should be admitted next (ahead of s1)
    item0 = await q.get()
    assert item0["message"] == "S0"
    await asyncio.sleep(0)
    assert admitted == ["C1"]

    # Pop c1; c2 should be admitted next (ahead of s1)
    item1 = await q.get()
    assert item1["message"] == "C1"
    await asyncio.sleep(0)
    assert admitted == ["C1", "C2"]

    # Pop c2; s1 should be admitted next
    item2 = await q.get()
    assert item2["message"] == "C2"
    await asyncio.sleep(0)
    assert admitted == ["C1", "C2", "S1"]

    # Pop s1; queue is now empty
    item3 = await q.get()
    assert item3["message"] == "S1"
    assert q.empty()

    await asyncio.gather(t_s1, t_c1, t_c2)


async def test_command_priority_queue_backpressure_cancellation() -> None:
    """Cancelled putters clean up cleanly and wake the next waiter in line."""
    q: CommandPriorityQueue = CommandPriorityQueue(maxsize=1)

    s0 = {"message": "S0", "is_status_request": True}
    c1 = {"message": "C1", "is_status_request": False}
    c2 = {"message": "C2", "is_status_request": False}

    await q.put(s0)

    t_c1 = asyncio.create_task(q.put(c1))
    t_c2 = asyncio.create_task(q.put(c2))
    await asyncio.sleep(0)

    # Cancel c1 while waiting
    t_c1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_c1

    # Pop s0; c2 should wake up
    assert (await q.get())["message"] == "S0"
    await asyncio.sleep(0)
    assert t_c2.done()
    assert (await q.get())["message"] == "C2"
    assert q.empty()


def test_command_priority_queue_storage_methods() -> None:
    """Test sequence, mapping, and mutation methods of _PriorityQueueStorage."""
    storage = _PriorityQueueStorage()
    assert len(storage) == 0
    assert not storage

    c1 = {"message": "C1", "is_status_request": False}
    c2 = {"message": "C2", "is_status_request": False}
    s1 = {"message": "S1", "is_status_request": True}
    sentinel = None

    storage.append(s1)
    storage.append(c1)
    storage.append(sentinel)
    storage.append(c2)

    assert len(storage) == 4
    assert bool(storage)
    assert repr(storage).startswith("_PriorityQueueStorage(")

    # Membership and count
    assert c1 in storage
    assert s1 in storage
    assert sentinel in storage
    assert "nonexistent" not in storage
    assert storage.count(c1) == 1
    assert storage.count("missing") == 0

    # Indexing & slicing
    assert storage[0] == c1
    assert storage[1] == c2
    assert storage[2] == s1
    assert storage[3] is None
    assert storage[-1] is None
    assert storage[-4] == c1

    with pytest.raises(IndexError):
        _ = storage[4]
    with pytest.raises(IndexError):
        _ = storage[-5]
    with pytest.raises(TypeError):
        _ = storage["invalid"]  # type: ignore[index]

    assert storage[1:3] == [c2, s1]
    assert list(reversed(storage)) == [None, s1, c2, c1]

    # Copy
    copied = storage.copy()
    assert len(copied) == 4
    assert list(copied) == list(storage)

    # Remove
    storage.remove(c1)
    assert len(storage) == 3
    assert storage[0] == c2

    storage.remove(s1)
    assert len(storage) == 2

    storage.remove(None)
    assert len(storage) == 1
    assert storage[0] == c2

    with pytest.raises(ValueError):
        storage.remove("not_present")

    # Pop tail
    assert storage.pop() == c2
    assert len(storage) == 0

    # Pop on empty raises IndexError
    with pytest.raises(IndexError):
        storage.popleft()
    with pytest.raises(IndexError):
        storage.pop()

    # Clear
    copied.clear()
    assert len(copied) == 0


async def test_worker_sends_user_command_ahead_of_pending_status_polls(
    mock_handler: MagicMock,
) -> None:
    """Worker sends high-priority user commands before preceding queued status polls."""
    pool = CommandWorkerPool(mock_handler)
    pool._event_session_ready.set()

    mock_session = MagicMock()
    mock_session.connect = AsyncMock(return_value={"Success": True})
    mock_session.is_connected = True
    mock_session.close = AsyncMock()
    mock_session._stream_reader = object()
    mock_session._stream_writer = object()

    sent_frames: list[str] = []

    async def mock_send(message: Any, is_status_request: bool = False) -> list[Any]:
        sent_frames.append(str(message))
        return []

    mock_session.send = AsyncMock(side_effect=mock_send)

    with patch(
        "custom_components.myhome.gateway.OWNCommandSession",
        return_value=mock_session,
    ):
        # Queue 5 status requests
        for i in range(1, 6):
            await pool.send_status_request(OWNCommand(f"*#1*{i}##"))

        # Queue 1 user command
        await pool.send(OWNCommand("*1*1*21##"))

        # Put sentinel to stop worker after draining
        await pool.send_buffer.put(None)

        # Run sending loop
        await pool.sending_loop(0)

    # Assert user command was sent FIRST before any of the status queries
    assert sent_frames[0] == "*1*1*21##"
    assert sent_frames[1:] == [
        "*#1*1##",
        "*#1*2##",
        "*#1*3##",
        "*#1*4##",
        "*#1*5##",
    ]
