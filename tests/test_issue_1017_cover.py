"""Regression tests for GitHub issue #1017 cover subsystem bugfixes."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_TILT_POSITION,
)
from homeassistant.const import CONF_MAC, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import RestoredExtraData
from homeassistant.util import dt as dt_util
from OWNd.message import OWNAutomationEvent, OWNEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.actuator_lock import ActuatorLockManager
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.cover import MyHOMECover, is_native_point_cover
from custom_components.myhome.cover_motion import (
    END_SETTLE_MARGIN,
    M0_RELAY_HOLD_WINDOW,
    PRE_MOVE_STOP_SETTLE_DELAY,
)
from custom_components.myhome.cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover


class _Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


async def _yield_loop(steps: int = 5) -> None:
    real_sleep = asyncio.sleep
    for _ in range(steps):
        await real_sleep(0)


def _make_gateway(
    *,
    model: str = "MH201",
    mac: str = "00:03:50:10:17:01",
    immediate_write: bool = True,
    clock: _Clock | None = None,
    with_lock_manager: bool = False,
) -> MagicMock:
    gw = MagicMock()
    gw.mac = gw.unique_id = mac
    gw.log_id = f"[{model}]"
    gw.model = model
    gw.emits_cover_pre_move_stop = model.upper() not in ("MH201", "MH202")
    gw.is_follower = False
    gw.sweep_active = False
    gw._command_pool = None
    gw._actuator_locks = ActuatorLockManager(gw) if with_lock_manager else None
    gw.actuator_lock_owner = None
    gw.sent: list[tuple[str, asyncio.Future[float]]] = []

    async def _send(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        raw = str(cmd)
        gw.sent.append((raw, fut))
        if immediate_write:
            fut.set_result(clock.now if clock is not None else 1000.0)
        return fut

    gw.send = AsyncMock(side_effect=_send)
    gw.send_status_request = AsyncMock(return_value=None)
    return gw


def _make_cover(
    hass: HomeAssistant,
    gw: MagicMock,
    where: str = "41",
    *,
    travel_time: int = 25,
    advanced: bool = False,
    allow_auto_advanced: bool = False,
    slat_tilt: bool = False,
    slat_time: float = 0.8,
    min_motor_pulse: float = 0.2,
    strategy_name: str | None = None,
    stop_before_move: bool | None = None,
    model: str = "L4671/2",
    pos: int | None = 50,
    tilt: int | None = None,
) -> MyHOMECover:
    cover = MyHOMECover(
        hass=hass,
        name=f"Cover {where}",
        entity_name=None,
        device_id=where,
        who="2",
        where=where,
        interface=None,
        advanced=advanced,
        manufacturer="BTicino",
        model=model,
        gateway=gw,
        travel_time=travel_time,
        slat_tilt=slat_tilt,
        slat_time=slat_time,
        min_motor_pulse=min_motor_pulse,
        strategy_name=strategy_name,
        allow_auto_advanced=allow_auto_advanced,
        stop_before_move=stop_before_move,
    )
    cover.hass = hass
    cover.entity_id = f"cover.cover_{where}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    if pos is not None and not cover._strategy.assumed_state:
        cover._attr_current_cover_position = pos
        cover._start_position = pos
        cover._attr_is_closed = pos == 0
    if tilt is not None:
        cover._attr_current_cover_tilt_position = tilt
    return cover


def _make_scope_cover(
    hass: HomeAssistant,
    gw: MagicMock,
    where: str,
    members: list[MyHOMECover],
    *,
    slat_tilt: bool = False,
    slat_time: float = 0.8,
    travel_time: int = 25,
    stop_before_move: bool | None = None,
) -> MyHOMEScopeCover:
    scope = CoverScope.of(where)
    assert scope is not None
    cov = MyHOMEScopeCover(
        hass=hass,
        name=f"Scope {where}",
        entity_name=None,
        device_id=where,
        who="2",
        where=where,
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Area",
        gateway=gw,
        travel_time=travel_time,
        slat_tilt=slat_tilt,
        slat_time=slat_time,
        min_motor_pulse=0.2,
        stop_before_move=stop_before_move,
        scope=scope,
    )
    cov.hass = hass
    cov.entity_id = f"cover.scope_{where}"
    cov.async_write_ha_state = MagicMock()
    cov.async_schedule_update_ha_state = MagicMock()
    family = CoverFamily()
    family.add(cov)
    for m in members:
        family.add(m)
    return cov


# ── Item 3 audit: MH201/MH202 opposite-direction scope interlock stop ────────


def test_item3_audit_mh201_opposite_direction_scope_interlock_stop(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confirm #977 opposite-direction scope interlock settles moving/held relays on MH201 without duplicating logic."""
    clock = _Clock(1000.0)
    monkeypatch.setattr("custom_components.myhome.cover.time.monotonic", clock)
    gw = _make_gateway(model="MH201", clock=clock)
    cov = _make_cover(hass, gw, "41", travel_time=25, pos=100)

    # External area-4 close starts moving; opposite area-4 open mid-run trips interlock to STOP.
    cov.handle_event(OWNEvent.parse("*2*2*4##"))
    assert cov.is_closing is True
    clock.now += 5.0
    cov.handle_event(OWNEvent.parse("*2*1*4##"))
    assert cov.is_opening is False and cov.is_closing is False
    assert cov.current_cover_position == 80
    cov._cancel_stop_task()


