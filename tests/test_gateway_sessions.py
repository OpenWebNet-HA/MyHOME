"""Standalone unit tests for gateway_sessions module (EventSessionRunner, CommandWorkerPool)."""
from __future__ import annotations

import asyncio
import collections
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
    _is_cover_stop,
    _PriorityQueueStorage,
    _resolve_written,
    _session_is_open,
    _wheres_overlap,
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


async def test_event_session_runner_retry_loop_and_backoff(mock_handler: MagicMock) -> None:
    """_listening_loop retries _run_event_session with backoff when it returns True."""
    runner = EventSessionRunner(mock_handler)
    calls = 0

    async def mock_run() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return True
        return False

    runner._run_event_session = AsyncMock(side_effect=mock_run)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await runner.listening_loop()
        assert calls == 2
        mock_sleep.assert_called_once()


async def test_event_session_runner_run_event_session_terminated_or_exceptions(
    mock_handler: MagicMock,
) -> None:
    """_run_event_session handles termination, TimeoutError stall, and generic exceptions."""
    runner = EventSessionRunner(mock_handler)

    # Terminated early return
    runner._terminate_listener = True
    assert await runner._run_event_session() is False
    runner._terminate_listener = False

    # Watchdog TimeoutError stall
    with patch.object(runner, "_read_event_session", side_effect=TimeoutError("stall")):
        with patch("asyncio.timeout") as mock_timeout:
            mock_ctx = MagicMock()
            mock_ctx.expired.return_value = True
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_ctx.__aexit__ = AsyncMock(side_effect=TimeoutError("stall"))
            mock_timeout.return_value = mock_ctx
            assert await runner._run_event_session() is True

    # Generic exception
    with patch.object(runner, "_read_event_session", side_effect=RuntimeError("boom")):
        assert await runner._run_event_session() is True


async def test_event_session_runner_read_session_reachable_change_and_process_error(
    mock_handler: MagicMock,
) -> None:
    """_read_event_session logs reachable transitions and survives _process_message exceptions."""
    runner = EventSessionRunner(mock_handler)
    mock_session = MagicMock()
    mock_session.connect = AsyncMock(return_value={"Success": True})
    mock_session.is_connected = True
    mock_session._stream_reader = object()
    mock_session._stream_writer = object()

    msg = OWNMessage("*1*1*11##")
    mock_handler._process_message.side_effect = ValueError("bad handler")

    step = 0

    async def get_next_side_effect() -> OWNMessage | None:
        nonlocal step
        step += 1
        if step == 1:
            return msg
        if step == 2:
            mock_session.is_connected = False
            mock_session._stream_reader = None
            return None
        if step == 3:
            mock_session.is_connected = True
            mock_session._stream_reader = object()
            return None
        runner._terminate_listener = True
        return None

    mock_session.get_next = AsyncMock(side_effect=get_next_side_effect)

    await runner._read_event_session(mock_session)
    assert step == 4


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
    """Worker shutdown sentinels (None and custom objects) are dequeued only after draining all commands and polls."""
    q: CommandPriorityQueue = CommandPriorityQueue()

    s1 = {"message": "S1", "is_status_request": True}
    c1 = {"message": "C1", "is_status_request": False}
    sentinel = None
    custom_sentinel = "WORKER_TERMINATE"
    c2 = {"message": "C2", "is_status_request": False}

    await q.put(s1)
    await q.put(c1)
    await q.put(sentinel)
    await q.put(custom_sentinel)
    await q.put(c2)

    assert q.command_qsize == 2
    assert q.status_qsize == 1
    assert q.other_qsize == 2
    assert q.qsize() == 5

    assert [item if item in (None, custom_sentinel) else item["message"] for item in list(q._queue)] == [
        "C1",
        "C2",
        "S1",
        None,
        custom_sentinel,
    ]

    assert (await q.get())["message"] == "C1"
    assert (await q.get())["message"] == "C2"
    assert (await q.get())["message"] == "S1"
    assert await q.get() is None
    assert await q.get() == "WORKER_TERMINATE"
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

    assert len(q._putters) == 2

    # Cancel c1 while waiting: it must be cleanly removed from _putters
    t_c1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t_c1

    assert len(q._putters) == 1

    # Pop s0; c2 should wake up
    assert (await q.get())["message"] == "S0"
    await asyncio.sleep(0)
    assert t_c2.done()
    assert (await q.get())["message"] == "C2"
    assert q.empty()
    assert len(q._putters) == 0


