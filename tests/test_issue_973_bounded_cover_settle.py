"""Tests for Issue #973: drift-bounded full-run settle timer for timed covers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import ATTR_CURRENT_POSITION
from homeassistant.const import STATE_OPEN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome.const import CONF_STRATEGY, CONF_TRAVEL_TIME
from custom_components.myhome.cover import CoverFamily, CoverScope, MyHOMECover
from custom_components.myhome.cover_motion import (
    END_SETTLE_MARGIN,
    SEGMENT_STOP_DRIFT,
    SEGMENT_TRAVEL_DRIFT_FACTOR,
    accumulate_drift_bound,
    compute_bounded_settle_travel,
)
from custom_components.myhome.cover_scope import MyHOMEScopeCover
from custom_components.myhome.cover_strategies import TimedCurtainStrategy

_REAL_SLEEP = asyncio.sleep


class _Clock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial
        self.hold_settle = False
        self.release: asyncio.Event | None = None


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def fake_clock(clock: _Clock):
    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def _fake_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        sleeps.append(delay)
        if clock.hold_settle and delay > 2.0 and clock.release is not None:
            await clock.release.wait()
        if delay > 0:
            clock.now += delay
        await real_sleep(0)

    with (
        patch("custom_components.myhome.cover.time") as cover_time,
        patch("custom_components.myhome.cover_scope.time") as scope_time,
        patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_fake_sleep),
        patch("custom_components.myhome.cover_scope.asyncio.sleep", side_effect=_fake_sleep),
    ):
        cover_time.monotonic.side_effect = lambda: clock.now
        scope_time.monotonic.side_effect = lambda: clock.now
        yield sleeps


async def _yield_loop(turns: int = 6) -> None:
    for _ in range(turns):
        await _REAL_SLEEP(0)


async def _start_run(cover: MyHOMECover, direction: str) -> None:
    """Queue an open/close run and deliver its motor-start echo frame."""
    if direction == "open":
        await cover.async_open_cover()
        what = 1
    else:
        await cover.async_close_cover()
        what = 2
    await _REAL_SLEEP(0)
    cover.handle_event(OWNEvent.parse(f"*2*{what}*{cover._full_where}##"))


async def _start_position_end_run(cover: MyHOMECover, target: int) -> None:
    """Queue a 0%/100% set_cover_position run and deliver its motor-start echo frame."""
    await cover.async_set_cover_position(position=target)
    await _REAL_SLEEP(0)
    what = 1 if target >= 100 else 2
    cover.handle_event(OWNEvent.parse(f"*2*{what}*{cover._full_where}##"))


def _make_gateway(
    *,
    model: str = "MyHomeServer1",
    emits_pre_stop: bool | None = True,
    immediate_write: bool = True,
    clock: _Clock | None = None,
) -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:aa:bb:cc"
    gw.unique_id = gw.mac
    gw.log_id = f"[{model} gateway]"
    gw.model = model
    gw.emits_cover_pre_move_stop = emits_pre_stop
    gw.config_entry = None
    gw.is_who_available = MagicMock(return_value=True)
    gw.send_status_request = AsyncMock()
    gw.sent: list[tuple[str, asyncio.Future[float]]] = []

    async def _send(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        gw.sent.append((str(cmd), fut))
        if immediate_write:
            fut.set_result(clock.now if clock is not None else time.monotonic())
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


def _make_cover(
    hass: HomeAssistant,
    gateway: Any,
    where: str = "41",
    *,
    interface: str | None = None,
    travel_time: float = 21.0,
    pos: int | None = None,
    confirmed: bool = False,
    advanced: bool = False,
    allow_auto_advanced: bool = False,
    slat_tilt: bool = False,
    slat_time: float = 2.0,
    strategy_name: str | None = None,
) -> MyHOMECover:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name=f"Cover {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=interface,
            advanced=advanced,
            allow_auto_advanced=allow_auto_advanced,
            slat_tilt=slat_tilt,
            slat_time=slat_time,
            strategy_name=strategy_name,
            manufacturer="BTicino",
            model="F411/2",
            gateway=gateway,
            travel_time=travel_time,
        )
    cover.hass = hass
    cover.entity_id = f"cover.cover_{where}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    if pos is not None and not advanced and not cover.strategy.assumed_state:
        cover._attr_current_cover_position = pos
        cover._start_position = pos
        cover._attr_is_closed = pos == 0
        if confirmed:
            cover._position_drift_bound = 0.0
    return cover


def _make_scope_cover(
    hass: HomeAssistant,
    gateway: Any,
    where: str,
    members: list[MyHOMECover],
    *,
    travel_time: float = 21.0,
) -> MyHOMEScopeCover:
    family = CoverFamily()
    for member in members:
        member.async_on_remove = MagicMock()
        family.add(member)
    scope = CoverScope.of(where, None, tuple(m._where for m in members))
    assert scope is not None
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        sc = MyHOMEScopeCover(
            hass=hass,
            name=f"Scope {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Group Cover",
            gateway=gateway,
            travel_time=travel_time,
            scope=scope,
        )
    sc.hass = hass
    sc.entity_id = f"cover.scope_{where.lstrip('#')}"
    sc.async_on_remove = MagicMock()
    sc.async_write_ha_state = MagicMock()
    sc.async_schedule_update_ha_state = MagicMock()
    family.add(sc)
    return sc


def test_drift_math_helpers() -> None:
    """Verify accumulate_drift_bound and compute_bounded_settle_travel edge cases."""
    assert accumulate_drift_bound(None, 4.0, 21.0) is None
    assert accumulate_drift_bound(0.0, 4.0, 0.0) is None
    assert accumulate_drift_bound(1.5, 0.0, 21.0) == pytest.approx(1.5)
    assert accumulate_drift_bound(21.0, 0.0, 21.0) is None
    assert accumulate_drift_bound(0.0, 4.0, 21.0) == pytest.approx(
        SEGMENT_STOP_DRIFT + SEGMENT_TRAVEL_DRIFT_FACTOR * 4.0
    )
    assert accumulate_drift_bound(20.0, 5.0, 21.0) is None

    assert compute_bounded_settle_travel(50, True, 0.0, 10.0, 0.0) == 0.0
    assert compute_bounded_settle_travel(None, True, 21.0, 5.0, 1.0) == 21.0
    assert compute_bounded_settle_travel(50, True, 21.0, 5.0, None) == 21.0
    assert compute_bounded_settle_travel(100, True, 21.0, 0.0, 0.0) == 21.0
    assert compute_bounded_settle_travel(0, False, 21.0, 0.0, 0.0) == 21.0
    assert compute_bounded_settle_travel(81, True, 21.0, 3.99, 1.10) == pytest.approx(5.09)
    assert compute_bounded_settle_travel(10, True, 21.0, 18.9, 5.0) == pytest.approx(21.0)


@pytest.mark.asyncio
async def test_issue_973_mid_run_reversal_settles_on_bounded_timer(
    hass: HomeAssistant, clock: _Clock, fake_clock: list[float]
) -> None:
    """Issue #973 / #959: reversal from confirmed run settles near arrival instead of full travel."""
    gw = _make_gateway(clock=clock)
    cover = _make_cover(hass, gw, travel_time=21.0, pos=None)

    # 1. Homing UP from unknown waits the full 21 + 2 s and confirms drift = 0.0.
    await _start_run(cover, "open")
    assert cover._stop_task is not None
    await cover._stop_task
    assert fake_clock[-1] == pytest.approx(21.0 + END_SETTLE_MARGIN)
    assert cover.current_cover_position == 100
    assert cover._position_drift_bound == 0.0

    # 2. Close for 4.0 s -> reverse UP (like @ermanno00 in #959).
    clock.hold_settle = True
    clock.release = asyncio.Event()
    await _start_run(cover, "close")
    close_task = cover._stop_task
    assert close_task is not None
    await _yield_loop(4)
    clock.now += 4.0
    assert cover.current_cover_position == 81

    clock.hold_settle = False
    fake_clock.clear()
    await _start_run(cover, "open")
    assert cover._position_drift_bound == pytest.approx(1.10)
    assert cover._stop_task is not None
    await cover._stop_task
    with contextlib.suppress(asyncio.CancelledError):
        await close_task

    # Remaining travel (19% of 21s = 3.99 s) + 1.10 s drift + 2.0 s END_SETTLE_MARGIN = 7.09 s.
    assert fake_clock[-1] == pytest.approx(3.99 + 1.10 + END_SETTLE_MARGIN)
    assert cover.is_opening is False
    assert cover.current_cover_position == 100
    assert cover._position_drift_bound == 0.0

    # 3. Symmetric reversal from 0 % confirmed -> open 4.0 s -> close settles early at 0 %.
    await _start_run(cover, "close")
    assert cover._stop_task is not None
    await cover._stop_task
    assert cover.current_cover_position == 0
    assert cover._position_drift_bound == 0.0

    clock.hold_settle = True
    clock.release = asyncio.Event()
    await _start_run(cover, "open")
    open_task = cover._stop_task
    assert open_task is not None
    await _yield_loop(4)
    clock.now += 4.0
    assert cover.current_cover_position == 19

    clock.hold_settle = False
    fake_clock.clear()
    await _start_run(cover, "close")
    assert cover._stop_task is not None
    await cover._stop_task
    with contextlib.suppress(asyncio.CancelledError):
        await open_task
    assert fake_clock[-1] == pytest.approx(3.99 + 1.10 + END_SETTLE_MARGIN)
    assert cover.is_closing is False
    assert cover.current_cover_position == 0
    assert cover._position_drift_bound == 0.0


