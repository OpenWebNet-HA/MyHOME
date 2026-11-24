"""Replay of the second field test of PR #991: MyHomeServer1 2.87.13 + F401 on an external Venetian blind.

Fixture: tests/fixtures/traces/pr_991_myhomeserver1_f401/myhome_trace_kitchen_MyHomeServer1_all_2026-11-23T14-42-26.json
(the Bus Monitor export as attached to the PR, 82 frames). Its README holds the reporter's table: the position and
tilt Home Assistant showed after each step, which this replay asserts. Same gateway, blind model and motor as the
K4672M2S run (tests/test_pr_991_myhomeserver1_k4672m2s_trace.py): the actuator is the only difference.
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
    / "pr_991_myhomeserver1_f401"
    / "myhome_trace_kitchen_MyHomeServer1_all_2026-11-23T14-42-26.json"
)
MAC = "00:03:50:aa:bb:62"
WHERE = "62"
_REAL_SLEEP = asyncio.sleep

# Index of each command frame of the capture -> the service call that wrote it (README, steps 1-5b).
SERVICE_CALLS: dict[int, tuple[str, int]] = {
    0: ("position", 0),  # step 1
    13: ("tilt", 50),  # step 2
    28: ("tilt", 100),  # step 3b
    47: ("tilt", 50),  # step 4
    62: ("position", 6),  # step 5a
    68: ("position", 3),  # step 5b
}
# Index of the last frame of each step -> (position, tilt) Home Assistant showed after it (README table).
EXPECTED_AFTER: dict[int, tuple[int, int]] = {
    8: (0, 0),  # step 1
    23: (1, 58),  # step 2
    38: (1, 100),  # step 3b
    57: (0, 42),  # step 4
    67: (6, 100),  # step 5a
    73: (3, 0),  # step 5b
    # Step 6, the wall keypad. The reporter read tilt 76: the pulse is 0.612181 s on the export's clock,
    # 76.52 % of slat_time 0.8, which is 0.0002 s from the rounding edge.
    79: (5, 77),
}


def _frames() -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = json.loads(TRACE_PATH.read_text(encoding="utf-8"))["frames"]
    return frames


@pytest.fixture
def blind_62() -> Iterator[tuple[MyHOMECover, SimpleNamespace, MagicMock]]:
    """Cover 62 as the reporter configured it, on a clock the test sets.

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
            hass=None, name="Blind 62", entity_name=None, device_id="62", who="2", where="62", interface=None,
            advanced=True, manufacturer="BTicino", model="F401", gateway=gateway, travel_time=25,
            slat_tilt=True, slat_time=0.8, min_motor_pulse=0.2, min_position_delta=1,
        )
    cover.entity_id = "cover.blind_62"
    cover.async_schedule_update_ha_state = MagicMock()
    # The level the actuator reports before the first run of the capture (frame 2).
    cover._attr_current_cover_position = 15
    with patch("custom_components.myhome.cover.time", SimpleNamespace(monotonic=lambda: clock.now)):
        yield cover, clock, gateway


async def _settle() -> None:
    for _ in range(5):
        await _REAL_SLEEP(0)


def _own(frame: dict[str, Any]) -> bool:
    """A frame of the blind under test (the capture also holds another blind, lights and the gateway's clock)."""
    return str(frame["who"]) == "2" and frame["where"] == WHERE


def test_pr_991_f401_trace_schema_and_frame_parsing() -> None:
    """The export is the reporter's: gateway, versions, frame count, and every frame parses."""
    data = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    assert data["gateway"]["model"] == "MyHomeServer1"
    assert data["gateway"]["firmware"] == "2.87.13"
    assert data["gateway"]["mac_prefix"] == "00:03:50"
    assert data["environment"]["integration_version"] == "2.0.1"
    assert data["environment"]["ownd_version"] == "2.0.1"
    assert data["capture"]["window"]["frames"] == 82
    assert data["capture"]["window"]["truncated"] is False
    frames = data["frames"]
    assert len(frames) == 82
    assert [i for i, f in enumerate(frames) if f["direction"] == "tx"] == [0, 13, 20, 28, 35, 47, 54, 62, 68]
    for frame in frames:
        assert OWNMessage.parse(frame["raw"]) is not None, frame["raw"]