async def test_command_priority_queue_woken_putter_cancellation_unblocks_next() -> None:
    """A putter cancelled after being woken up by get() does not lose the slot for subsequent waiters."""
    q: CommandPriorityQueue = CommandPriorityQueue(maxsize=1)

    await q.put({"message": "S0", "is_status_request": True})

    t1 = asyncio.create_task(q.put({"message": "C1", "is_status_request": False}))
    t2 = asyncio.create_task(q.put({"message": "C2", "is_status_request": False}))
    await asyncio.sleep(0)

    # Consumer pops S0, which unblocks t1
    val = q.get_nowait()
    assert val["message"] == "S0"

    # t1 is cancelled immediately upon waking
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1

    # t2 must NOT deadlock; it must be unblocked and successfully admit C2
    done, _ = await asyncio.wait([t2], timeout=0.5)
    assert t2 in done
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
    custom = "custom_obj"

    storage.append(s1)
    storage.append(c1)
    storage.append(sentinel)
    storage.append(c2)
    storage.append(custom)

    assert len(storage) == 5
    assert bool(storage)
    assert repr(storage).startswith("_PriorityQueueStorage(")

    # Membership and count
    assert c1 in storage
    assert s1 in storage
    assert sentinel in storage
    assert custom in storage
    assert "nonexistent" not in storage
    assert storage.count(c1) == 1
    assert storage.count("missing") == 0

    # Indexing & slicing
    assert storage[0] == c1
    assert storage[1] == c2
    assert storage[2] == s1
    assert storage[3] is None
    assert storage[4] == custom
    assert storage[-1] == custom
    assert storage[-5] == c1

    with pytest.raises(IndexError):
        _ = storage[5]
    with pytest.raises(IndexError):
        _ = storage[-6]
    with pytest.raises(TypeError):
        _ = storage["invalid"]  # type: ignore[index]

    assert storage[1:3] == [c2, s1]
    assert list(reversed(storage)) == [custom, None, s1, c2, c1]
    assert storage.index(c2) == 1
    assert storage.index(c2, 0, 3) == 1
    assert storage.index(custom) == 4

    # Copy and equality
    copied = storage.copy()
    assert len(copied) == 5
    assert list(copied) == list(storage)
    assert copied == storage
    assert storage == list(storage)
    assert storage == collections.deque(list(storage))
    assert storage != "not_a_queue"
    assert storage != [c1]

    # Appendleft across all tiers
    c0 = {"message": "C0", "is_status_request": False}
    s0 = {"message": "S0", "is_status_request": True}
    o0 = "other_0"
    copied.appendleft(c0)
    assert copied[0] == c0
    copied.appendleft(s0)
    assert s0 in copied
    copied.appendleft(o0)
    assert o0 in copied

    # Extend and extendleft
    c3 = {"message": "C3", "is_status_request": False}
    s3 = {"message": "S3", "is_status_request": True}
    o3 = "other_3"
    copied.extend([c3])
    assert c3 in copied
    copied.extendleft([c3, s3, o3])
    assert s3 in copied
    assert o3 in copied

    # Remove
    storage.remove(c1)
    assert len(storage) == 4
    assert storage[0] == c2

    storage.remove(s1)
    assert len(storage) == 3

    storage.remove(None)
    assert len(storage) == 2
    assert storage[0] == c2

    storage.remove(custom)
    assert len(storage) == 1

    with pytest.raises(ValueError):
        storage.remove("not_present")

    # Pop tail across all tiers: _others, _status, and _commands
    pop_storage = _PriorityQueueStorage()
    pop_storage.append({"message": "CMD", "is_status_request": False})
    pop_storage.append({"message": "STAT", "is_status_request": True})
    pop_storage.append("OTHER")

    assert pop_storage.pop() == "OTHER"  # pops from _others
    assert pop_storage.pop()["message"] == "STAT"  # pops from _status
    assert pop_storage.pop()["message"] == "CMD"  # pops from _commands

    # Pop on empty raises IndexError
    with pytest.raises(IndexError):
        pop_storage.popleft()
    with pytest.raises(IndexError):
        pop_storage.pop()

    # Clear
    copied.clear()
    assert len(copied) == 0