@pytest.mark.asyncio
async def test_mh200_f422_trace_reversal_and_m0_cutoff_noop(
    hass: HomeAssistant, clock: _Clock
) -> None:
    """Replay the live MH200 (2.1.0) + F422 12#4#02 reversal cadence with bounded settle."""
    gw = _make_gateway(model="MH200", emits_pre_stop=True, clock=clock)
    cover = _make_cover(hass, gw, where="12", interface="02", travel_time=25.0, pos=100, confirmed=True)

    scheduled_sleeps: list[float] = []
    release_settle = asyncio.Event()

    async def _controlled_sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        scheduled_sleeps.append(delay)
        if delay > 2.0:
            await release_settle.wait()
            clock.now += delay
        else:
            clock.now += delay
            await _REAL_SLEEP(0)

    with (
        patch("custom_components.myhome.cover.time") as cover_time,
        patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep),
    ):
        cover_time.monotonic.side_effect = lambda: clock.now

        # Start close at t=0: MH200 emits pre-move STOP (+0.195 s) then DOWN (+0.500 s).
        await cover.async_close_cover()
        await _yield_loop(2)
        clock.now += 0.195
        cover.handle_event(OWNEvent.parse("*2*0*12#4#02##"))
        clock.now += 0.500
        cover.handle_event(OWNEvent.parse("*2*2*12#4#02##"))
        await _yield_loop(4)

        # After 10.067 s downward travel, reverse UP on MH200 -> pre-move STOP +0.109 s -> UP +0.500 s.
        clock.now += 10.067
        scheduled_sleeps.clear()
        await cover.async_open_cover()
        await _yield_loop(2)
        clock.now += 0.109
        cover.handle_event(OWNEvent.parse("*2*0*12#4#02##"))
        clock.now += 0.500
        cover.handle_event(OWNEvent.parse("*2*1*12#4#02##"))
        await _yield_loop(4)

        # Downward segment was 10.176 s -> drift = 0.5 + 0.15*10.176 = 2.0264 s; bounded settle < 16 s.
        assert scheduled_sleeps[-1] < 16.0

        release_settle.set()
        assert cover._stop_task is not None
        await cover._stop_task
        assert cover.is_opening is False
        assert cover.current_cover_position == 100
        assert cover._position_drift_bound == 0.0

        # Actuator's +60.963 s M=0 cutoff arrives later and is a clean no-op.
        clock.now += 60.963 - scheduled_sleeps[-1]
        cover.handle_event(OWNEvent.parse("*2*0*12#4#02##"))
        assert cover.current_cover_position == 100
        assert cover._position_drift_bound == 0.0


