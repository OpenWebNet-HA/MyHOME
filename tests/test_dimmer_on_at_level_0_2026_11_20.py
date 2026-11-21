"""A dimmer that is on at exact level 0 is on, 2026-11-20.

Live, an F418 at 0010 behind an F461 2.0.11 and an F455 1.0.76
(tests/fixtures/traces/dimmer_on_at_level_0_2026_11_20): switched on, it reported
``*1*2*0010##`` and answered the level read with ``*#1*0010*1*100*10##``, level 0 in
DIMENSION 1, steadily. OWNd reads level 0 as "off", the entity followed, and the light was
shown off while its own status said on: the header toggle of a card left it on at the next
"all off", and ``light.turn_on`` changed nothing (the dimmer was on already).

The rule (reworked after the review of #970, see ``test_dimmer_level_0_verified_2026_11_22.py``):
a DIMENSION 1 level 0 that follows an "on" status of the light is verified with a status request
(``*#1*<where>##``). Answered "on", the dimmer is on at the minimum (1 %) and stays so at every
later level 0; closed with no "on" answer, it is off (an MH200 reports an off with a ramp as
nothing but that level 0). Until the answer the level 0 is shown on only when the "on" status is
at most ``_LEVEL_ZERO_STATUS_FRESH_S`` old. Everything else is as before: an "off" status, a
DIMENSION 4 level 0 and a DIMENSION 1 level 0 with no "on" status before it mean off.

Every frame is in a capture (``test_the_frames_this_file_rests_on``).
"""

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import ColorMode
from OWNd.message import OWNLightingEvent, OWNMessage

from custom_components.myhome import light as light_module
from custom_components.myhome.light import MyHOMELight

_TRACES = Path(__file__).parent / "fixtures" / "traces"
CAPTURE = json.loads((_TRACES / "dimmer_on_at_level_0_2026_11_20" / "frames.json").read_text(encoding="utf-8"))
ON_AT_2, LEVEL_0, LEVEL_1 = "*1*2*0010##", "*#1*0010*1*100*10##", "*#1*0010*1*101*10##"
OFF, OFF_LEVEL = "*1*0*0010##", "*#1*0010*4*100*5##"
PLAIN_ON = "*1*1*0010##"
# An F418U2 at 32 behind an MH202, which answers a level read for a dimmer that is off in
# DIMENSION 1 (tests/fixtures/traces/issue_466, MH202 F418U2 trace).
MH202_OFF_LEVEL = "*#1*32*1*100*0##"


@pytest.fixture(autouse=True)
def _no_grace(monkeypatch: Any) -> None:
    """The wait for the replies that follow a closed request: nothing to wait for in a test."""
    monkeypatch.setattr(light_module, "_LEVEL_ZERO_CHECK_GRACE_S", 0, raising=False)


def _light(
    hass: Any, where: str = "0010", *, answers: tuple[str, ...] = (), dimmable: bool = True
) -> tuple[MyHOMELight, Any]:
    """A light and a gateway that answers its status request ``*#1*<where>##``.

    ``gateway.requests``: every status request, in order. ``gateway.status_answers``: the frames
    the light receives for its status request. ``gateway.status_outcome``: ``answered`` (the
    request is closed, then the answers arrive, as the command pool does), ``not_written`` (the
    delivery future is cancelled), ``nack`` (cancelled with the mark of a NACK) or ``silent``
    (never closed).
    """
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.log_id = "[gw]"
    gateway.requests = []
    gateway.status_answers = list(answers)
    gateway.status_outcome = "answered"
    light = MyHOMELight(
        hass=hass, name="Dimmer", entity_name="Dimmer", icon=None, icon_on=None,
        device_id=where, who="1", where=where, interface=None, dimmable=dimmable,
        manufacturer="BTicino", model="F418", gateway=gateway,
    )

    async def send_status_request(message: Any) -> "asyncio.Future[float]":
        raw = str(message)
        gateway.requests.append(raw)
        written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        if raw != f"*#1*{where}##":
            written.set_result(0.0)
        elif gateway.status_outcome == "answered":
            written.set_result(0.0)
            frames = list(gateway.status_answers)
            asyncio.get_running_loop().call_soon(lambda: _feed(light, *frames))
        elif gateway.status_outcome in ("not_written", "nack"):
            if gateway.status_outcome == "nack":
                setattr(written, "_gateway_nack", True)
            written.cancel()
        return written

    gateway.send_status_request = send_status_request
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    return light, gateway


def _feed(light: MyHOMELight, *frames: str) -> None:
    """Hand the light its frames as the platform does: events only, never a relayed command."""
    for raw in frames:
        message = OWNMessage.parse(raw)
        if isinstance(message, OWNLightingEvent):
            light.handle_event(message)


def _requests(gateway: Any) -> list[str]:
    return list(gateway.requests)


