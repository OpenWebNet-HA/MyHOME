"""The Dimension 1 refresh of a fine dimmer (#851) must not feed itself.

Capture: Home Assistant debug log of a test instance on a 3488 gateway (set up as MyHomeServer1),
2026-11-21, dimmer ``06`` lit at 100 %. ``CAPTURE`` below holds its lines for that light, in order,
with the log's own timestamps. The gateway answers the read ``*#1*06*1##`` with the level
(``*#1*06*1*200*2##``) AND with the preset frame ``*1*10*06##`` ("Collected command response" for
both); the event session shows the same frames. The preset frame asked Dimension 1 again, the
answer brought the preset again: 70 reads of that one light in 3.5 minutes from one instance, and
every other MyHOME instance on the bus answered each burst with a read of its own.

Frames marked BUILT are not captures: the capture has only one level (200) and one preset (10).
``*#1*0010*1*153*4##`` and ``*1*7*0010##`` are the live F461 frames of #851
(``tests/test_issue_851_fine_dimmer_preset_echo.py``); that a gateway sends them together in one
answer is BUILT after the capture above.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome import light as light_module
from custom_components.myhome.light import MyHOMELight

# (log time, what the log line says, frame)
CAPTURE = [
    ("21:46:43.405", "received", "*#1*06*1*200*2##"),
    ("21:46:43.706", "received", "*1*10*06##"),
    ("21:46:43.706", "queued", "*#1*06*1##"),
    ("21:46:44.565", "received", "*#1*06*1*200*2##"),
    ("21:46:45.706", "received", "*#1*06*1*200*2##"),
    ("21:46:45.716", "sent", "*#1*06*1##"),
    ("21:46:45.827", "received", "*#1*06*1*200*2##"),
    ("21:46:46.012", "received", "*#1*06*1*200*2##"),
    ("21:46:46.281", "received", "*1*10*06##"),
    ("21:46:46.282", "queued", "*#1*06*1##"),  # the loop: the answer's preset frame asks again
    ("21:46:46.709", "received", "*1*10*06##"),
]


def _seconds(stamp: str) -> float:
    hours, minutes, seconds = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


class _Clock:
    """Stands in for the ``time`` module of light.py (it uses ``time.monotonic`` only)."""

    def __init__(self) -> None:
        self.now = 50_000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(light_module, "time", fake)
    return fake


def _light(hass, where="06"):
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.log_id = "[3488]"
    light = MyHOMELight(
        hass=hass, name="Dimmer", entity_name="Dimmer", icon=None, icon_on=None,
        device_id=where, who="1", where=where, interface=None, dimmable=True,
        manufacturer="BTicino", model="MyHomeServer1", gateway=gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    return light, gateway


def _held_reads(gateway):
    """Return an unresolved delivery Future for each status request, matching CommandWorkerPool."""
    reads: list[tuple[str, asyncio.Future]] = []

    async def send_status_request(message):
        done = asyncio.get_running_loop().create_future()
        reads.append((str(message), done))
        return done

    gateway.send_status_request = send_status_request
    return reads


async def _tick() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


def _feed(light, *frames: str) -> None:
    for frame in frames:
        light.handle_event(OWNMessage.parse(frame))


async def test_the_captured_answer_does_not_ask_again(hass, clock):
    """Replay of CAPTURE: one read, and the preset frame its answer carries asks nothing."""
    light, gateway = _light(hass)
    reads = _held_reads(gateway)
    start = _seconds(CAPTURE[0][0])
    base = clock.now
    for stamp, kind, frame in CAPTURE:
        clock.now = base + (_seconds(stamp) - start)
        if kind == "received":
            _feed(light, frame)
            await _tick()
    assert [frame for frame, _ in reads] == ["*#1*06*1##"]
    # the reply is complete (the gateway's ACK); nothing follows on the bus
    clock.now += 0.2
    reads[0][1].set_result(None)
    await hass.async_block_till_done()
    assert [frame for frame, _ in reads] == ["*#1*06*1##"]
    assert light.is_on is True
    assert light._attr_brightness_pct == 100


async def test_preset_frame_of_the_own_answer_keeps_the_exact_level(hass, clock):
    """BUILT: an answer that carries level 53 % and then WHAT 7 leaves 53 % shown, state complete."""
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")  # an echo: the mapped value is shown at once and Dimension 1 asked (#851)
    await _tick()
    assert light._attr_brightness_pct == 50
    assert len(reads) == 1
    clock.now += 0.3
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 0.2
    _feed(light, "*1*7*0010##")  # BUILT: the preset frame inside the answer
    await _tick()
    assert light._attr_brightness_pct == 53
    assert light._bus_state_complete is True
    assert len(reads) == 1
    reads[0][1].set_result(None)
    await hass.async_block_till_done()
    assert len(reads) == 1
    assert light._attr_brightness_pct == 53


async def test_a_different_preset_while_the_read_is_out_asks_once_more(hass, clock):
    """BUILT: the level moved on while the first read was out: one more read when it is done."""
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")
    await _tick()
    assert len(reads) == 1
    clock.now += 0.2
    _feed(light, "*1*8*0010##")  # BUILT: a second step of the same press
    await _tick()
    assert light._attr_brightness_pct == 60  # the mapped preset, shown at once (#828)
    assert len(reads) == 1  # not while the first read is out
    clock.now += 0.2
    reads[0][1].set_result(None)
    await _tick()
    assert [frame for frame, _ in reads] == ["*#1*0010*1##", "*#1*0010*1##"]
    _feed(light, "*#1*0010*1*163*4##")  # BUILT: its answer
    reads[1][1].set_result(None)
    await hass.async_block_till_done()
    assert len(reads) == 2
    assert light._attr_brightness_pct == 63


def _burst(light, clock) -> None:
    """One answer to another client's read, as the event session shows it (frames and gaps of CAPTURE)."""
    _feed(light, "*#1*06*1*200*2##")
    clock.now += 0.185
    _feed(light, "*#1*06*1*200*2##")
    clock.now += 0.269
    _feed(light, "*1*10*06##")
    clock.now += 0.428
    _feed(light, "*1*10*06##")


