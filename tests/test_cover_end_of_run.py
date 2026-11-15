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

from custom_components.myhome.cover_motion import END_SETTLE_MARGIN, MotionPosition
from custom_components.myhome.cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover
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


# set_cover_position 0 / 100: full runs, as open / close.

@pytest.mark.parametrize("known", [True, False], ids=["from_known", "from_unknown"])
async def test_position_zero_is_a_full_run_that_settles_at_the_model_end(modelled, known):
    m = modelled
    if known:
        at_end(m, 100)
    await m.cover.async_set_cover_position(position=0)
    start = await written_and_started(m, CLOSE)
    await until(m, start, CLOSING_S - 1)
    assert m.cover.state == "closing"
    await until(m, start, CLOSING_S + END_SETTLE_MARGIN + 0.1)
    assert state(m) == ("closed", 0)
    assert m.cover._motion.position == MotionPosition(0, 0)
    assert commands(m) == [CLOSE]  # a full run ends on the limit switch: no Stop is written


@pytest.mark.parametrize("known", [True, False], ids=["from_known", "from_unknown"])
async def test_position_hundred_is_a_full_run_that_settles_at_the_model_end(modelled, known):
    m = modelled
    if known:
        at_end(m, 0)
    await m.cover.async_set_cover_position(position=100)
    start = await written_and_started(m, OPEN)
    await until(m, start, OPENING_S + END_SETTLE_MARGIN + 0.1)
    assert state(m) == ("open", 100)
    assert commands(m) == [OPEN]


async def test_position_zero_on_the_sill_closes_the_slats_with_a_full_run(modelled):
    """Height 0 with the slats still open: 0 % owes the closure of the slats, run to the limit switch."""
    m = modelled
    at_end(m, 0)
    m.cover._motion.position = MotionPosition(0, 0.4)
    await m.cover.async_set_cover_position(position=0)
    start = await written_and_started(m, CLOSE)
    await until(m, start, CLOSING_S + END_SETTLE_MARGIN + 0.1)
    assert state(m) == ("closed", 0)
    assert m.cover._motion.position == MotionPosition(0, 0)
    assert commands(m) == [CLOSE]


async def test_the_late_timer_stop_after_a_position_run_changes_nothing(modelled):
    m = modelled
    at_end(m, 100)
    await m.cover.async_set_cover_position(position=0)
    start = await written_and_started(m, CLOSE)
    await until(m, start, TIMER_S)
    bus(m, STOP)  # the actuator's relay timer, long after the curtain arrived
    assert state(m) == ("closed", 0)
    assert m.cover._motion.position == MotionPosition(0, 0)
    assert commands(m) == [CLOSE]


async def test_an_end_already_reached_sends_nothing(modelled):
    m = modelled
    at_end(m, 0)
    await m.cover.async_set_cover_position(position=0)
    assert commands(m) == []


async def test_position_zero_of_a_modelled_scope_cover_is_a_plain_full_run(modelled):
    """A scope cover does not settle its own full runs: 0 % writes one close and no Stop, settles nothing."""
    m = modelled
    area = MyHOMEScopeCover(
        hass=m.hass, gateway=m.plant.gateways[0], name="Area 1", entity_name=None, device_id="1", who="2",
        where="1", interface=None, advanced=False, manufacturer="BTicino", model="Area",
        travel_time=CLOSING_S, scope=CoverScope.of("1"),
    )
    area.hass, area.entity_id = m.hass, "cover.area_1"
    area.async_write_ha_state = area.async_schedule_update_ha_state = MagicMock()
    area._motion.configure(m.cover._motion.model)  # the same panel profile, bound to the scope cover
    assert not area._settles_full_run(False)
    try:
        await area.async_set_cover_position(position=0)
        await settle()
        assert commands(m) == ["*2*2*1##"]
        assert area._stop_task is None and area._full_run_to_end is False  # no settle on the travel timer
        await m.clock.advance(CLOSING_S + END_SETTLE_MARGIN + 0.1)
        assert commands(m) == ["*2*2*1##"]  # the limit switch ends the run: no Stop is written
    finally:
        area._cancel_run_timeout()
        area._cancel_stop_task()


