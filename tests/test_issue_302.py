"""Issue #302: timed covers anchored on the real write / motor start (MyHOMEServer1 echo model).

Sequence measured by the reporter after a direction command is queued:
  enqueue -> (queue wait, up to 1.6 s with 12 covers) -> frame written
  +0.10 s  gateway relays a real stop status  *2*0*<where>##   (not a translation)
  +0.15 s  translation                          *2*1000#<dir>*<where>##
  +0.55 s  motor starts, direction status       *2*<dir>*<where>##
  stop command: motor stops 0.08 s after the stop frame is written.
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION
from OWNd.message import OWNEvent

from custom_components.myhome.cover import (
    ECHO_WINDOW,
    MOTOR_START_DELAY,
    WRITE_TIMEOUT,
    MyHOMECover,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake_time(clock):
    """Replace cover.time with a controllable monotonic clock (asyncio's own clock untouched)."""
    with patch("custom_components.myhome.cover.time") as mock_time:
        mock_time.monotonic.side_effect = lambda: clock.now
        yield mock_time


_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def sleeps(clock):
    """Record requested sleep durations and advance the fake clock instead of waiting."""
    recorded = []

    async def fake_sleep(delay, *args, **kwargs):
        if delay > 0:
            recorded.append(delay)
            clock.now += delay
        await _REAL_SLEEP(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=fake_sleep):
        yield recorded


async def _yield(n: int = 3):
    for _ in range(n):
        await _REAL_SLEEP(0)  # real sleep: unpatched reference so yielding does not pollute the fake-sleep trace


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:01"
    gw.log_id = "[MyHOMEServer1 gateway - test]"
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.device_registry_id = None
    gw.send_status_request = AsyncMock()
    # every send() hands back a fresh, unresolved delivery future
    gw.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        gw.deliveries.append((str(message), fut))
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


@pytest.fixture
def cover(hass, gateway):
    c = MyHOMECover(
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
        gateway=gateway,
        travel_time=10,
    )
    c.hass = hass
    c.async_write_ha_state = MagicMock()
    c.async_schedule_update_ha_state = MagicMock()
    c._attr_current_cover_position = 100
    c._start_position = 100
    return c


async def test_set_position_survives_relayed_stop_and_anchors_on_motor_start(cover, gateway, clock, fake_time, sleeps):
    """The relayed stop must not cancel the run; the timer starts at the motor-start echo."""
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})  # 50% of 10 s = 5 s run
    assert cover.is_closing is True
    assert cover._pending_cmd == "close"
    assert cover._move_start_time is None  # clock does not start at enqueue
    assert cover._stop_task is not None
    await _yield()

    # Queue was busy: frame leaves 1.6 s after enqueue
    clock.now = 1.6
    frame, written = gateway.deliveries[0]
    assert frame == "*2*2*21##"
    written.set_result(1.6)
    await _yield()
    assert cover._move_start_time == 1.6  # provisional anchor at write
    assert cover._echo_until == pytest.approx(1.6 + ECHO_WINDOW)

    # +0.10 s: gateway relays a REAL stop status for our own command
    clock.now = 1.7
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover._stop_task is not None and not cover._stop_task.done()
    assert cover.is_closing is True

    # +0.15 s: translation frame (always ignored)
    clock.now = 1.75
    cover.handle_event(OWNEvent.parse("*2*1000#2*21##"))
    assert cover.is_closing is True

    # +0.55 s: motor really starts
    clock.now = 2.15
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    assert cover._move_start_time == 2.15
    assert cover._pending_cmd is None  # window closed: later frames are genuine
    assert cover._motor_started.is_set()
    await _yield()

    # Auto-stop slept the full run from the motor start, not from enqueue
    assert [s for s in sleeps if s][-1] == pytest.approx(5.0)  # last real sleep is the 5 s run
    await _yield()
    assert clock.now == pytest.approx(7.15)
    stop_frame, stop_written = gateway.deliveries[-1]
    assert stop_frame == "*2*0*21##"
    # Until the stop frame is written the motor is still running past the target
    assert cover.is_closing is True
    assert cover.current_cover_position == 50

    # Stop leaves the queue 0.1 s later: the estimate settles at target + overshoot
    clock.now = 7.25
    stop_written.set_result(7.25)
    await _yield()
    assert cover.is_closing is False
    assert cover._move_start_time is None
    assert cover._attr_current_cover_position == 49

    # ... and the relayed stop confirmation changes nothing
    clock.now = 7.35
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover._attr_current_cover_position == 49


