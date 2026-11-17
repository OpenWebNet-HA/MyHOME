"""Issue #380: cover position must not advance when its direction frame never reached the bus.

Tests the fix ensuring that when a direction frame is cancelled, fails with an exception,
or times out in the send queue:
1. The motion estimate is immediately aborted.
2. The scheduled auto-stop task exits without updating position and without sending a STOP frame.
3. is_opening / is_closing attributes revert to False.
4. Physical MH200 plant covers (with F422 interface addresses like 11#4#02) are used as golden samples.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_POSITION
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNEvent

from custom_components.myhome.cover import MyHOMECover


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake_time(clock):
    """Replace cover.time with a controllable monotonic clock."""
    with patch("custom_components.myhome.cover.time") as mock_time:
        mock_time.monotonic.side_effect = lambda: clock.now
        yield mock_time


_REAL_SLEEP = asyncio.sleep


@pytest.fixture
def sleeps(clock):
    """Record requested sleep durations and advance the fake clock."""
    recorded = []

    async def fake_sleep(delay, *args, **kwargs):
        if delay > 0:
            recorded.append(delay)
            clock.now += delay
        await _REAL_SLEEP(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=fake_sleep):
        yield recorded


async def _yield(n: int = 5):
    for _ in range(n):
        await _REAL_SLEEP(0)


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:02:00"
    gw.log_id = "[MH200 gateway - test]"
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.device_registry_id = None
    gw.send_status_request = AsyncMock()
    gw.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        gw.deliveries.append((str(message), fut))
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


@pytest.fixture
def mh200_cover_11i02(hass, gateway):
    """Physical MH200 plant cover with F422 interface: where='11', interface='02' -> 11#4#02."""
    c = MyHOMECover(
        hass=hass,
        name="Cover 11I02",
        entity_name=None,
        device_id="11#4#02",
        who="2",
        where="11",
        interface="02",
        advanced=False,
        manufacturer="BTicino",
        model="MH200 Cover",
        gateway=gateway,
        travel_time=25,
    )
    c.hass = hass
    c.async_write_ha_state = MagicMock()
    c.async_schedule_update_ha_state = MagicMock()
    c._attr_current_cover_position = 100
    c._start_position = 100
    return c


@pytest.fixture
def mh200_cover_85(hass, gateway):
    """Physical MH200 plant cover without interface: where='85'."""
    c = MyHOMECover(
        hass=hass,
        name="Cover 85",
        entity_name=None,
        device_id="85",
        who="2",
        where="85",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="MH200 Cover",
        gateway=gateway,
        travel_time=20,
    )
    c.hass = hass
    c.async_write_ha_state = MagicMock()
    c.async_schedule_update_ha_state = MagicMock()
    c._attr_current_cover_position = 0
    c._start_position = 0
    c._attr_is_closed = True
    return c


# ── Issue #380 Reproduction Tests ──────────────────────────────────────────


async def test_issue_380_position_does_not_advance_when_direction_frame_cancelled(
    mh200_cover_11i02, gateway, clock, fake_time, sleeps
):
    """Exact Issue #380 sequence:
    1. Cover is at 100%.
    2. User requests position 25%.
    3. Direction frame *2*2*11#4#02## is queued.
    4. Stale socket / disconnect causes the delivery future to be cancelled.
    5. Time elapses beyond the run duration (18.75 s).
    6. Cover must remain at 100%, is_closing must be False, and no STOP frame must be sent.
    """
    cover = mh200_cover_11i02
    assert cover.current_cover_position == 100

    await cover.async_set_cover_position(**{ATTR_POSITION: 25})

    assert len(gateway.deliveries) == 1
    direction_frame, written = gateway.deliveries[0]
    assert direction_frame == "*2*2*11#4#02##"

    # Simulate delivery cancellation (e.g. queue flushed on disconnect)
    written.cancel()
    clock.now = 30.0  # Well past the 18.75s run duration

    with patch("custom_components.myhome.cover.ECHO_WINDOW", 0.0):
        await _yield(10)

    # Position MUST NOT advance to 25%
    assert cover.current_cover_position == 100
    assert cover.is_closing is False
    assert cover.is_opening is False
    assert cover.is_closed is False
    assert cover._move_start_time is None
    assert cover._stop_task is None

    # NO stop frame (*2*0*11#4#02##) should be queued
    assert len(gateway.deliveries) == 1
    assert [f for f, _ in gateway.deliveries] == ["*2*2*11#4#02##"]


async def test_issue_380_position_does_not_advance_when_direction_frame_times_out(
    mh200_cover_85, gateway, clock, fake_time, sleeps
):
    """When the direction frame delivery times out past WRITE_TIMEOUT:
    Motion must abort, position must stay at 0%, and no stop frame is sent.
    """
    cover = mh200_cover_85
    assert cover.current_cover_position == 0
    assert cover.is_closed is True

    with patch("custom_components.myhome.cover.WRITE_TIMEOUT", 0.05), patch("custom_components.myhome.cover.ECHO_WINDOW", 0.0):
        await cover.async_set_cover_position(**{ATTR_POSITION: 80})
        assert len(gateway.deliveries) == 1
        assert gateway.deliveries[0][0] == "*2*1*85##"
        _, written = gateway.deliveries[0]

        try:
            await asyncio.wait_for(asyncio.Event().wait(), 0.3)
        except TimeoutError:
            pass

    assert not written.done()
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover.is_opening is False
    assert cover.is_closing is False
    assert cover._move_start_time is None
    # Only the initial direction frame was enqueued, no stop frame
    assert [f for f, _ in gateway.deliveries] == ["*2*1*85##"]