# ── Item 4 (#962 M-04): delayed pre-move STOP echo during 0.6 s settle pause ─


@pytest.mark.asyncio
async def test_item4_pre_move_stop_echo_during_settle_pause_and_supersession(
    hass: HomeAssistant,
) -> None:
    """Own delayed *2*0*W## during settle pause preserves pending move; General/Area/1000# stops supersede."""
    clock = _Clock(1000.0)
    with patch("custom_components.myhome.cover.PRE_MOVE_STOP_SETTLE_DELAY", 0.05):
        # 1. Point cover: first *2*0*41## closes stop echo window; a SECOND delayed *2*0*41##
        # arriving while already stopped during the 0.6 s pause must NOT supersede *2*2*41##!
        gw1 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov1 = _make_cover(hass, gw1, "41", stop_before_move=True, pos=100)
        close_task = asyncio.create_task(cov1.async_close_cover())
        await asyncio.sleep(0.01)
        assert [f for f, _ in gw1.sent] == ["*2*0*41##"]
        assert cov1._pre_move_stop_gen == cov1._run_generation
        # First echo closes _echo_until; second delayed echo hits handle_event's stopped branch!
        cov1.handle_event(OWNEvent.parse("*2*0*41##"))
        assert cov1._pending_cmd is None
        cov1.handle_event(OWNEvent.parse("*2*0*41##"))
        await close_task
        assert [f for f, _ in gw1.sent] == ["*2*0*41##", "*2*2*41##"]
        assert cov1._pre_move_stop_gen is None
        cov1._cancel_stop_task()

        # 2. Area scope cover: delayed own *2*0*4## during settle pause must NOT supersede *2*2*4##!
        gw2 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        m41 = _make_cover(hass, gw2, "41", pos=100)
        area4 = _make_scope_cover(hass, gw2, "4", [m41], stop_before_move=True)
        scope_task = asyncio.create_task(area4.async_close_cover())
        await asyncio.sleep(0.01)
        assert [f for f, _ in gw2.sent] == ["*2*0*4##"]
        area4.handle_event(OWNEvent.parse("*2*0*4##"))  # closes echo window
        area4.handle_event(OWNEvent.parse("*2*0*4##"))  # delayed own scope stop echo
        await scope_task
        assert [f for f, _ in gw2.sent] == ["*2*0*4##", "*2*2*4##"]
        assert area4._pre_move_stop_gen is None
        m41._cancel_stop_task()
        area4._cancel_stop_task()

        # 3. General stop (*2*0*0##) during point cover settle pause MUST supersede!
        gw3 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov3 = _make_cover(hass, gw3, "41", stop_before_move=True, pos=100)
        t3 = asyncio.create_task(cov3.async_close_cover())
        await asyncio.sleep(0.01)
        cov3.handle_event(OWNEvent.parse("*2*0*41##"))
        cov3.handle_event(OWNEvent.parse("*2*0*0##"))
        await t3
        assert [f for f, _ in gw3.sent] == ["*2*0*41##"]
        assert cov3._pre_move_stop_gen is None

        # 4. Area stop (*2*0*4##) during point cover settle pause MUST supersede!
        gw4 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov4 = _make_cover(hass, gw4, "41", stop_before_move=True, pos=100)
        t4 = asyncio.create_task(cov4.async_close_cover())
        await asyncio.sleep(0.01)
        cov4.handle_event(OWNEvent.parse("*2*0*41##"))
        cov4.handle_event(OWNEvent.parse("*2*0*4##"))
        await t4
        assert [f for f, _ in gw4.sent] == ["*2*0*41##"]

        # 5. Command-translation stop (*2*1000#0*41##) during settle pause MUST supersede!
        gw5 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        cov5 = _make_cover(hass, gw5, "41", stop_before_move=True, pos=100)
        t5 = asyncio.create_task(cov5.async_close_cover())
        await asyncio.sleep(0.01)
        cov5.handle_event(OWNEvent.parse("*2*0*41##"))
        cov5.handle_event(OWNEvent.parse("*2*1000#0*41##"))
        await t5
        assert [f for f, _ in gw5.sent] == ["*2*0*41##"]

        # 6. General stop (*2*0*0##) during Area scope cover settle pause MUST supersede!
        gw6 = _make_gateway(model="MH201", immediate_write=True, clock=clock)
        m42 = _make_cover(hass, gw6, "42", pos=100)
        area4_b = _make_scope_cover(hass, gw6, "4", [m42], stop_before_move=True)
        t6 = asyncio.create_task(area4_b.async_close_cover())
        await asyncio.sleep(0.01)
        area4_b.handle_event(OWNEvent.parse("*2*0*4##"))
        area4_b.handle_event(OWNEvent.parse("*2*0*0##"))
        await t6
        assert [f for f, _ in gw6.sent] == ["*2*0*4##"]
        assert area4_b._pre_move_stop_gen is None


