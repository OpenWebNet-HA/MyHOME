"""Replay of the field test of PR #991: MyHomeServer1 2.87.13 + K4672M2S on an external Venetian blind.

Fixture: tests/fixtures/traces/pr_991_myhomeserver1_k4672m2s/myhome_trace_MyHomeServer1_all_2026-11-23T09-34-10.json
(the Bus Monitor export as attached to the PR, 60 frames). Its README holds the reporter's table: the position and
tilt Home Assistant showed after each step, which this replay asserts.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome.cover import MyHOMECover

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "pr_991_myhomeserver1_k4672m2s"
    / "myhome_trace_MyHomeServer1_all_2026-11-23T09-34-10.json"
)
MAC = "00:03:50:aa:bb:31"
_REAL_SLEEP = asyncio.sleep

# Index of each command frame of the capture -> the service call that wrote it (README, steps 1-5b).
SERVICE_CALLS: dict[int, tuple[str, int]] = {
    0: ("position", 0),  # step 1
    9: ("tilt", 50),  # step 2
    20: ("tilt", 100),  # step 3b
    31: ("tilt", 50),  # step 4
    42: ("position", 6),  # step 5a
    50: ("position", 3),  # step 5b
}
# Index of the last frame of each step -> (position, tilt) Home Assistant showed after it (README table).
EXPECTED_AFTER: dict[int, tuple[int, int]] = {
    8: (0, 0),  # step 1
    19: (1, 57),  # step 2
    30: (2, 100),  # step 3b
    41: (1, 42),  # step 4
    47: (6, 100),  # step 5a
    55: (3, 0),  # step 5b
    59: (5, 52),  # step 6, the wall keypad
}


def _frames() -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = json.loads(TRACE_PATH.read_text(encoding="utf-8"))["frames"]
    return frames


def test_pr_991_trace_schema_and_frame_parsing() -> None:
    """The export is the reporter's: gateway, versions, frame count, and every frame parses."""
    data = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    assert data["gateway"]["model"] == "MyHomeServer1"
    assert data["gateway"]["firmware"] == "2.87.13"
    assert data["gateway"]["mac_prefix"] == "00:03:50"
    assert data["gateway"]["identification"]["profile"] == "MyHomeServer1Profile"
    assert data["environment"]["integration_version"] == "2.0.1"
    assert data["environment"]["ownd_version"] == "2.0.1"
    assert data["capture"]["window"]["frames"] == 60
    assert data["capture"]["window"]["truncated"] is False
    frames = data["frames"]
    assert len(frames) == 60
    assert [i for i, f in enumerate(frames) if f["direction"] == "tx"] == [0, 9, 16, 20, 27, 31, 38, 42, 50]
    for frame in frames:
        assert OWNMessage.parse(frame["raw"]) is not None, frame["raw"]


def test_pr_991_trace_stopped_level_follows_each_pulse_stop() -> None:
    """After each slat pulse's stop the K4672M2S reports its level one step further, with no motion frame."""
    frames = _frames()
    # (index of the pulse's stop command, the stopped level that follows it)
    for stop, level in ((16, "*#2*31*10*10*1*001*0##"), (27, "*#2*31*10*10*2*001*0##"), (38, "*#2*31*10*10*1*001*0##")):
        assert (frames[stop]["direction"], frames[stop]["raw"]) == ("tx", "*2*0*31##")
        assert [f["raw"] for f in frames[stop + 1 : stop + 4]] == ["*2*1000#0*31##", level, "*2*0*31##"]
        assert 0.09 < frames[stop + 2]["timestamp"] - frames[stop]["timestamp"] < 0.13


def test_pr_991_trace_motor_starts_after_the_gateway_pre_stop() -> None:
    """The MyHomeServer1 stops the actuator before it moves it: the motor starts 0.2-0.27 s after the command."""
    frames = _frames()
    for command, moving in ((9, 14), (20, 25), (31, 36)):
        assert frames[command + 1]["raw"] == "*2*1000#0*31##"
        assert frames[moving]["raw"].startswith(("*#2*31*10*11*", "*#2*31*10*12*"))
        assert 0.2 < frames[moving]["timestamp"] - frames[command]["timestamp"] < 0.27


@pytest.fixture
def blind_31() -> Iterator[tuple[MyHOMECover, SimpleNamespace, MagicMock]]:
    """Cover 31 as the reporter configured it, on a clock the test sets.

    A written frame is confirmed when the next frame is received (the export holds no acknowledgement
    times): ``gateway.held`` are the writes still waiting.
    """
    clock = SimpleNamespace(now=0.0)
    gateway = MagicMock()
    gateway.mac = gateway.unique_id = MAC
    gateway.log_id = "[MyHomeServer1]"
    gateway.sent = []
    gateway.held = []

    async def _send(message: Any, *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        gateway.sent.append(str(message))
        written: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        gateway.held.append(written)
        return written

    gateway.send = AsyncMock(side_effect=_send)
    gateway.send_status_request = AsyncMock()
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=None, name="Blind 31", entity_name=None, device_id="31", who="2", where="31", interface=None,
            advanced=True, manufacturer="BTicino", model="K4672M2S", gateway=gateway, travel_time=25,
            slat_tilt=True, slat_time=0.8, min_motor_pulse=0.2, min_position_delta=1,
        )
    cover.entity_id = "cover.blind_31"
    cover.async_schedule_update_ha_state = MagicMock()
    # The level the actuator reports before the first run of the capture (frame 2).
    cover._attr_current_cover_position = 9
    with patch("custom_components.myhome.cover.time", SimpleNamespace(monotonic=lambda: clock.now)):
        yield cover, clock, gateway