def _received(trace: str) -> list[str]:
    return [raw for _time, direction, raw in CAPTURE[trace] if direction == "rx"]


def test_the_frames_this_file_rests_on() -> None:
    captured = {raw for key, rows in CAPTURE.items() if not key.startswith("_") for _time, _direction, raw in rows}
    for raw in (ON_AT_2, LEVEL_0, LEVEL_1, OFF, OFF_LEVEL, PLAIN_ON, "*1*4*0010##", "*#1*0010*1*120*10##",
                "*#1*0010*4*100*2##", "*1*8*0010##", "*#1*0010*1*160*10##"):
        assert raw in captured, raw
    mh202 = (_TRACES / "issue_466" / "myhome_trace_MH202_f418u2_dimmer_2026-09-27T09-43-45.json").read_text(encoding="utf-8")
    assert MH202_OFF_LEVEL in mh202
    # The stuck state as it was captured: on, then level 0 in DIMENSION 1, on both gateways.
    for trace in ("f455_on_at_level_0", "f461_on_at_level_0"):
        frames = _received(trace)
        assert frames.index(ON_AT_2) < frames.index(LEVEL_0), trace
        assert frames[-1] == ON_AT_2, trace  # twelve seconds later it still says on


async def test_on_then_level_0_is_on_at_the_minimum(hass: Any) -> None:
    light, gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, OFF, PLAIN_ON, ON_AT_2, LEVEL_0)
    assert light.is_on is True and light._attr_brightness_pct == 1  # at once: its status has just said on
    await hass.async_block_till_done()
    assert light.is_on is True
    assert light._attr_brightness_pct == 1 and light.brightness == 3
    assert light.color_mode == ColorMode.BRIGHTNESS
    assert light._last_brightness_pct != 0  # the level to come back to is not "0"
    assert _requests(gateway).count("*#1*0010##") == 1  # asked once, answered on

    _feed(light, OFF, OFF_LEVEL)  # switched off: its status, and DIMENSION 4 level 0
    assert light.is_on is False


@pytest.mark.parametrize("trace", ["f455_on_at_level_0", "f461_on_at_level_0"])
async def test_the_capture_of_the_stuck_dimmer_replayed(hass: Any, trace: str) -> None:
    light, _gateway = _light(hass, answers=(ON_AT_2,))
    frames = _received(trace)
    at_level_0 = frames.index(LEVEL_0)
    _feed(light, *frames[: at_level_0 + 1])
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1  # right after the level 0 frame
    _feed(light, *frames[at_level_0 + 1 :])
    assert light.is_on is True and light._attr_brightness_pct == 1


async def test_the_capture_of_the_levels_replayed(hass: Any) -> None:
    """Off, on at level 0 (three reads), 1, 2, 5, 10, 20 %, off, on at 20 %: the state after each frame."""
    light, _gateway = _light(hass, answers=(ON_AT_2,))
    seen: list[tuple[str, bool | None, int | None]] = []
    for raw in _received("f461_levels"):
        _feed(light, raw)
        seen.append((raw, light.is_on, light._attr_brightness_pct))
    states = {raw: (is_on, pct) for raw, is_on, pct in seen}  # the last time each frame came
    assert states[LEVEL_1] == (True, 1)
    assert states["*#1*0010*1*102*10##"] == (True, 2)
    assert states["*#1*0010*1*105*10##"] == (True, 5)
    assert states["*#1*0010*1*110*10##"] == (True, 10)
    assert states["*#1*0010*1*120*10##"] == (True, 20)
    assert sum(raw == LEVEL_0 for raw, _is_on, _pct in seen) >= 3
    assert all(is_on is True and pct == 1 for raw, is_on, pct in seen if raw == LEVEL_0)
    assert all(is_on is False for raw, is_on, _pct in seen if raw in (OFF, OFF_LEVEL))
    assert seen[-1][1:] == (True, 20)  # off, then on again: back at 20 %


@pytest.mark.parametrize("trace", ["f461_dim_steps", "f461_level_0_write_through_f455", "f461_off_with_speed"])
async def test_the_other_captures_replayed_are_never_on_at_zero(hass: Any, trace: str) -> None:
    """Dim steps, a level 0 write through the F455, off with a speed: the light is never on at 0 %."""
    light, _gateway = _light(hass)
    for raw in _received(trace):
        _feed(light, raw)
        assert not (light.is_on and light._attr_brightness_pct == 0), raw
        if raw in (OFF, OFF_LEVEL, "*#1*0010*4*100*2##"):
            assert light.is_on is False, raw
    assert light.is_on is True  # each of these traces ends with the dimmer on