async def test_answers_to_other_clients_reads_are_asked_twice_then_held(hass, clock):
    """Every MyHOME instance on the bus sees every answer: two reads that bring nothing new, then none."""
    light, gateway = _light(hass)
    _feed(light, "*#1*06*1*200*2##")
    for _ in range(10):
        clock.now += 3.0
        _burst(light, clock)
        await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 2
    assert light._attr_brightness_pct == 100
    assert light._bus_state_complete is True


async def test_the_hold_ends_when_the_light_has_been_quiet(hass, clock):
    light, gateway = _light(hass)
    _feed(light, "*#1*06*1*200*2##")
    for _ in range(6):
        clock.now += 3.0
        _burst(light, clock)
        await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 2
    clock.now += 31.0  # no frame of this light for more than 30 s
    _burst(light, clock)
    await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 3


async def test_the_hold_ends_on_another_preset(hass, clock):
    light, gateway = _light(hass)
    _feed(light, "*#1*06*1*200*2##")
    for _ in range(6):
        clock.now += 3.0
        _burst(light, clock)
        await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 2
    clock.now += 3.0
    _feed(light, "*1*7*06##")  # BUILT: the level was changed at the wall
    await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 3
    assert light._attr_brightness_pct == 50


async def test_the_hold_ends_when_the_light_goes_off(hass, clock):
    light, gateway = _light(hass)
    _feed(light, "*#1*06*1*200*2##")
    for _ in range(6):
        clock.now += 3.0
        _burst(light, clock)
        await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 2
    clock.now += 3.0
    _feed(light, "*1*0*06##")  # BUILT
    clock.now += 3.0
    _feed(light, "*1*10*06##")
    await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 3


async def test_a_held_echo_keeps_the_exact_level(hass, clock):
    """BUILT levels: while held, the preset frame no longer replaces the level Dimension 1 reported."""
    light, gateway = _light(hass, where="0010")
    _feed(light, "*#1*0010*1*153*4##")
    for _ in range(4):
        clock.now += 3.0
        _feed(light, "*#1*0010*1*153*4##")
        clock.now += 0.3
        _feed(light, "*1*7*0010##")
        await hass.async_block_till_done()
    assert gateway.send_status_request.await_count == 2
    assert light._attr_brightness_pct == 53
    assert light._bus_state_complete is True


