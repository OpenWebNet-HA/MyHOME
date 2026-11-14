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
    _same_target,
    _targets_overlap,
)


def test_frame_scheduler_is_standalone_not_asyncio_queue_subclass() -> None:
    """FrameScheduler must not subclass asyncio.Queue or rely on CPython internals."""
    assert CommandPriorityQueue is FrameScheduler
    assert not issubclass(FrameScheduler, asyncio.Queue)


def test_extract_target_key_variants_and_canonicalization() -> None:
    """Target key extractor canonicalizes OWNCommand, raw frame strings, local bus, padding, and tuples."""
    assert _extract_target_key(None) is None
    assert _extract_target_key({}) is None
    assert _extract_target_key({"target": (2, "   ")}) is None
    assert _extract_target_key({"target": (2, "12")}) == (2, "12")
    assert _extract_target_key({"target": (2, "11#4#2")}) == (2, "11#4#02")
    assert _extract_target_key({"message": OWNCommand("*2*2*12##")}) == (2, "12")
    # OWNCommand splits where="11" and where_param=["4", "02"] / ["4", "2"]
    assert _extract_target_key({"message": OWNCommand("*2*1*11#4#02##")}) == (2, "11#4#02")
    assert _extract_target_key({"message": OWNCommand("*2*1*11#4#2##")}) == (2, "11#4#02")
    assert _extract_target_key({"message": "*1*5*21#4#01##"}) == (1, "21#4#01")
    assert _extract_target_key({"message": "*1*5*21#4#1##"}) == (1, "21#4#01")
    assert _extract_target_key({"message": "*#1*21##"}) == (1, "21")
    # Dimension write frames put WHERE in the second field, not the last field
    assert _extract_target_key({"message": "*#2*31*#11#001*40##"}) == (2, "31")
    assert _extract_target_key({"message": "*#1*21#4#1*#1*150*3##"}) == (1, "21#4#01")
    # Leading-zero canonicalization for non-APL addresses, groups, and WHO=4 zones
    assert _extract_target_key({"message": "*1*1*0021##"}) == (1, "21")
    assert _extract_target_key({"message": "*2*1*0011##"}) == (2, "0011")
    assert _extract_target_key({"message": "*2*1*#03##"}) == (2, "#3")
    assert _extract_target_key({"message": "*#4*02*0##"}) == (4, "2")
    assert _extract_target_key({"message": "UNPARSEABLE"}) is None

    # _same_target and _targets_overlap predicates
    assert _same_target((2, "02"), (2, "2")) is True
    assert _same_target((1, "11"), (2, "11")) is False
    assert _same_target((1, "11"), (1, "12")) is False
    assert _targets_overlap((2, "1"), (2, "11")) is True
    assert _targets_overlap((1, "1"), (2, "1")) is False
    assert _targets_overlap((1, "11"), (1, "12")) is False


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
    """A command with is_cancel=True or WHO=2 stop drops queued non-cancel commands for the same target."""
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
    # Implicit WHO=2 WHAT=0 stop frame without explicit is_cancel=True:
    # drops *2*2*12## and jumps ahead of unrelated *2*1*15## (#645).
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
    assert items == ["*2*0*12##", "*2*1*15##"]

    # Alternate WHERE representation ("02" vs "2") also coalesces on stop
    area0_down_written = loop.create_future()
    area0_stop_written = loop.create_future()
    await scheduler.put(
        {
            "message": OWNCommand("*2*2*02##"),
            "is_status_request": False,
            "written": area0_down_written,
        }
    )
    await scheduler.put(
        {
            "message": OWNCommand("*2*0*2##"),
            "is_status_request": False,
            "written": area0_stop_written,
        }
    )
    assert area0_down_written.cancelled()
    assert not area0_stop_written.done()
    assert str((await scheduler.get())["message"]) == "*2*0*2##"

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


