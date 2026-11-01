"""End of run of a modelled timed cover: percentage endpoints and the restored model state.

The bus timeline is the one recorded on a MyHOMEServer1 with F411-type actuators: a
direction frame is answered by a stop status (~0.08 s) and the direction status
(~0.57 s, the motor start); the actuator reports nothing at the physical end stop and
sends its stop status only when its relay timer expires, 60 s after the start (120 s
with M=1). The cover in these tests closes in 17 s and opens in 20 s.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNMessage

from tests.test_cover_nonlinear_backend import nonlinear
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_cover_travel_scaling import write

plant = plant_fixture

REAL_SLEEP = asyncio.sleep
CLOSE, OPEN, STOP = "*2*2*11##", "*2*1*11##", "*2*0*11##"
CLOSING_S, OPENING_S = 17, 20
RELAYED_STOP_S, MOTOR_START_S = 0.08, 0.57  # after the write, from the bus traces
TIMER_S = 60.0  # the actuator's relay timer: its only stop status for a full run


async def settle(rounds=12):
    for _ in range(rounds):
        await REAL_SLEEP(0)


class BusClock:
    """Monotonic time for the cover module; its asyncio.sleep waits for `advance`."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleepers: list[list] = []

    async def sleep(self, delay, *args, **kwargs):
        if delay <= 0:
            await REAL_SLEEP(0)
            return
        entry = [self.now + delay, asyncio.get_running_loop().create_future()]
        self.sleepers.append(entry)
        try:
            await entry[1]
        finally:
            if entry in self.sleepers:
                self.sleepers.remove(entry)

    async def advance(self, seconds):
        target = self.now + seconds
        while True:
            await settle()
            due = [entry for entry in self.sleepers if entry[0] <= target and not entry[1].done()]
            if not due:
                break
            entry = min(due, key=lambda item: item[0])
            self.sleepers.remove(entry)
            self.now = max(self.now, entry[0])
            entry[1].set_result(None)
        self.now = target
        await settle()


@pytest.fixture
async def modelled(hass, plant):
    await write(hass, plant, profile=nonlinear(opening_time=OPENING_S, closing_time=CLOSING_S,
                                               reference_travel_cm=None))
    cover = plant.covers[0]
    assert cover._motion.model is not None and cover._motion.model.closing_time_s == CLOSING_S
    cover.platform = MagicMock()  # _publish_state writes through async_schedule_update_ha_state
    clock = BusClock()
    sent: list[tuple[str, asyncio.Future]] = []

    async def send(command):
        # Written at once (an idle queue); the tests play the gateway's relayed frames.
        future = asyncio.get_running_loop().create_future()
        future.set_result(clock.now)
        sent.append((str(command), future))
        return future

    plant.gateways[0].send = AsyncMock(side_effect=send)
    with patch("custom_components.myhome.cover.time") as cover_time, \
         patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock.now), \
         patch("custom_components.myhome.cover.asyncio.sleep", side_effect=clock.sleep):
        cover_time.monotonic.side_effect = lambda: clock.now
        yield SimpleNamespace(cover=cover, clock=clock, sent=sent, plant=plant, hass=hass)
        cover._cancel_stop_task()
        await settle()


def bus(m, raw):
    m.cover.handle_event(OWNMessage.parse(raw))


def at_end(m, height):
    """Put the cover at a confirmed end, as a previous full run leaves it."""
    m.cover._motion.confirm(height)
    m.cover._attr_current_cover_position = m.cover._start_position = height
    m.cover._attr_is_closed = height == 0


async def written_and_started(m, raw):
    """The frame HA queued reaches the bus; the gateway relays a stop status, then the motor start."""
    await settle()
    assert m.sent[-1][0] == raw
    await m.clock.advance(RELAYED_STOP_S)
    bus(m, STOP)
    await m.clock.advance(MOTOR_START_S - RELAYED_STOP_S)
    bus(m, raw)
    return m.clock.now  # motor start


async def wall_run(m, raw):
    """A keypad press: the same relayed frames, no frame from Home Assistant."""
    bus(m, STOP)
    await m.clock.advance(MOTOR_START_S - RELAYED_STOP_S)
    bus(m, raw)
    return m.clock.now


def state(m):
    return m.cover.state, m.cover.current_cover_position


async def until(m, motor_start, seconds):
    await m.clock.advance(motor_start + seconds - m.clock.now)


def commands(m):
    return [command for command, _ in m.sent]
