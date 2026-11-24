"""Edge cases of a slat pulse and its stopped level (review of PR #991).

Every frame is one of tests/fixtures/traces/pr_991_myhomeserver1_k4672m2s (MyHomeServer1 2.87.13 + K4672M2S,
WHERE 31), named by its index there. The orders are constructed: the capture holds no reversed pulse, no level
command during a pulse and no failed write.
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
from custom_components.myhome.cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover

TRACE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "pr_991_myhomeserver1_k4672m2s"
    / "myhome_trace_MyHomeServer1_all_2026-11-23T09-34-10.json"
)
_REAL_SLEEP = asyncio.sleep
FRAMES: list[dict[str, Any]] = json.loads(TRACE_PATH.read_text(encoding="utf-8"))["frames"]

Blind = tuple[MyHOMECover, SimpleNamespace, MagicMock, asyncio.Event]


def _raw(index: int, expected: str) -> str:
    """Frame ``index`` of the capture, checked against the text the test relies on."""
    raw: str = FRAMES[index]["raw"]
    assert raw == expected, (index, raw)
    return raw


@pytest.fixture
def blind() -> Iterator[Blind]:
    """Cover 31 of the capture (advanced, slat_time 0.8) on a clock the test sets.

    A write is confirmed when it is sent, except a stop while ``gateway.hold_stop_write`` is set
    (``gateway.held``). ``release`` lets the pulse's auto-stop fire.
    """
    clock = SimpleNamespace(now=1000.0)
    gateway = MagicMock()
    gateway.mac = gateway.unique_id = "00:03:50:aa:bb:31"
    gateway.log_id = "[MyHomeServer1]"
    gateway.sent = []
    gateway.hold_stop_write = False
    gateway.held = []

    async def _send(message: Any, *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        gateway.sent.append(str(message))
        written: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        if gateway.hold_stop_write and str(message) == "*2*0*31##":
            gateway.held.append(written)
        else:
            written.set_result(clock.now)
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
    release = asyncio.Event()

    async def _sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release.wait()
        await _REAL_SLEEP(0)

    fake_time = SimpleNamespace(monotonic=lambda: clock.now)
    with (
        patch("custom_components.myhome.cover.time", fake_time),
        patch("custom_components.myhome.cover_scope.time", fake_time),
        patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep),
    ):
        yield cover, clock, gateway, release


async def _settle() -> None:
    for _ in range(5):
        await _REAL_SLEEP(0)


async def _receive(cover: MyHOMECover, clock: SimpleNamespace, raw: str, after: float = 0.04) -> None:
    clock.now += after
    cover.handle_event(OWNMessage.parse(raw))
    await _settle()


async def _pulse_running(blind: Blind, position: int, tilt: int, target: int) -> None:
    """Start a slat pulse from ``(position, tilt)`` and receive the gateway's frames up to the moving motor."""
    cover, clock, gateway, _release = blind
    cover._attr_current_cover_position = position
    cover._attr_current_cover_tilt_position = tilt
    opening = target > tilt
    await cover.async_set_cover_tilt_position(tilt_position=target)
    assert gateway.sent[-1] == ("*2*1*31##" if opening else "*2*2*31##")
    await _receive(cover, clock, _raw(10, "*2*1000#0*31##"))
    await _receive(cover, clock, f"*#2*31*10*10*{position}*001*0##")
    await _receive(cover, clock, _raw(12, "*2*0*31##"))
    if opening:
        await _receive(cover, clock, _raw(13, "*2*1000#1*31##"))
        await _receive(cover, clock, f"*#2*31*10*11*{position}*001*0##")
        await _receive(cover, clock, _raw(15, "*2*1*31##"))
    else:
        await _receive(cover, clock, _raw(34, "*2*1000#2*31##"))
        await _receive(cover, clock, f"*#2*31*10*12*{position}*001*0##")
        await _receive(cover, clock, _raw(37, "*2*2*31##"))
    assert cover._is_tilting