@pytest.mark.parametrize(("opening", "known"), [(False, True), (False, False), (True, True), (True, False)],
                         ids=["close_known", "close_unknown", "open_known", "open_unknown"])
async def test_position_end_of_a_modelled_scope_cover_runs_its_members_as_open_close(modelled, opening, known):
    """0 % / 100 % of a modelled area on the main bus is its close / open: one frame, the members settle."""
    m = modelled
    members = m.plant.covers[:2]  # 11 (modelled) and 12 (linear), both on the first gateway
    family = CoverFamily()
    area = MyHOMEScopeCover(
        hass=m.hass, gateway=m.plant.gateways[0], name="Area 1", entity_name=None, device_id="1", who="2",
        where="1", interface=None, advanced=False, manufacturer="BTicino", model="Area",
        travel_time=CLOSING_S, scope=CoverScope.of("1"),
    )
    area.hass, area.entity_id = m.hass, "cover.area_1"
    area.async_write_ha_state = area.async_schedule_update_ha_state = area.async_on_remove = MagicMock()
    area._motion.configure(m.cover._motion.model)
    for cover in (*members, area):
        family.add(cover)
    if known:
        at_end(m, 100 if not opening else 0)
        area._motion.confirm(100 if not opening else 0)
    raw = OPEN.replace("11", "1") if opening else CLOSE.replace("11", "1")

    def runs():
        return [(c._full_run_to_end, c._stop_task is not None, c.is_opening, c.is_closing) for c in members]

    try:
        await (area.async_open_cover() if opening else area.async_close_cover())
        await settle()
        reference = (commands(m), runs())
        for cover in (*members, area):
            cover._cancel_stop_task()
            cover._cancel_wall_settle()
        area._cancel_run_timeout()
        m.sent.clear()
        if known:
            at_end(m, 100 if not opening else 0)
            area._motion.confirm(100 if not opening else 0)
        await area.async_set_cover_position(position=100 if opening else 0)
        await settle()
        assert (commands(m), runs()) == reference
        assert reference[0] == [raw]
        assert all(full_run and settling for full_run, settling, _, _ in reference[1])
    finally:
        for cover in (*members, area):
            cover._cancel_stop_task()
            cover._cancel_wall_settle()
        area._cancel_run_timeout()


# A run from mid travel settles at the model's remaining time.

def _at_half(m):
    at_end(m, 100)
    m.cover._motion.position = MotionPosition(0.5, 1)
    m.cover._attr_current_cover_position = m.cover._start_position = 50
    return m.cover._motion.model.duration(MotionPosition(0.5, 1), MotionPosition(0, 0), opening=False)


async def test_direct_close_from_mid_travel_settles_at_the_remaining_model_time(modelled):
    m = modelled
    remaining = _at_half(m)
    assert remaining < CLOSING_S
    await m.cover.async_close_cover()
    start = await written_and_started(m, CLOSE)
    await until(m, start, remaining + END_SETTLE_MARGIN - 0.2)
    assert m.cover.state == "closing"
    await until(m, start, remaining + END_SETTLE_MARGIN + 0.1)
    assert state(m) == ("closed", 0)
    assert commands(m) == [CLOSE]


async def test_a_wall_close_from_mid_travel_arms_the_remaining_model_time(modelled):
    m = modelled
    remaining = _at_half(m)
    with patch("custom_components.myhome.cover.async_call_later") as call_later:
        await wall_run(m, CLOSE)
    ((_, delay, _),) = [call.args for call in call_later.call_args_list]
    assert delay == pytest.approx(remaining + END_SETTLE_MARGIN)


async def test_a_run_from_an_unknown_start_settles_on_the_full_time(modelled):
    m = modelled
    await m.cover.async_close_cover()
    start = await written_and_started(m, CLOSE)
    await until(m, start, CLOSING_S + END_SETTLE_MARGIN - 0.2)
    assert m.cover.state == "closing"
    await until(m, start, CLOSING_S + END_SETTLE_MARGIN + 0.1)
    assert state(m) == ("closed", 0)
