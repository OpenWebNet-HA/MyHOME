"""A light whose status does not follow an acknowledged command is read back (F455 status loss).

Two replays of a physical F455 (firmware 1.0.76) driven by Home Assistant on 2026-11-18, copied
line by line from that instance's debug log (`write` = "was successfully unqueued by worker",
`ack` = "was successfully sent", `rx` = "Message received"):

* ``F455_SERVICE_RUN``: `light.turn_on` / `light.turn_off` for four lights in one call, six
  rounds 4 s apart. Rounds 1 and 2 are complete; in rounds 3 to 6 the event session left out
  three, one, one and one status although every light switched (an F461 on the same bus relayed
  all of them).
* ``F455_TILE_RUN``: six light tiles tapped about 0.1 s apart in the Home Assistant UI. Lights 07
  and 03 on interface 01 stayed "Off" on the page while they were on.

In the real-user traces of this repository every `*1*0|1*<where>##` the integration sent was
followed by the light's status within 0.33 s (F454, F461, MH200N, MH201, MH202, MyHomeServer1),
so on those gateways the watchdog sends nothing.
"""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util
from OWNd.message import OWNCommand, OWNMessage
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome import gateway_confirm
from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    SUPPORTED_GATEWAY_MODELS,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_confirm import LightEchoWatchdog, echo_watchdog_of
from tests.mock_gateway_harness import MockGatewayHarness

F455_SERVICE_RUN = [
    ("02:15:08.868", "write", "*1*1*03#4#01##"),
    ("02:15:08.869", "write", "*1*1*07##"),
    ("02:15:08.880", "ack", "*1*1*03#4#01##"),
    ("02:15:08.883", "ack", "*1*1*07##"),
    ("02:15:08.931", "write", "*1*1*05##"),
    ("02:15:08.933", "write", "*1*1*09#4#01##"),
    ("02:15:08.934", "ack", "*1*1*05##"),
    ("02:15:08.936", "ack", "*1*1*09#4#01##"),
    ("02:15:08.998", "rx", "*1*1*03#4#01##"),
    ("02:15:09.078", "rx", "*1*1*07##"),
    ("02:15:09.159", "rx", "*1*1*05##"),
    ("02:15:09.288", "rx", "*1*1*09#4#01##"),
    ("02:15:12.887", "write", "*1*0*03#4#01##"),
    ("02:15:12.888", "write", "*1*0*07##"),
    ("02:15:12.891", "ack", "*1*0*03#4#01##"),
    ("02:15:12.891", "ack", "*1*0*07##"),
    ("02:15:12.942", "write", "*1*0*05##"),
    ("02:15:12.942", "write", "*1*0*09#4#01##"),
    ("02:15:12.946", "ack", "*1*0*05##"),
    ("02:15:12.947", "ack", "*1*0*09#4#01##"),
    ("02:15:13.008", "rx", "*1*0*03#4#01##"),
    ("02:15:13.178", "rx", "*1*0*07##"),
    ("02:15:13.269", "rx", "*1*0*05##"),
    ("02:15:13.308", "rx", "*1*0*09#4#01##"),
    ("02:15:16.907", "write", "*1*1*03#4#01##"),
    ("02:15:16.908", "write", "*1*1*07##"),
    ("02:15:16.913", "ack", "*1*1*03#4#01##"),
    ("02:15:16.913", "ack", "*1*1*07##"),
    ("02:15:16.964", "write", "*1*1*05##"),
    ("02:15:16.964", "write", "*1*1*09#4#01##"),
    ("02:15:16.968", "ack", "*1*1*05##"),
    ("02:15:16.968", "ack", "*1*1*09#4#01##"),
    ("02:15:17.389", "rx", "*1*1*09#4#01##"),
    ("02:15:20.923", "write", "*1*0*03#4#01##"),
    ("02:15:20.923", "write", "*1*0*07##"),
    ("02:15:20.926", "ack", "*1*0*03#4#01##"),
    ("02:15:20.926", "ack", "*1*0*07##"),
    ("02:15:20.978", "write", "*1*0*05##"),
    ("02:15:20.978", "write", "*1*0*09#4#01##"),
    ("02:15:20.980", "ack", "*1*0*05##"),
    ("02:15:20.981", "ack", "*1*0*09#4#01##"),
    ("02:15:21.178", "rx", "*1*0*07##"),
    ("02:15:21.318", "rx", "*1*0*05##"),
    ("02:15:21.338", "rx", "*1*0*09#4#01##"),
    ("02:15:24.938", "write", "*1*1*03#4#01##"),
    ("02:15:24.938", "write", "*1*1*07##"),
    ("02:15:24.941", "ack", "*1*1*03#4#01##"),
    ("02:15:24.941", "ack", "*1*1*07##"),
    ("02:15:24.993", "write", "*1*1*05##"),
    ("02:15:24.993", "write", "*1*1*09#4#01##"),
    ("02:15:24.996", "ack", "*1*1*05##"),
    ("02:15:24.997", "ack", "*1*1*09#4#01##"),
    ("02:15:25.058", "rx", "*1*1*03#4#01##"),
    ("02:15:25.228", "rx", "*1*1*05##"),
    ("02:15:25.348", "rx", "*1*1*09#4#01##"),
    ("02:15:28.943", "write", "*1*0*03#4#01##"),
    ("02:15:28.943", "write", "*1*0*07##"),
    ("02:15:28.946", "ack", "*1*0*03#4#01##"),
    ("02:15:28.947", "ack", "*1*0*07##"),
    ("02:15:28.998", "write", "*1*0*05##"),
    ("02:15:28.998", "write", "*1*0*09#4#01##"),
    ("02:15:29.001", "ack", "*1*0*05##"),
    ("02:15:29.001", "ack", "*1*0*09#4#01##"),
    ("02:15:29.058", "rx", "*1*0*03#4#01##"),
    ("02:15:29.177", "rx", "*1*0*07##"),
    ("02:15:29.348", "rx", "*1*0*09#4#01##"),
]

