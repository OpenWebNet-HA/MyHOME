"""A DIMENSION 1 level 0 after an "on" status is verified, 2026-11-22 (review of #970).

The first version of #970 showed such a level 0 as "on at 1 %" for 10 s after an "on" status. The
maintainer's review showed where that is wrong
(``tests/fixtures/traces/dimmer_on_at_level_0_2026_11_20/review_frames.json``):

- an MH200 2.1.0 reports an off with a ramp as nothing but ``*#1*62*1*100*2##``: no
  ``*1*0*62##``, no DIMENSION 4 frame. Asked for its status (``*#1*62##``) it answers
  ``*1*0*62##``.
- an MH200N 1.1.8 reports even a plain off of a dimmer with a soft-off ramp as
  ``*#1*55*1*100*2##`` only. What it answers to ``*#1*55##`` for a dimmer that has just gone off
  is not in any capture (another MH200N plant answered ``*1*1*55##`` for a light that was on,
  ``tests/fixtures/traces/issue_612/myhome_trace_MH200N_startup_cen_scene_2026-10-07T20-38-24.json:1929-1941``).
- an F461 2.0.11 and an F455 1.0.76 with a dimmer that is on at level 0 answer the status
  request with ``*1*2*0010##`` (``frames.json`` of the same folder).

So the frame alone cannot say which, and the light is asked. The rule these tests fix:

1. A DIMENSION 1 level 0 while the light's last on / off status says on starts one status
   request ``*#1*<where>##`` (at most 3 in 30 s for one light).
2. Until it is closed the level 0 is shown on at 1 % when the "on" status is at most 2 s old,
   off otherwise.
3. An "on" answer (``*1*1*<where>##`` or ``*1*2*<where>##``) confirms "on at level 0": on at
   1 % at every later level 0, re-verified in the background at most every 30 s.
4. A request that is closed with no "on" status means off.
5. An off status, a DIMENSION 4 level 0, a level above 0, a higher preset and a switch-off from
   Home Assistant end the confirmation.
6. Only an "on" status that arrives while a request is out answers it. An "on" status after the
   request has ended is the light's own status: it shows the light on and confirms nothing.

Frames marked BUILT are not in a capture; a sequence or a pairing marked BUILT is put together
from captured frames that no capture shows in that order.
"""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import ColorMode

from custom_components.myhome import light as light_module
from tests.test_dimmer_on_at_level_0_2026_11_20 import (
    CAPTURE,
    LEVEL_0,
    LEVEL_1,
    OFF,
    OFF_LEVEL,
    ON_AT_2,
    PLAIN_ON,
    _feed,
    _light,
    _requests,
)

_FOLDER = Path(__file__).parent / "fixtures" / "traces" / "dimmer_on_at_level_0_2026_11_20"
REVIEW = json.loads((_FOLDER / "review_frames.json").read_text(encoding="utf-8"))
MH200 = REVIEW["mh200_off_with_speed"]
MH200_STATUS_REQUEST, MH200_OFF = "*#1*62##", "*1*0*62##"
MH200N_ON, MH200N_LEVEL_0 = "*1*6*55##", "*#1*55*1*100*2##"
# The answer of an MH200N to an area request, for a dimmer that is off
# (tests/fixtures/traces/issue_746/myhome_trace_MH200N_all_2026-10-17T14-28-20.json:1159).
MH200N_AREA_LEVEL_0 = "*#1*55*1*100*1##"
# BUILT as an answer: no capture has an MH200N asked for the status of a dimmer that has just gone
# off. The MH200 answered ``*1*0*62##`` to ``*#1*62##``; this is the same frame for WHERE 55 (in the
# review the frame appears only as one that was sent).
MH200N_OFF_BUILT = "*1*0*55##"
# A light at WHERE 55 of another MH200N plant, on
# (tests/fixtures/traces/issue_612/myhome_trace_MH200N_startup_cen_scene_2026-10-07T20-38-24.json:1941).
MH200N_PLAIN_ON = "*1*1*55##"
ON_AT_8, LEVEL_60 = "*1*8*0010##", "*#1*0010*1*160*10##"
STATUS_REQUEST, LEVEL_REQUEST = "*#1*0010##", "*#1*0010*1##"


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch: Any) -> None:
    monkeypatch.setattr(light_module, "_LEVEL_ZERO_CHECK_GRACE_S", 0, raising=False)


@pytest.fixture
def clock(monkeypatch: Any) -> list[float]:
    now = [1000.0]
    monkeypatch.setattr(light_module.time, "monotonic", lambda: now[0])
    return now