async def _settle() -> None:
    for _ in range(5):
        await _REAL_SLEEP(0)


@pytest.mark.asyncio
async def test_pr_991_trace_replay_every_step(blind_31: tuple[MyHOMECover, SimpleNamespace, MagicMock]) -> None:
    """The six steps of the field test end at the position and tilt the reporter read in Home Assistant.

    Steps 2, 3b and 4 are slat pulses whose stopped level comes one step further (0 -> 1, 1 -> 2, 2 -> 1): the
    angle stays where the pulse left it (57, 100, 42), not a full rotation further. Step 3a asks for a pulse
    shorter than ``min_motor_pulse`` and writes nothing. Steps 5a and 5b are level commands: the slats follow the
    curtain. Step 6 is a keypad pulse with motion frames: the slats turn in proportion to its 0.419 s (52 %).
    """
    cover, clock, gateway = blind_31
    frames = _frames()
    release = asyncio.Event()

    async def _sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release.wait()
        await _REAL_SLEEP(0)

    seen: dict[int, tuple[int | None, int | None]] = {}
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep):
        for index, frame in enumerate(frames):
            clock.now = frame["timestamp"]
            if frame["direction"] == "tx":
                if index == 20:
                    # Step 3a, before step 3b: 57 -> 75 is 0.144 s of pulse, below min_motor_pulse.
                    written = len(gateway.sent)
                    await cover.async_set_cover_tilt_position(tilt_position=75)
                    assert len(gateway.sent) == written
                    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 57)
                if index in SERVICE_CALLS:
                    kind, value = SERVICE_CALLS[index]
                    release.clear()
                    if kind == "position":
                        await cover.async_set_cover_position(position=value)
                    else:
                        await cover.async_set_cover_tilt_position(tilt_position=value)
                else:
                    # The pulse's auto-stop writes its stop where the capture has it.
                    release.set()
                    assert cover._stop_task is not None
                    await asyncio.wait_for(asyncio.shield(cover._stop_task), 1.0)
                await _settle()
                assert gateway.sent[-1] == frame["raw"], index
                continue
            for written_future in gateway.held:
                if not written_future.done():
                    written_future.set_result(clock.now)
            gateway.held.clear()
            await _settle()
            if str(frame["who"]) == "2":
                cover.handle_event(OWNMessage.parse(frame["raw"]))
                await _settle()
            if index in EXPECTED_AFTER:
                seen[index] = (cover.current_cover_position, cover.current_cover_tilt_position)
                assert not cover._is_tilting and not cover.is_opening and not cover.is_closing, index

    assert seen == EXPECTED_AFTER
    # Nothing was written that the capture does not hold, and nothing the capture holds was left out.
    assert gateway.sent == [f["raw"] for f in frames if f["direction"] == "tx"]


@pytest.mark.asyncio
async def test_pr_991_trace_pulse_is_timed_from_the_motor_start(
    blind_31: tuple[MyHOMECover, SimpleNamespace, MagicMock],
) -> None:
    """Step 2: a 0 -> 50 tilt is a 0.4 s pulse counted from the moving level, not from the command.

    The command of frame 9 reaches the motor only after the gateway's pre-stop: the moving level of frame 14 is
    0.209 s later. Counting from the command would have stopped the slats at about a quarter.
    """
    cover, clock, gateway = blind_31
    frames = _frames()
    cover._attr_current_cover_position = 0
    cover._attr_current_cover_tilt_position = 0
    release = asyncio.Event()

    async def _sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release.wait()
        await _REAL_SLEEP(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep):
        clock.now = frames[9]["timestamp"]
        await cover.async_set_cover_tilt_position(tilt_position=50)
        assert gateway.sent == ["*2*1*31##"]
        assert cover._tilt_duration == pytest.approx(0.4)
        for frame in frames[10:16]:
            clock.now = frame["timestamp"]
            for written_future in gateway.held:
                if not written_future.done():
                    written_future.set_result(clock.now)
            gateway.held.clear()
            await _settle()
            cover.handle_event(OWNMessage.parse(frame["raw"]))
            await _settle()
        assert cover._is_tilting
        assert cover._tilt_start_time == pytest.approx(frames[14]["timestamp"])
        release.set()
        assert cover._stop_task is not None
        await asyncio.wait_for(asyncio.shield(cover._stop_task), 1.0)