# The dimmers 0011 / 0010 show their command back before its ACK, then report their level;
# the two `*#1*<where>*1##` rows are the dimmers' own brightness requests (status requests
# never reach the watchdog).
F455_TILE_RUN = [
    ("03:04:27.063", "write", "*1*1*05##"),
    ("03:04:27.070", "write", "*1*1*0011##"),
    ("03:04:27.078", "ack", "*1*1*05##"),
    ("03:04:27.088", "rx", "*1*1*0011##"),
    ("03:04:27.089", "ack", "*1*1*0011##"),
    ("03:04:27.130", "write", "*1*1*0010##"),
    ("03:04:27.135", "rx", "*1*1*0010##"),
    ("03:04:27.136", "ack", "*1*1*0010##"),
    ("03:04:27.139", "write", "*1*1*07##"),
    ("03:04:27.144", "ack", "*1*1*07##"),
    ("03:04:27.161", "rx", "*1*1*05##"),
    ("03:04:27.188", "write", "*1*1*09#4#01##"),
    ("03:04:27.194", "ack", "*1*1*09#4#01##"),
    ("03:04:27.194", "write", "*1*1*03#4#01##"),
    ("03:04:27.198", "ack", "*1*1*03#4#01##"),
    ("03:04:27.241", "rx", "*1*5*0011##"),
    ("03:04:27.331", "rx", "*1*5*0010##"),
    ("03:04:27.532", "rx", "*1*1*09#4#01##"),
    ("03:04:27.792", "rx", "*#1*0011*1*130*2##"),
    ("03:04:27.921", "rx", "*#1*0010*1*126*10##"),
    ("03:04:28.291", "rx", "*#1*0010*1*126*10##"),
]


def _seconds(stamp: str) -> float:
    hours, minutes, seconds = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@pytest.fixture
def watchdog(hass):
    """A watchdog on a gateway double that records the status requests it is asked to send."""
    handler = SimpleNamespace(hass=hass, log_id="[F455]", send_status_request=AsyncMock())
    dog = LightEchoWatchdog(handler)  # type: ignore[arg-type]
    yield dog
    dog.cancel_all()


def _requests(dog: LightEchoWatchdog) -> list[str]:
    return [str(call.args[0]) for call in dog.handler.send_status_request.await_args_list]


