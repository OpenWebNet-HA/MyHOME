"""Tests for standalone FrameScheduler and per-target worker serialization."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNAutomationCommand, OWNCommand

from custom_components.myhome.cover import MyHOMECover
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

    # "02" (point A=00 PL=02) and "2" (area 2) are different addresses: the stop for
    # area 2 does not drop the queued down for point 02. It still queues behind it
    # (the overlap check stays conservative), so both are written in order.
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
    assert not area0_down_written.done()
    assert not area0_stop_written.done()
    items = [str((await scheduler.get())["message"]) for _ in range(2)]
    assert items == ["*2*2*02##", "*2*0*2##"]

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


@pytest.mark.parametrize(
    ("queued", "cancel"),
    [
        ("*2*1*5##", "*2*2*05##"),  # area 5 is not point A=00 PL=05
        ("*2*1*05##", "*2*2*5##"),
        ("*2*1*11##", "*2*2*0011##"),  # point A=1 PL=1 is not point A=00 PL=11
        ("*2*1*0011##", "*2*2*11##"),
    ],
)
async def test_cancel_does_not_drop_a_command_for_another_address_with_the_same_number(
    queued: str, cancel: str
) -> None:
    """A cancel item supersedes commands for its own address only (the Dimension 10 hand-off)."""
    scheduler = FrameScheduler()
    loop = asyncio.get_running_loop()
    queued_written = loop.create_future()
    cancel_written = loop.create_future()

    await scheduler.put(
        {
            "message": OWNCommand(queued),
            "is_status_request": False,
            "is_cancel": False,
            "written": queued_written,
        }
    )
    await scheduler.put(
        {
            "message": OWNCommand(cancel),
            "is_status_request": False,
            "is_cancel": True,
            "written": cancel_written,
        }
    )

    assert not queued_written.done()
    assert [str(item["message"]) for item in scheduler._queue] == [queued, cancel]
    # The two still never run at the same time on two workers.
    first = scheduler.acquire_nowait()
    assert str(first["message"]) == queued
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()


@pytest.mark.parametrize(
    ("queued", "cancel"),
    [
        ("*2*1*05##", "*2*2*05##"),
        ("*2*1*11#4#2##", "*2*2*11#4#02##"),  # one interface, two spellings
        ("*2*1*#03##", "*2*2*#3##"),  # one group, two spellings
    ],
)
async def test_cancel_still_drops_the_queued_command_for_its_own_address(
    queued: str, cancel: str
) -> None:
    scheduler = FrameScheduler()
    loop = asyncio.get_running_loop()
    queued_written = loop.create_future()

    await scheduler.put(
        {
            "message": OWNCommand(queued),
            "is_status_request": False,
            "is_cancel": False,
            "written": queued_written,
        }
    )
    await scheduler.put(
        {"message": OWNCommand(cancel), "is_status_request": False, "is_cancel": True}
    )

    assert queued_written.cancelled()
    assert [str(item["message"]) for item in scheduler._queue] == [cancel]


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


def _cmd(frame: str) -> dict[str, Any]:
    """Envelope with the shape ``CommandWorkerPool._enqueue`` builds."""
    return {"message": OWNCommand(frame), "is_status_request": False}


@pytest.mark.parametrize(
    ("scope_frame", "member_frame"),
    [
        ("*2*1*1##", "*2*0*12##"),  # area 1 up, then stop 12
        ("*2*2*0##", "*2*1*12##"),  # general down, then 12 up
        ("*2*1*#3##", "*2*0*12##"),  # group 3 up, then stop 12 (membership unknown)
        ("*2*1*1##", "*2*2*12##"),  # area 1 up, then 12 down
    ],
)
async def test_frame_does_not_pass_a_waiting_scope_frame_queued_ahead_of_it(
    scope_frame: str, member_frame: str
) -> None:
    """WHO 2 overlap is not transitive: 11 and 12 do not overlap, both overlap the scope.

    With ``*2*1*11##`` in flight the scope frame waits. The frame queued behind it
    for another cover of that scope must wait too, or the bus gets it first and the
    scope frame after it (stop 12, then area 1 up: cover 12 runs).
    """
    scheduler = FrameScheduler()
    scheduler.put_nowait(_cmd("*2*1*11##"))
    in_flight = scheduler.acquire_nowait()
    scheduler.put_nowait(_cmd(scope_frame))
    scheduler.put_nowait(_cmd(member_frame))
    assert [str(item["message"]) for item in scheduler._queue] == [scope_frame, member_frame]

    # The second worker gets nothing: both are held, in queue order.
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()

    # Frames nothing waits for still go out on the second worker.
    scheduler.put_nowait(_cmd("*1*1*21##"))
    light = scheduler.acquire_nowait()
    assert str(light["message"]) == "*1*1*21##"
    scheduler.complete_task(light)

    scheduler.complete_task(in_flight)
    first = scheduler.acquire_nowait()
    assert str(first["message"]) == scope_frame
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()
    scheduler.complete_task(first)
    second = scheduler.acquire_nowait()
    assert str(second["message"]) == member_frame
    scheduler.complete_task(second)
    await scheduler.join()


async def test_unrelated_cover_passes_a_waiting_scope_frame() -> None:
    """Only frames that overlap a waiting frame are held: a cover of another area is not."""
    scheduler = FrameScheduler()
    scheduler.put_nowait(_cmd("*2*1*11##"))
    in_flight = scheduler.acquire_nowait()
    scheduler.put_nowait(_cmd("*2*1*1##"))
    scheduler.put_nowait(_cmd("*2*0*12##"))
    scheduler.put_nowait(_cmd("*2*0*25##"))

    # The stop for 25 was queued at the front (#645) and overlaps nothing held.
    other = scheduler.acquire_nowait()
    assert str(other["message"]) == "*2*0*25##"
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()

    scheduler.complete_task(other)
    scheduler.complete_task(in_flight)
    assert [str(scheduler.acquire_nowait()["message"]) for _ in range(1)] == ["*2*1*1##"]


async def test_status_read_waits_behind_a_waiting_command_for_an_overlapping_target() -> None:
    """The order holds across tiers: a read of 12 is not sent before the area frame it follows."""
    scheduler = FrameScheduler()
    scheduler.put_nowait(_cmd("*2*1*11##"))
    in_flight = scheduler.acquire_nowait()
    scheduler.put_nowait(_cmd("*2*1*1##"))
    scheduler.put_nowait({"message": OWNCommand("*#2*12##"), "is_status_request": True})
    scheduler.put_nowait({"message": OWNCommand("*#1*21##"), "is_status_request": True})

    assert scheduler._queue.has_eligible(scheduler._in_flight_targets) is True
    read = scheduler.acquire_nowait()
    assert str(read["message"]) == "*#1*21##"
    assert scheduler._queue.has_eligible(scheduler._in_flight_targets) is False
    with pytest.raises(IndexError):
        scheduler._queue.pop_eligible(scheduler._in_flight_targets)

    scheduler.complete_task(read)
    scheduler.complete_task(in_flight)
    area = scheduler.acquire_nowait()
    assert str(area["message"]) == "*2*1*1##"
    scheduler.complete_task(area)
    assert str(scheduler.acquire_nowait()["message"]) == "*#2*12##"


async def test_two_workers_keep_queue_order_for_a_stop_behind_an_area_frame() -> None:
    """Three frames, two workers, zero pacing: up 11, area 1 up, stop 12 reach the wire in that order."""
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
            self._stream_reader: object | None = object()
            self._stream_writer: object | None = object()
            self.connect_calls = 0

        async def connect(self) -> dict[str, Any]:
            self.connect_calls += 1
            if self.idx == 0 and self.connect_calls == 1:
                # Initial connect succeeds, then the socket is closed for idle.
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

    session_iter = iter([FakeSession(0), FakeSession(1)])

    with patch(
        "custom_components.myhome.gateway.OWNCommandSession",
        side_effect=lambda **_: next(session_iter),
    ):
        w0 = asyncio.create_task(pool.sending_loop(0))
        w1 = asyncio.create_task(pool.sending_loop(1))
        await asyncio.sleep(0)

        # Worker 0 takes the up for cover 11 and has to reopen its session first.
        f_up = await pool.send(OWNCommand("*2*1*11##"))
        await worker0_connecting.wait()
        assert (2, "11") in pool.send_buffer.in_flight_targets

        # Area 1 up, the stop for cover 12 behind it, and a cover of another area.
        f_area = await pool.send(OWNCommand("*2*1*1##"))
        f_stop = await pool.send(OWNCommand("*2*0*12##"))
        f_other = await pool.send(OWNCommand("*2*1*25##"))

        # Worker 1 (open session) sends the unrelated cover and nothing else.
        await asyncio.wait_for(f_other, timeout=1.0)
        await asyncio.sleep(0.02)
        assert wire_order == [(1, "*2*1*25##")]
        assert not f_area.done()
        assert not f_stop.done()

        allow_worker0_connect.set()
        await asyncio.wait_for(asyncio.gather(f_up, f_area, f_stop), timeout=1.0)

        await pool.send_buffer.put(None)
        await pool.send_buffer.put(None)
        await asyncio.wait_for(asyncio.gather(w0, w1), timeout=1.0)

    area1_frames = [frame for _, frame in wire_order if frame != "*2*1*25##"]
    assert area1_frames == ["*2*1*11##", "*2*1*1##", "*2*0*12##"]


async def test_shutdown_sentinel_is_handed_out_while_frames_are_held() -> None:
    """An item without a target (the worker sentinel) is never held back by waiting frames."""
    scheduler = FrameScheduler()
    scheduler.put_nowait(_cmd("*2*1*11##"))
    in_flight = scheduler.acquire_nowait()
    scheduler.put_nowait(_cmd("*2*1*1##"))
    scheduler.put_nowait(_cmd("*2*0*12##"))
    scheduler.put_nowait(None)

    assert scheduler.acquire_nowait() is None
    scheduler.task_done()
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()
    scheduler.complete_task(in_flight)
    assert str(scheduler.acquire_nowait()["message"]) == "*2*1*1##"


def test_same_target_is_exact_outside_who_2() -> None:
    """Only WHO 2 has the lenient numeric overlap; elsewhere a target is its exact key."""
    assert _same_target((1, "05"), (1, "05"))
    assert not _same_target((1, "05"), (1, "5"))
    assert not _same_target((1, "05"), (2, "05"))


async def test_withdrawn_area_must_not_block_an_independent_stop():
    s = FrameScheduler()
    s.put_nowait(_cmd("*2*1*11##"))
    held = s.acquire_nowait()
    # A cancelled `written` future: the area frame will not be written (the worker
    # discards a withdrawn frame), so it must not hold the stop of another cover.
    written = asyncio.get_running_loop().create_future()
    s.put_nowait(dict(_cmd("*2*1*1##"), written=written))
    s.put_nowait(_cmd("*2*0*12##"))
    written.cancel()
    delivered = []
    while True:
        try:
            item = s.acquire_nowait()
        except asyncio.QueueEmpty:
            break
        if not (item.get("written") and item["written"].cancelled()):
            delivered.append(str(item["message"]))
        s.complete_task(item)
    assert delivered == ["*2*0*12##"]
    s.complete_task(held)


async def test_new_direction_cancels_stale_motion_for_alias():
    # `0009` is the area-zero point `09` (tests/test_canonical_point_where.py): one address, two spellings.
    scheduler = FrameScheduler()
    written = asyncio.get_running_loop().create_future()
    await scheduler.put(
        dict(_cmd(str(OWNAutomationCommand.raise_shutter("09#4#01"))), written=written)
    )
    await scheduler.put(_cmd(str(OWNAutomationCommand.stop_shutter("0009#4#01"))))
    assert written.cancelled(), [str(i["message"]) for i in scheduler._queue]


async def test_discarding_withdrawn_frame_does_not_release_another_workers_target_lock() -> None:
    """Worker 1 discarding a withdrawn frame for the same cover must not unlock Worker 0's in-flight write."""
    scheduler = FrameScheduler()
    scheduler.put_nowait(_cmd("*2*1*11##"))
    worker0_item = scheduler.acquire_nowait()
    assert (2, "11") in scheduler.in_flight_targets

    withdrawn_fut = asyncio.get_running_loop().create_future()
    scheduler.put_nowait(dict(_cmd("*2*2*11##"), written=withdrawn_fut))
    scheduler.put_nowait(_cmd("*2*1*11##"))
    withdrawn_fut.cancel()

    # Worker 1 pops and completes the withdrawn frame; Worker 0's lock on (2, "11") MUST remain held.
    discarded = scheduler.acquire_nowait()
    assert discarded["written"].cancelled()
    scheduler.complete_task(discarded)
    assert (2, "11") in scheduler.in_flight_targets

    # The live command for cover 11 must still wait for Worker 0 to finish.
    with pytest.raises(asyncio.QueueEmpty):
        scheduler.acquire_nowait()

    scheduler.complete_task(worker0_item)
    live_cmd = scheduler.acquire_nowait()
    assert str(live_cmd["message"]) == "*2*1*11##"
    scheduler.complete_task(live_cmd)


