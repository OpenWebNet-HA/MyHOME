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
    """Make every status request wait until the test completes it (a reply still being collected)."""
    reads: list[tuple[str, asyncio.Future]] = []

    async def send_status_request(message):
        done = asyncio.get_running_loop().create_future()
        reads.append((str(message), done))
        await done

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