async def _expire(hass) -> None:
    """Let every armed watch run out (ECHO_CONFIRM_DELAY_S is 1.5 s)."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=2))
    await hass.async_block_till_done()


async def _replay(hass, dog: LightEchoWatchdog, rows: list[tuple[str, str, str]]) -> list[list[str]]:
    """Feed the rows in order; a gap of more than 2 s lets the watches run out.

    Returns the status requests of each round, sorted (timers of one round expire together).
    """
    rounds: list[list[str]] = []
    written: dict[str, float] = {}
    previous = _seconds(rows[0][0])
    for stamp, kind, frame in rows:
        if _seconds(stamp) - previous > 2:
            before = len(_requests(dog))
            await _expire(hass)
            rounds.append(sorted(_requests(dog)[before:]))
        previous = _seconds(stamp)
        if kind == "write":
            written[frame] = time.monotonic()
        elif kind == "ack":
            dog.command_acknowledged(OWNCommand.parse(frame), written.pop(frame))
        else:
            dog.status_seen(OWNMessage.parse(frame))
    before = len(_requests(dog))
    await _expire(hass)
    rounds.append(sorted(_requests(dog)[before:]))
    return rounds


async def test_f455_service_run_reads_back_exactly_the_lights_whose_status_was_left_out(hass, watchdog) -> None:
    rounds = await _replay(hass, watchdog, F455_SERVICE_RUN)
    assert rounds == [
        [],  # round 1: four statuses for four commands, nothing sent
        [],
        ["*#1*03#4#01##", "*#1*05##", "*#1*07##"],  # only `*1*1*09#4#01##` came back
        ["*#1*03#4#01##"],
        ["*#1*07##"],
        ["*#1*05##"],
    ]
    assert watchdog.pending == {}


async def test_f455_tile_run_reads_back_the_two_lights_the_page_showed_off(hass, watchdog) -> None:
    rounds = await _replay(hass, watchdog, F455_TILE_RUN)
    assert rounds == [["*#1*03#4#01##", "*#1*07##"]]


async def test_a_status_of_the_other_polarity_is_not_the_answer(hass, watchdog) -> None:
    """ON then OFF in quick succession: the late ON status must not hide a lost OFF status."""
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*07##"), time.monotonic())
    watchdog.command_acknowledged(OWNCommand.parse("*1*0*07##"), time.monotonic())
    watchdog.status_seen(OWNMessage.parse("*1*1*07##"))
    assert watchdog.pending == {"07": "*1*0*07##"}
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*07##"]
    # The read's answer needs nothing more: a read never arms the watchdog.
    watchdog.status_seen(OWNMessage.parse("*1*0*07##"))
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*07##"]


async def test_commands_without_a_polarity_take_any_status_of_the_light(hass, watchdog) -> None:
    """A dim step (`*1*30*0010##`) and a level write are answered by whatever the dimmer reports."""
    watchdog.command_acknowledged(OWNCommand.parse("*1*30*0010##"), time.monotonic())
    watchdog.status_seen(OWNMessage.parse("*1*10*0010##"))
    watchdog.command_acknowledged(OWNCommand.parse("*#1*0011*#1*179*0##"), time.monotonic())
    watchdog.status_seen(OWNMessage.parse("*#1*0011*1*179*2##"))
    assert watchdog.pending == {}
    await _expire(hass)
    assert _requests(watchdog) == []


async def test_frames_that_are_no_light_status_or_no_light_command_are_ignored(hass, watchdog) -> None:
    now = time.monotonic()
    for frame in (
        "*1*1*0#4#01##",  # general on a riser
        "*1*0*00##",  # area
        "*1*30*#1##",  # group
        "*2*1*06##",  # WHO 2: covers anchor on the write time
        "*14*0*05##",
        "*#1*05##",  # a status request sent as a command
        "*#1*0010*1##",
    ):
        watchdog.command_acknowledged(OWNCommand.parse(frame), now)
    watchdog.command_acknowledged("*1*1*07##", now)  # not a parsed message
    nameless = MagicMock(spec=OWNMessage, who=1, is_general=False, is_area=False, is_group=False, entity=None, where=None)
    watchdog.command_acknowledged(nameless, now)  # a WHO 1 frame that names no address
    assert watchdog.pending == {}

    watchdog.command_acknowledged(OWNCommand.parse("*1*1*07##"), now)
    watchdog.status_seen(OWNMessage.parse("*1*1000#1*07##"))  # command translation, not the actuator
    watchdog.status_seen(OWNMessage.parse("*1*1*05##"))  # another light
    watchdog.status_seen(OWNMessage.parse("*2*1*06##"))
    watchdog.status_seen(None)
    assert watchdog.pending == {"07": "*1*1*07##"}


async def test_a_newer_command_replaces_the_watch_and_closing_drops_them(hass, watchdog) -> None:
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*07##"), time.monotonic())
    watchdog.command_acknowledged(OWNCommand.parse("*1*0*07##"), time.monotonic())
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*05##"), time.monotonic())
    assert watchdog.pending == {"07": "*1*0*07##", "05": "*1*1*05##"}
    watchdog.cancel_all()
    assert watchdog.pending == {}
    await _expire(hass)
    assert _requests(watchdog) == []

    # A listener that is closing sends nothing.
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*07##"), time.monotonic())
    watchdog.handler._terminate_listener = True
    await _expire(hass)
    assert _requests(watchdog) == []
    assert watchdog.pending == {}


async def test_remembered_statuses_are_pruned(hass, watchdog, monkeypatch) -> None:
    """The pre-ACK status memory stays small: old entries go once more than 64 lights reported."""
    clock = [1000.0]
    monkeypatch.setattr(gateway_confirm.time, "monotonic", lambda: clock[0])
    for area in range(1, 8):  # 70 synthetic point-to-point addresses 11..79
        for point in range(1, 10):
            watchdog.status_seen(OWNMessage.parse(f"*1*1*{area}{point}##"))
    clock[0] += 60
    for point in range(1, 8):
        watchdog.status_seen(OWNMessage.parse(f"*1*0*8{point}##"))
    assert sorted(watchdog._recent_status) == ["81", "82", "83", "84", "85", "86", "87"]


# ── review of MyHOME#964 (2026-11-20): polarity of a level write, what counts as a status,
#    the WHERE of the read-back, a gateway that is closing ──────────────────────────────────
#
# Frames of light 32 (an F418U2 dimmer) are from
# tests/fixtures/traces/issue_466/myhome_trace_MH202_f418u2_dimmer_2026-09-27T09-43-45.json: the MH202
# relays each write on its event session (`*#1*32*#1*130*0##`, then `*#1*32*#13*2*130*0*0##`) about
# 30 ms before the dimmer's status `*#1*32*1*130*5##`.


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ("*#1*32*#1*130*0##", True),  # level 30 %
        ("*#1*32*#1*100*0##", False),  # level 0: off
        ("*#1*32*#4*130*0##", True),  # the 100-level form
        ("*#1*32*#4*100*0##", False),
        ("*#1*62*#12*120*50*80##", True),  # BUILT by OWNd's set_hsv_color: V 80
        ("*#1*62*#12*120*50*0##", False),  # BUILT: V 0
        ("*#1*25*#14*300##", True),  # BUILT by OWNd's set_color_temperature: 300 mireds
        ("*#1*25*14##", None),  # Dimension 14 read says nothing
        ("*#1*0010*1##", None),  # a read says nothing
        ("*1*30*0010##", None),  # a dim step
    ],
)
def test_a_level_write_says_whether_it_turns_the_light_on(frame: str, expected: bool | None) -> None:
    assert gateway_confirm._commanded_on(OWNCommand.parse(frame)) is expected


def test_the_hsv_frames_are_what_ownd_builds() -> None:
    from OWNd.message import OWNLightingCommand

    assert str(OWNLightingCommand.set_hsv_color("62", 120, 50, 80)) == "*#1*62*#12*120*50*80##"
    assert str(OWNLightingCommand.set_hsv_color("62", 120, 50, 0)) == "*#1*62*#12*120*50*0##"


async def test_a_level_write_is_not_answered_by_the_off_before_it(hass, watchdog, monkeypatch) -> None:
    """Off, then a level write: the off's status came between the write and its ACK.

    Seen live on an MH200 with an F418U2 (review of MyHOME#964): `*1*0*62##` was on the event
    session 61 ms after the level write was written and 49 ms before its ACK. That "off" is the
    answer of the command before, not of the level write, which must stay watched.
    """
    clock = [1000.0]
    monkeypatch.setattr(gateway_confirm.time, "monotonic", lambda: clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*1*0*32##"), clock[0])
    written_at = clock[0] + 0.042  # the level write goes out
    clock[0] += 0.103
    watchdog.status_seen(OWNMessage.parse("*1*0*32##"))  # the off's status, late
    assert watchdog.pending == {}  # it answered the off
    clock[0] += 0.049
    watchdog.command_acknowledged(OWNCommand.parse("*#1*32*#1*130*0##"), written_at)
    assert watchdog.pending == {"32": "*#1*32*#1*130*0##"}  # an "off" is not its answer

    watchdog.status_seen(OWNMessage.parse("*#1*32*1*130*5##"))  # the dimmer at 30 %
    assert watchdog.pending == {}
    await _expire(hass)
    assert _requests(watchdog) == []

    # The same when the dimmer's status never comes: both status and Dimension 1 are asked.
    clock[0] += 5  # a later write: the status above is older than it
    watchdog.command_acknowledged(OWNCommand.parse("*#1*32*#1*130*0##"), clock[0])
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*32##", "*#1*32*1##"]


async def test_an_echoed_write_and_frames_without_on_or_off_are_no_status(hass, watchdog) -> None:
    """What the MH202 relays before the dimmer's status does not end the watch of the write."""
    now = time.monotonic()
    watchdog.command_acknowledged(OWNCommand.parse("*#1*32*#1*130*0##"), now)
    for relayed in ("*#1*32*#1*130*0##", "*#1*32*#13*2*130*0*0##", "*#1*32*13*2*130*5*0##"):
        watchdog.status_seen(OWNMessage.parse(relayed))  # the write, its #13 echo, a frame with no on / off
        assert watchdog.pending == {"32": "*#1*32*#1*130*0##"}, relayed
    assert "32" not in watchdog._recent_status
    watchdog.status_seen(OWNMessage.parse("*#1*32*1*130*5##"))
    assert watchdog.pending == {}

    # A command with no polarity (a dim step) is not answered by a colour frame, an anomaly or a
    # motion frame, nor by a request relayed on the event session; a level status answers it.
    watchdog.command_acknowledged(OWNCommand.parse("*1*30*0010##"), now)
    for frame in ("*#1*0010*12*511*127*255##", "*#1*0010*1##", "*#1*0010##"):
        watchdog.status_seen(OWNMessage.parse(frame))
        assert watchdog.pending == {"0010": "*1*30*0010##"}, frame
    watchdog.status_seen(OWNMessage.parse("*1*8*0010##"))
    assert watchdog.pending == {}

    # A dim step relayed for the light (OWNd reads it as "on", the light entity ignores it) does
    # not answer an "on", and does not replace the status remembered for the light.
    watchdog.status_seen(OWNMessage.parse("*1*0*0010##"))
    remembered = watchdog._recent_status["0010"]
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*0010##"), time.monotonic())
    for step in ("*1*30*0010##", "*1*31*0010##"):
        watchdog.status_seen(OWNMessage.parse(step))
    assert watchdog.pending == {"0010": "*1*1*0010##"}
    assert watchdog._recent_status["0010"] == remembered
    for frame in ("*1*19*74##", "*1*34*52##"):  # anomaly, motion: nothing is remembered for them
        watchdog.status_seen(OWNMessage.parse(frame))
    assert "74" not in watchdog._recent_status and "52" not in watchdog._recent_status
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*0010##"]


async def test_the_read_back_carries_the_whole_where(hass, watchdog) -> None:
    """A Zigbee address keeps its `#9`, an address behind an interface its `#4#II`.

    The Zigbee frames are BUILT (no capture holds a WHO 1 frame with `#9`): the address is the
    example of the review of MyHOME#964. `message.entity` is `1-701234501` for it, without the `#9`.
    """
    assert OWNCommand.parse("*1*1*701234501#9##").entity == "1-701234501"
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*701234501#9##"), time.monotonic())
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*09#4#01##"), time.monotonic())
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*0#01#9##"), time.monotonic())  # BUILT: unit broadcast
    assert watchdog.pending == {"701234501#9": "*1*1*701234501#9##", "09#4#01": "*1*1*09#4#01##"}
    watchdog.status_seen(OWNMessage.parse("*1*1*09#4#01##"))  # the same key for command and status
    assert watchdog.pending == {"701234501#9": "*1*1*701234501#9##"}
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*701234501#9##"]

    watchdog.command_acknowledged(OWNCommand.parse("*1*0*701234501#9##"), time.monotonic())
    watchdog.status_seen(OWNMessage.parse("*1*0*701234501#9##"))
    assert watchdog.pending == {}


async def test_nothing_is_armed_or_sent_once_the_sender_is_closing(hass, watchdog) -> None:
    """A send that finishes right after the gateway was closed arms no timer; a timer that runs out sends nothing."""
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*07##"), time.monotonic())
    watchdog.handler._terminate_sender = True
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*05##"), time.monotonic())
    assert watchdog.pending == {"07": "*1*1*07##"}  # 05 was not armed
    await _expire(hass)
    assert _requests(watchdog) == [] and watchdog.pending == {}

    watchdog.handler._terminate_sender = False
    watchdog.handler._terminate_listener = True
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*05##"), time.monotonic())
    assert watchdog.pending == {}


def test_a_handler_double_without_a_watchdog_is_skipped() -> None:
    assert echo_watchdog_of(MagicMock()) is None
    assert echo_watchdog_of(SimpleNamespace()) is None
    dog = LightEchoWatchdog(MagicMock())
    assert echo_watchdog_of(SimpleNamespace(_echo_watchdog=dog)) is dog


# ── end to end: real handler, worker and OWNd session against a mock gateway ──────────


def _entry(port: int, model: str = "F455") -> MagicMock:
    entry = MagicMock()
    entry.data = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: port,
        CONF_PASSWORD: None,
        CONF_SSDP_LOCATION: "",
        CONF_SSDP_ST: "",
        CONF_DEVICE_TYPE: "Gateway",
        CONF_FRIENDLY_NAME: "GW",
        CONF_MANUFACTURER: "Bticino",
        CONF_MANUFACTURER_URL: "",
        CONF_NAME: model,
        CONF_FIRMWARE: "1.0.76",
        CONF_MAC: "00:03:50:00:04:55",
        CONF_UDN: "1234",
    }
    entry.options = {}
    return entry