async def test_command_priority_queue_shutdown_and_wakeup_skip() -> None:
    """Queue shutdown raises exception and _wakeup_next skips completed waiters."""
    q: CommandPriorityQueue = CommandPriorityQueue(maxsize=1)
    await q.put({"message": "S1", "is_status_request": True})

    # Test shutdown
    setattr(q, "_is_shutdown", True)
    shutdown_exc = getattr(asyncio, "QueueShutDown", Exception)
    with pytest.raises(shutdown_exc):
        await q.put({"message": "C1", "is_status_request": False})
    setattr(q, "_is_shutdown", False)

    # Test _wakeup_next skipping completed/cancelled waiters
    waiter1: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    waiter1.cancel()  # already done/cancelled
    waiter2: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    waiters: collections.deque[asyncio.Future[Any]] = collections.deque([waiter1, waiter2])
    q._wakeup_next(waiters)

    assert waiter1.cancelled()
    assert waiter2.done()
    assert waiter2.result() is None


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


# ── Cover stop priority (#645) ─────────────────────────────────────────────────


def _cmd(raw: str) -> dict[str, Any]:
    return {"message": OWNCommand(raw), "is_status_request": False}


def _order(storage: _PriorityQueueStorage) -> list[str]:
    return [str(item["message"]) for item in storage]


@pytest.mark.parametrize(
    ("raw", "is_stop"),
    [
        ("*2*0*11##", True),
        ("*2*10*11##", True),
        ("*2*10#001#1*0##", True),
        ("*2*0*11#4#02##", True),
        ("*2*1*11##", False),
        ("*2*2*11##", False),
        ("*#2*11##", False),
        ("*#2*11*#10*50##", False),
        ("*1*0*11##", False),
        ("*2*0##", False),
    ],
)
def test_is_cover_stop(raw: str, is_stop: bool) -> None:
    assert _is_cover_stop(raw) is is_stop


@pytest.mark.parametrize(
    ("first", "second", "overlap"),
    [
        ("11", "11", True),
        ("11", "12", False),
        ("11#4#02", "11", False),
        ("11#4#02", "11#4#02", True),
        ("11#4#2", "11#4#02", True),
        ("02", "2", True),
        ("02#4#1", "2#4#01", True),
        ("0", "11", True),
        ("0", "11#4#02", True),  # main-bus general may reach covers behind an interface: be safe
        ("1", "11", True),
        ("1", "21", False),
        ("1", "11#4#02", False),
        ("00", "0011", True),
        ("#3", "21", True),
        ("#3", "21#4#02", True),  # group membership is unknown: be safe
        ("0#4#01", "11#4#02", False),
        ("#3#4#01", "21#4#02", False),
    ],
)
def test_wheres_overlap(first: str, second: str, overlap: bool) -> None:
    assert _wheres_overlap(first, second) is overlap
    assert _wheres_overlap(second, first) is overlap


def test_stop_overtakes_other_covers_frames() -> None:
    storage = _PriorityQueueStorage()
    for raw in ("*2*1*21##", "*2*1*31##", "*1*1*11##"):
        storage.append(_cmd(raw))
    storage.append(_cmd("*2*0*11##"))
    assert _order(storage)[0] == "*2*0*11##"


def test_stop_never_overtakes_its_own_cover_frames() -> None:
    storage = _PriorityQueueStorage()
    for raw in ("*2*1*21##", "*2*1*11##", "*2*1*31##"):
        storage.append(_cmd(raw))
    storage.append(_cmd("*2*0*11##"))
    assert _order(storage) == ["*2*1*21##", "*2*1*11##", "*2*0*11##", "*2*1*31##"]


@pytest.mark.parametrize("scope_frame", ["*2*1*0##", "*2*1*1##", "*2*1*#3##"])
def test_stop_never_overtakes_scope_frames_covering_it(scope_frame: str) -> None:
    storage = _PriorityQueueStorage()
    for raw in ("*2*1*21##", scope_frame, "*2*1*41##"):
        storage.append(_cmd(raw))
    storage.append(_cmd("*2*0*11##"))
    assert _order(storage).index("*2*0*11##") == _order(storage).index(scope_frame) + 1