async def _auto_stop(blind: Blind) -> None:
    cover, _clock, gateway, release = blind
    release.set()
    assert cover._stop_task is not None
    await asyncio.wait_for(asyncio.shield(cover._stop_task), 1.0)
    await _settle()
    assert gateway.sent[-1] == "*2*0*31##"


def test_the_levels_the_helpers_build_are_frames_of_the_capture() -> None:
    """``_pulse_running`` fills in the level: each text it builds is a frame of the capture."""
    assert _raw(11, "*#2*31*10*10*0*001*0##") and _raw(22, "*#2*31*10*10*1*001*0##")
    assert _raw(33, "*#2*31*10*10*2*001*0##")
    assert _raw(14, "*#2*31*10*11*0*001*0##") and _raw(25, "*#2*31*10*11*1*001*0##")
    assert _raw(36, "*#2*31*10*12*2*001*0##")


# ── Review 2.1: a tilt reversed while the pulse runs ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reversed_pulse_keeps_its_auto_stop_when_the_first_pulse_level_arrives(blind: Blind) -> None:
    """The first pulse's stopped level, reported at the gateway's pre-stop, does not cancel the second pulse's stop.

    The first pulse (opening) ran 0.3 s and the actuator booked it as one level (1 -> 2). The reversed pulse's
    command makes the gateway stop the actuator first, so that level arrives while the second pulse is open.
    It is the first pulse's own: taken as an external move it cancelled the auto-stop and the blind ran to its end.
    """
    cover, clock, gateway, _release = blind
    await _pulse_running(blind, 1, 57, 100)
    clock.now += 0.3
    await cover.async_set_cover_tilt_position(tilt_position=20)
    assert gateway.sent[-1] == "*2*2*31##"
    await _receive(cover, clock, _raw(32, "*2*1000#0*31##"))
    await _receive(cover, clock, _raw(29, "*#2*31*10*10*2*001*0##"))
    await _receive(cover, clock, _raw(35, "*2*0*31##"))
    await _receive(cover, clock, _raw(34, "*2*1000#2*31##"))
    await _receive(cover, clock, _raw(36, "*#2*31*10*12*2*001*0##"))
    await _receive(cover, clock, _raw(37, "*2*2*31##"))
    assert cover._is_tilting and cover.is_closing
    assert cover._stop_task is not None and not cover._stop_task.done()
    await _auto_stop(blind)
    assert cover.current_cover_position == 2
    assert not cover._is_tilting


# ── Review 2.3: a level command of another client during or right after a pulse ──────────────────────


@pytest.mark.asyncio
async def test_a_level_command_of_another_client_takes_the_motor_from_a_running_pulse(blind: Blind) -> None:
    """Another client's `*#2*31*#11#001#1*6##` during our pulse: our auto-stop must not stop its run."""
    cover, clock, gateway, release = blind
    await _pulse_running(blind, 1, 57, 100)
    sent = list(gateway.sent)
    await _receive(cover, clock, _raw(43, "*#2*31*#11#001#1*6##"))
    assert not cover._is_tilting
    release.set()
    await _settle()
    assert gateway.sent == sent, "the pulse's auto-stop was written into the other client's run"
    await _receive(cover, clock, _raw(44, "*#2*31*10*11*1*001*0##"))
    await _receive(cover, clock, _raw(45, "*2*1*31##"))
    await _receive(cover, clock, _raw(46, "*#2*31*10*10*6*001*0##"), after=2.2)
    await _receive(cover, clock, _raw(47, "*2*0*31##"))
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (6, 100)
    assert gateway.sent == sent


@pytest.mark.asyncio
async def test_a_level_command_of_another_client_ends_the_claim_of_a_finished_pulse(blind: Blind) -> None:
    """After our pulse's stop, another client's level command owns the next level: it is not the pulse's."""
    cover, clock, _gateway, _release = blind
    await _pulse_running(blind, 2, 100, 50)
    clock.now += 0.36  # 0.4 s of motor with the 0.04 s since its start
    await _auto_stop(blind)
    await _receive(cover, clock, _raw(51, "*#2*31*#11#001#1*3##"), after=0.02)
    # The stopped level one step down comes with no motion frame; after that command it is its run's.
    await _receive(cover, clock, _raw(40, "*#2*31*10*10*1*001*0##"), after=0.05)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 0)