async def test_worker_reads_back_an_acknowledged_command_and_dispatches_the_answer(hass, monkeypatch) -> None:
    """The gateway acknowledges `*1*1*07##`, relays no status, and answers `*#1*07##` on the command session."""
    monkeypatch.setattr(gateway_confirm, "ECHO_CONFIRM_DELAY_S", 0.05)
    harness = MockGatewayHarness()

    def _gateway(frame: str, _writer) -> str:
        return "*1*1*07##*#*1##" if frame == "*#1*07##" else "*#*1##"

    harness.set_custom_handler(_gateway)
    port = await harness.start()
    handler = MyHOMEGatewayHandler(hass, _entry(port))
    handler._event_session_ready.set()
    received: list[str] = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{handler.mac}", lambda msg: received.append(str(msg)))
    worker = asyncio.create_task(handler.sending_loop(0))
    try:
        written = await handler.send(OWNCommand.parse("*1*1*07##"))
        await asyncio.wait_for(written, 2)
        assert handler._echo_watchdog.pending == {"07": "*1*1*07##"}
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0.02)
        assert harness.received_messages.count("*1*1*07##") == 1
        assert harness.received_messages.count("*#1*07##") == 1
        assert received == ["*1*1*07##"]  # what a light entity at 07 now gets
        assert handler._echo_watchdog.pending == {}

        # A status request and a frame for an exposed (virtual) address arm nothing.
        await asyncio.wait_for(await handler.send_status_request(OWNCommand.parse("*#1*05##")), 2)
        monkeypatch.setattr(handler._command_pool, "_is_exposed_target", lambda task: True)
        await asyncio.wait_for(await handler.send(OWNCommand.parse("*1*1*05##")), 2)
        assert handler._echo_watchdog.pending == {}

        # With no worker left, a pending read-back is dropped.
        monkeypatch.setattr(handler._command_pool, "_is_exposed_target", lambda task: False)
        monkeypatch.setattr(gateway_confirm, "ECHO_CONFIRM_DELAY_S", 30)
        await asyncio.wait_for(await handler.send(OWNCommand.parse("*1*0*07##")), 2)
        assert handler._echo_watchdog.pending == {"07": "*1*0*07##"}
        await handler.close_listener()
        assert handler._echo_watchdog.pending == {}
    finally:
        unsub()
        handler._command_pool.close()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await harness.stop()