async def test_off_with_a_speed_is_off_at_its_dimension_4_frame(hass: Any) -> None:
    """`*1*0#2*0010##` from another client: the dimmer says `*#1*0010*4*100*2##` at once, its off status later."""
    light, _gateway = _light(hass)
    _feed(light, "*1*8*0010##", "*#1*0010*1*160*10##")
    assert light.is_on is True and light._attr_brightness_pct == 60
    _feed(light, "*#1*0010*4*100*2##")
    assert light.is_on is False
    _feed(light, LEVEL_0)  # and a DIMENSION 1 level 0 after it is "off" too: the last word was off
    assert light.is_on is False


async def test_level_0_with_no_status_of_the_light_is_off(hass: Any) -> None:
    """What an MH202 answers a level read with for a dimmer that is off; nothing said it was on."""
    light, gateway = _light(hass, where="32")
    _feed(light, MH202_OFF_LEVEL)
    await hass.async_block_till_done()
    assert light.is_on is False
    assert _requests(gateway) == []

    # On by its level reports only (no status frame): a level 0 is off, as before.
    _feed(light, "*#1*32*1*130*5##")
    assert light.is_on is True
    _feed(light, MH202_OFF_LEVEL)
    assert light.is_on is False


async def test_level_0_after_an_off_status_is_off(hass: Any) -> None:
    light, _gateway = _light(hass)
    _feed(light, ON_AT_2, LEVEL_1, OFF, LEVEL_0)
    assert light.is_on is False


async def test_level_0_long_after_the_on_status_is_off_and_the_light_is_asked(hass: Any, monkeypatch: Any) -> None:
    """An "on" status that is not recent: off until the light has answered, and one status request."""
    clock = [1000.0]
    monkeypatch.setattr(light_module.time, "monotonic", lambda: clock[0])
    light, gateway = _light(hass, answers=(ON_AT_2,))
    _feed(light, ON_AT_2, LEVEL_1)
    clock[0] += 60
    _feed(light, LEVEL_0)
    assert light.is_on is False  # nothing recent says on
    await hass.async_block_till_done()
    # It answered "on": the light is on at the minimum, and its level is asked (a fine dimmer).
    assert light.is_on is True and light._attr_brightness_pct == 1
    assert _requests(gateway).count("*#1*0010##") == 1
    assert _requests(gateway).count("*#1*0010*1##") == 1

    clock[0] += 0.4
    _feed(light, LEVEL_0)  # the level that comes back
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1

    # The same level 15 s later is still the level of a light that is on, and asks nothing.
    clock[0] += 15
    _feed(light, LEVEL_0)
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1
    assert _requests(gateway).count("*#1*0010##") == 1

    # Later than the interval it is verified again, in the background: never shown off.
    clock[0] += 30
    _feed(light, LEVEL_0)
    assert light.is_on is True
    await hass.async_block_till_done()
    assert light.is_on is True and light._attr_brightness_pct == 1
    assert _requests(gateway).count("*#1*0010##") == 2


async def test_it_answers_off_when_asked(hass: Any, monkeypatch: Any) -> None:
    clock = [1000.0]
    monkeypatch.setattr(light_module.time, "monotonic", lambda: clock[0])
    light, _gateway = _light(hass)
    _feed(light, ON_AT_2)
    clock[0] += 60
    _feed(light, LEVEL_0, OFF)
    assert light.is_on is False


async def test_a_state_restored_at_start_is_not_a_status(hass: Any) -> None:
    """Home Assistant restored "on"; the bus has said nothing: a level 0 is off."""
    light, _gateway = _light(hass)
    light._attr_is_on = True
    light._is_on_restored = True
    _feed(light, LEVEL_0)
    assert light.is_on is False


async def test_a_dim_step_and_a_level_report_are_no_status(hass: Any) -> None:
    light, _gateway = _light(hass)
    _feed(light, "*1*30*0010##", "*#1*0010*1*120*10##")  # a dim step, a level: the light is on by its level
    assert light._status_on is None
    _feed(light, LEVEL_0)
    assert light.is_on is False


async def test_no_load_ends_the_on_status(hass: Any) -> None:
    light, _gateway = _light(hass, where="74")
    _feed(light, "*1*1*74##", "*1*19*74##")
    assert light._status_on is False


async def test_level_0_while_on_does_not_end_a_fade(hass: Any) -> None:
    light, _gateway = _light(hass)
    _feed(light, ON_AT_2)
    light._fade_engine = MagicMock(is_fading=True)
    light._cancel_fade_if_active = MagicMock()
    light._attr_brightness_pct = 5
    _feed(light, LEVEL_0)
    light._cancel_fade_if_active.assert_not_called()
    assert light.is_on is True and light._attr_brightness_pct == 5  # the fade's own value stays


async def test_the_light_asks_nothing_before_it_is_added(hass: Any, monkeypatch: Any) -> None:
    clock = [1000.0]
    monkeypatch.setattr(light_module.time, "monotonic", lambda: clock[0])
    light, gateway = _light(hass)
    light.hass = None
    _feed(light, ON_AT_2)
    clock[0] += 60
    _feed(light, LEVEL_0)
    assert _requests(gateway) == []