@pytest.mark.asyncio
async def test_wall_reversal_and_mid_run_stop_settle_behavior(
    hass: HomeAssistant, clock: _Clock
) -> None:
    """External bus reversal uses _arm_wall_settle with bounded travel; mid-run wall stop still freezes."""
    gw = _make_gateway(clock=clock)
    cover = _make_cover(hass, gw, travel_time=21.0, pos=100, confirmed=True)
    base_dt = dt_util.utcnow()

    with patch("custom_components.myhome.cover.time") as cover_time:
        cover_time.monotonic.side_effect = lambda: clock.now

        # External wall CLOSE for 4.0 s -> external wall OPEN reversal.
        cover.handle_event(OWNEvent.parse("*2*2*41##"))
        clock.now += 4.0
        cover.handle_event(OWNEvent.parse("*2*1*41##"))
        assert cover._position_drift_bound == pytest.approx(1.10)
        assert cover._wall_settle_unsub is not None

        # Bounded settle is 3.99 + 1.10 + 2.0 = 7.09 s (not 23.0 s).
        clock.now += 7.1
        async_fire_time_changed(hass, base_dt + timedelta(seconds=7.1))
        await hass.async_block_till_done()
        assert cover.is_opening is False
        assert cover.current_cover_position == 100
        assert cover._position_drift_bound == 0.0

        # External CLOSE -> mid-run STOP after 5.0 s -> freezes at 76%, drift=1.25 -> external OPEN -> wall stop after 1 s!
        cover.handle_event(OWNEvent.parse("*2*2*41##"))
        clock.now += 5.0
        cover.handle_event(OWNEvent.parse("*2*0*41##"))
        assert cover.current_cover_position == 76
        assert cover._position_drift_bound == pytest.approx(1.25)

        cover.handle_event(OWNEvent.parse("*2*1*41##"))
        clock.now += 1.0
        cover.handle_event(OWNEvent.parse("*2*1000#0*41##"))
        cover.handle_event(OWNEvent.parse("*2*0*41##"))
        assert cover.is_opening is False
        assert cover.current_cover_position == 81