@pytest.mark.parametrize("model", SUPPORTED_GATEWAY_MODELS)
async def test_the_read_back_is_the_same_on_every_gateway_model(hass, monkeypatch, model: str) -> None:
    """Nothing in the read-back looks at the model: every supported one gets it, and none is asked needlessly.

    Light 05: the gateway acknowledges the command and relays no status, so it is asked once and
    its answer reaches the entities. Light 07: its status follows the acknowledgement, so nothing
    is asked.
    """
    monkeypatch.setattr(gateway_confirm, "ECHO_CONFIRM_DELAY_S", 0.05)
    harness = MockGatewayHarness()

    def _gateway(frame: str, _writer) -> str:
        return "*1*1*05##*#*1##" if frame == "*#1*05##" else "*#*1##"

    harness.set_custom_handler(_gateway)
    port = await harness.start()
    handler = MyHOMEGatewayHandler(hass, _entry(port, model))
    assert echo_watchdog_of(handler) is handler._echo_watchdog
    handler._event_session_ready.set()
    received: list[str] = []
    unsub = async_dispatcher_connect(hass, f"myhome_message_{handler.mac}", lambda msg: received.append(str(msg)))
    worker = asyncio.create_task(handler.sending_loop(0))
    try:
        await asyncio.wait_for(await handler.send(OWNCommand.parse("*1*1*05##")), 5)
        assert handler._echo_watchdog.pending == {"05": "*1*1*05##"}
        for _ in range(250):
            if received:
                break
            await asyncio.sleep(0.02)
        assert harness.received_messages.count("*#1*05##") == 1
        assert received == ["*1*1*05##"]
        assert handler._echo_watchdog.pending == {}

        await asyncio.wait_for(await handler.send(OWNCommand.parse("*1*1*07##")), 5)
        assert handler._echo_watchdog.pending == {"07": "*1*1*07##"}
        handler._echo_watchdog.status_seen(OWNMessage.parse("*1*1*07##"))  # the status came by itself
        assert handler._echo_watchdog.pending == {}
        await asyncio.sleep(0.2)
        assert "*#1*07##" not in harness.received_messages
    finally:
        unsub()
        handler._command_pool.close()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await harness.stop()