async def test_stop_overtakes_withdrawn_motion_for_same_cover() -> None:
    """A withdrawn motion frame in the command tier does not hold back an overlapping or opt-out stop."""
    scheduler = FrameScheduler()
    scope_written = asyncio.get_running_loop().create_future()
    scheduler.put_nowait(dict(_cmd("*2*1*1##"), written=scope_written))
    scope_written.cancel()
    scheduler.put_nowait(_cmd("*2*0*11##"))

    # The point stop was inserted at index 0 ahead of the withdrawn overlapping scope frame.
    first = scheduler.acquire_nowait()
    assert str(first["message"]) == "*2*0*11##"
    scheduler.complete_task(first)
    second = scheduler.acquire_nowait()
    assert second["written"].cancelled()
    scheduler.complete_task(second)

    # Also verify explicit is_cancel=False opt-out on the exact same cover WHERE.
    same_written = asyncio.get_running_loop().create_future()
    scheduler.put_nowait(dict(_cmd("*2*1*11##"), written=same_written))
    same_written.cancel()
    scheduler.put_nowait(dict(_cmd("*2*0*11##"), is_cancel=False))
    opt_out_stop = scheduler.acquire_nowait()
    assert str(opt_out_stop["message"]) == "*2*0*11##"
    scheduler.complete_task(opt_out_stop)
    withdrawn_same = scheduler.acquire_nowait()
    assert withdrawn_same["written"].cancelled()
    scheduler.complete_task(withdrawn_same)