def test_scope_stop_waits_behind_member_frames() -> None:
    storage = _PriorityQueueStorage()
    for raw in ("*2*1*21##", "*2*1*11##", "*2*1*31##"):
        storage.append(_cmd(raw))
    storage.append(_cmd("*2*0*1##"))
    assert _order(storage) == ["*2*1*21##", "*2*1*11##", "*2*0*1##", "*2*1*31##"]


def test_stop_stays_behind_earlier_stop_of_same_cover() -> None:
    storage = _PriorityQueueStorage()
    storage.append(_cmd("*2*1*21##"))
    storage.append(_cmd("*2*0*11##"))
    storage.append(_cmd("*2*0*11##"))
    assert _order(storage) == ["*2*0*11##", "*2*0*11##", "*2*1*21##"]


async def test_stop_overtakes_in_real_queue_and_status_stays_behind() -> None:
    queue: CommandPriorityQueue = CommandPriorityQueue(maxsize=10)
    await queue.put({"message": OWNCommand("*#2*11##"), "is_status_request": True})
    await queue.put(_cmd("*2*1*21##"))
    await queue.put(_cmd("*2*0*11##"))
    first = await queue.get()
    assert str(first["message"]) == "*2*0*11##"


async def test_workers_keep_overlapping_cover_frames_in_order(mock_handler: MagicMock) -> None:
    """Two workers: a stop dequeued while its direction frame is still being written waits."""
    pool = CommandWorkerPool(mock_handler)
    pool._event_session_ready.set()
    events: list[str] = []
    release_direction = asyncio.Event()

    def _session() -> MagicMock:
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.close = AsyncMock()
        session._stream_reader = object()
        session._stream_writer = object()

        async def send(message: Any, is_status_request: bool = False) -> list[Any]:
            raw = str(message)
            events.append(f"start {raw}")
            if raw == "*2*1*11##":
                await release_direction.wait()
            events.append(f"end {raw}")
            return []

        session.send = AsyncMock(side_effect=send)
        return session

    with patch("custom_components.myhome.gateway.OWNCommandSession", side_effect=lambda **_: _session()):
        await pool.send(OWNCommand("*2*1*11##"))
        workers = [asyncio.create_task(pool.sending_loop(i)) for i in range(2)]
        await asyncio.sleep(0.05)
        await pool.send(OWNCommand("*2*0*11##"))  # other cover: independent
        await pool.send(OWNCommand("*2*0*21##"))
        await asyncio.sleep(0.05)
        assert "start *2*0*21##" in events
        assert "start *2*0*11##" not in events  # waits for the direction frame to finish
        release_direction.set()
        await asyncio.sleep(0.05)
        assert events.index("end *2*1*11##") < events.index("start *2*0*11##")
        pool.close()
        await asyncio.gather(*workers)
    assert pool._cover_in_flight == []


def test_cover_stop_priority_with_firmware_oracle_who2_frames() -> None:
    """Verify _is_cover_stop and _PriorityQueueStorage ordering across WHO 2 firmware oracle frames."""
    import json
    from pathlib import Path

    oracle_path = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
    verdicts = json.loads(oracle_path.read_text(encoding="utf-8"))["verdicts"]

    # Every WHO 2 command in firmware_oracle.json must classify cleanly
    who2_frames = sorted(
        f for f in verdicts if f.startswith("*2*") or f.startswith("*#2*")
    )
    assert len(who2_frames) >= 16

    for frame in who2_frames:
        if frame.startswith("*2*0*") or frame.startswith("*2*10*") or frame.startswith("*2*10#"):
            assert _is_cover_stop(frame) is True, f"Expected stop for {frame}"
        else:
            assert _is_cover_stop(frame) is False, f"Expected non-stop for {frame}"

    # Verify stop (*2*0*31##) stays behind advanced step (*2*11#1#001*31##) and
    # Dimension 11 write (*#2*31*#11#001*40##) on the same cover 31 while overtaking
    # independent cover 11#4#02 (*2*1*11#4#02##).
    storage = _PriorityQueueStorage()
    storage.append(_cmd("*2*11#1#001*31##"))
    storage.append(_cmd("*#2*31*#11#001*40##"))
    storage.append(_cmd("*2*1*11#4#02##"))
    storage.append(_cmd("*2*0*31##"))
    assert _order(storage) == [
        "*2*11#1#001*31##",
        "*#2*31*#11#001*40##",
        "*2*0*31##",
        "*2*1*11#4#02##",
    ]