# ── Review 2.4: a blind that follows a scope cover's pulse ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_follower_ends_at_its_angle_when_its_level_comes_before_the_scope_stop_is_confirmed(
    blind: Blind,
) -> None:
    """The scope's stop is queued, not confirmed, when the member reports its stopped level: the pulse ends there."""
    cover, clock, _gateway, _release = blind
    cover._attr_current_cover_position = 2
    cover._attr_current_cover_tilt_position = 100
    leader = MagicMock()
    leader._pulse_stop_written = asyncio.get_running_loop().create_future()  # queued, not written yet
    cover._follow_pulse(leader, "close")
    cover._tilt_start_time = clock.now  # the leader's motor start (MyHOMEScopeCover._pulse_started)
    await _receive(cover, clock, _raw(40, "*#2*31*10*10*1*001*0##"), after=0.4)
    assert not cover._is_tilting
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 50)


# ── Review 2.5: a level command for the level the blind is at ────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_position_to_the_current_level_during_a_pulse_stops_the_motor(blind: Blind) -> None:
    """`set_cover_position` to the current level writes no level frame: the running pulse must still be stopped."""
    cover, clock, gateway, release = blind
    await _pulse_running(blind, 2, 100, 50)
    clock.now += 0.16  # 0.2 s of motor with the 0.04 s since its start
    await cover.async_set_cover_position(position=2)
    await _settle()
    assert gateway.sent[-1] == "*2*0*31##", "the pulse lost its auto-stop and nothing stopped the motor"
    release.set()
    await _settle()
    assert gateway.sent.count("*2*0*31##") == 1
    assert not cover._is_tilting
    assert cover.current_cover_tilt_position == 75


@pytest.mark.asyncio
async def test_set_position_to_the_current_level_after_a_pulse_keeps_the_pulse_claim(blind: Blind) -> None:
    """A level command that writes nothing takes nothing over: the pulse's stopped level is still its own."""
    cover, clock, gateway, _release = blind
    await _pulse_running(blind, 2, 100, 50)
    clock.now += 0.36  # 0.4 s of motor with the 0.04 s since its start
    await _auto_stop(blind)
    sent = list(gateway.sent)
    await cover.async_set_cover_position(position=2)
    assert gateway.sent == sent
    await _receive(cover, clock, _raw(39, "*2*1000#0*31##"), after=0.02)
    await _receive(cover, clock, _raw(40, "*#2*31*10*10*1*001*0##"))
    await _receive(cover, clock, _raw(41, "*2*0*31##"))
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 50)


# ── Review 2.6: the claim is used once; a failed stop write; a first level of 0 or 100 ───────────────


@pytest.mark.asyncio
async def test_the_claim_of_a_pulse_is_used_once(blind: Blind) -> None:
    """After the pulse's own stopped level, a second level change inside the window is an external move."""
    cover, clock, _gateway, _release = blind
    await _pulse_running(blind, 0, 0, 50)
    clock.now += 0.36  # 0.4 s of motor with the 0.04 s since its start
    await _auto_stop(blind)
    await _receive(cover, clock, _raw(17, "*2*1000#0*31##"), after=0.02)
    await _receive(cover, clock, _raw(18, "*#2*31*10*10*1*001*0##"))
    await _receive(cover, clock, _raw(19, "*2*0*31##"))
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 50)
    await _receive(cover, clock, _raw(29, "*#2*31*10*10*2*001*0##"), after=0.3)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (2, 100)