async def test_position_estimate_frozen_at_stop_write_not_enqueue(cover, gateway, clock, fake_time):
    """Stop latency is the queue wait plus 0.08 s: freeze the estimate when the stop is written."""
    cover._attr_current_cover_position = 0
    cover._start_position = 0
    await cover.async_open_cover()
    _, written = gateway.deliveries[0]
    clock.now = 1.0
    written.set_result(1.0)
    await _yield()
    clock.now = 1.5
    cover.handle_event(OWNEvent.parse("*2*1*21##"))  # motor start
    assert cover._move_start_time == 1.5

    clock.now = 4.5  # user presses stop in HA: 3 s of travel so far (30 %)
    await cover.async_stop_cover()
    assert cover._pending_cmd == "stop"
    assert cover.current_cover_position == 30  # still interpolating until written
    _, stop_written = gateway.deliveries[1]
    clock.now = 5.5  # busy queue: stop frame leaves 1 s later -> motor ran 4 s
    stop_written.set_result(5.5)
    await _yield()
    assert cover._attr_current_cover_position == 40
    assert cover._move_start_time is None
    assert cover.is_opening is False

    # The relayed stop confirmation is then a no-op, not a second freeze
    clock.now = 5.6
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover._attr_current_cover_position == 40


async def test_opposite_direction_inside_window_is_external(cover, gateway, clock, fake_time, sleeps):
    """Someone closing from the keypad right after our open takes over the run."""
    cover._attr_current_cover_position = 0
    cover._start_position = 0
    await cover.async_set_cover_position(**{ATTR_POSITION: 60})
    generation = cover._run_generation
    _, written = gateway.deliveries[0]
    clock.now = 0.5
    written.set_result(0.5)
    await _yield()

    clock.now = 0.8
    cover.handle_event(OWNEvent.parse("*2*2*21##"))  # closing, not our direction
    assert cover.is_closing is True and cover.is_opening is False
    assert cover._pending_cmd is None
    assert cover._stop_task is None
    assert cover._run_generation > generation


async def test_stale_auto_stop_generation_is_a_noop(cover, gateway, clock, fake_time, sleeps):
    """A timer from a previous run never stops a newer one."""
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    first_task = cover._stop_task
    _, written = gateway.deliveries[0]
    written.set_result(0.2)
    await _yield()
    # A second command supersedes the first before its timer fires
    await cover.async_open_cover()
    assert first_task.cancelled() or first_task.done() or cover._stop_task is None
    await _yield()
    # Only the direction frames were sent: no stop from the stale run
    assert [f for f, _ in gateway.deliveries] == ["*2*2*21##", "*2*1*21##"]


async def test_echo_window_bounded_without_delivery_info(cover, clock, fake_time):
    """A gateway object whose send() returns no future still gets a bounded echo window."""
    cover._gateway_handler.send = AsyncMock(return_value=None)
    await cover.async_open_cover()
    assert cover._echo_until == pytest.approx(clock.now + ECHO_WINDOW)
    clock.now = ECHO_WINDOW + 0.1
    cover.handle_event(OWNEvent.parse("*2*0*21##"))  # after the window: genuine stop
    assert cover.is_opening is False

    # stop without delivery info freezes immediately
    cover._move_start_time = clock.now
    cover._attr_is_opening = True
    await cover.async_stop_cover()
    assert cover._move_start_time is None


async def test_motion_anchor_falls_back_when_write_is_cancelled(cover, gateway, clock, fake_time, sleeps):
    """Gateway shutdown cancels the delivery: the run anchors on 'now' instead of hanging."""
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    _, written = gateway.deliveries[0]
    written.cancel()
    clock.now = 3.0
    with patch("custom_components.myhome.cover.ECHO_WINDOW", 0.0):
        await _yield(5)
    assert cover._move_start_time is not None


# ── gateway side: the delivery future ────────────────────────────────────


@pytest.fixture
def handler():
    entry = MagicMock()
    entry.entry_id = "e302"
    entry.data = {"host": "1.2.3.4", "port": 20000, "password": "x", "mac": "00:03:50:00:00:01", "name": "MyHomeServer1"}
    entry.options = {}
    hass = MagicMock()
    hass.data = {}
    return MyHOMEGatewayHandler(hass, entry)