@pytest.mark.asyncio
async def test_scope_reversal_sets_bounded_per_member_and_full_for_unknown(
    hass: HomeAssistant, clock: _Clock, fake_clock: list[float]
) -> None:
    """Scope reversal bounds confirmed member settle while unknown member still uses full travel."""
    gw = _make_gateway(clock=clock)
    known = _make_cover(hass, gw, where="11", travel_time=21.0, pos=100, confirmed=True)
    unknown = _make_cover(hass, gw, where="12", travel_time=21.0, pos=None)
    scope = _make_scope_cover(hass, gw, "#1", [known, unknown], travel_time=21.0)

    try:
        clock.hold_settle = True
        clock.release = asyncio.Event()
        await scope.async_close_cover()
        await _REAL_SLEEP(0)
        known.handle_event(OWNEvent.parse("*2*2*11##"))
        unknown.handle_event(OWNEvent.parse("*2*2*12##"))
        await _yield_loop(4)
        clock.now += 4.0

        fake_clock.clear()
        clock.release = asyncio.Event()
        await scope.async_open_cover()
        await _REAL_SLEEP(0)
        known.handle_event(OWNEvent.parse("*2*1*11##"))
        unknown.handle_event(OWNEvent.parse("*2*1*12##"))
        await _yield_loop(4)

        assert sorted(fake_clock) == pytest.approx(
            [3.99 + 1.10 + END_SETTLE_MARGIN, 21.0 + END_SETTLE_MARGIN]
        )

        clock.hold_settle = False
        clock.release.set()
        assert known._stop_task is not None and unknown._stop_task is not None
        await asyncio.gather(known._stop_task, unknown._stop_task)
        assert known.current_cover_position == 100
        assert unknown.current_cover_position == 100
    finally:
        clock.hold_settle = False
        if clock.release is not None:
            clock.release.set()
        for member in (known, unknown):
            if member._stop_task is not None and not member._stop_task.done():
                member._stop_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await member._stop_task