@pytest.mark.asyncio
async def test_an_unchanged_stopped_level_uses_the_claim_too(blind: Blind) -> None:
    """The pulse's stopped level may be the level it started from (too short to count): the claim ends with it."""
    cover, clock, _gateway, _release = blind
    await _pulse_running(blind, 1, 0, 50)
    clock.now += 0.36  # 0.4 s of motor with the 0.04 s since its start
    await _auto_stop(blind)
    await _receive(cover, clock, _raw(22, "*#2*31*10*10*1*001*0##"), after=0.1)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 50)
    await _receive(cover, clock, _raw(29, "*#2*31*10*10*2*001*0##"), after=0.3)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (2, 100)


@pytest.mark.asyncio
async def test_a_stop_write_that_fails_after_the_pulse_ended_is_retrieved(blind: Blind) -> None:
    """The pulse ended on the actuator's own report; its stop write then fails: no unretrieved exception."""
    cover, clock, gateway, _release = blind
    gateway.hold_stop_write = True
    await _pulse_running(blind, 0, 0, 50)
    clock.now += 0.36  # 0.4 s of motor with the 0.04 s since its start
    await _auto_stop(blind)
    (stop_written,) = gateway.held
    await _receive(cover, clock, _raw(17, "*2*1000#0*31##"), after=0.02)
    await _receive(cover, clock, _raw(18, "*#2*31*10*10*1*001*0##"))
    assert not cover._is_tilting
    stop_written.set_exception(RuntimeError("not delivered"))
    await _settle()
    try:
        assert stop_written._log_traceback is False, "the failed stop write's exception was never retrieved"
    finally:
        stop_written.exception()


@pytest.mark.asyncio
async def test_a_first_level_of_0_after_a_pulse_does_not_close_the_slats(blind: Blind) -> None:
    """Position unknown, slats opened by a full pulse: the first level reported (0) is no slat angle."""
    cover, clock, gateway, _release = blind
    assert cover.current_cover_position is None
    await cover.async_set_cover_tilt_position(tilt_position=100)
    assert gateway.sent == ["*2*1*31##"]
    await _receive(cover, clock, _raw(15, "*2*1*31##"))
    clock.now += 0.85
    await _auto_stop(blind)
    assert cover.current_cover_tilt_position == 100
    await _receive(cover, clock, _raw(11, "*#2*31*10*10*0*001*0##"), after=0.1)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (0, 100)


# ── Review 2.4: the scope cover (area 3) that leads the pulse of blind 31 ────────────────────────────


def _area_3(blind: Blind) -> MyHOMEScopeCover:
    """The scope cover of area 3 on the local bus, with blind 31 as its member."""
    cover, _clock, gateway, _release = blind
    scope_def = CoverScope.of("3")
    assert scope_def is not None
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        area = MyHOMEScopeCover(
            hass=None, name="Area 3", entity_name=None, device_id="3", who="2", where="3", interface=None,
            advanced=False, manufacturer="BTicino", model="Area", gateway=gateway, slat_tilt=True, slat_time=0.8,
            min_motor_pulse=0.2, scope=scope_def,
        )
    area.entity_id = "cover.area_3"
    area.async_schedule_update_ha_state = MagicMock()
    family = CoverFamily()
    family.add(area)
    family.add(cover)
    assert area._members() == [cover]
    return area