async def test_issue_380_position_does_not_advance_on_write_exception(
    mh200_cover_11i02, gateway, clock, fake_time, sleeps
):
    """When the delivery future fails with a socket error:
    Motion must abort immediately, position must not advance, and no stop is queued.
    """
    cover = mh200_cover_11i02
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})

    _, written = gateway.deliveries[0]
    written.set_exception(ConnectionResetError("MH200 gateway connection reset"))

    await _yield(5)

    assert cover.current_cover_position == 100
    assert cover.is_closing is False
    assert cover.is_opening is False
    assert cover._move_start_time is None
    assert [f for f, _ in gateway.deliveries] == ["*2*2*11#4#02##"]


async def test_issue_380_open_cover_aborts_motion_on_cancelled_delivery(
    mh200_cover_85, gateway, clock, fake_time
):
    """Calling async_open_cover when delivery is cancelled reverts is_opening to False."""
    cover = mh200_cover_85
    assert cover.is_closed is True

    await cover.async_open_cover()
    assert cover.is_opening is True

    _, written = gateway.deliveries[0]
    written.cancel()
    await _yield(5)

    assert cover.is_opening is False
    assert cover.is_closed is True
    assert cover._move_start_time is None


async def test_issue_380_close_cover_aborts_motion_on_write_exception(
    mh200_cover_11i02, gateway, clock, fake_time
):
    """Calling async_close_cover when delivery fails with an exception reverts is_closing to False."""
    cover = mh200_cover_11i02
    assert cover.current_cover_position == 100

    await cover.async_close_cover()
    assert cover.is_closing is True

    _, written = gateway.deliveries[0]
    written.set_exception(OSError("Network unreachable"))
    await _yield(5)

    assert cover.is_closing is False
    assert cover.current_cover_position == 100
    assert cover._move_start_time is None


async def test_issue_380_pre_cancelled_delivery_raises_immediately(
    mh200_cover_11i02, gateway
):
    """If send returns an already cancelled future, async_move raises HomeAssistantError immediately."""
    loop = asyncio.get_running_loop()
    cancelled_fut = loop.create_future()
    cancelled_fut.cancel()

    gateway.send = AsyncMock(return_value=cancelled_fut)
    cover = mh200_cover_11i02

    with pytest.raises(HomeAssistantError, match="delivery was cancelled"):
        await cover.async_close_cover()

    assert cover.is_closing is False
    assert cover._move_start_time is None


async def test_issue_380_pre_failed_delivery_raises_immediately(
    mh200_cover_11i02, gateway
):
    """If send returns an already errored future, async_move raises HomeAssistantError immediately."""
    loop = asyncio.get_running_loop()
    failed_fut = loop.create_future()
    failed_fut.set_exception(RuntimeError("Send queue full"))

    gateway.send = AsyncMock(return_value=failed_fut)
    cover = mh200_cover_11i02

    with pytest.raises(HomeAssistantError, match="delivery failed"):
        await cover.async_open_cover()

    assert cover.is_opening is False
    assert cover._move_start_time is None


async def test_issue_380_calibration_aborts_immediately_on_write_failure(
    mh200_cover_11i02, gateway
):
    """Calibration must fail immediately without hanging for 60 seconds if direction frame fails."""
    cover = mh200_cover_11i02

    cal_task = asyncio.create_task(cover.async_calibrate())
    await _yield(3)

    assert len(gateway.deliveries) == 1
    _, written = gateway.deliveries[0]
    written.cancel()

    with pytest.raises(HomeAssistantError, match="delivery was cancelled"):
        await cal_task

    assert cover._calibrating is False
    assert cover.is_opening is False
    assert cover.is_closing is False


async def test_issue_380_normal_delivery_and_stop_remains_intact(
    mh200_cover_11i02, gateway, clock, fake_time, sleeps
):
    """Verify that normal command delivery and auto-stop continues to work smoothly."""
    cover = mh200_cover_11i02
    assert cover.current_cover_position == 100

    clock.now = 1000.0
    await cover.async_set_cover_position(**{ATTR_POSITION: 50})

    assert len(gateway.deliveries) == 1
    _, written = gateway.deliveries[0]

    # Frame is successfully written at 1000.2
    clock.now = 1000.2
    written.set_result(clock.now)
    await _yield(3)

    # Motor start echo arrives at 1000.55
    clock.now = 1000.55
    cover.handle_event(OWNEvent.parse("*2*2*11#4#02##"))
    assert cover._motor_started.is_set()
    await _yield()

    # Travel time is 25s, 50% = 12.5s duration from 1000.55 -> target at 10013.05
    assert sleeps and sleeps[-1] == pytest.approx(12.5)

    # Stop frame should be sent at the end of auto-stop
    await _yield(5)
    assert len(gateway.deliveries) == 2
    assert gateway.deliveries[1][0] == "*2*0*11#4#02##"
    assert cover.current_cover_position == 50