@pytest.mark.parametrize("use_async_put", [False, True])
async def test_superseded_scope_wakes_all_waiting_getters(use_async_put: bool) -> None:
    """Dropping a held scope frame via an ordinary WHO=2 stop wakes every sleeping worker."""
    scheduler = FrameScheduler()
    scheduler.put_nowait(_cmd("*2*1*11##"))
    worker0_item = scheduler.acquire_nowait()

    # Area 1 waits behind in-flight cover 11 and holds back independent covers 12 and 13.
    scheduler.put_nowait(_cmd("*2*1*1##"))
    scheduler.put_nowait(_cmd("*2*2*12##"))
    scheduler.put_nowait(_cmd("*2*2*13##"))

    w1 = asyncio.create_task(scheduler.acquire_next())
    w2 = asyncio.create_task(scheduler.acquire_next())
    await asyncio.sleep(0)
    assert not w1.done()
    assert not w2.done()

    # Superseding *2*1*1## with a standard *2*0*1## envelope (no explicit is_cancel=True)
    # removes it from index 0 and appends the stop behind 12 and 13, waking BOTH workers.
    cancel_area = _cmd("*2*0*1##")
    if use_async_put:
        await scheduler.put(cancel_area)
    else:
        scheduler.put_nowait(cancel_area)

    got1, got2 = await asyncio.wait_for(asyncio.gather(w1, w2), timeout=1.0)
    assert {str(got1["message"]), str(got2["message"])} == {"*2*2*12##", "*2*2*13##"}

    scheduler.complete_task(got1)
    scheduler.complete_task(got2)
    scheduler.complete_task(worker0_item)
    stop_item = scheduler.acquire_nowait()
    assert str(stop_item["message"]) == "*2*0*1##"
    scheduler.complete_task(stop_item)