# ── Item 5 (#1004 M-06): _pulse_stop_inflight_gen race on Dim 10 tilt ────────


@pytest.mark.asyncio
async def test_item5_dim10_stopped_level_during_stop_send_yield_point_and_scope(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dim 10 stopped level arriving during await gateway.send(stop) claims its own pulse without snapping slats."""
    clock = _Clock(1000.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr("custom_components.myhome.cover.time", fake_time)
    monkeypatch.setattr("custom_components.myhome.cover_scope.time", fake_time)
    real_sleep = asyncio.sleep

    # 1. Point cover: Dim 10 stopped level (*#2*31*10*10*1*001*0##) arrives INSIDE gateway.send("*2*0*31##")!
    gw1 = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind1 = _make_cover(
        hass,
        gw1,
        "31",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        min_motor_pulse=0.2,
        model="K4672M2S",
        pos=0,
        tilt=0,
    )
    release1 = asyncio.Event()

    async def _sleep1(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release1.wait()
        await real_sleep(0)

    async def _send_with_in_yield_dim10(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        raw = str(cmd)
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        gw1.sent.append((raw, fut))
        if raw == "*2*0*31##":
            # Stop is in flight inside send(); _pulse_stop_written is STILL None!
            assert blind1._pulse_stop_written is None
            assert blind1._pulse_stop_inflight_gen == blind1._run_generation
            # Actuator stopped Dim 10 frame arrives right now during the await yield:
            blind1.handle_event(OWNMessage.parse("*#2*31*10*10*1*001*0##"))
        fut.set_result(clock.now)
        return fut

    gw1.send = AsyncMock(side_effect=_send_with_in_yield_dim10)
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep1):
        await blind1.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        blind1.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start at t=1000.0
        clock.now += 0.4  # half slat rotation -> 50%
        release1.set()
        assert blind1._stop_task is not None
        await asyncio.wait_for(blind1._stop_task, 1.0)
        await _yield_loop()

    assert not blind1._is_tilting
    assert blind1.current_cover_position == 1
    assert blind1.current_cover_tilt_position == 50  # NOT snapped to 100%!
    assert blind1._pulse_stop_inflight_gen is None

    # 1b. Point cover: exception inside send(stop) still cleans up _pulse_stop_inflight_gen in finally.
    gw1b = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind1b = _make_cover(
        hass,
        gw1b,
        "32",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        model="K4672M2S",
        pos=0,
        tilt=0,
    )
    release1b = asyncio.Event()

    async def _sleep1b(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release1b.wait()
        await real_sleep(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep1b):
        await blind1b.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        blind1b.handle_event(OWNMessage.parse("*2*1*32##"))
        gw1b.send = AsyncMock(side_effect=RuntimeError("socket dropped"))
        with pytest.raises(RuntimeError, match="socket dropped"):
            await blind1b.async_stop_cover_tilt()
        assert blind1b._pulse_stop_inflight_gen is None
        blind1b._cancel_stop_task()
        release1b.set()

    # 2. Scope cover LEADING a slat pulse: follower receives Dim 10 inside scope's await send("*2*0*3##")!
    gw2 = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind2 = _make_cover(
        hass,
        gw2,
        "31",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        model="K4672M2S",
        pos=2,
        tilt=100,
    )
    area2 = _make_scope_cover(hass, gw2, "3", [blind2], slat_tilt=True, slat_time=0.8)
    release2 = asyncio.Event()

    async def _sleep2(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release2.wait()
        await real_sleep(0)

    async def _send_scope_leader_race(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        raw = str(cmd)
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        gw2.sent.append((raw, fut))
        if raw == "*2*0*3##":
            assert area2._pulse_stop_written is None
            assert area2._pulse_stop_inflight_gen == area2._run_generation
            # Follower receives stopped Dim 10 (2 -> 1 closing) during await yield!
            blind2.handle_event(OWNMessage.parse("*#2*31*10*10*1*001*0##"))
        fut.set_result(clock.now)
        return fut

    gw2.send = AsyncMock(side_effect=_send_scope_leader_race)
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep2):
        await area2.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        assert blind2._tilt_follows is area2
        area2._tilt_start_time = clock.now
        area2._pulse_started()
        clock.now += 0.4  # half rotation down -> 50%
        await area2.async_stop_cover()
        await _yield_loop()
        release2.set()

    assert not blind2._is_tilting and not area2._is_tilting
    assert blind2.current_cover_position == 1
    assert blind2.current_cover_tilt_position == 50  # NOT snapped to 0%!
    assert area2._pulse_stop_inflight_gen is None

    # 3. Scope cover stopping an INDEPENDENTLY tilting member (cover_scope.py tilting_leaders race)!
    gw3 = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind3 = _make_cover(
        hass,
        gw3,
        "31",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        model="K4672M2S",
        pos=0,
        tilt=0,
    )
    timed3 = _make_cover(hass, gw3, "32", advanced=False, pos=50)
    area3 = _make_scope_cover(hass, gw3, "3", [blind3, timed3], slat_tilt=True, slat_time=0.8)
    release3 = asyncio.Event()

    async def _sleep3(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release3.wait()
        await real_sleep(0)

    async def _send_scope_member_race(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        raw = str(cmd)
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        gw3.sent.append((raw, fut))
        if raw == "*2*0*3##":
            # area3 is NOT tilting; blind3 is in tilting_leaders with _pulse_stop_inflight_gen set!
            assert not area3._is_tilting
            assert blind3._pulse_stop_written is None
            assert blind3._pulse_stop_inflight_gen == blind3._run_generation
            blind3.handle_event(OWNMessage.parse("*#2*31*10*10*1*001*0##"))
        fut.set_result(clock.now)
        return fut

    gw3.send = AsyncMock(side_effect=_send_scope_member_race)
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep3):
        await blind3.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        blind3.handle_event(OWNMessage.parse("*2*1*31##"))
        clock.now += 0.4  # 50% open
        await area3.async_stop_cover()
        await _yield_loop()
        release3.set()

    assert not blind3._is_tilting
    assert blind3.current_cover_position == 1
    assert blind3.current_cover_tilt_position == 50  # NOT snapped to 100%!
    assert blind3._pulse_stop_inflight_gen is None

    # 3a. Same scope-stop race with an UNRESOLVED delivery Future (real CommandWorkerPool behavior):
    #     blind3 still binds _pulse_stop_written = fut while in flight and consumes late Future errors
    #     even if area3's _run_generation advances before fut fails!
    loop = asyncio.get_running_loop()
    loop_errors: list[dict[str, Any]] = []
    orig_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _l, ctx: loop_errors.append(ctx))
    try:
        gw3u = _make_gateway(model="MyHomeServer1", immediate_write=False, clock=clock)
        blind3u = _make_cover(
            hass,
            gw3u,
            "34",
            advanced=True,
            slat_tilt=True,
            slat_time=0.8,
            model="K4672M2S",
            pos=0,
            tilt=0,
        )
        area3u = _make_scope_cover(hass, gw3u, "3", [blind3u], slat_tilt=True, slat_time=0.8)
        blind3u._is_tilting = True
        blind3u._tilt_direction = "open"
        blind3u._tilt_initial_position = 0
        blind3u._tilt_start_time = clock.now - 0.4
        pending_stop_futs: list[asyncio.Future[float]] = []

        async def _send_scope_unresolved(cmd: object, **kw: Any) -> asyncio.Future[float]:
            raw = str(cmd)
            fut: asyncio.Future[float] = loop.create_future()
            gw3u.sent.append((raw, fut))
            if raw == "*2*0*3##":
                pending_stop_futs.append(fut)
                blind3u.handle_event(OWNMessage.parse("*#2*34*10*10*1*001*0##"))
            return fut

        gw3u.send = AsyncMock(side_effect=_send_scope_unresolved)
        await area3u.async_stop_cover()
        assert not blind3u._is_tilting
        assert blind3u.current_cover_tilt_position == 50
        assert len(pending_stop_futs) == 1
        assert blind3u._pulse_stop_written is pending_stop_futs[0]
        # Advance area3u's generation and fail the stop future: both _track_write and _settle must consume it.
        area3u._run_generation += 1
        pending_stop_futs[0].set_exception(RuntimeError("scope stop nack"))
        pending_stop_futs.clear()
        await _yield_loop()
        assert blind3u._pulse_stop_written is None
        assert loop_errors == []
    finally:
        loop.set_exception_handler(orig_handler)

    # 3b. Scope cover stop raising mid-send cleans up BOTH follower_gens and tilting_leaders._pulse_stop_inflight_gen!
    release3b = asyncio.Event()

    async def _sleep3b(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release3b.wait()
        await real_sleep(0)

    async def _send_scope_stop_fail(cmd: object, **kw: Any) -> asyncio.Future[float]:
        if str(cmd) == "*2*0*3##":
            raise RuntimeError("scope stop failed")
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        fut.set_result(clock.now)
        return fut

    gw3.send = AsyncMock(side_effect=_send_scope_stop_fail)
    blind3._attr_current_cover_tilt_position = 0
    timed3._attr_is_opening = True
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep3b):
        await blind3.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        assert blind3._is_tilting
        with pytest.raises(RuntimeError, match="scope stop failed"):
            await area3.async_stop_cover()
        assert blind3._pulse_stop_inflight_gen is None
        assert timed3._pending_cmd is None
        blind3._cancel_stop_task()
        release3b.set()


# ── Item 9 (#1014): unwritten WHO=2 cancel-coalesced move ────────────────────


@pytest.mark.asyncio
async def test_item9_unwritten_cancel_coalesced_move_venetian_simple_and_locked(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unwritten WHO=2 move cancelled with _not_written=True neither turns slats, flips is_closed, nor sets _ran_since_start."""
    clock = _Clock(1000.0)
    monkeypatch.setattr("custom_components.myhome.cover.time.monotonic", clock)

    # 1. Venetian blind without lock tracking (_has_lock_tracking() == False):
    #    open_cover queued unwritten -> stop_cover cancels it with _not_written=True -> slats stay 0%!
    gw_ven = _make_gateway(model="F461", immediate_write=False, clock=clock, with_lock_manager=False)
    ven = _make_cover(
        hass,
        gw_ven,
        "21",
        travel_time=25,
        slat_tilt=True,
        slat_time=1.5,
        pos=50,
        tilt=0,
    )
    await ven.async_open_cover()
    _, move_fut = gw_ven.sent[-1]
    assert ven._move_written is move_fut
    assert ven._ran_since_start is False

    # Simulate CommandWorkerPool implicit WHO=2 stop cancel-coalescing:
    setattr(move_fut, "_not_written", True)
    move_fut.cancel()
    await ven.async_stop_cover()
    _, stop_fut = gw_ven.sent[-1]
    stop_fut.set_result(clock.now)
    await _yield_loop()

    assert ven.is_opening is False and ven.is_closing is False
    assert ven.current_cover_position == 50
    assert ven.current_cover_tilt_position == 0  # slats did NOT turn!
    assert ven._ran_since_start is False
    assert ven._move_written is None
    ven._cancel_stop_task()

    # 2. Simple assumed-state cover (travel_time=0) without lock tracking:
    #    starting closed -> open_cover queued unwritten and cancel-coalesced -> stays closed & _ran_since_start=False!
    gw_simp = _make_gateway(model="F461", immediate_write=False, clock=clock, with_lock_manager=False)
    simple = _make_cover(hass, gw_simp, "22", travel_time=0, strategy_name="simple", pos=None)
    simple._attr_is_closed = True
    await simple.async_open_cover()
    _, simple_move_fut = gw_simp.sent[-1]
    assert simple._move_written is simple_move_fut

    setattr(simple_move_fut, "_not_written", True)
    simple_move_fut.cancel()
    await simple.async_stop_cover()
    _, simple_stop_fut = gw_simp.sent[-1]
    simple_stop_fut.set_result(clock.now)
    await _yield_loop()

    assert simple.is_opening is False and simple.is_closing is False
    assert simple.is_closed is True  # did NOT flip to False!
    assert simple._ran_since_start is False
    assert simple._move_written is None

    # 3. With WHO=14 lock tracking (_has_lock_tracking() == True):
    #    3a. Normal stop delivery settles via _settle_unconfirmed_stop(), clears _move_written, keeps state intact.
    gw_lock = _make_gateway(model="F461", immediate_write=False, clock=clock, with_lock_manager=True)
    cov_lock = _make_cover(hass, gw_lock, "23", travel_time=25, slat_tilt=True, slat_time=1.5, pos=50, tilt=0)
    assert cov_lock._has_lock_tracking() is True
    assert ven._has_lock_tracking() is False
    await cov_lock.async_open_cover()
    _, lock_move_fut_ok = gw_lock.sent[-1]
    assert cov_lock._move_written is lock_move_fut_ok
    setattr(lock_move_fut_ok, "_not_written", True)
    lock_move_fut_ok.cancel()
    await cov_lock.async_stop_cover()
    _, lock_stop_fut_ok = gw_lock.sent[-1]
    lock_stop_fut_ok.set_result(clock.now)
    await _yield_loop()
    assert cov_lock.is_opening is False
    assert cov_lock.current_cover_position == 50
    assert cov_lock.current_cover_tilt_position == 0
    assert cov_lock._ran_since_start is False
    assert cov_lock._move_written is None
    assert cov_lock._actuation_unconfirmed is False

    #    3b. If stop echo window closes before *2*0*23## arrives while move_fut was cancelled _not_written=True,
    #    handle_event must abort cleanly WITHOUT setting _actuation_unconfirmed = True!
    await cov_lock.async_open_cover()
    _, lock_move_fut = gw_lock.sent[-1]
    setattr(lock_move_fut, "_not_written", True)
    lock_move_fut.cancel()
    await cov_lock.async_stop_cover()
    _, lock_stop_fut = gw_lock.sent[-1]
    # Stop write fails -> closes echo window, leaving cov_lock opening with unwritten move_fut
    lock_stop_fut.set_exception(RuntimeError("stop nack"))
    await _yield_loop()
    assert cov_lock.is_opening is True and cov_lock._pending_cmd is None
    cov_lock.handle_event(OWNEvent.parse("*2*0*23##"))
    assert cov_lock._actuation_unconfirmed is False
    assert cov_lock.is_opening is False
    assert cov_lock._move_written is None
    cov_lock._cancel_stop_task()


# ── Item 15 (#962/#990/#1007 M-08): relay-hold across settle, restore, Dim 10


@pytest.mark.asyncio
async def test_item15_relay_hold_restore_gating_dim10_clear_and_settle_persistence(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify M-08: restore gating on ran_since_start/moving_on_add, Dim 10 clear, and timer-settle restart persistence."""
    clock = _Clock(1000.0)
    monkeypatch.setattr("custom_components.myhome.cover.time.monotonic", clock)
    gw = _make_gateway(model="MH201", immediate_write=True, clock=clock)

    # 1. Gate restore relay-hold on not ran_since_start:
    #    live run completes (*2*1*41## -> *2*0*41##) before async_restore_last_state ->
    #    stale pre-restart "opening" state must NOT re-arm _relay_hold_until!
    cov_ran = _make_cover(hass, gw, "41", travel_time=25, pos=None)
    cov_ran.handle_event(OWNEvent.parse("*2*1*41##"))
    clock.now += 5.0
    cov_ran.handle_event(OWNEvent.parse("*2*0*41##"))
    assert cov_ran._ran_since_start is True
    assert cov_ran._relay_hold_until is None
    future_hold = (dt_util.utcnow() + timedelta(seconds=40)).isoformat(timespec="milliseconds")
    cov_ran.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"relay_hold_until": future_hold, "relay_hold_direction": "open"})
    )
    await cov_ran.async_restore_last_state(State("cover.cover_41", "opening", {ATTR_CURRENT_POSITION: 30}))
    assert cov_ran._relay_hold_until is None

    # 1b. Gate restore relay-hold on not moving_on_add:
    #     live opening frame arrived before restore -> stale pre-restart "closing" must NOT flip _relay_hold_opening!
    cov_mov = _make_cover(hass, gw, "42", travel_time=25, pos=50)
    cov_mov.handle_event(OWNEvent.parse("*2*1*42##"))
    assert cov_mov._relay_hold_opening is True
    await cov_mov.async_restore_last_state(State("cover.cover_42", "closing", {ATTR_CURRENT_POSITION: 50}))
    assert cov_mov._relay_hold_opening is True
    cov_mov._cancel_stop_task()

    # 2. Clear _relay_hold_until on non-advanced Dim 10 stop (both mid-move and post-settle):
    cov_dim10 = _make_cover(hass, gw, "43", travel_time=25, allow_auto_advanced=False, pos=50)
    cov_dim10.handle_event(OWNEvent.parse("*2*2*43##"))
    assert cov_dim10._relay_hold_until is not None
    cov_dim10.handle_event(OWNAutomationEvent.parse("*#2*43*10*10*30*001*0##"))
    assert cov_dim10._relay_hold_until is None
    assert cov_dim10._relay_hold_opening is None
    # Also clear when Dim 10 stopped arrives after timer-settle while hold was still armed:
    cov_dim10._arm_relay_hold(clock.now, opening=True)
    assert cov_dim10._relay_hold_until is not None
    cov_dim10.handle_event(OWNAutomationEvent.parse("*#2*43*10*10*30*001*0##"))
    assert cov_dim10._relay_hold_until is None

    # 3. Timer-settled full run preserves remaining M=0 65 s relay-hold across HA restart on MH201!
    cov_settle = _make_cover(hass, gw, "44", travel_time=25, pos=0)
    cov_settle.handle_event(OWNEvent.parse("*2*1*44##"))
    cov_settle._cancel_wall_settle()
    assert cov_settle._relay_hold_until == pytest.approx(clock.now + M0_RELAY_HOLD_WINDOW)
    assert cov_settle._relay_hold_opening is True

    # Full run settles on timer at t + 27 s (38 s of M=0 relay hold still remaining):
    clock.now += 25.0 + END_SETTLE_MARGIN
    cov_settle._finish_end_of_run_settle(True)
    assert cov_settle._motion_started_at is None
    assert cov_settle._last_run == {}
    assert cov_settle.current_cover_position == 100
    assert cov_settle._relay_hold_until is not None

    # Export extra_restore_state_data 5 s later (33 s remaining):
    clock.now += 5.0
    saved = cov_settle.extra_restore_state_data
    assert saved is not None
    saved_dict = saved.as_dict()
    assert "relay_hold_until" in saved_dict
    assert saved_dict["relay_hold_direction"] == "open"
    assert "motion_started_at" not in saved_dict

    # 3a. Restore onto a fresh entity (reachable "open" state, e.g. normal HA restart):
    restored_reachable = _make_cover(hass, gw, "44", travel_time=25, pos=None)
    restored_reachable.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(saved_dict))
    await restored_reachable.async_restore_last_state(
        State("cover.cover_44", "open", {ATTR_CURRENT_POSITION: 100})
    )
    assert restored_reachable._relay_hold_until is not None
    assert restored_reachable._relay_hold_until - clock.now == pytest.approx(33.0, abs=1.5)
    assert restored_reachable._relay_hold_opening is True
    assert restored_reachable._stops_before_move(False) is True
    # Opposite-direction scope *2*2*4## during restored UP hold trips interlock at 100%:
    restored_reachable.handle_event(OWNEvent.parse("*2*2*4##"))
    assert restored_reachable.is_closing is False and restored_reachable.current_cover_position == 100

    # 3b. Restore onto a fresh entity saved while STATE_UNAVAILABLE:
    restored_unavail = _make_cover(hass, gw, "44", travel_time=25, pos=None)
    restored_unavail.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(saved_dict))
    await restored_unavail.async_restore_last_state(State("cover.cover_44", STATE_UNAVAILABLE, {}))
    assert restored_unavail._relay_hold_until is not None
    assert restored_unavail._relay_hold_opening is True
    assert restored_unavail._stops_before_move(False) is True

    # 3c. Export with _relay_hold_opening=None (post-interlock settle window) omits relay_hold_direction,
    #     and expired hold (>65 s) is neither exported nor restored!
    cov_settle._relay_hold_until = clock.now + PRE_MOVE_STOP_SETTLE_DELAY
    cov_settle._relay_hold_opening = None
    saved_nodir = cov_settle.extra_restore_state_data
    assert saved_nodir is not None
    assert "relay_hold_until" in saved_nodir.as_dict()
    assert "relay_hold_direction" not in saved_nodir.as_dict()
    restored_nodir = _make_cover(hass, gw, "44", travel_time=25, pos=None)
    restored_nodir.async_get_last_extra_data = AsyncMock(return_value=RestoredExtraData(saved_nodir.as_dict()))
    await restored_nodir.async_restore_last_state(State("cover.cover_44", "open", {ATTR_CURRENT_POSITION: 100}))
    assert restored_nodir._relay_hold_until is not None
    assert restored_nodir._relay_hold_opening is None

    # Expired hold at export time is omitted:
    clock.now += 10.0
    saved_expired = cov_settle.extra_restore_state_data
    assert saved_expired is not None
    assert "relay_hold_until" not in saved_expired.as_dict()

    # Expired relay_hold_until in restore record is ignored:
    past_iso = (dt_util.utcnow() - timedelta(seconds=5)).isoformat(timespec="milliseconds")
    restored_past = _make_cover(hass, gw, "44", travel_time=25, pos=None)
    restored_past.async_get_last_extra_data = AsyncMock(
        return_value=RestoredExtraData({"position": 100, "relay_hold_until": past_iso, "relay_hold_direction": "open"})
    )
    await restored_past.async_restore_last_state(State("cover.cover_44", "open", {ATTR_CURRENT_POSITION: 100}))
    assert restored_past._relay_hold_until is None

    # 3d. Post-interlock 0.6 s settle WITH recent last_run_ended_at must NOT be clobbered to 65 s directional hold!
    cov_interlock = _make_cover(hass, gw, "45", travel_time=25, pos=100)
    cov_interlock.handle_event(OWNEvent.parse("*2*2*45##"))
    clock.now += 5.0
    cov_interlock.handle_event(OWNEvent.parse("*2*1*4##"))  # trips interlock -> 0.6 s hold, dir=None, last_run=close
    assert cov_interlock.is_closing is False
    assert cov_interlock._relay_hold_opening is None
    assert cov_interlock._last_run["direction"] == "close"
    interlock_extra = cov_interlock.extra_restore_state_data
    assert interlock_extra is not None
    interlock_attrs = dict(cov_interlock.extra_state_attributes, current_position=cov_interlock.current_cover_position)
    restored_interlock = _make_cover(hass, gw, "45", travel_time=25, pos=None)
    restored_interlock.async_get_last_extra_data = AsyncMock(return_value=interlock_extra)
    await restored_interlock.async_restore_last_state(State("cover.cover_45", "open", interlock_attrs))
    assert restored_interlock._relay_hold_until is not None
    assert restored_interlock._relay_hold_until - clock.now == pytest.approx(PRE_MOVE_STOP_SETTLE_DELAY, abs=0.2)
    assert restored_interlock._relay_hold_opening is None
    # Once the 0.6 s settle elapses, a wall-switch Area 4 OPEN starts opening instead of being dropped.
    clock.now += PRE_MOVE_STOP_SETTLE_DELAY + 0.1
    restored_interlock.handle_event(OWNEvent.parse("*2*1*4##"))
    assert restored_interlock.is_opening is True
    restored_interlock._cancel_stop_task()
    restored_interlock._cancel_wall_settle()
    restored_interlock._stop_live_refresh()

    # 3e. Explicit *2*0*W## stop clears _relay_hold_until=None while keeping recent last_run_ended_at:
    #     export records relay_hold_until=None so restart does NOT resurrect a 65 s hold.
    cov_stopped = _make_cover(hass, gw, "46", travel_time=25, pos=100)
    cov_stopped.handle_event(OWNEvent.parse("*2*2*46##"))
    clock.now += 5.0
    cov_stopped.handle_event(OWNEvent.parse("*2*0*46##"))
    assert cov_stopped._relay_hold_until is None
    assert cov_stopped._last_run["direction"] == "close"
    stopped_extra = cov_stopped.extra_restore_state_data
    assert stopped_extra is not None
    assert stopped_extra.as_dict()["relay_hold_until"] is None
    stopped_attrs = dict(cov_stopped.extra_state_attributes, current_position=cov_stopped.current_cover_position)
    restored_stopped = _make_cover(hass, gw, "46", travel_time=25, pos=None)
    restored_stopped.async_get_last_extra_data = AsyncMock(return_value=stopped_extra)
    await restored_stopped.async_restore_last_state(State("cover.cover_46", "open", stopped_attrs))
    assert restored_stopped._relay_hold_until is None
    assert restored_stopped._stops_before_move(False) is False


# ── Item 16 (cover.py): is_native_point_cover checks gateway_mac & entry_mac ─


def test_item16_is_native_point_cover_checks_gateway_mac_and_entry_mac(hass: HomeAssistant) -> None:
    """is_native_point_cover resolves entity registry lookup via entry_mac when gateway_mac casing differs."""
    entry_mac = "AA:BB:CC:DD:EE:FF"
    gateway_mac = "aa:bb:cc:dd:ee:ff"
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_MAC: entry_mac}, title="MH")
    entry.add_to_hass(hass)

    ent_reg = er.async_get(hass)
    reg_entry = ent_reg.async_get_or_create(
        "cover",
        DOMAIN,
        f"{entry_mac}-2-15",
        config_entry=entry,
    )
    hass.states.async_set(reg_entry.entity_id, "open", {"min_position_delta": 2})

    # gateway_mac lookup misses, entry_mac lookup hits and returns True:
    assert is_native_point_cover(hass, entry, gateway_mac, "15") is True
    # Matching gateway_mac directly hits on first iteration:
    assert is_native_point_cover(hass, entry, entry_mac, "15") is True
    # Unregistered device exhausts both MACs and returns False:
    assert is_native_point_cover(hass, entry, gateway_mac, "16") is False
    # config_entry=None safely handles empty entry_mac:
    assert is_native_point_cover(hass, None, gateway_mac, "16") is False


# ── Item 17 (#1004): failed mid-pulse Venetian tilt reversal sends STOP ──────


@pytest.mark.asyncio
async def test_item17_failed_mid_pulse_venetian_tilt_reversal_sends_emergency_stop(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When reversing a running Venetian slat pulse fails to send, an emergency *2*0*W## stop halts the motor."""
    clock = _Clock(1000.0)
    fake_time = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr("custom_components.myhome.cover.time", fake_time)
    real_sleep = asyncio.sleep

    gw = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind = _make_cover(
        hass,
        gw,
        "31",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        min_motor_pulse=0.2,
        model="K4672M2S",
        pos=0,
        tilt=0,
    )
    release = asyncio.Event()

    async def _sleep(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release.wait()
        await real_sleep(0)

    # 1. Reversal direction send raises; emergency stop returns a Future that later fails ->
    #    emergency *2*0*31## IS sent and _consume_write_exception retrieves its failure cleanly!
    stop_futs: list[asyncio.Future[float]] = []

    async def _send_fail_reversal(cmd: object, **kwargs: Any) -> asyncio.Future[float]:
        raw = str(cmd)
        if raw == "*2*2*31##":
            raise HomeAssistantError("reversal queue closed")
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        gw.sent.append((raw, fut))
        if raw == "*2*0*31##":
            stop_futs.append(fut)
        else:
            fut.set_result(clock.now)
        return fut

    gw.send = AsyncMock(side_effect=_send_fail_reversal)
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep):
        await blind.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        blind.handle_event(OWNMessage.parse("*2*1*31##"))
        clock.now += 0.36  # past min_motor_pulse so tilt=0 reverses to close
        assert blind._is_tilting and blind._stop_task is not None
        with pytest.raises(HomeAssistantError, match="reversal queue closed"):
            await blind.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 0})
        release.set()

    assert [f for f, _ in gw.sent] == ["*2*1*31##", "*2*0*31##"]
    assert not blind._is_tilting
    assert blind._pulse_ended_at is None
    assert len(stop_futs) == 1
    stop_futs[0].set_exception(RuntimeError("stop delivery failed"))
    await _yield_loop()
    with contextlib.suppress(RuntimeError):
        assert getattr(stop_futs[0], "_log_traceback", False) is False

    # 2. If emergency stop send ALSO raises, the secondary exception is suppressed and the original propagates.
    gw2 = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind2 = _make_cover(
        hass,
        gw2,
        "32",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        min_motor_pulse=0.2,
        model="K4672M2S",
        pos=0,
        tilt=0,
    )
    release2 = asyncio.Event()

    async def _sleep2(delay: float, *args: Any, **kwargs: Any) -> None:
        if delay > 0:
            await release2.wait()
        await real_sleep(0)

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_sleep2):
        await blind2.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
        blind2.handle_event(OWNMessage.parse("*2*1*32##"))
        clock.now += 0.36
        gw2.send = AsyncMock(side_effect=[RuntimeError("reversal failed"), RuntimeError("stop also failed")])
        with pytest.raises(RuntimeError, match="reversal failed"):
            await blind2.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 0})
        release2.set()
    assert gw2.send.await_count == 2

    # 3. Initial tilt failure (was_tilting=False) does NOT send a spurious *2*0*W## stop.
    gw3 = _make_gateway(model="MyHomeServer1", immediate_write=True, clock=clock)
    blind3 = _make_cover(
        hass,
        gw3,
        "33",
        advanced=True,
        slat_tilt=True,
        slat_time=0.8,
        model="K4672M2S",
        pos=0,
        tilt=0,
    )
    gw3.send = AsyncMock(side_effect=RuntimeError("initial tilt failed"))
    with pytest.raises(RuntimeError, match="initial tilt failed"):
        await blind3.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 50})
    assert gw3.send.await_count == 1