async def test_dimension_watches_ignore_plain_on_cache_fast_dim_and_keep_two_frame_watches(
    hass, watchdog, monkeypatch
) -> None:
    """Audit #977 items 1, 2a, 2b, 2c: Dim 1/12/14 watch isolation, pre-ACK cache, and 2-frame writes."""
    clock = [2000.0]
    monkeypatch.setattr(gateway_confirm.time, "monotonic", lambda: clock[0])

    # 2a: Plain *1*1*W## / coarse *1*7*W## does NOT cancel Dim 1, Dim 12, or Dim 14 watches;
    # at expiry, plain *#1*W## is skipped (already in _recent_status) while the missing dimension IS polled.
    watchdog.command_acknowledged(OWNCommand.parse("*#1*32*#1*180*0##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*#1*26*#12*120*50*80##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25*#14*300##"), clock[0])
    clock[0] += 0.05
    watchdog.status_seen(OWNMessage.parse("*1*1*32##"))
    watchdog.status_seen(OWNMessage.parse("*1*7*32##"))
    watchdog.status_seen(OWNMessage.parse("*1*1*26##"))
    watchdog.status_seen(OWNMessage.parse("*1*1*25##"))
    assert set(watchdog.pending) == {"32", "26", "25"}
    await _expire(hass)
    assert sorted(_requests(watchdog)) == sorted(["*#1*32*1##", "*#1*26*12##", "*#1*25*14##"])
    watchdog.handler.send_status_request.reset_mock()

    # 2b: Fast-event Dim 12 and Dim 14 echoes overtaking ACK cache in _recent_status -> 0 watches armed!
    clock[0] += 5.0
    w_at = clock[0]
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*26*12*120*50*80##"))
    watchdog.status_seen(OWNMessage.parse("*#1*25*14*300##"))
    clock[0] += 0.02
    watchdog.command_acknowledged(OWNCommand.parse("*#1*26*#12*120*50*80##"), w_at)
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25*#14*300##"), w_at)
    assert watchdog.pending == {}
    await _expire(hass)
    assert _requests(watchdog) == []

    # 2c: Two-frame writes (*#1*25*#14*300## + *#1*25*#1*180*0##, and *#1*55*#1*170*0## + *1*11*55##)
    # preserve both independent (where, cmd_dim) watches and coalesce simultaneous *#1*W## polls!
    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25*#14*300##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25*#1*180*0##"), clock[0])
    assert len([k for k in watchdog._watches if k[0] == "25"]) == 2
    clock[0] += 0.05
    watchdog.status_seen(OWNMessage.parse("*#1*25*14*300##"))
    assert watchdog.pending == {"25": "*#1*25*#1*180*0##"}
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*25##", "*#1*25*1##"]
    watchdog.handler.send_status_request.reset_mock()

    # Both watches expiring together coalesce duplicate *#1*55## via _COALESCE_STATUS_POLL_S
    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*55*#1*170*0##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*1*11*55##"), clock[0])
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*55##", "*#1*55*1##"]
    watchdog.handler.send_status_request.reset_mock()

    # Opposite-polarity *1*0*25## cancels all pending ON watches for 25
    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25*#14*300##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25*#1*180*0##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*1*0*25##"), clock[0])
    assert [k for k in watchdog._watches if k[0] == "25"] == [("25", None)]
    watchdog.cancel_all()

    # Item 1 fallback: raw send_message('*#1*25#4#02*#14*169##') echoed by *#1*25#4#02*14*169##
    # while registered light entity is still OFF arms a (where, None) status read-back!
    off_ent = SimpleNamespace(_full_where="25#4#02", _where="25", _attr_is_on=False)
    on_ent = SimpleNamespace(_full_where="26#4#02", _where="26", _attr_is_on=True)
    watchdog.handler.config_entry = SimpleNamespace(
        runtime_data=MyHOMERuntimeData(
            gateway=watchdog.handler,
            entities={"light": {"a": off_ent, "b": on_ent}},
        )
    )
    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25#4#02*#14*169##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*#1*26#4#02*#14*169##"), clock[0])
    clock[0] += 0.05
    watchdog.status_seen(OWNMessage.parse("*#1*25#4#02*14*169##"))
    watchdog.status_seen(OWNMessage.parse("*#1*26#4#02*14*169##"))
    assert ("25#4#02", None) in watchdog._watches
    assert ("26#4#02", None) not in watchdog._watches
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*25#4#02##"]
    watchdog.handler.send_status_request.reset_mock()

    # Also cover pre-ACK Dim 14 echo while entity is still OFF
    clock[0] += 5.0
    w_raw = clock[0]
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*25#4#02*14*169##"))
    clock[0] += 0.02
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25#4#02*#14*169##"), w_raw)
    assert ("25#4#02", None) in watchdog._watches
    watchdog.cancel_all()

    # Unregistered Dim 14 entity & runtime_data=None guard + pre-ACK plain OFF answering Dim 1 level 0 + Dim 12 cancelling plain ON watch
    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*99#4#02*#14*169##"), clock[0])
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*99#4#02*14*169##"))
    assert ("99#4#02", None) not in watchdog._watches

    watchdog.handler.config_entry = SimpleNamespace(runtime_data=None)
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25#4#02*#14*169##"), clock[0])
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*25#4#02*14*169##"))
    assert ("25#4#02", None) not in watchdog._watches

    clock[0] += 5.0
    w_off = clock[0]
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*1*0*32##"))
    clock[0] += 0.02
    watchdog.command_acknowledged(OWNCommand.parse("*#1*32*#1*100*0##"), w_off)
    assert ("32", 1) not in watchdog._watches

    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*1*1*26##"), clock[0])
    assert ("26", None) in watchdog._watches
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*26*12*120*50*80##"))
    assert ("26", None) not in watchdog._watches

    # Warm Standby + secondary bus config_entry + un-padded #4#2 entity resolution in _entity_still_not_on
    from custom_components.myhome.actuator_lock import ActuatorLockManager

    sec_light = SimpleNamespace(_full_where="27#4#2", _where="27", _attr_is_on=False)
    sec_entry = SimpleNamespace(
        runtime_data=MyHOMERuntimeData(
            gateway=SimpleNamespace(is_secondary=True, primary_gateway_mac="00:11:22:33:44:55"),
            entities={"light": {"sec_dali": sec_light}},
        )
    )
    pri_light = SimpleNamespace(_full_where="25#4#2", _where="25", _attr_is_on=False)
    pri_handler = SimpleNamespace(
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda _d: [sec_entry])),
        mac="00:11:22:33:44:55",
        is_follower=False,
        is_connected=False,
        config_entry=SimpleNamespace(
            runtime_data=MyHOMERuntimeData(
                gateway=None,  # type: ignore[arg-type]
                entities={"light": {"pri_dali": pri_light}},
            )
        ),
    )
    pri_mgr = ActuatorLockManager(pri_handler)  # type: ignore[arg-type]
    pri_handler._actuator_locks = pri_mgr

    watchdog.handler.is_standby = True
    watchdog.handler._get_primary_gateway = lambda: pri_handler
    watchdog.handler.actuator_lock_owner = lambda: pri_mgr
    watchdog.handler.config_entry = SimpleNamespace(runtime_data=None)

    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25#4#02*#14*169##"), clock[0])
    watchdog.command_acknowledged(OWNCommand.parse("*#1*27#4#02*#14*169##"), clock[0])
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*25#4#02*14*169##"))
    watchdog.status_seen(OWNMessage.parse("*#1*27#4#02*14*169##"))
    assert ("25#4#02", None) in watchdog._watches
    assert ("27#4#02", None) in watchdog._watches
    watchdog.cancel_all()

    # Orphan standby (primary_gw is None) returns False cleanly
    watchdog.handler._get_primary_gateway = lambda: None
    watchdog.handler.actuator_lock_owner = lambda: None
    clock[0] += 5.0
    watchdog.command_acknowledged(OWNCommand.parse("*#1*25#4#02*#14*169##"), clock[0])
    clock[0] += 0.02
    watchdog.status_seen(OWNMessage.parse("*#1*25#4#02*14*169##"))
    assert ("25#4#02", None) not in watchdog._watches