async def test_withdrawn_envelope_uses_dict_stamp_not_external_id_set() -> None:
    """Withdrawn envelopes stamp `_unlocked` directly so skipping complete_task leaves no stale state."""
    scheduler = FrameScheduler()
    assert not hasattr(scheduler, "_unlocked_items")

    withdrawn_fut = asyncio.get_running_loop().create_future()
    env = dict(_cmd("*2*1*11##"), written=withdrawn_fut)
    scheduler.put_nowait(env)
    withdrawn_fut.cancel()

    popped = scheduler.acquire_nowait()
    assert popped is env
    assert popped.get("_unlocked") is True
    # Finishing via complete_task clears the stamp without disturbing any active lock.
    scheduler.complete_task(popped)
    assert "_unlocked" not in popped

    # Acquiring another withdrawn envelope and calling task_done() directly leaves no side state.
    withdrawn_fut2 = asyncio.get_running_loop().create_future()
    scheduler.put_nowait(dict(_cmd("*2*1*11##"), written=withdrawn_fut2))
    withdrawn_fut2.cancel()
    popped2 = scheduler.acquire_nowait()
    assert popped2.get("_unlocked") is True
    scheduler.task_done()

    # A subsequent live envelope for the same cover acquires and releases its lock normally.
    scheduler.put_nowait(_cmd("*2*1*11##"))
    live = scheduler.acquire_nowait()
    assert (2, "11") in scheduler.in_flight_targets
    scheduler.complete_task(live)
    assert (2, "11") not in scheduler.in_flight_targets


