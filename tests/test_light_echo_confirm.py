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

    # The same when the dimmer's status never comes: it is asked.
    clock[0] += 5  # a later write: the status above is older than it
    watchdog.command_acknowledged(OWNCommand.parse("*#1*32*#1*130*0##"), clock[0])
    await _expire(hass)
    assert _requests(watchdog) == ["*#1*32##"]


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