async def test_send_future_resolves_at_write(handler):
    """send() returns a future completed with the monotonic write time by the worker."""
    import time as _time

    handler._event_session_ready.set()
    before = _time.monotonic()
    # NB: never patch time.monotonic here - asyncio's loop clock is the same function.
    with patch("custom_components.myhome.gateway.OWNCommandSession") as session_cls:
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.is_connected = True
        session.send = AsyncMock(return_value=[])
        session.close = AsyncMock()
        session_cls.return_value = session

        from OWNd.message import OWNAutomationCommand

        written = await handler.send(OWNAutomationCommand.raise_shutter("21"))
        assert isinstance(written, asyncio.Future) and not written.done()
        status = await handler.send_status_request(OWNAutomationCommand.status("21"))
        await handler.send_buffer.put(None)  # stop the worker after draining
        await handler.sending_loop(0)

    assert before <= written.result() <= status.result() <= _time.monotonic()
    session.send.assert_awaited()


async def test_close_listener_cancels_undelivered_futures(handler):
    """Frames still queued at shutdown will never be written: their futures are cancelled."""
    from OWNd.message import OWNAutomationCommand

    written = await handler.send(OWNAutomationCommand.raise_shutter("21"))
    assert await handler.close_listener() is True
    assert written.cancelled()
    # Only the worker sentinels remain in the queue
    remaining = []
    while not handler.send_buffer.empty():
        remaining.append(handler.send_buffer.get_nowait())
    assert remaining and all(item is None for item in remaining)


# ── edge paths ───────────────────────────────────────────────────────────


async def test_no_motor_echo_anchors_on_write_time(cover, gateway, clock, fake_time, sleeps):
    """Gateways that never relay the direction status: the run anchors on the write
    plus the measured motor-start delay, so the run is not ~0.55 s too long."""
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    _, written = gateway.deliveries[0]
    clock.now = 1.0
    written.set_result(1.0)
    with patch("custom_components.myhome.cover.ECHO_WINDOW", 0.05):
        # the window (0.05 s real, since the fake clock stands still) elapses with
        # no echo; wait on a timer, not asyncio.sleep (faked by the fixture)
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 0.3)
        except TimeoutError:
            pass
    # The 5 s run was measured from the motor start (write + MOTOR_START_DELAY), then the stop was queued
    assert [s for s in sleeps if s][-1] == pytest.approx(5.0 + MOTOR_START_DELAY)  # last real sleep is the run
    assert [f for f, _ in gateway.deliveries] == ["*2*2*21##", "*2*0*21##"]


async def test_generation_guard_before_and_after_sleep(cover, gateway, clock, fake_time):
    """A run superseded while waiting (anchor or sleep) never sends its stop."""
    # 1. superseded while waiting for the motion anchor
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    _, written = gateway.deliveries[0]
    cover._run_generation += 1  # e.g. a manual command handled elsewhere
    cover._motor_started.set()  # anchor available immediately
    written.set_result(0.1)
    await _yield(5)
    assert cover._stop_task.done()
    assert [f for f, _ in gateway.deliveries] == ["*2*2*21##"]

    # 2. superseded during the sleep
    cover._stop_task = None
    real_sleep = asyncio.sleep

    async def bump_generation_sleep(delay, *args, **kwargs):
        cover._run_generation += 1
        await real_sleep(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=bump_generation_sleep):
        await cover.async_set_cover_position(**{ATTR_POSITION: 20})
        _, written2 = gateway.deliveries[-1]
        cover._motor_started.set()
        written2.set_result(0.2)
        for _ in range(5):
            await real_sleep(0)
    assert cover._stop_task.done()
    assert [f for f, _ in gateway.deliveries] == ["*2*2*21##", "*2*2*21##"]


async def test_echo_path_tolerates_state_write_runtime_error(cover, gateway, clock, fake_time):
    """Echo handling survives async_schedule_update_ha_state raising (entity being removed)."""
    await cover.async_open_cover()
    cover.async_schedule_update_ha_state = MagicMock(side_effect=RuntimeError("no loop"))
    cover.handle_event(OWNEvent.parse("*2*0*21##"))  # echo inside the window
    assert cover.is_opening is True


# ── review of #318 ───────────────────────────────────────────────────────