async def test_acquire_next_forwards_wakeup_when_cancelled_after_resolution() -> None:
    """If a woken acquire_next() task is cancelled before resuming, the next waiter is woken."""
    scheduler = FrameScheduler()
    w1 = asyncio.create_task(scheduler.acquire_next())
    w2 = asyncio.create_task(scheduler.acquire_next())
    await asyncio.sleep(0)
    assert len(scheduler._getters) == 2

    # put_nowait() resolves w1's getter future; cancel w1 on the same tick before it resumes.
    scheduler.put_nowait(_cmd("*2*1*11##"))
    w1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w1

    got = await asyncio.wait_for(w2, timeout=1.0)
    assert str(got["message"]) == "*2*1*11##"
    scheduler.complete_task(got)


async def test_get_forwards_wakeup_when_cancelled_after_resolution() -> None:
    """If a woken get() task is cancelled before resuming, the next waiter is woken."""
    scheduler = FrameScheduler()
    w1 = asyncio.create_task(scheduler.get())
    w2 = asyncio.create_task(scheduler.get())
    await asyncio.sleep(0)
    assert len(scheduler._getters) == 2

    scheduler.put_nowait(_cmd("*2*1*11##"))
    w1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await w1

    got = await asyncio.wait_for(w2, timeout=1.0)
    assert str(got["message"]) == "*2*1*11##"
    scheduler.task_done()


async def test_cover_async_stop_supersedes_queued_move_in_command_worker_pool(hass: Any) -> None:
    """Production cover._async_stop() through MyHOMEGatewayHandler + CommandWorkerPool drops queued moves."""
    gw = MagicMock()
    gw.hass = hass
    gw.mac = "00:03:50:aa:bb:cc"
    gw.log_id = "[test-gw]"
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.device_registry_id = None
    gw.config_entry = None
    gw.command_session_idle_timeout = 5.0
    gw.gateway = MagicMock()
    gw.gateway.profile.max_queue_size = 250
    gw.gateway.profile.command_queue_delay = 0.0
    gw.bus_monitor = MagicMock()
    gw._delegated_target = MagicMock(return_value=None)
    gw._failover_target = MagicMock(return_value=None)
    gw.send_status_request = AsyncMock()
    gw.send = lambda msg, **kw: MyHOMEGatewayHandler.send(gw, msg, **kw)

    pool = CommandWorkerPool(gw)
    pool._event_session_ready.set()
    gw._command_pool = pool
    gw.send_buffer = pool.send_buffer

    cover = MyHOMECover(
        hass=hass,
        name="Shutter 21",
        entity_name=None,
        device_id="21",
        who="2",
        where="21",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=gw,
        travel_time=10,
    )
    cover.hass = hass
    cover.async_write_ha_state = MagicMock()  # type: ignore[method-assign]
    cover.async_schedule_update_ha_state = MagicMock()  # type: ignore[method-assign]
    cover._attr_current_cover_position = 100
    cover._start_position = 100

    wire_frames: list[str] = []
    blocker_sending = asyncio.Event()
    release_blocker = asyncio.Event()

    class FakeSession:
        def __init__(self) -> None:
            self._stream_reader: object | None = object()
            self._stream_writer: object | None = object()

        async def connect(self) -> dict[str, Any]:
            self._stream_reader = object()
            self._stream_writer = object()
            return {"Success": True}

        async def send(self, message: Any, is_status_request: bool = False) -> list[Any]:
            raw = str(message)
            if raw == "*1*1*55##":
                blocker_sending.set()
                await release_blocker.wait()
            wire_frames.append(raw)
            return []

        async def close(self) -> None:
            self._stream_reader = None
            self._stream_writer = None

    with patch(
        "custom_components.myhome.gateway.OWNCommandSession",
        side_effect=lambda **_: FakeSession(),
    ):
        worker = asyncio.create_task(pool.sending_loop(0))
        await asyncio.sleep(0)

        # Occupy the worker with an unrelated light frame so subsequent commands queue up.
        f_blocker = await gw.send(OWNCommand("*1*1*55##"))
        await blocker_sending.wait()

        # Queue a cover close via MyHOMECover, followed by an unrelated cover move.
        await cover.async_close_cover()
        queued_close = pool.send_buffer._queue._commands[0]
        assert str(queued_close["message"]) == "*2*2*21##"
        assert "is_cancel" not in queued_close
        close_written = queued_close["written"]

        f_unrelated = await gw.send(OWNCommand("*2*1*35##"))

        # User stops the cover before *2*2*21## leaves send_buffer:
        # _async_stop() calls gw.send(*2*0*21##) without is_cancel=True, which must implicitly
        # drop *2*2*21##, cancel its delivery future, and jump ahead of *2*1*35## (#645, #1010).
        await cover.async_stop_cover()

        assert close_written.cancelled()
        queued_frames = [str(item["message"]) for item in pool.send_buffer._queue._commands]
        assert queued_frames == ["*2*0*21##", "*2*1*35##"]

        release_blocker.set()
        await asyncio.wait_for(asyncio.gather(f_blocker, f_unrelated), timeout=1.0)
        await pool.send_buffer.join()

        await pool.send_buffer.put(None)
        await asyncio.wait_for(worker, timeout=1.0)

    assert wire_frames == ["*1*1*55##", "*2*0*21##", "*2*1*35##"]
    assert cover.current_cover_position == 100
    assert cover.is_closing is False
    assert cover.is_opening is False