async def test_differently_formatted_cover_wheres_and_scopes_cannot_be_in_flight_simultaneously() -> None:
    """Two differently-formatted WHEREs for the same cover or an overlapping area cannot run concurrently."""
    scheduler = FrameScheduler()

    # 1. Area-0 "02" in-flight blocks "2", while independent cover "15" remains eligible
    scheduler.put_nowait({"message": OWNCommand("*2*1*02##"), "is_status_request": False})
    first = await scheduler.acquire_next()
    assert str(first["message"]) == "*2*1*02##"
    assert (2, "02") in scheduler.in_flight_targets

    scheduler.put_nowait({"message": OWNCommand("*2*0*2##"), "is_status_request": False})
    scheduler.put_nowait({"message": OWNCommand("*2*1*15##"), "is_status_request": False})

    # Next acquire must skip *2*0*2## (overlaps in-flight "02") and return *2*1*15##
    second = await scheduler.acquire_next()
    assert str(second["message"]) == "*2*1*15##"
    assert scheduler.in_flight_targets == frozenset({(2, "02"), (2, "15")})

    # No more eligible items while (2, "02") is in flight
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()

    # Releasing via alternate representation (2, "2") releases (2, "02") and unblocks *2*0*2##
    scheduler.release_target((2, "2"))
    scheduler.task_done()
    third = await scheduler.acquire_next()
    assert str(third["message"]) == "*2*0*2##"
    scheduler.complete_task(second)
    scheduler.complete_task(third)
    assert not scheduler.in_flight_targets

    # 2. Local bus "11#4#2" vs "11#4#02" and Area "1" vs point "11"
    scheduler.put_nowait({"message": OWNCommand("*2*1*11#4#2##"), "is_status_request": False})
    scheduler.put_nowait(
        {"message": OWNCommand("*2*0*11#4#02##"), "is_status_request": False, "is_cancel": False}
    )
    scheduler.put_nowait({"message": OWNCommand("*2*1*1##"), "is_status_request": False})
    scheduler.put_nowait({"message": OWNCommand("*2*2*11##"), "is_status_request": False})

    lb_first = await scheduler.acquire_next()
    assert str(lb_first["message"]) == "*2*1*11#4#2##"
    assert (2, "11#4#02") in scheduler.in_flight_targets

    # Area 1 on main bus does not overlap local bus #4#02, so *2*1*1## can be acquired
    area_first = await scheduler.acquire_next()
    assert str(area_first["message"]) == "*2*1*1##"

    # Both *2*0*11#4#02## (same local bus cover) and *2*2*11## (member of Area 1) are now blocked
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()

    scheduler.complete_task(lb_first)
    lb_second = await scheduler.acquire_next()
    assert str(lb_second["message"]) == "*2*0*11#4#02##"
    scheduler.complete_task(lb_second)

    scheduler.complete_task(area_first)
    member_item = await scheduler.acquire_next()
    assert str(member_item["message"]) == "*2*2*11##"
    scheduler.complete_task(member_item)
    await scheduler.join()


async def test_cancel_arrives_while_queue_is_full_and_putter_is_blocked() -> None:
    """When a cancel/superseding item arrives on a full queue, it frees space and wakes blocked putters."""
    scheduler = FrameScheduler(maxsize=2)
    loop = asyncio.get_running_loop()

    f1_written = loop.create_future()
    f2_written = loop.create_future()
    blocked_written = loop.create_future()
    cancel_written = loop.create_future()

    # Fill queue (maxsize=2) with two items for target (1, "21"): a command and a fade step
    scheduler.put_nowait(
        {"message": OWNCommand("*1*1*21##"), "is_status_request": False, "written": f1_written}
    )
    scheduler.put_nowait(
        {
            "message": OWNCommand("*1*5*21##"),
            "is_status_request": False,
            "is_fade_step": True,
            "written": f2_written,
        }
    )
    assert scheduler.full()

    # Start a putter for an unrelated target (1, "22") that blocks because the queue is full
    blocked_put_task = asyncio.create_task(
        scheduler.put(
            {
                "message": OWNCommand("*1*1*22##"),
                "is_status_request": False,
                "written": blocked_written,
            }
        )
    )
    await asyncio.sleep(0)
    assert not blocked_put_task.done()

    # Now a cancel item for (1, "21") arrives: it drops BOTH queued items for (1, "21"),
    # enqueues itself (leaving qsize=1 < maxsize=2), and wakes the blocked putter for (1, "22")!
    await scheduler.put(
        {
            "message": OWNCommand("*1*0*21##"),
            "is_status_request": False,
            "is_cancel": True,
            "written": cancel_written,
        }
    )
    await asyncio.wait_for(blocked_put_task, timeout=1.0)

    assert f1_written.cancelled()
    assert f2_written.cancelled()
    assert not cancel_written.done()
    assert not blocked_written.done()
    assert scheduler.qsize() == 2

    out1 = await scheduler.acquire_next()
    out2 = await scheduler.acquire_next()
    assert [str(out1["message"]), str(out2["message"])] == ["*1*0*21##", "*1*1*22##"]
    scheduler.complete_task(out1)
    scheduler.complete_task(out2)
    await scheduler.join()


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
    blocked_on_shutdown = asyncio.create_task(
        scheduler.put({"message": "*1*1*14##", "is_status_request": False})
    )
    await asyncio.sleep(0)
    scheduler.shutdown(immediate=True)
    assert fut2.cancelled()
    assert scheduler.empty()

    shutdown_exc = getattr(asyncio, "QueueShutDown", Exception)
    with pytest.raises(shutdown_exc):
        await blocked_on_shutdown
    with pytest.raises(shutdown_exc):
        scheduler.put_nowait({"message": "*1*1*13##", "is_status_request": False})
    with pytest.raises(shutdown_exc):
        await scheduler.get()
    with pytest.raises(shutdown_exc):
        await scheduler.acquire_next()

    # CommandWorkerPool.send_fade_step, send(..., is_cancel=True), and _cover_in_flight fallback
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

    pool.send_buffer = asyncio.Queue()
    assert pool._cover_in_flight == []


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