@pytest.mark.asyncio
async def test_a_scope_stop_that_was_not_delivered_leaves_no_claim_on_its_followers(blind: Blind) -> None:
    """The scope's stop never reached the bus: the member's motor runs on, and its next level is not the pulse's."""
    cover, clock, gateway, _release = blind
    area = _area_3(blind)
    cover._attr_current_cover_position = 2
    cover._attr_current_cover_tilt_position = 100
    gateway.hold_stop_write = True

    async def _send(message: Any, *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        gateway.sent.append(str(message))
        written: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        if str(message) == "*2*0*3##":
            gateway.held.append(written)
        else:
            written.set_result(clock.now)
        return written

    gateway.send = AsyncMock(side_effect=_send)
    await area.async_set_cover_tilt_position(tilt_position=0)
    assert gateway.sent[-1] == "*2*2*3##"
    assert cover._is_tilting and cover._tilt_follows is area
    await _receive(cover, clock, _raw(36, "*#2*31*10*12*2*001*0##"))
    await _receive(cover, clock, _raw(37, "*2*2*31##"))
    clock.now += 0.36
    await area.async_stop_cover()
    (stop_written,) = gateway.held
    stop_written.set_exception(RuntimeError("not delivered"))
    await _settle()
    assert not cover._is_tilting
    # The motor ran on to the end of its travel: the level that follows is a curtain move.
    await _receive(cover, clock, _raw(40, "*#2*31*10*10*1*001*0##"), after=0.5)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (1, 0)


@pytest.mark.asyncio
async def test_a_scope_run_to_the_end_takes_the_motor_from_a_member_pulse(blind: Blind) -> None:
    """Area 3 is opened while blind 31 runs its own slat pulse: the pulse's auto-stop must not stop the run."""
    cover, _clock, gateway, release = blind
    area = _area_3(blind)
    await _pulse_running(blind, 2, 100, 50)
    await area.async_open_cover()
    assert gateway.sent[-1] == "*2*1*3##"
    assert not cover._is_tilting
    release.set()
    await _settle()
    assert "*2*0*31##" not in gateway.sent, "the member's pulse stop was written into the area's run"


@pytest.mark.asyncio
async def test_a_scope_cover_whose_members_stopped_can_tilt(blind: Blind) -> None:
    """The members ended the area's run on their own frames: the area is not moving and its slats can be turned."""
    cover, _clock, gateway, _release = blind
    area = _area_3(blind)
    cover._attr_current_cover_position = 2
    cover._attr_current_cover_tilt_position = 0
    area._attr_is_opening = True  # left by the area's own direction frame; no stop comes for an area
    assert not area.is_opening and not area.is_closing
    await area.async_set_cover_tilt_position(tilt_position=100)
    assert gateway.sent[-1] == "*2*1*3##"
    # End the pulse the test started.
    await area.async_stop_cover()
    await _settle()
    assert gateway.sent[-1] == "*2*0*3##"
    assert not area._is_tilting and not cover._is_tilting


@pytest.mark.asyncio
async def test_failed_mid_pulse_reversal_clears_pulse_end_claim(blind: Blind) -> None:
    """If reversing a running tilt pulse fails to send, the old pulse is not claimed as ended."""
    cover, clock, gateway, _release = blind
    await _pulse_running(blind, 0, 0, 50)
    clock.now += 0.36  # slats moved past min_motor_pulse so tilt=0 triggers a close reversal
    assert cover._is_tilting and cover._tilt_direction == "open"
    gateway.send = AsyncMock(side_effect=RuntimeError("gateway unavailable"))
    with pytest.raises(RuntimeError, match="gateway unavailable"):
        await cover.async_set_cover_tilt_position(tilt_position=0)
    assert cover._pulse_ended_at is None
    await _receive(cover, clock, _raw(18, "*#2*31*10*10*1*001*0##"), after=0.1)
    assert cover.current_cover_tilt_position == 100


@pytest.mark.asyncio
async def test_follower_checks_leaders_pulse_written_failure(blind: Blind) -> None:
    """A member following a scope pulse checks the leader's _pulse_written before claiming a pulse end."""
    cover, clock, gateway, _release = blind
    area = _area_3(blind)
    cover._attr_current_cover_position = 2
    cover._attr_current_cover_tilt_position = 100
    pending: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    gateway.send = AsyncMock(return_value=pending)
    await area.async_set_cover_tilt_position(tilt_position=0)
    assert cover._is_tilting and cover._tilt_follows is area
    pending.set_exception(RuntimeError("nack"))
    pending.exception()  # mark retrieved
    assert cover._pulse_direction_failed() is True
    cover._apply_tilt_stop(clock.now)
    assert cover._pulse_ended_at is None


@pytest.mark.asyncio
async def test_scope_failed_reversal_clears_follower_pulse_end(
    blind: Blind,
) -> None:
    """When a scope tilt reversal fails to send, neither leader nor follower keeps a pulse-end claim."""
    cover, clock, gateway, release = blind
    area = _area_3(blind)
    cover._attr_current_cover_position = 2
    cover._attr_current_cover_tilt_position = 0
    await area.async_set_cover_tilt_position(tilt_position=100)
    assert area._is_tilting and cover._is_tilting and cover._tilt_follows is area
    area._tilt_start_time = clock.now
    area._pulse_started()
    clock.now += 0.36
    gateway.send = AsyncMock(side_effect=RuntimeError("gateway unavailable"))
    with pytest.raises(RuntimeError, match="gateway unavailable"):
        await area.async_set_cover_tilt_position(tilt_position=0)
    assert not area._is_tilting and not cover._is_tilting
    assert area._pulse_ended_at is None
    assert cover._pulse_ended_at is None
    release.set()
    await _settle()


@pytest.mark.asyncio
async def test_async_failed_mid_pulse_reversal_clears_pulse_end_claim(
    blind: Blind,
) -> None:
    """When a mid-pulse reversal's queued Future fails asynchronously, the unstopped prior pulse is not claimed."""
    cover, clock, gateway, _release = blind
    await _pulse_running(blind, 0, 0, 50)
    clock.now += 0.36
    pending: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    gateway.send = AsyncMock(return_value=pending)
    await cover.async_set_cover_tilt_position(tilt_position=0)
    assert cover._is_tilting and cover._pulse_ended_unstopped is True
    pending.set_exception(RuntimeError("nack on queue"))
    await _settle()
    assert not cover._is_tilting
    assert cover._pulse_ended_at is None
    await _receive(cover, clock, _raw(18, "*#2*31*10*10*1*001*0##"), after=0.1)
    assert cover.current_cover_tilt_position == 100


@pytest.mark.asyncio
async def test_scope_async_failed_reversal_clears_follower_pulse_end(
    blind: Blind,
) -> None:
    """When a scope tilt reversal's queued Future fails asynchronously, neither leader nor follower keeps a claim."""
    cover, clock, gateway, release = blind
    area = _area_3(blind)
    cover._attr_current_cover_position = 1
    cover._attr_current_cover_tilt_position = 0
    await area.async_set_cover_tilt_position(tilt_position=100)
    area._tilt_start_time = clock.now
    area._pulse_started()
    clock.now += 0.36
    pending: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    gateway.send = AsyncMock(return_value=pending)
    await area.async_set_cover_tilt_position(tilt_position=0)
    assert area._is_tilting and cover._is_tilting
    pending.set_exception(RuntimeError("nack on queue"))
    await _settle()
    assert not area._is_tilting and not cover._is_tilting
    assert area._pulse_ended_at is None
    assert cover._pulse_ended_at is None
    assert cover.current_cover_tilt_position == 45
    await _receive(cover, clock, _raw(29, "*#2*31*10*10*2*001*0##"), after=0.1)
    assert (cover.current_cover_position, cover.current_cover_tilt_position) == (2, 100)
    release.set()
    await _settle()


@pytest.mark.asyncio
async def test_scope_async_failed_pulse_clears_interrupted_member_claim_only(
    blind: Blind,
) -> None:
    """An aborted scope pulse clears an interrupted member's claim while preserving a stopped sibling's claim."""
    cover_31, clock, gateway, _release = blind
    area = _area_3(blind)
    assert area._family is not None
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover_32 = MyHOMECover(
            hass=None, name="Blind 32", entity_name=None, device_id="32", who="2", where="32", interface=None,
            advanced=True, manufacturer="BTicino", model="K4672M2S", gateway=gateway, travel_time=25,
            slat_tilt=True, slat_time=0.8, min_motor_pulse=0.2, min_position_delta=1,
        )
    cover_32.entity_id = "cover.blind_32"
    cover_32.async_schedule_update_ha_state = MagicMock()
    area._family.add(cover_32)
    cover_32._attr_current_cover_position = 0
    cover_32._attr_current_cover_tilt_position = 50
    cover_32._pulse_ended_at = clock.now
    cover_32._pulse_ended_direction = "open"
    cover_32._pulse_ended_unstopped = False

    await _pulse_running(blind, 0, 0, 50)
    clock.now += 0.2
    pending: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    gateway.send = AsyncMock(return_value=pending)
    await area.async_set_cover_tilt_position(tilt_position=100)
    assert cover_31._pulse_ended_unstopped is True
    assert cover_32._pulse_ended_unstopped is False
    pending.set_exception(RuntimeError("nack on queue"))
    await _settle()
    assert cover_31._pulse_ended_at is None
    assert cover_32._pulse_ended_at is not None


@pytest.mark.asyncio
async def test_scope_partial_set_position_takes_motor_from_member_pulse_and_clears_claim(
    blind: Blind,
) -> None:
    """An intermediate `area.set_cover_position(50)` cancels a member's pulse stop and clears settled claims."""
    cover, clock, gateway, release = blind
    area = _area_3(blind)
    await _pulse_running(blind, 2, 100, 50)
    await area.async_set_cover_position(position=50)
    assert gateway.sent[-1] == "*2*1*3##"
    assert not cover._is_tilting
    area._cancel_stop_task()
    release.set()
    await _settle()
    assert "*2*0*31##" not in gateway.sent, "member pulse auto-stop fired into scope partial position run"

    # Also clears a settled member claim when no pulse is actively running.
    cover._pulse_ended_at = clock.now
    cover._pulse_ended_direction = "close"
    await area.async_set_cover_position(position=60)
    area._cancel_stop_task()
    assert cover._pulse_ended_at is None


@pytest.mark.asyncio
async def test_scope_pulse_interrupting_member_pulse_via_service_keeps_claim(
    blind: Blind,
) -> None:
    """`area.async_set_cover_tilt_position` interrupts a member's running slat pulse instead of raising busy."""
    cover, clock, gateway, release = blind
    area = _area_3(blind)
    await _pulse_running(blind, 2, 100, 50)
    clock.now += 0.16  # 0.20 s elapsed with the 0.04 s after *#2*31*10*12*2*001*0## -> member tilt at 75%

    # Calling area tilt to 75% (run_duration <= 0 while member tilts) stops via scope stop.
    await area.async_set_cover_tilt_position(tilt_position=75)
    await _settle()
    assert gateway.sent[-1] == "*2*0*3##"
    assert not cover._is_tilting and cover.current_cover_tilt_position == 75

    # Now start another member pulse (closing from 75 -> 0) and interrupt it mid-pulse with an opening scope pulse.
    release.clear()
    await _pulse_running(blind, 2, 75, 0)
    clock.now += 0.16  # 0.20 s elapsed with the 0.04 s after *#2*31*10*12*2*001*0## -> member tilt at 50%
    assert cover._live_tilt(clock.now) == 50
    await area.async_set_cover_tilt_position(tilt_position=100)
    assert gateway.sent[-1] == "*2*1*3##"
    assert area._is_tilting and cover._is_tilting and cover._tilt_follows is area
    # Pre-move stop causes actuator to emit the interrupted closing pulse's stopped level (2 -> 1): member stays attached.
    await _receive(cover, clock, _raw(40, "*#2*31*10*10*1*001*0##"), after=0.05)
    assert cover.current_cover_position == 1
    assert cover._is_tilting and cover._tilt_follows is area
    await area.async_stop_cover()
    release.set()
    await _settle()


@pytest.mark.asyncio
async def test_partial_set_position_on_mh201_pre_stops_running_pulse_before_takeover(
    blind: Blind,
) -> None:
    """On MH201/MH202, partial `set_cover_position` emits pre-move STOP before clearing a timed pulse's relay state."""
    cover, _clock, gateway, release = blind
    gateway.emits_cover_pre_move_stop = False
    area = _area_3(blind)
    assert area._family is not None
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover_32 = MyHOMECover(
            hass=None, name="Blind 32", entity_name=None, device_id="32", who="2", where="32", interface=None,
            advanced=False, manufacturer="BTicino", model="L4671/2", gateway=gateway, travel_time=25,
            slat_tilt=True, slat_time=0.8, min_motor_pulse=0.2,
        )
    cover_32.entity_id = "cover.blind_32"
    cover_32.async_schedule_update_ha_state = MagicMock()
    area._family.add(cover_32)

    # 1. Scope partial takeover while timed member `cover_32` runs its own slat pulse (no `_relay_hold_until`).
    cover._attr_current_cover_position = 50
    cover_32._attr_current_cover_position = 50
    cover_32._attr_current_cover_tilt_position = 0
    await cover_32.async_set_cover_tilt_position(tilt_position=50)
    assert gateway.sent[-1] == "*2*1*32##"
    assert cover_32._is_tilting and cover_32._relay_hold_until is None

    scope_task = asyncio.create_task(area.async_set_cover_position(position=20))
    await _settle()
    assert gateway.sent[-1] == "*2*0*3##", "MH201/MH202 pre-move scope STOP was skipped"
    assert "*2*2*3##" not in gateway.sent
    assert not cover_32._is_tilting

    release.set()
    await scope_task
    assert gateway.sent[-2:] == ["*2*0*3##", "*2*2*3##"]
    assert "*2*0*32##" not in gateway.sent
    area._cancel_stop_task()

    # 2. Point partial takeover while timed `cover_32` runs a slat pulse.
    release.clear()
    cover_32._attr_current_cover_position = 50
    cover_32._attr_current_cover_tilt_position = 50
    cover_32._attr_is_opening = False
    cover_32._attr_is_closing = False
    cover_32._clear_relay_hold()
    await cover_32.async_set_cover_tilt_position(tilt_position=0)
    assert gateway.sent[-1] == "*2*2*32##"
    assert cover_32._is_tilting and cover_32._relay_hold_until is None

    sent_idx = len(gateway.sent)
    point_task = asyncio.create_task(cover_32.async_set_cover_position(position=80))
    await _settle()
    assert gateway.sent[sent_idx:] == ["*2*0*32##"], "MH201/MH202 pre-move point STOP was skipped"

    release.set()
    await point_task
    assert gateway.sent[sent_idx:] == ["*2*0*32##", "*2*1*32##"]
    assert cover_32._pulse_ended_at is None
    cover_32._cancel_stop_task()


@pytest.mark.asyncio
async def test_scope_set_position_at_current_level_checks_member_tilting_and_motion(
    blind: Blind,
) -> None:
    """`area.set_cover_position(curr_pos)` stops a member's own pulse or curtain move, and ignores stale scope flags."""
    cover, clock, gateway, release = blind
    area = _area_3(blind)

    # 1. `diff == 0` while a member runs its own slat pulse stops via `*2*0*3##`.
    await _pulse_running(blind, 2, 100, 50)
    clock.now += 0.16  # 0.20 s elapsed -> member tilt at 75%
    await area.async_set_cover_position(position=2)
    await _settle()
    assert gateway.sent[-1] == "*2*0*3##"
    assert not cover._is_tilting and cover.current_cover_tilt_position == 75
    release.set()
    await _settle()
    assert "*2*0*31##" not in gateway.sent

    # 2. `diff == 0` when members are stationary (even with stale `area._attr_is_opening`) writes nothing and keeps claim.
    cover._attr_current_cover_position = 50
    cover._pulse_ended_at = clock.now
    cover._pulse_ended_direction = "close"
    area._attr_is_opening = True
    sent_before = list(gateway.sent)
    await area.async_set_cover_position(position=50)
    assert gateway.sent == sent_before
    assert cover._pulse_ended_at is not None

    # 3. `diff == 0` while a member's curtain moves on its own (with `area._attr_is_opening == False`) sends `*2*0*3##`.
    area._attr_is_opening = False
    cover._attr_is_opening = True
    await area.async_set_cover_position(position=50)
    assert gateway.sent[-1] == "*2*0*3##"