async def test_only_one_read_of_a_light_is_out_at_a_time(hass, clock):
    """The request refuses a second read while one is out, whoever asks and however long it takes."""
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")
    await _tick()
    assert len(reads) == 1
    clock.now += 5.0  # the one-second window has passed; the read is still being answered
    assert light._request_brightness_refresh() is False
    assert light._request_brightness_refresh(force=True) is False
    await _tick()
    assert len(reads) == 1
    reads[0][1].set_result(None)
    await hass.async_block_till_done()
    assert len(reads) == 1


async def test_inflight_preset_change_ignores_first_reads_own_preset_echo(hass, clock):
    """While read 1 (preset 7) is out and preset 8 arrives, read 1's own `*1*7*` echo does not clobber 60 %."""
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")
    await _tick()
    assert len(reads) == 1

    clock.now += 0.2
    _feed(light, "*1*8*0010##")  # user stepped to preset 8 while read 1 is still out
    await _tick()
    assert light._attr_brightness_pct == 60
    assert len(reads) == 1

    # Read 1's own 3488/MHS1 echo for preset 7 arrives before read 1 completes: ignored, 60 % kept
    clock.now += 0.1
    _feed(light, "*1*7*0010##")
    await _tick()
    assert light._attr_brightness_pct == 60
    assert len(reads) == 1

    reads[0][1].set_result(clock.now)
    await _tick()
    assert len(reads) == 2
    assert light._brightness_refresh_preset == 8
    _feed(light, "*#1*0010*1*163*4##")
    reads[1][1].set_result(clock.now)
    await hass.async_block_till_done()
    assert light._attr_brightness_pct == 63


async def test_different_preset_during_cooldown_asks_immediately(hass, clock):
    """After read 1 finishes, a new preset inside the 1.0 s cooldown still triggers a Dimension 1 read."""
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")
    await _tick()
    reads[0][1].set_result(clock.now)
    await _tick()
    assert len(reads) == 1

    clock.now += 0.3  # inside 1.0 s cooldown window, but preset changed 7 -> 8
    _feed(light, "*1*8*0010##")
    await _tick()
    assert len(reads) == 2
    assert light._attr_brightness_pct == 60
    reads[1][1].set_result(clock.now)
    await hass.async_block_till_done()


async def test_unload_cancels_in_flight_refresh_without_spawning_followup(hass, clock):
    """Removing the entity cancels the in-flight refresh task and does not spawn a follow-up read."""
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")
    await _tick()
    _feed(light, "*1*8*0010##")  # would normally schedule a follow-up read in finally
    await _tick()
    assert len(reads) == 1

    await light.async_will_remove_from_hass()
    await _tick()
    assert len(reads) == 1
    assert light._brightness_refresh_out is False
    assert light._brightness_refresh_task is None


async def test_brightness_refresh_timeout_releases_inflight_slot(hass, clock, monkeypatch):
    """A delivery future that never settles times out cleanly without cancelling the pool's future."""
    monkeypatch.setattr(light_module, "_BRIGHTNESS_REFRESH_TIMEOUT_S", 0.01)
    light, gateway = _light(hass, where="0010")
    reads = _held_reads(gateway)
    _feed(light, "*#1*0010*1*153*4##")
    clock.now += 60.0
    _feed(light, "*1*7*0010##")
    await asyncio.sleep(0.05)
    assert len(reads) == 1
    assert not reads[0][1].cancelled()
    assert light._brightness_refresh_out is False