def test_pr_991_f401_trace_stopped_level_after_each_pulse_stop() -> None:
    """After a slat pulse's stop the F401 reports a stopped level with no motion frame: one step up, unchanged, one down.

    The K4672M2S on the same gateway and blind model reported 0 -> 1, 1 -> 2 and 2 -> 1 for the same three pulses.
    """
    frames = _frames()
    # (index of the pulse's stop command, the stopped level that follows it)
    for stop, level in ((20, "*#2*62*10*10*1*001*0##"), (35, "*#2*62*10*10*1*001*0##"), (54, "*#2*62*10*10*0*001*0##")):
        assert (frames[stop]["direction"], frames[stop]["raw"]) == ("tx", "*2*0*62##")
        assert [f["raw"] for f in frames[stop + 1 : stop + 4]] == ["*2*1000#0*62##", level, "*2*0*62##"]
        assert 0.06 < frames[stop + 2]["timestamp"] - frames[stop]["timestamp"] < 0.12
    # The level before each of the three pulses: 0, 1, 1.
    assert [frames[i]["raw"] for i in (15, 30, 49)] == [
        "*#2*62*10*10*0*001*0##",
        "*#2*62*10*10*1*001*0##",
        "*#2*62*10*10*1*001*0##",
    ]


def test_pr_991_f401_trace_motor_starts_after_the_gateway_pre_stop() -> None:
    """The MyHomeServer1 stops the actuator before it moves it: the motor starts about 0.2 s after the command."""
    frames = _frames()
    for command, moving in ((13, 18), (28, 33), (47, 52)):
        assert frames[command + 1]["raw"] == "*2*1000#0*62##"
        assert frames[moving]["raw"].startswith(("*#2*62*10*11*", "*#2*62*10*12*"))
        assert 0.19 < frames[moving]["timestamp"] - frames[command]["timestamp"] < 0.22


def test_pr_991_f401_trace_level_command_downwards_has_no_motion_frame() -> None:
    """Step 5b: the F401 answers the level command 6 -> 3 with stopped levels only, no moving level in between."""
    frames = _frames()
    assert [f["raw"] for f in frames[68:74]] == [
        "*#2*62*#11#001*3##",
        "*#2*62*#11#001#1*3##",
        "*#2*62*10*10*6*001*0##",
        "*2*0*62##",
        "*#2*62*10*10*3*001*0##",
        "*2*0*62##",
    ]


def test_pr_991_f401_trace_keypad_pulse_is_reported_as_advanced_commands() -> None:
    """Step 6: the wall keypad's press and release reach the gateway as advanced up and advanced stop."""
    frames = _frames()
    assert [f["raw"] for f in frames[74:80]] == [
        "*2*1000#11#100#001#1*62##",
        "*#2*62*10*11*3*001*0##",
        "*2*1*62##",
        "*2*1000#10#001#1*62##",
        "*#2*62*10*10*5*001*0##",
        "*2*0*62##",
    ]
    assert frames[78]["timestamp"] - frames[75]["timestamp"] == pytest.approx(0.612, abs=0.001)


@pytest.mark.asyncio
async def test_pr_991_f401_trace_replay_every_step(blind_62: tuple[MyHOMECover, SimpleNamespace, MagicMock]) -> None:
    """The six steps of the field test end at the position and tilt the reporter read in Home Assistant.

    Steps 2, 3b and 4 are slat pulses; the stopped level after them is 0 -> 1, 1 -> 1 and 1 -> 0, and the angle
    stays where the pulse left it (58, 100, 42). Step 3a asks for a pulse shorter than ``min_motor_pulse`` and
    writes nothing. Steps 5a and 5b are level commands: the slats follow the curtain, in 5b without a moving
    level. Step 6 is a keypad pulse with motion frames: the slats turn in proportion to its 0.612 s (76.5 %:
    77 on the export's clock, 76 in the reporter's Home Assistant).
    """
    cover, clock, gateway = blind_62
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
                if index == 28:
                    # Step 3a, before step 3b: 58 -> 75 is 0.136 s of pulse, below min_motor_pulse.
                    written = len(gateway.sent)
                    await cover.async_set_cover_tilt_position(tilt_position=75)
                    assert len(gateway.sent) == written
                    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 58)
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
            if _own(frame):
                cover.handle_event(OWNMessage.parse(frame["raw"]))
                await _settle()
            if index in EXPECTED_AFTER:
                seen[index] = (cover.current_cover_position, cover.current_cover_tilt_position)
                assert not cover._is_tilting and not cover.is_opening and not cover.is_closing, index

    assert seen == EXPECTED_AFTER
    # Nothing was written that the capture does not hold, and nothing the capture holds was left out.
    assert gateway.sent == [f["raw"] for f in frames if f["direction"] == "tx"]