def test_the_frames_this_file_rests_on() -> None:
    review = {raw for key, rows in REVIEW.items() if not key.startswith("_") for _time, _direction, raw in rows}
    for raw in (MH200_STATUS_REQUEST, MH200_OFF, MH200N_ON, MH200N_LEVEL_0, "*1*5*62##", "*#1*62*1*100*2##"):
        assert raw in review, raw
    capture = {raw for key, rows in CAPTURE.items() if not key.startswith("_") for _time, _direction, raw in rows}
    for raw in (ON_AT_8, LEVEL_60):
        assert raw in capture, raw
    issue_746 = Path(__file__).parent / "fixtures" / "traces" / "issue_746"
    mh200n = (issue_746 / "myhome_trace_MH200N_all_2026-10-17T14-28-20.json").read_text(encoding="utf-8")
    assert MH200N_AREA_LEVEL_0 in mh200n
    issue_612 = Path(__file__).parent / "fixtures" / "traces" / "issue_612"
    plain_on = (issue_612 / "myhome_trace_MH200N_startup_cen_scene_2026-10-07T20-38-24.json").read_text(encoding="utf-8")
    assert f'"raw": "{MH200N_PLAIN_ON}"' in plain_on
    received = {raw for key, rows in REVIEW.items() if not key.startswith("_") for _time, direction, raw in rows
                if direction != "tx"}
    assert MH200N_OFF_BUILT not in received  # sent in the review, never received: as an answer it is built


# --- the MH200 family: a level 0 after "on" is the light going off ---------------------------


async def test_the_mh200_off_with_a_speed_replayed(hass: Any, clock: list[float]) -> None:
    """The received frames of the review in their order: off from the level 0 at 2.671 s on, asked once.

    BUILT timing of the answer: in the review the status request was sent by hand at 6.921 s; here the
    light is asked at the first level 0 and the double answers with the frame the MH200 answered.
    """
    light, gateway = _light(hass, where="62", answers=(MH200_OFF,))
    states: list[tuple[str, str, bool | None]] = []
    for at, direction, raw in MH200:
        if direction == "tx":
            continue
        clock[0] = 1000.0 + float(at)
        _feed(light, raw)
        at_once = light.is_on
        await hass.async_block_till_done()
        states.append((at, raw, light.is_on))
        if float(at) >= 2.671:
            assert at_once is False, (at, raw)  # 2.593 s after the "on" status: not shown on, even for a moment
    assert states[0] == ("0.078", "*1*5*62##", True)
    assert states[1] == ("0.203", "*#1*62*1*130*2##", True)
    assert [is_on for at, _raw, is_on in states if float(at) >= 2.671] == [False, False, False, False]
    assert _requests(gateway) == [MH200_STATUS_REQUEST]  # one request, and no level read


async def test_a_level_0_right_after_on_is_on_only_until_the_light_has_answered_off(
    hass: Any, clock: list[float]
) -> None:
    """MH200N: off 1 s after on (BUILT timing: the review gives 2.36 to 7.31 s). The answer is BUILT."""
    light, gateway = _light(hass, where="55", answers=(MH200N_OFF_BUILT,))
    _feed(light, MH200N_ON)
    clock[0] += 1.0
    _feed(light, MH200N_LEVEL_0)
    assert light.is_on is True and light._attr_brightness_pct == 1  # its status said on 1 s ago
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == ["*#1*55##"]

    clock[0] += 3.0
    _feed(light, MH200N_LEVEL_0)  # read again: off, and nothing more to ask
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == ["*#1*55##"]