async def test_pool_stop_supersedes_held_scope_and_wakes_blocked_workers() -> None:
    """Implicit WHO=2 stop via CommandWorkerPool.send() drops a held scope move and wakes workers."""
    mock_handler = MagicMock()
    mock_handler.mac = "00:03:50:aa:bb:cc"
    mock_handler.log_id = "[test-gw]"
    mock_handler.config_entry = None
    mock_handler.command_session_idle_timeout = 5.0
    mock_handler.gateway = MagicMock()
    mock_handler.gateway.profile.max_queue_size = 250
    mock_handler.gateway.profile.command_queue_delay = 0.0
    mock_handler.bus_monitor = MagicMock()

    pool = CommandWorkerPool(mock_handler)
    pool._event_session_ready.set()

    wire_order: list[tuple[int, str]] = []
    w0_in_flight = asyncio.Event()
    release_w0 = asyncio.Event()

    class FakeSession:
        def __init__(self, idx: int) -> None:
            self.idx = idx
            self._stream_reader: object | None = object()
            self._stream_writer: object | None = object()

        async def connect(self) -> dict[str, Any]:
            self._stream_reader = object()
            self._stream_writer = object()
            return {"Success": True}

        async def send(self, message: Any, is_status_request: bool = False) -> list[Any]:
            raw = str(message)
            if self.idx == 0 and raw == "*2*1*11##":
                w0_in_flight.set()
                await release_w0.wait()
            wire_order.append((self.idx, raw))
            return []

        async def close(self) -> None:
            self._stream_reader = None
            self._stream_writer = None

    sessions = iter([FakeSession(0), FakeSession(1)])

    with patch(
        "custom_components.myhome.gateway.OWNCommandSession",
        side_effect=lambda **_: next(sessions),
    ):
        w0 = asyncio.create_task(pool.sending_loop(0))
        w1 = asyncio.create_task(pool.sending_loop(1))
        await asyncio.sleep(0)

        f_11 = await pool.send(OWNCommand("*2*1*11##"))
        await w0_in_flight.wait()

        # Area 1 waits behind in-flight 11, holding back independent covers 12 and 13 on Worker 1.
        f_area = await pool.send(OWNCommand("*2*1*1##"))
        f_12 = await pool.send(OWNCommand("*2*2*12##"))
        f_13 = await pool.send(OWNCommand("*2*2*13##"))
        await asyncio.sleep(0.02)
        assert wire_order == []

        # Normal stop (default is_cancel=False) drops *2*1*1## and wakes Worker 1 immediately.
        f_stop_area = await pool.send(OWNCommand("*2*0*1##"))
        assert f_area.cancelled()

        await asyncio.wait_for(asyncio.gather(f_12, f_13), timeout=1.0)
        assert [frame for _, frame in wire_order] == ["*2*2*12##", "*2*2*13##"]
        assert not f_stop_area.done()

        release_w0.set()
        await asyncio.wait_for(asyncio.gather(f_11, f_stop_area), timeout=1.0)

        await pool.send_buffer.put(None)
        await pool.send_buffer.put(None)
        await asyncio.wait_for(asyncio.gather(w0, w1), timeout=1.0)

    assert [frame for _, frame in wire_order] == [
        "*2*2*12##",
        "*2*2*13##",
        "*2*1*11##",
        "*2*0*1##",
    ]
