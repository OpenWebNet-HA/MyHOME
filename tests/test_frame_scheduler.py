"""Tests for standalone FrameScheduler and per-target worker serialization."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNCommand

from custom_components.myhome.gateway import FrameScheduler, MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import (
    CommandPriorityQueue,
    CommandWorkerPool,
    _extract_target_key,
    _PriorityQueueStorage,
)


def test_frame_scheduler_is_standalone_not_asyncio_queue_subclass() -> None:
    """FrameScheduler must not subclass asyncio.Queue or rely on CPython internals."""
    assert CommandPriorityQueue is FrameScheduler
    assert not issubclass(FrameScheduler, asyncio.Queue)


def test_extract_target_key_variants() -> None:
    """Target key extractor handles OWNCommand, raw frame strings, explicit tuples, and non-dicts."""
    assert _extract_target_key(None) is None
    assert _extract_target_key({}) is None
    assert _extract_target_key({"target": (2, "12")}) == (2, "12")
    assert _extract_target_key({"message": OWNCommand("*2*2*12##")}) == (2, "12")
    assert _extract_target_key({"message": "*1*5*21#4#01##"}) == (1, "21#4#01")
    assert _extract_target_key({"message": "*#1*21##"}) == (1, "21")
    assert _extract_target_key({"message": "UNPARSEABLE"}) is None


async def test_four_tier_priority_ordering() -> None:
    """FrameScheduler orders commands (0) > fade steps (1) > status (2) > sentinels (3)."""
    scheduler = FrameScheduler()

    sentinel = None
    status = {"message": "*#1*21##", "is_status_request": True}
    fade1 = {"message": "*1*3*31##", "is_status_request": False, "is_fade_step": True}
    fade2 = {"message": "*1*6*32##", "is_status_request": False, "is_fade_step": True}
    cmd1 = {"message": "*1*1*11##", "is_status_request": False}
    cmd2 = {"message": "*1*0*12##", "is_status_request": False}

    scheduler.put_nowait(sentinel)
    scheduler.put_nowait(status)
    scheduler.put_nowait(fade1)
    scheduler.put_nowait(cmd1)
    scheduler.put_nowait(fade2)
    scheduler.put_nowait(cmd2)

    assert scheduler.command_qsize == 2
    assert scheduler.fade_qsize == 2
    assert scheduler.status_qsize == 1
    assert scheduler.other_qsize == 1
    assert scheduler.qsize() == 6

    popped = [await scheduler.get() for _ in range(6)]
    assert [p["message"] if isinstance(p, dict) else p for p in popped] == [
        "*1*1*11##",
        "*1*0*12##",
        "*1*3*31##",
        "*1*6*32##",
        "*#1*21##",
        None,
    ]


async def test_fade_step_superseding_and_command_override() -> None:
    """New fade steps or user commands supersede older queued fade steps for the same target."""
    scheduler = FrameScheduler()
    loop = asyncio.get_running_loop()

    f1_written = loop.create_future()
    f2_written = loop.create_future()
    other_written = loop.create_future()
    cmd_written = loop.create_future()

    await scheduler.put(
        {
            "message": OWNCommand("*1*2*21##"),
            "is_status_request": False,
            "is_fade_step": True,
            "written": f1_written,
        }
    )
    await scheduler.put(
        {
            "message": OWNCommand("*1*4*22##"),
            "is_status_request": False,
            "is_fade_step": True,
            "written": other_written,
        }
    )
    # Supersede target (1, "21") with newer fade step
    await scheduler.put(
        {
            "message": OWNCommand("*1*5*21##"),
            "is_status_request": False,
            "is_fade_step": True,
            "written": f2_written,
        }
    )
    assert f1_written.cancelled()
    assert not f2_written.done()
    assert not other_written.done()
    assert scheduler.fade_qsize == 2

    # User command for (1, "21") drops the remaining fade step for (1, "21")
    await scheduler.put(
        {
            "message": OWNCommand("*1*0*21##"),
            "is_status_request": False,
            "written": cmd_written,
        }
    )
    assert f2_written.cancelled()
    assert not other_written.done()
    assert scheduler.command_qsize == 1
    assert scheduler.fade_qsize == 1

    first = await scheduler.get()
    second = await scheduler.get()
    assert str(first["message"]) == "*1*0*21##"
    assert str(second["message"]) == "*1*4*22##"


async def test_cancel_command_coalesces_queued_commands_for_same_target() -> None:
    """A command with is_cancel=True or WHO=2 WHAT=0 stop drops queued non-cancel commands for the same target."""
    scheduler = FrameScheduler()
    loop = asyncio.get_running_loop()

    down_written = loop.create_future()
    other_written = loop.create_future()
    stop_written = loop.create_future()

    await scheduler.put(
        {
            "message": OWNCommand("*2*2*12##"),
            "is_status_request": False,
            "written": down_written,
        }
    )
    await scheduler.put(
        {
            "message": OWNCommand("*2*1*15##"),
            "is_status_request": False,
            "written": other_written,
        }
    )
    # Implicit WHO=2 WHAT=0 stop frame without explicit is_cancel=True
    await scheduler.put(
        {
            "message": OWNCommand("*2*0*12##"),
            "is_status_request": False,
            "written": stop_written,
        }
    )

    assert down_written.cancelled()
    assert not other_written.done()
    assert not stop_written.done()
    assert scheduler.command_qsize == 2

    items = [str((await scheduler.get())["message"]) for _ in range(2)]
    assert items == ["*2*1*15##", "*2*0*12##"]

    # Explicit is_cancel=True on non-WHO=2 command also coalesces non-cancel commands
    on_written = loop.create_future()
    off_cancel_written = loop.create_future()
    await scheduler.put(
        {"message": OWNCommand("*1*1*21##"), "is_status_request": False, "written": on_written}
    )
    await scheduler.put(
        {
            "message": OWNCommand("*1*0*21##"),
            "is_status_request": False,
            "is_cancel": True,
            "written": off_cancel_written,
        }
    )
    assert on_written.cancelled()
    assert not off_cancel_written.done()
    assert str((await scheduler.get())["message"]) == "*1*0*21##"


async def test_multi_worker_cold_vs_warm_socket_preserves_per_target_order() -> None:
    """Warm worker 1 cannot overtake cold worker 0 for the same (who, where) target."""
    mock_handler = MagicMock()
    mock_handler.mac = "00:03:50:aa:bb:cc"
    mock_handler.log_id = "[test-gw]"
    mock_handler.command_session_idle_timeout = 5.0
    mock_handler.gateway = MagicMock()
    mock_handler.gateway.profile.max_queue_size = 250
    mock_handler.gateway.profile.command_queue_delay = 0.0
    mock_handler.bus_monitor = MagicMock()

    pool = CommandWorkerPool(mock_handler)
    pool._event_session_ready.set()

    wire_order: list[tuple[int, str]] = []
    worker0_connecting = asyncio.Event()
    allow_worker0_connect = asyncio.Event()

    class FakeSession:
        def __init__(self, idx: int) -> None:
            self.idx = idx
            # Worker 0 starts with a closed socket after initial connect; Worker 1 stays warm
            self._stream_reader: object | None = object()
            self._stream_writer: object | None = object()
            self.connect_calls = 0

        async def connect(self) -> dict[str, Any]:
            self.connect_calls += 1
            if self.idx == 0 and self.connect_calls == 1:
                # Initial connect succeeds, then simulate idle close before first frame
                self._stream_reader = None
                self._stream_writer = None
                return {"Success": True}
            if self.idx == 0 and self.connect_calls == 2:
                worker0_connecting.set()
                await allow_worker0_connect.wait()
            self._stream_reader = object()
            self._stream_writer = object()
            return {"Success": True}

        async def send(self, message: Any, is_status_request: bool = False) -> list[Any]:
            wire_order.append((self.idx, str(message)))
            return []

        async def close(self) -> None:
            self._stream_reader = None
            self._stream_writer = None

    sessions = [FakeSession(0), FakeSession(1)]
    session_iter = iter(sessions)

    with patch(
        "custom_components.myhome.gateway.OWNCommandSession",
        side_effect=lambda **_: next(session_iter),
    ):
        w0 = asyncio.create_task(pool.sending_loop(0))
        w1 = asyncio.create_task(pool.sending_loop(1))
        await asyncio.sleep(0)

        # 1. Enqueue DOWN for cover 12 -> acquired by Worker 0 (whose socket is cold)
        f_down = await pool.send(OWNCommand("*2*2*12##"))
        await worker0_connecting.wait()
        assert (2, "12") in pool.send_buffer.in_flight_targets

        # 2. Enqueue STOP for cover 12 AND UP for cover 15 while Worker 0 is still connecting
        f_stop = await pool.send(OWNCommand("*2*0*12##"))
        f_other = await pool.send(OWNCommand("*2*1*15##"))

        # Give Worker 1 (warm socket) a chance to run: it MUST send cover 15 immediately
        # and MUST NOT send cover 12's STOP while cover 12's DOWN is still in flight on Worker 0!
        await asyncio.wait_for(f_other, timeout=1.0)
        assert wire_order == [(1, "*2*1*15##")]
        assert not f_down.done()
        assert not f_stop.done()

        # 3. Unblock Worker 0's socket connect -> DOWN sends first, then STOP sends second
        allow_worker0_connect.set()
        await asyncio.wait_for(asyncio.gather(f_down, f_stop), timeout=1.0)

        await pool.send_buffer.put(None)
        await pool.send_buffer.put(None)
        await asyncio.wait_for(asyncio.gather(w0, w1), timeout=1.0)

    cover12_frames = [frame for _, frame in wire_order if frame.endswith("*12##")]
    assert cover12_frames == ["*2*2*12##", "*2*0*12##"]


async def test_frame_scheduler_storage_and_lifecycle_edge_cases() -> None:
    """Cover storage operations, join, task_done errors, full/empty/shutdown, and pool methods."""
    storage = _PriorityQueueStorage()
    with pytest.raises(IndexError, match="no eligible item"):
        storage.pop_eligible({(1, "11")})

    fade = {"message": "*1*5*21##", "is_status_request": False, "is_fade_step": True}
    storage.appendleft(fade)
    assert storage[0] == fade
    storage.remove(fade)
    assert len(storage) == 0

    storage.append(fade)
    assert storage.pop() == fade

    scheduler = FrameScheduler(maxsize=1)
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()

    fut = asyncio.get_running_loop().create_future()
    scheduler.put_nowait({"message": "*1*1*11##", "is_status_request": False, "written": fut})
    with pytest.raises(asyncio.QueueFull):
        scheduler.put_nowait({"message": "*1*1*12##", "is_status_request": False})

    item = scheduler.acquire_nowait()
    assert (1, "11") in scheduler.in_flight_targets
    scheduler.complete_task(item)
    assert (1, "11") not in scheduler.in_flight_targets
    await scheduler.join()

    with pytest.raises(ValueError, match="task_done"):
        scheduler.task_done()

    fut2 = asyncio.get_running_loop().create_future()
    scheduler.put_nowait({"message": "*1*1*12##", "is_status_request": False, "written": fut2})
    scheduler.shutdown(immediate=True)
    assert fut2.cancelled()
    assert scheduler.empty()

    shutdown_exc = getattr(asyncio, "QueueShutDown", Exception)
    with pytest.raises(shutdown_exc):
        scheduler.put_nowait({"message": "*1*1*13##", "is_status_request": False})
    with pytest.raises(shutdown_exc):
        await scheduler.get()
    with pytest.raises(shutdown_exc):
        await scheduler.acquire_next()

    # CommandWorkerPool.send_fade_step and send(..., is_cancel=True)
    mock_handler = MagicMock()
    mock_handler.gateway.profile.max_queue_size = 10
    pool = CommandWorkerPool(mock_handler)
    f_fade = await pool.send_fade_step(OWNCommand("*1*5*21##"))
    assert not f_fade.done()
    assert pool.send_buffer.fade_qsize == 1  # type: ignore[union-attr]
    f_cancel = await pool.send(OWNCommand("*1*0*21##"), is_cancel=True)
    assert f_fade.cancelled()
    assert not f_cancel.done()
    assert pool.send_buffer.command_qsize == 1  # type: ignore[union-attr]
    pool.close()



async def test_gateway_send_fade_step_and_is_cancel_delegation() -> None:
    """MyHOMEGatewayHandler forwards send_fade_step and is_cancel to delegated/standby/pool."""
    gw = object.__new__(MyHOMEGatewayHandler)
    gw._command_pool = MagicMock()
    gw._command_pool.send = AsyncMock(return_value="pool_send")
    gw._command_pool.send_fade_step = AsyncMock(return_value="pool_fade")

    secondary = MagicMock()
    secondary.send = AsyncMock(return_value="sec_send")
    secondary.send_fade_step = AsyncMock(return_value="sec_fade")

    standby = MagicMock()
    standby.send = AsyncMock(return_value="std_send")
    standby.send_fade_step = AsyncMock(return_value="std_fade")

    cmd = OWNCommand("*1*1*21##")

    # Direct pool path
    gw._delegated_target = MagicMock(return_value=None)  # type: ignore[method-assign]
    gw._failover_target = MagicMock(return_value=None)  # type: ignore[method-assign]
    assert await gw.send(cmd, is_cancel=True) == "pool_send"
    gw._command_pool.send.assert_awaited_once_with(cmd, is_cancel=True)
    assert await gw.send_fade_step(cmd) == "pool_fade"

    # Standby path
    gw._failover_target = MagicMock(return_value=standby)  # type: ignore[method-assign]
    assert await gw.send(cmd, is_cancel=True) == "std_send"
    standby.send.assert_awaited_once_with(cmd, is_cancel=True)
    assert await gw.send_fade_step(cmd) == "std_fade"

    # Delegated path
    gw._delegated_target = MagicMock(return_value=secondary)  # type: ignore[method-assign]
    assert await gw.send(cmd, is_cancel=True) == "sec_send"
    secondary.send.assert_awaited_once_with(cmd, is_cancel=True)
    assert await gw.send_fade_step(cmd) == "sec_fade"