@pytest.mark.asyncio
async def test_safety_invariants_keep_full_travel_fallback(
    hass: HomeAssistant, clock: _Clock, fake_clock: list[float]
) -> None:
    """Unknown, restored, endpoint re-homes, saturated drift, disconnect, and edited travel keep full travel."""
    gw = _make_gateway(clock=clock)

    # 1. Restored state -> _position_drift_bound is None -> waits full 23 s.
    restored = _make_cover(hass, gw, where="21", travel_time=21.0, pos=None)
    await restored.async_restore_last_state(
        State("cover.cover_21", STATE_OPEN, {ATTR_CURRENT_POSITION: 80})
    )
    assert restored.current_cover_position == 80
    assert restored._position_drift_bound is None
    fake_clock.clear()
    await _start_run(restored, "open")
    assert restored._stop_task is not None
    await restored._stop_task
    assert fake_clock[-1] == pytest.approx(21.0 + END_SETTLE_MARGIN)

    # 2. Endpoint re-homes: open at 100% / close at 0% (via open_cover / set_cover_position) always wait full travel.
    cov = _make_cover(hass, gw, where="22", travel_time=21.0, pos=100, confirmed=True)
    for coro in (_start_run(cov, "open"), _start_position_end_run(cov, 100)):
        fake_clock.clear()
        await coro
        assert cov._stop_task is not None
        await cov._stop_task
        assert fake_clock[-1] == pytest.approx(21.0 + END_SETTLE_MARGIN)

    cov2 = _make_cover(hass, gw, where="23", travel_time=21.0, pos=0, confirmed=True)
    for coro in (_start_run(cov2, "close"), _start_position_end_run(cov2, 0)):
        fake_clock.clear()
        await coro
        assert cov2._stop_task is not None
        await cov2._stop_task
        assert fake_clock[-1] == pytest.approx(21.0 + END_SETTLE_MARGIN)

    # 3. Cumulative drift saturation across many partial runs -> None -> waits full travel.
    cov3 = _make_cover(hass, gw, where="24", travel_time=10.0, pos=50, confirmed=True)
    for i in range(15):
        target = 80 if i % 2 == 0 else 20
        cov3._start_position = cov3._attr_current_cover_position
        cov3._run_started_at = clock.now
        cov3._move_start_time = clock.now
        cov3._attr_is_opening = target > cov3._start_position
        cov3._attr_is_closing = not cov3._attr_is_opening
        clock.now += 3.0
        cov3._freeze_position(clock.now)
    assert cov3._position_drift_bound is None
    fake_clock.clear()
    await _start_run(cov3, "open")
    assert cov3._stop_task is not None
    await cov3._stop_task
    assert fake_clock[-1] == pytest.approx(10.0 + END_SETTLE_MARGIN)

    # 4. Availability drop invalidates drift bound; reconnect (available=True) leaves it None until re-homed.
    cov4 = _make_cover(hass, gw, where="25", travel_time=21.0, pos=70, confirmed=True)
    gw.is_who_available.return_value = True
    cov4._handle_availability_update()
    assert cov4._position_drift_bound == 0.0
    gw.is_who_available.return_value = False
    cov4._handle_availability_update()
    assert cov4._position_drift_bound is None
    gw.is_who_available.return_value = True

    # 5. Travel-time edits & reset (idle and mid-run) invalidate drift bound and recompute to full travel.
    cov5 = _make_cover(hass, gw, where="26", travel_time=20.0, pos=50, confirmed=True)
    cov5._device_config = lambda: {CONF_TRAVEL_TIME: 20}  # type: ignore[method-assign]
    await cov5.async_set_travel_time(travel_time=18.0)
    assert cov5._position_drift_bound is None
    cov5._position_drift_bound = 0.0
    await cov5.async_reset_travel_time()
    assert cov5._position_drift_bound is None