async def test_write_timeout_covers_the_measured_queue():
    """#302: with twelve covers the last frame goes out up to 12 s after enqueue."""
    assert WRITE_TIMEOUT >= 12.0


async def test_echo_window_is_bounded_from_enqueue_and_closes_when_delivery_fails(cover, gateway, clock, fake_time):
    """A frame that never reaches the bus must not leave the window open: the next stop is real."""
    clock.now = 100.0
    await cover.async_open_cover()
    assert cover._echo_until == pytest.approx(100.0 + WRITE_TIMEOUT + ECHO_WINDOW)  # bounded, not infinite
    _, written = gateway.deliveries[0]
    written.cancel()
    await _yield()
    assert cover._echo_until is None and cover._pending_cmd is None
    # the wall switch stops the cover: handled, not swallowed as an echo
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.is_opening is False

    # the same when delivery fails with an exception
    clock.now = 200.0
    await cover.async_close_cover()
    _, written = gateway.deliveries[1]
    written.set_exception(RuntimeError("socket gone"))
    await _yield()
    assert cover._echo_until is None
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.is_closing is False


async def test_write_that_never_happens_times_the_run_from_now(cover, gateway, clock, fake_time, sleeps):
    """The wait for the write is bounded: past WRITE_TIMEOUT the run is timed from now
    and the echo window is closed, so a later stop status is handled as real."""
    clock.now = 10.0
    with patch("custom_components.myhome.cover.WRITE_TIMEOUT", 0.05), patch("custom_components.myhome.cover.ECHO_WINDOW", 0.0):
        await cover.async_set_cover_position(**{ATTR_POSITION: 50})
        _, written = gateway.deliveries[0]
        try:
            await asyncio.wait_for(asyncio.Event().wait(), 0.3)  # real timer: the fake clock stands still
        except TimeoutError:
            pass
    assert not written.done()  # still stuck in the queue ...
    assert sleeps and sleeps[-1] == pytest.approx(5.0)  # ... the run measured from "now"
    assert [f for f, _ in gateway.deliveries] == ["*2*2*21##", "*2*0*21##"]
    # the open command's window (10.05) is gone; the one left is the stop's own, opened at its enqueue
    assert cover._echo_until == pytest.approx(clock.now + 0.05)


async def test_write_confirmation_repaints_the_estimate(cover, gateway, clock, fake_time):
    """The clock starts / freezes at the write: the state is written then, without waiting for a status frame."""
    cover.platform = MagicMock()
    cover.entity_id = "cover.shutter_21"
    await cover.async_close_cover()
    _, written = gateway.deliveries[0]
    cover.async_schedule_update_ha_state.reset_mock()
    clock.now = 2.0
    written.set_result(2.0)
    await _yield()
    assert cover._move_start_time == 2.0
    cover.async_schedule_update_ha_state.assert_called()

    cover.async_schedule_update_ha_state.reset_mock()
    clock.now = 4.0
    await cover.async_stop_cover()
    _, written = gateway.deliveries[1]
    written.set_result(4.0)
    await _yield()
    assert cover._move_start_time is None  # frozen at the write
    cover.async_schedule_update_ha_state.assert_called()


async def test_cancelling_the_run_task_really_stops_it(cover, gateway, clock, fake_time, sleeps):
    """Cancellation of the auto-stop task (a newer command, entity removal) is not swallowed."""
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})
    task = cover._stop_task
    _, written = gateway.deliveries[0]
    await _yield()  # the task is now waiting for the write
    await cover.async_will_remove_from_hass()
    await _yield()
    assert task.cancelled() or task.done()
    generation = cover._run_generation
    written.set_result(1.0)  # the frame goes out after all: nothing must act on it
    await _yield(5)
    assert [f for f, _ in gateway.deliveries] == ["*2*2*21##"]  # no stop was queued
    assert cover._run_generation == generation