@pytest.mark.parametrize("answers", [(MH200N_LEVEL_0,), ()], ids=["the level frame only", "nothing"])
async def test_a_status_request_closed_with_no_on_status_means_off(
    hass: Any, clock: list[float], answers: tuple[str, ...]
) -> None:
    """What an MH200N answers to the status request is not known: no "on" status is "off" either way."""
    light, gateway = _light(hass, where="55", answers=answers)
    _feed(light, MH200N_ON)
    clock[0] += 1.0
    _feed(light, MH200N_LEVEL_0)
    assert light.is_on is True
    await hass.async_block_till_done()
    assert light.is_on is False and light._attr_brightness_pct == 0
    assert _requests(gateway) == ["*#1*55##"]

    clock[0] += 0.5
    _feed(light, MH200N_LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == ["*#1*55##"]  # the "on" status is spent: no second request


async def test_the_mh200n_answer_to_an_area_request_is_off_and_the_light_is_asked(
    hass: Any, clock: list[float]
) -> None:
    """An area off never reaches the light; the resync's answer is DIMENSION 1 level 0, 5 s after on."""
    light, gateway = _light(hass, where="55")
    _feed(light, MH200N_ON)
    clock[0] += 5.0
    _feed(light, MH200N_AREA_LEVEL_0)
    assert light.is_on is False
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == ["*#1*55##"]


# --- F461 / F455: a level 0 after "on" is a dimmer that is on at level 0 ------------------------


async def test_on_at_level_0_is_confirmed_and_then_holds(hass: Any, clock: list[float]) -> None:
    """No level above 0 was ever seen: confirmed by the status answer, its level is read, never off."""
    light, gateway = _light(hass, answers=(ON_AT_2,))
    seen: list[bool | None] = []
    _feed(light, OFF, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    seen.append(light.is_on)
    await hass.async_block_till_done()
    seen.append(light.is_on)
    assert light._attr_brightness_pct == 1 and light.color_mode == ColorMode.BRIGHTNESS
    # The status answer is a preset echo of a dimmer now known to report exact levels (#851).
    assert _requests(gateway) == [STATUS_REQUEST, LEVEL_REQUEST]

    clock[0] += 0.3
    _feed(light, LEVEL_0)  # the level read's answer
    seen.append(light.is_on)
    clock[0] += 15
    _feed(light, LEVEL_0)
    seen.append(light.is_on)
    await hass.async_block_till_done()
    assert _requests(gateway).count(STATUS_REQUEST) == 1  # within the interval: nothing asked

    clock[0] += 20  # 35.3 s after the request
    _feed(light, LEVEL_0)
    _feed(light, LEVEL_0)  # a copy while the background request is out asks nothing more
    seen.append(light.is_on)
    await hass.async_block_till_done()
    seen.append(light.is_on)
    assert _requests(gateway).count(STATUS_REQUEST) == 2
    assert seen == [True] * 6
    assert light._attr_brightness_pct == 1


@pytest.mark.parametrize("ending", [OFF, OFF_LEVEL], ids=["off status", "DIMENSION 4 level 0"])
async def test_an_off_ends_the_confirmation(hass: Any, clock: list[float], ending: str) -> None:
    light, gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is True
    _feed(light, ending)
    assert light.is_on is False
    clock[0] += 1.0
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway).count(STATUS_REQUEST) == 1


@pytest.mark.parametrize("ending", [LEVEL_1, ON_AT_8], ids=["a level above 0", "a higher preset"])
async def test_a_level_above_0_ends_the_confirmation(hass: Any, clock: list[float], ending: str) -> None:
    light, _gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    _feed(light, ending)
    assert light.is_on is True
    clock[0] += 40
    _feed(light, LEVEL_0)
    assert light.is_on is False  # not confirmed any more, and nothing recent says on
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1  # asked again, answered on


async def test_a_higher_preset_as_the_answer_does_not_confirm(hass: Any, clock: list[float]) -> None:
    """The light answers "on at 60 %" (BUILT pairing of two captured frames): on, and not confirmed at level 0."""
    light, _gateway = _light(hass, answers=(ON_AT_8,))
    _feed(light, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 60
    clock[0] += 40
    _feed(light, LEVEL_0)
    assert light.is_on is False


async def test_a_plain_on_answer_confirms_at_the_minimum(hass: Any, clock: list[float]) -> None:
    light, _gateway = _light(hass, answers=(PLAIN_ON,))
    _feed(light, ON_AT_2, LEVEL_1)
    clock[0] += 60
    _feed(light, LEVEL_0)
    assert light.is_on is False and light._attr_brightness_pct == 0
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1 and light.brightness == 3
    clock[0] += 5
    _feed(light, LEVEL_0)
    assert light.is_on is True and light._attr_brightness_pct == 1


async def test_an_on_off_entity_is_promoted_and_shown_on(hass: Any, clock: list[float]) -> None:
    light, _gateway = _light(hass, answers=(ON_AT_2,), dimmable=False)
    assert light.color_mode == ColorMode.ONOFF
    _feed(light, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.color_mode == ColorMode.BRIGHTNESS
    assert light.is_on is True and light._attr_brightness_pct == 1


async def test_an_on_after_a_request_that_ended_unanswered_confirms_nothing(hass: Any, clock: list[float]) -> None:
    """MH200N, BUILT sequence: off with a ramp, on again 5 s later, off with a ramp 10 s after that.

    The first request is closed with no status. The plain on that follows is the light's own
    status, not the answer of that request: the second level 0 is asked about again and ends as off.
    """
    light, gateway = _light(hass, where="55")
    _feed(light, MH200N_ON)
    clock[0] += 3.0
    _feed(light, MH200N_LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False

    clock[0] += 5.0
    _feed(light, MH200N_PLAIN_ON)
    await hass.async_block_till_done()
    assert light.is_on is True

    clock[0] += 10.0
    _feed(light, MH200N_LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway).count("*#1*55##") == 2


# --- Home Assistant's own commands ----------------------------------------------------------------


async def test_a_switch_off_from_home_assistant_spends_the_on_status(hass: Any, clock: list[float]) -> None:
    """`light.turn_off` on an MH200 (the plain call): the level 0 that follows is off, nothing asked."""
    light, gateway = _light(hass, where="62")
    _feed(light, "*1*5*62##")
    await light.async_turn_off()
    clock[0] += 0.5
    _feed(light, "*#1*62*1*100*2##")
    assert light.is_on is False
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == []


async def test_a_switch_off_from_home_assistant_ends_the_confirmation(hass: Any, clock: list[float]) -> None:
    light, gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    await light.async_turn_off()
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway).count(STATUS_REQUEST) == 1


@pytest.mark.parametrize("switched_on", [False, True], ids=["left alone", "switched on meanwhile"])
async def test_a_request_that_is_never_closed_ends_as_off_unless_the_light_was_switched_on(
    hass: Any, monkeypatch: Any, switched_on: bool
) -> None:
    """Real clock: the wait for the request ends by its own timeout."""
    monkeypatch.setattr(light_module, "_LEVEL_ZERO_CHECK_TIMEOUT_S", 0.01, raising=False)
    light, gateway = _light(hass)
    gateway.status_outcome = "silent"
    _feed(light, ON_AT_2, LEVEL_0)
    assert light.is_on is True
    if switched_on:
        await light.async_turn_on()
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert light.is_on is switched_on
    assert _requests(gateway).count(STATUS_REQUEST) == 1


@pytest.mark.parametrize("outcome", ["not_written", "nack"])
async def test_a_request_that_was_not_answered_ends_as_off(hass: Any, clock: list[float], outcome: str) -> None:
    light, gateway = _light(hass)
    gateway.status_outcome = outcome
    _feed(light, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    assert light.is_on is True
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == [STATUS_REQUEST]


async def test_a_confirmed_dimmer_keeps_its_state_when_the_background_request_is_not_answered(
    hass: Any, clock: list[float]
) -> None:
    light, gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    gateway.status_outcome = "not_written"
    clock[0] += 31
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1
    assert _requests(gateway).count(STATUS_REQUEST) == 2

    gateway.status_outcome = "answered"
    gateway.status_answers = []  # closed with no "on" status: it went off without saying so
    clock[0] += 31
    _feed(light, LEVEL_0)
    assert light.is_on is True
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway).count(STATUS_REQUEST) == 3


async def test_a_gateway_double_that_returns_no_future_counts_as_closed(hass: Any, clock: list[float]) -> None:
    light, gateway = _light(hass)
    gateway.send_status_request = AsyncMock()
    _feed(light, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    assert light.is_on is True
    await hass.async_block_till_done()
    assert light.is_on is False
    assert gateway.send_status_request.await_count == 1


async def test_a_removed_light_drops_its_request_and_publishes_nothing(hass: Any) -> None:
    light, gateway = _light(hass)
    gateway.status_outcome = "silent"
    _feed(light, ON_AT_2, LEVEL_0)
    await asyncio.sleep(0)
    assert _requests(gateway) == [STATUS_REQUEST]
    light._publish_state = MagicMock()
    await light.async_will_remove_from_hass()
    await hass.async_block_till_done()
    light._publish_state.assert_not_called()
    assert light.is_on is True  # as it was shown; nothing concluded for a light that is gone


async def test_an_on_after_the_request_was_given_up_is_the_lights_own_status(hass: Any, monkeypatch: Any) -> None:
    """A request not closed at the timeout, then the light says on (BUILT sequence).

    The late "on" shows the light on and confirms nothing (rule 6); the level 0 that follows is
    asked about again, and that answer confirms.
    """
    monkeypatch.setattr(light_module, "_LEVEL_ZERO_CHECK_TIMEOUT_S", 0.01)
    light, gateway = _light(hass, answers=(ON_AT_2,))
    gateway.status_outcome = "silent"
    _feed(light, ON_AT_2, LEVEL_0)
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert light.is_on is False

    _feed(light, ON_AT_2)
    await hass.async_block_till_done()
    assert light.is_on is True

    gateway.status_outcome = "answered"
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1
    assert _requests(gateway).count(STATUS_REQUEST) == 2


# --- never a request loop ---------------------------------------------------------------------------


async def test_at_most_three_status_requests_in_thirty_seconds(hass: Any, clock: list[float]) -> None:
    """A light that keeps answering "on at 60 %" while its level says 0 (BUILT pairing) is not asked in a loop."""
    light, gateway = _light(hass, answers=(ON_AT_8,))
    for _ in range(10):
        _feed(light, ON_AT_2)
        clock[0] += 0.5
        _feed(light, LEVEL_0)
        await hass.async_block_till_done()
        clock[0] += 0.5
    assert _requests(gateway).count(STATUS_REQUEST) == 3

    _feed(light, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    assert light.is_on is False  # no request can be sent: as before the fix, a level 0 is off
    clock[0] += 31
    _feed(light, ON_AT_2)
    clock[0] += 0.5
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert _requests(gateway).count(STATUS_REQUEST) == 4


async def test_a_confirmed_dimmer_read_ten_times_is_asked_once(hass: Any, clock: list[float]) -> None:
    light, gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_0)
    await hass.async_block_till_done()
    for _ in range(10):
        clock[0] += 1.0
        _feed(light, LEVEL_0)
        await hass.async_block_till_done()
        assert light.is_on is True
    assert _requests(gateway).count(STATUS_REQUEST) == 1


# --- a plain "on" after a level 0 --------------------------------------------------------------------


async def test_a_plain_on_after_level_0_comes_back_at_the_last_level(hass: Any, clock: list[float]) -> None:
    """`*1*1*<where>##` carries no level: the light was shown on at 0 %."""
    light, gateway = _light(hass)
    _feed(light, ON_AT_8, LEVEL_60, OFF, OFF_LEVEL)
    assert light.is_on is False and light._attr_brightness_pct == 0
    clock[0] += 5
    _feed(light, PLAIN_ON)
    assert light.is_on is True
    assert light._attr_brightness_pct == 60 and light.brightness == 153
    await hass.async_block_till_done()
    assert _requests(gateway) == [LEVEL_REQUEST]  # a dimmer that reports exact levels is asked for its level


async def test_a_plain_on_after_level_0_on_a_dimmer_with_no_exact_level_asks_nothing(hass: Any) -> None:
    light, gateway = _light(hass)
    _feed(light, OFF_LEVEL)
    assert light._attr_brightness_pct == 0
    _feed(light, PLAIN_ON)
    assert light.is_on is True and light._attr_brightness_pct == 100  # the entity's default "last level"
    await hass.async_block_till_done()
    assert _requests(gateway) == []


async def test_a_light_with_icons_takes_its_off_icon_when_the_request_ends_as_off(
    hass: Any, clock: list[float]
) -> None:
    light, _gateway = _light(hass, where="55")
    light._off_icon, light._on_icon = "mdi:lightbulb-off", "mdi:lightbulb-on"
    _feed(light, MH200N_ON)
    clock[0] += 1.0
    _feed(light, MH200N_LEVEL_0)
    assert light.is_on is True
    await hass.async_block_till_done()
    assert light.is_on is False
    assert light.icon == "mdi:lightbulb-off"


async def test_a_switch_on_written_after_the_request_ended_no_longer_touches_it(hass: Any, monkeypatch: Any) -> None:
    """Real clock. The switch-on is still queued when the request is given up: that request decides."""
    monkeypatch.setattr(light_module, "_LEVEL_ZERO_CHECK_TIMEOUT_S", 0.01, raising=False)
    light, gateway = _light(hass)
    gateway.status_outcome = "silent"
    queued: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    gateway.send.return_value = queued
    _feed(light, ON_AT_2, LEVEL_0)
    await light.async_turn_on()
    await asyncio.sleep(0.05)
    await hass.async_block_till_done()
    assert light._level_zero_check_task is None
    assert light.is_on is False  # until the light reports the switch-on itself
    queued.set_result(0.0)  # written now: there is no request left to supersede
    await asyncio.sleep(0)
    assert light.is_on is False and light._level_zero_pending is False
    assert _requests(gateway).count(STATUS_REQUEST) == 1


def test_the_constants_of_the_rule() -> None:
    assert light_module._LEVEL_ZERO_STATUS_FRESH_S == 2.0
    assert light_module._LEVEL_ZERO_STATUS_CHECK_INTERVAL_S == 30.0
    assert light_module._LEVEL_ZERO_CHECK_BURST == 3
    assert light_module._LEVEL_ZERO_CHECK_TIMEOUT_S == 20.0
    assert light_module._LEVEL_ZERO_ON_PCT == 1