@pytest.mark.asyncio
async def test_calibration_strategy_switches_venetian_and_edge_branches(
    hass: HomeAssistant, clock: _Clock, fake_clock: list[float]
) -> None:
    """Cover calibration, strategy transitions, Venetian tilt/overshoot, echo reanchor, and custom strategy."""
    gw = _make_gateway(clock=clock)

    # 1. Calibration clears drift on start, ignores mid-calibration end stops, confirms 0.0 on completion,
    #    and clears drift on interruption.
    cal_cov = _make_cover(hass, gw, where="31", travel_time=21.0, pos=100, confirmed=True)
    cal_cov._calibrating = True
    cal_cov._run_started_at = clock.now
    cal_cov._move_start_time = clock.now
    cal_cov._attr_is_opening = True
    cal_cov._position_drift_bound = None
    clock.now += 21.0
    cal_cov._freeze_position(clock.now)
    assert cal_cov._position_drift_bound is None
    cal_cov._interrupt_calibration("user cancelled")
    assert cal_cov._position_drift_bound is None
    cal_cov._calibrating = False
    cal_cov._calibration_interrupted = None

    async def _cal_run(direction: str) -> float:
        cal_cov._position_drift_bound = None
        return 12.0 if direction == "close" else 13.0

    cal_cov._calibration_run = _cal_run  # type: ignore[method-assign]
    await cal_cov.async_calibrate()
    assert cal_cov._position_drift_bound == 0.0

    # 2. Failed bus delivery from unknown position clears drift via _abort_motion, and frozen=None clears drift.
    unk = _make_cover(hass, gw, where="32", travel_time=21.0, pos=None)
    unk._position_drift_bound = 0.5
    unk._run_started_at = clock.now
    unk._move_start_time = clock.now
    unk._attr_is_opening = True
    unk._freeze_position(clock.now + 2.0)
    assert unk.current_cover_position is None
    assert unk._position_drift_bound is None

    unk._position_drift_bound = 0.5
    unk._begin_run("open")
    unk._abort_motion()
    assert unk._position_drift_bound is None

    # 3. SimpleUpDownStopStrategy & Native promotion/demotion clear drift cleanly.
    simp = _make_cover(hass, gw, where="33", travel_time=0.0, strategy_name="simple")
    assert simp.strategy.assumed_state is True
    simp._position_drift_bound = 0.0
    simp._accumulate_position_drift(3.0, True)
    assert simp._position_drift_bound == 0.0

    promo = _make_cover(hass, gw, where="34", travel_time=21.0, pos=50, confirmed=True)
    promo._promote_to_native_strategy(50)
    assert promo._position_drift_bound is None

    dem = _make_cover(hass, gw, where="35", travel_time=21.0, pos=50, confirmed=True)
    dem._demote_to_simple_strategy()
    assert dem._position_drift_bound is None

    # 4. Explicit bus position frame on non-advanced cover (during slat pulse and idle) sets drift = 0.0.
    ven = _make_cover(
        hass,
        gw,
        where="36",
        travel_time=20.0,
        slat_tilt=True,
        slat_time=2.0,
        pos=50,
        allow_auto_advanced=False,
    )
    ven._is_tilting = True
    ven._tilt_initial_position = 50
    ven._tilt_target_position = 100
    ven._tilt_direction = "open"
    ven._attr_current_cover_tilt_position = 50
    ven.handle_event(OWNEvent.parse("*#2*36*10*10*50*000*0##"))
    assert ven._position_drift_bound == 0.0
    ven._is_tilting = False

    # Slat pulse overshoot moves curtain -> accumulates drift.
    ven._tilt_start_time = clock.now - 3.5
    ven._tilt_direction = "open"
    ven._tilt_initial_position = 50
    ven._carry_overshoot_into_curtain(clock.now)
    assert ven._position_drift_bound is not None and ven._position_drift_bound > 0.0

    # Idle explicit Dimension 10 report confirms drift = 0.0 again; remaining slat lead is included in bounded settle.
    ven.handle_event(OWNEvent.parse("*#2*36*10*10*50*000*0##"))
    assert ven._position_drift_bound == 0.0
    ven._attr_current_cover_tilt_position = 50
    fake_clock.clear()
    await _start_run(ven, "open")
    assert ven._stop_task is not None
    await ven._stop_task
    # 50% -> 100% curtain (9 s) + remaining 50% slat lead (1.0 s) + 2.0 s margin = 12.0 s (< 22.0 s).
    assert fake_clock[-1] == pytest.approx(10.0 + END_SETTLE_MARGIN)

    # 5. Stop-echo re-anchoring after full travel confirms _position_drift_bound = 0.0.
    echo_cov = _make_cover(hass, gw, where="37", travel_time=10.0, pos=None)
    ctx = {
        "run_started_at": clock.now - 10.2,
        "run_start_position": None,
        "is_opening": True,
        "is_closing": False,
    }
    TimedCurtainStrategy(travel_time=10.0).reanchor_on_stop_echo(echo_cov, ctx, clock.now)
    assert echo_cov.current_cover_position == 100
    assert echo_cov._position_drift_bound == 0.0

    # 6. Custom non-linear CoverKinematicsStrategy keeps full travel even with drift = 0.0.
    class _CustomStrategy(TimedCurtainStrategy):
        pass

    custom_cov = _make_cover(hass, gw, where="38", travel_time=21.0, pos=80, confirmed=True)
    custom_cov._strategy = _CustomStrategy(travel_time=21.0)
    assert custom_cov._uses_bounded_settle() is False
    assert custom_cov._full_run_settle_travel(True) == 21.0

    assert CONF_STRATEGY == "strategy"