async def test_warm_standby_dim14_bridges_to_primary_before_echo_watchdog_status_seen(
    hass, monkeypatch
) -> None:
    """PR #977 review item 5: warm standby bridges Dim 14 echo to primary before status_seen."""
    from homeassistant.components.light import ATTR_COLOR_TEMP_KELVIN
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        DOMAIN,
        ROLE_PRIMARY,
        ROLE_STANDBY,
        TOPOLOGY_SHARED,
    )
    from custom_components.myhome.light import MyHOMELight

    pri_mac = "00:03:50:dd:00:01"
    stb_mac = "00:03:50:dd:00:02"

    pri_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.201",
            CONF_PORT: 20000,
            CONF_PASSWORD: "12345",
            CONF_MAC: pri_mac,
            CONF_NAME: "F461",
        },
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    pri_entry.add_to_hass(hass)
    pri_gw = MyHOMEGatewayHandler(hass, pri_entry)
    pri_gw.is_connected = False
    pri_gw._available = False
    pri_entry.runtime_data = MyHOMERuntimeData(gateway=pri_gw)

    harness = MockGatewayHarness()
    harness.set_custom_handler(
        lambda frame, _w: "*#1*25#4#02*14*200##*#*1##"
        if frame == "*#1*25#4#02*#14*200##"
        else "*#*1##"
    )
    port = await harness.start()

    stb_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: port,
            CONF_PASSWORD: None,
            CONF_MAC: stb_mac,
            CONF_NAME: "F461",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: pri_mac,
        },
    )
    stb_entry.add_to_hass(hass)
    stb_gw = MyHOMEGatewayHandler(hass, stb_entry)
    stb_gw.is_connected = True
    stb_gw._available = True
    stb_gw._event_session_ready.set()
    stb_entry.runtime_data = MyHOMERuntimeData(gateway=stb_gw)

    dali = MyHOMELight(
        hass=hass,
        name="Primary DALI",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="25#4#02",
        who="1",
        where="25",
        interface="02",
        dimmable=True,
        manufacturer="BTicino",
        model="F429",
        gateway=pri_gw,
        color_temp=True,
    )
    dali.hass = hass
    dali.entity_id = "light.primary_dali_25"
    dali.async_write_ha_state = MagicMock()
    dali.async_schedule_update_ha_state = MagicMock()
    dali._attr_is_on = False
    pri_entry.runtime_data.entities["light"] = {"25#4#02": dali}
    unsub_pri = async_dispatcher_connect(
        hass,
        f"myhome_message_{pri_gw.mac}",
        lambda msg: dali.handle_event(msg) if getattr(msg, "who", None) == 1 else None,
    )

    worker = asyncio.create_task(stb_gw.sending_loop(0))
    try:
        # 1. Event-session path (GatewayEventDispatcher): Dim 14 echo bridges to Primary first -> 0 watches!
        with monkeypatch.context() as mp:
            mp.setattr(stb_gw, "send", AsyncMock())
            await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})
        stb_gw._echo_watchdog.command_acknowledged(
            OWNCommand.parse("*#1*25#4#02*#14*250##"), time.monotonic()
        )
        assert ("25#4#02", 14) in stb_gw._echo_watchdog._watches
        await stb_gw._process_message(OWNMessage.parse("*#1*25#4#02*14*250##"))
        assert dali.is_on is True
        assert stb_gw._echo_watchdog.pending == {}

        # 2. Command-session reply path (CommandWorkerPool): inline Dim 14 reply bridges first -> 0 watches!
        dali._attr_is_on = False
        await dali.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 5000})
        for _ in range(50):
            if dali.is_on is True:
                break
            await asyncio.sleep(0.02)
        assert dali.is_on is True
        assert stb_gw._echo_watchdog.pending == {}
    finally:
        unsub_pri()
        stb_gw._command_pool.close()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await harness.stop()