async def test_level_zero_while_on_and_dimension_4_and_no_load_and_timed_on(hass, clock):
    """#970 level-0-while-ON keeps its exact level (1 %) across preset 2 echoes; Dim 4 updates exact level;
    WHAT 19 no-load, _settle_level_zero, and async_turn_on_timed reset the hold.
    """
    light, gateway = _light(hass, where="0010")
    # Confirm ON at exact level 0 (#970)
    _feed(light, "*#1*0010*1*153*4##")
    light._level_zero_on = True
    light._status_on = True
    light._status_on_at = clock.now
    def _dim1_reads() -> int:
        return sum(
            1
            for call in gateway.send_status_request.await_args_list
            if str(call.args[0]) == "*#1*0010*1##"
        )

    for _ in range(4):
        clock.now += 3.0
        _feed(light, "*#1*0010*1*100*10##")  # brightness=0, level_zero_while_on=True -> level=1
        assert light.is_on is True
        assert light._exact_level_pct == 1
        clock.now += 0.3
        _feed(light, "*1*2*0010##")
        await hass.async_block_till_done()
    # 2 Dimension 1 reads, then held (did NOT wipe _exact_level_pct or loop endlessly)
    assert _dim1_reads() == 2

    # Dimension 4 exact level updates _exact_level_pct and breaks a stale hold
    clock.now += 3.0
    _feed(light, "*#1*0010*4*140*2##")
    assert light._exact_level_pct == 40
    _feed(light, "*1*2*0010##")
    await hass.async_block_till_done()
    assert _dim1_reads() == 3

    # Hold again, then WHAT 19 no-load resets guard
    for _ in range(3):
        clock.now += 3.0
        _feed(light, "*1*2*0010##")
        await hass.async_block_till_done()
    before_noload = _dim1_reads()
    _feed(light, "*1*19*0010##")
    assert light._exact_level_pct is None
    assert light._brightness_refresh_repeats == 0
    clock.now += 3.0
    _feed(light, "*1*2*0010##")
    await hass.async_block_till_done()
    assert _dim1_reads() == before_noload + 1

    # _settle_level_zero settling off resets guard
    light._exact_level_pct = 1
    light._brightness_refresh_repeats = 2
    light._level_zero_pending = True
    light._level_zero_shown_on = True
    light._level_zero_on = False
    light._attr_is_on = True
    light._settle_level_zero(answered=True)
    assert light.is_on is False
    assert light._exact_level_pct is None
    assert light._brightness_refresh_repeats == 0

    # async_turn_on_timed calls _send_switch_on and resets guard
    light.async_write_ha_state = MagicMock()
    light._brightness_refresh_repeats = 2
    light._brightness_refresh_preset = 7
    await light.async_turn_on_timed(duration=60)
    assert light._brightness_refresh_repeats == 0
    assert light._brightness_refresh_preset is None


async def test_brightness_refresh_queue_shutdown_swallowed_and_cancels_followup(hass, clock):
    """QueueShutDown inside _async_brightness_refresh logs no unretrieved task error and spawns no follow-up."""
    loop = asyncio.get_running_loop()
    unhandled: list[dict] = []
    prev_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
    try:
        # Case 1: async QueueShutDown while suspended waiting to enqueue, with a follow-up preset armed.
        light, gateway = _light(hass, where="0010")
        gate = loop.create_future()
        attempts: list[str] = []

        async def _fail_after_suspend(message):
            attempts.append(str(message))
            await gate
            raise asyncio.QueueShutDown

        gateway.send_status_request = _fail_after_suspend
        _feed(light, "*#1*0010*1*153*4##")
        clock.now += 60.0
        _feed(light, "*1*7*0010##")
        await _tick()
        assert len(attempts) == 1
        assert light._brightness_refresh_out is True

        # A different preset arrives while read 1 is still pending, arming _brightness_refresh_again.
        clock.now += 0.2
        _feed(light, "*1*8*0010##")
        assert light._brightness_refresh_again is True
        assert light._brightness_refresh_next_preset == 8

        # Command queue shuts down: read 1 must swallow QueueShutDown and NOT fire a follow-up read.
        gate.set_result(None)
        await hass.async_block_till_done()
        assert len(attempts) == 1
        assert light._brightness_refresh_out is False
        assert light._brightness_refresh_task is None
        assert light._brightness_refresh_again is False
        assert light._brightness_refresh_next_preset is None
        assert unhandled == []

        # Case 2: immediate/eager QueueShutDown returns False without advancing repeat hold state.
        light2, gateway2 = _light(hass, where="0010")
        eager_attempts: list[str] = []

        async def _fail_immediately(message):
            eager_attempts.append(str(message))
            raise asyncio.QueueShutDown

        gateway2.send_status_request = _fail_immediately
        _feed(light2, "*#1*0010*1*153*4##")
        clock.now += 60.0
        _feed(light2, "*1*7*0010##")
        await hass.async_block_till_done()
        assert len(eager_attempts) == 1
        assert light2._brightness_refresh_out is False
        assert light2._brightness_refresh_task is None
        assert light2._brightness_refresh_repeats == 0
        assert light2._brightness_refresh_preset is None
        assert unhandled == []
    finally:
        loop.set_exception_handler(prev_handler)