def _ownd_like_session(session_cls, send_results, connect_results=None):
    """A command session that behaves like OWNd 2.0.0b6: ``close()`` drops the
    streams and leaves ``is_connected`` as ``connect()`` last set it; ``send()``
    reopens the streams itself when they are gone. ``connect_results`` lists the
    result of each ``connect()`` call (default: success)."""
    session = MagicMock()
    session._stream_reader = session._stream_writer = None
    session.is_connected = False
    connected_at: list[float] = []
    connects = list(connect_results or [])

    async def _connect():
        await asyncio.sleep(0.05)  # the TCP open + handshake
        result = connects.pop(0) if connects else {"Success": True}
        if isinstance(result, dict) and result.get("Success"):
            session._stream_reader = session._stream_writer = object()
            session.is_connected = True
        connected_at.append(time.monotonic())
        return result

    async def _close():
        session._stream_reader = session._stream_writer = None  # is_connected stays as it was

    async def _send(**_kwargs):
        if session._stream_writer is None:  # what OWNd's send() does with a closed session
            await _connect()
        return send_results.pop(0)

    session.connect = AsyncMock(side_effect=_connect)
    session.close = AsyncMock(side_effect=_close)
    session.send = AsyncMock(side_effect=_send)
    session_cls.return_value = session
    return session, connected_at


async def test_send_future_is_stamped_after_reconnect_and_cancelled_on_failure(handler):
    """After the session is closed the worker itself reopens the session and only then
    takes the write time: OWNd's ``send()`` would reconnect *after* our stamp, and
    ``is_connected`` still reads True after ``close()``. A frame OWNd could not
    deliver cancels its future instead of starting a run."""
    handler._event_session_ready.set()
    with patch("custom_components.myhome.gateway.OWNCommandSession") as session_cls:
        session, connected_at = _ownd_like_session(session_cls, send_results=[True, True, None])
        from OWNd.message import OWNAutomationCommand

        first = await handler.send(OWNAutomationCommand.raise_shutter("21"))
        worker = asyncio.ensure_future(handler.sending_loop(0))
        await first
        await session.close()  # simulate gateway-side disconnect
        assert session.close.await_count == 1 and session.is_connected  # closed, flag still True
        assert session.connect.await_count == 1

        after_reconnect = await handler.send(OWNAutomationCommand.lower_shutter("21"))
        undelivered = await handler.send(OWNAutomationCommand.stop_shutter("21"))
        await handler.send_buffer.put(None)
        await worker

    assert session.connect.await_count == 2  # start-up, then the worker's explicit reopen
    assert first.result() >= connected_at[0]
    assert after_reconnect.result() >= connected_at[1]  # stamped after the handshake, not before it
    assert undelivered.cancelled()


async def test_worker_gives_up_on_a_frame_when_reconnect_fails(handler):
    """``connect()`` returning None (gateway unreachable after its retries) is not
    followed by ``send()`` running the same cycle again: the frame's future is
    cancelled and the next frame gets its own chance."""
    handler._event_session_ready.set()
    with patch("custom_components.myhome.gateway.OWNCommandSession") as session_cls:
        session, _ = _ownd_like_session(session_cls, send_results=[True, True], connect_results=[{"Success": True}, None])
        from OWNd.message import OWNAutomationCommand

        first = await handler.send(OWNAutomationCommand.raise_shutter("21"))
        worker = asyncio.ensure_future(handler.sending_loop(0))
        await first
        await session.close()  # simulate gateway-side disconnect

        dropped = await handler.send(OWNAutomationCommand.lower_shutter("21"))
        delivered = await handler.send(OWNAutomationCommand.stop_shutter("21"))
        await handler.send_buffer.put(None)
        await worker

    assert dropped.cancelled()
    assert delivered.done() and not delivered.cancelled()
    assert session.send.await_count == 2  # send() never ran for the dropped frame


async def test_worker_terminates_when_the_gateway_refuses_the_reconnect(handler):
    """A refused negotiation on the reopen (wrong password) terminates the worker
    as the start-up path does, instead of negotiating again on every frame."""
    handler._event_session_ready.set()
    with patch("custom_components.myhome.gateway.OWNCommandSession") as session_cls:
        session, _ = _ownd_like_session(
            session_cls, send_results=[True], connect_results=[{"Success": True}, {"Success": False, "Message": "password_error"}]
        )
        from OWNd.message import OWNAutomationCommand

        first = await handler.send(OWNAutomationCommand.raise_shutter("21"))
        worker = asyncio.ensure_future(handler.sending_loop(0))
        await first
        await session.close()  # simulate gateway-side disconnect

        refused = await handler.send(OWNAutomationCommand.lower_shutter("21"))
        await asyncio.wait_for(worker, 2)  # returned on its own: no sentinel was queued

    assert refused.cancelled()
    assert session.send.await_count == 1

