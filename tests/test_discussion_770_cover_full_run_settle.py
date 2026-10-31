"""Discussion #770 (#discussioncomment-19245565): unified full-run settling and stopwatch preservation.

Verifies that on timed covers (e.g. BTicino F411/2 actuators set to M=1 = 120 s
driving 60 s and 86 s shutters behind an MH200N, or M=4 / gateways that never
relay stop status):
1. ``open_cover`` and ``close_cover`` from a known position settle at
   ``travel_time + END_SETTLE_MARGIN`` (62 s / 88 s) instead of waiting 120 s
   for ``*2*0*WHERE##``, matching ``set_cover_position: 0`` / ``100``.
2. Physical wall-switch / centralized keypad full runs settle at
   ``travel_time + END_SETTLE_MARGIN`` identically to Home Assistant app runs.
3. Scope ``open_cover`` / ``close_cover`` settle both known and unknown timed
   members on each member's own ``travel_time + END_SETTLE_MARGIN``.
4. Mid-run stops (HA ``stop_cover`` or wall ``*2*0*WHERE##``) still cancel the
   settle timer and freeze the interpolated position (or stay ``unknown`` on a
   partial run from ``unknown``).
5. Manual stopwatch measurement (``last_run_seconds``, Method 3) still records
   the true motor-start-to-stop time up to ``CALIBRATION_MAX_RUN`` (300 s) when
   a stop arrives after an uncalibrated cover already settled its UI at 27 s.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome.const import CALIBRATION_MAX_RUN
from custom_components.myhome.cover import (
    END_SETTLE_MARGIN,
    MOTOR_STOP_DELAY,
    CoverFamily,
    CoverScope,
    MyHOMECover,
    MyHOMEScopeCover,
)
from custom_components.myhome.router import FrameRouter

_REAL_SLEEP = asyncio.sleep


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def fake_time(clock: Clock):
    with patch("custom_components.myhome.cover.time") as mock_time:
        mock_time.monotonic.side_effect = lambda: clock.now
        yield mock_time


@pytest.fixture
def gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:77:00:01"
    gw.log_id = "[MH200N - #770]"
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.device_registry_id = None
    gw.config_entry = None
    gw.send_status_request = AsyncMock()
    gw.deliveries = []

    async def _send(message):
        fut = asyncio.get_running_loop().create_future()
        gw.deliveries.append((str(message), fut))
        return fut

    gw.send = AsyncMock(side_effect=_send)
    return gw


async def _yield(n: int = 3) -> None:
    for _ in range(n):
        await _REAL_SLEEP(0)


def _controlled_sleep(release: asyncio.Event):
    async def _sleep(delay: float, *args, **kwargs) -> None:
        if delay > 0:
            await release.wait()
        await _REAL_SLEEP(0)

    return _sleep


def _make_cover(
    hass: HomeAssistant | None,
    gateway,
    *,
    where: str = "21",
    name: str = "Shutter",
    travel_time: float = 86.0,
    slat_tilt: bool = False,
    slat_time: float = 2.0,
    position: int | None = None,
) -> MyHOMECover:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name=name,
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="F411",
            gateway=gateway,
            travel_time=int(round(travel_time)),
            slat_tilt=slat_tilt,
            slat_time=slat_time,
        )
    cover.hass = hass
    cover.entity_id = f"cover.{name.lower().replace(' ', '_')}"
    cover.async_write_ha_state = MagicMock()
    cover.async_schedule_update_ha_state = MagicMock()
    cover._travel_time_down = float(travel_time)
    cover._travel_time_up = float(travel_time)
    if position is not None:
        cover._attr_current_cover_position = position
        cover._start_position = position
        cover._attr_is_closed = position == 0
        if slat_tilt:
            cover._attr_current_cover_tilt_position = position
    return cover


def _make_scope(
    hass: HomeAssistant,
    gateway,
    where: str,
    members: list[MyHOMECover],
) -> MyHOMEScopeCover:
    family = CoverFamily()
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        scope = MyHOMEScopeCover(
            hass=hass,
            name=f"Scope {where}",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=gateway,
            travel_time=25,
            scope=CoverScope.of(where),
        )
    scope.hass = hass
    scope.entity_id = f"cover.scope_{where}"
    scope.async_write_ha_state = MagicMock()
    scope.async_schedule_update_ha_state = MagicMock()
    for c in (*members, scope):
        family.add(c)
    return scope


async def test_app_open_and_close_from_known_position_settle_before_f411_m1_120s_stop(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """#770: 86 s Stiege oben and 60 s Essen Fixglas on F411 M=1 (120 s) settle at travel_time + 2 s."""
    stiege = _make_cover(hass, gateway, where="21", name="Stiege oben", travel_time=86.0, position=100)
    essen = _make_cover(hass, gateway, where="22", name="Essen Fixglas", travel_time=60.0, position=0)

    # 1. Close Stiege oben (86 s) from known 100%: settles to 0% (closed) at 88 s, not 120 s.
    release_close = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_close)):
        try:
            await stiege.async_close_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*2*21##"
            assert stiege._end_run_from_unknown is False
            assert stiege._stop_task is not None
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            stiege.handle_event(OWNEvent.parse("*2*2*21##"))  # motor start at t0 + 0.12
            clock.now += 43.0
            assert stiege.is_closing is True
            assert stiege.current_cover_position == 50
            clock.now += 43.0 + END_SETTLE_MARGIN
        finally:
            release_close.set()
        await asyncio.wait_for(stiege._stop_task, 1.0)

    assert stiege.is_closing is False
    assert stiege.is_closed is True
    assert stiege.current_cover_position == 0
    assert stiege.extra_state_attributes["motion_started_at"] is None
    assert [r for r, _ in gateway.deliveries] == ["*2*2*21##"]  # no stop frame sent

    # At 120 s after motor start, F411 M=1 hardware timer emits *2*0*21##: state stays 0% / closed.
    clock.now += 120.0 - (86.0 + END_SETTLE_MARGIN)
    stiege.handle_event(OWNEvent.parse("*2*0*21##"))
    assert stiege.current_cover_position == 0
    assert stiege.is_closed is True
    assert stiege.is_closing is False
    assert stiege.extra_state_attributes["last_run_seconds"] == pytest.approx(120.0)
    assert stiege.extra_state_attributes["last_run_direction"] == "close"

    # 2. Open Essen Fixglas (60 s) from known 0% on M=4 (never emits stop): settles to 100% at 62 s.
    release_open = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_open)):
        try:
            await essen.async_open_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*1*22##"
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            essen.handle_event(OWNEvent.parse("*2*1*22##"))
            clock.now += 60.0 + END_SETTLE_MARGIN
        finally:
            release_open.set()
        await asyncio.wait_for(essen._stop_task, 1.0)

    assert essen.is_opening is False
    assert essen.is_closed is False
    assert essen.current_cover_position == 100


async def test_app_midrun_stop_from_known_position_cancels_settle_and_freezes_estimate(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Stopping an app open/close midway (from wall or HA) freezes the interpolated position."""
    cover = _make_cover(hass, gateway, where="21", name="Stiege oben", travel_time=86.0, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await cover.async_close_cover()
            _, written = gateway.deliveries[-1]
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            cover.handle_event(OWNEvent.parse("*2*2*21##"))
            clock.now += 43.0
            # Wall switch stop midway cancels the 88 s settle task and freezes at 50%.
            cover.handle_event(OWNEvent.parse("*2*0*21##"))
            assert cover._stop_task is None
            assert cover.is_closing is False
            assert cover.current_cover_position == 50
            assert cover.is_closed is False
        finally:
            release.set()


async def test_wall_switch_full_run_settles_identically_to_app_from_known_and_unknown(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Wall-switch full runs settle at travel_time + 2 s just like app commands (#770)."""
    # 1. Known cover (86 s) closed from wall switch (*2*2*21##), F411 M=1 stops at 120 s.
    cover = _make_cover(hass, gateway, where="21", name="Stiege oben", travel_time=86.0, position=100)
    t0 = dt_util.utcnow()

    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    armed_first = cover._wall_settle_unsub
    assert armed_first is not None
    assert cover.is_closing is True

    # Duplicate direction status 100 ms later preserves anchor and single settle timer.
    clock.now += 0.1
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    assert cover._wall_settle_unsub is armed_first

    # At 87 s (< 86 + 2 s): still closing.
    clock.now += 86.9
    async_fire_time_changed(hass, t0 + timedelta(seconds=87.0))
    await hass.async_block_till_done()
    assert cover.is_closing is True

    # At 88.1 s (>= 86 + 2 s): settled to 0% (closed) without waiting for 120 s stop!
    clock.now += 1.1
    async_fire_time_changed(hass, t0 + timedelta(seconds=88.1))
    await hass.async_block_till_done()
    assert cover.is_closing is False
    assert cover.is_closed is True
    assert cover.current_cover_position == 0
    assert cover._wall_settle_unsub is None

    # Late 120 s *2*0*21## hardware cutoff leaves 0% (closed) intact and updates last_run_seconds.
    clock.now += 31.9
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(120.0)

    # 2. Fresh unknown cover opened from wall switch (*2*1*22##): full wall run settles to 100%.
    fresh = _make_cover(hass, gateway, where="22", name="Essen Fixglas", travel_time=60.0, position=None)
    t1 = dt_util.utcnow()
    # Brief wall pulse from unknown stopped after 3 s stays unknown.
    fresh.handle_event(OWNEvent.parse("*2*1*22##"))
    clock.now += 3.0
    fresh.handle_event(OWNEvent.parse("*2*0*22##"))
    assert fresh.current_cover_position is None
    assert fresh.is_closed is None
    assert fresh._wall_settle_unsub is None

    # Uninterrupted full wall run from unknown settles to 100% at 62 s!
    fresh.handle_event(OWNEvent.parse("*2*1*22##"))
    clock.now += 62.1
    async_fire_time_changed(hass, t1 + timedelta(seconds=62.1))
    await hass.async_block_till_done()
    assert fresh.is_opening is False
    assert fresh.is_closed is False
    assert fresh.current_cover_position == 100

    # 3. Wall mid-run reversal replaces opening settle with closing settle.
    t2 = dt_util.utcnow()
    fresh.handle_event(OWNEvent.parse("*2*1*22##"))
    open_unsub = fresh._wall_settle_unsub
    clock.now += 10.0
    fresh.handle_event(OWNEvent.parse("*2*2*22##"))
    assert fresh._wall_settle_unsub is not None and fresh._wall_settle_unsub is not open_unsub
    clock.now += 62.1
    async_fire_time_changed(hass, t2 + timedelta(seconds=72.2))
    await hass.async_block_till_done()
    assert fresh.is_closing is False
    assert fresh.is_closed is True
    assert fresh.current_cover_position == 0


async def test_wall_broadcast_general_and_area_settle_all_timed_members_and_scope(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Centralized keypad UP (*2*1*1##) settles 60 s and 86 s members and their scope cover."""
    essen = _make_cover(hass, gateway, where="11", name="Essen Fixglas", travel_time=60.0, position=0)
    stiege = _make_cover(hass, gateway, where="12", name="Stiege oben", travel_time=86.0, position=0)
    area = _make_scope(hass, gateway, "1", [essen, stiege])

    router = FrameRouter()
    for c in (essen, stiege, area):
        router.subscribe("2", [c._device_id, "general"], c.handle_event)

    t0 = dt_util.utcnow()
    router.publish("2", ["1", *area._family.keys_moved_by("1", None)], OWNEvent.parse("*2*1*1##"))
    assert essen.is_opening and stiege.is_opening and area.is_opening

    # At 62.1 s: 60 s member settles to 100%, 86 s member and area still opening.
    clock.now += 62.1
    async_fire_time_changed(hass, t0 + timedelta(seconds=62.1))
    await hass.async_block_till_done()
    assert essen.is_opening is False and essen.current_cover_position == 100
    assert stiege.is_opening is True
    assert area.is_opening is True

    # At 88.1 s: 86 s member settles to 100%, area becomes open (100%) without 120 s stop frames!
    clock.now += 26.0
    async_fire_time_changed(hass, t0 + timedelta(seconds=88.1))
    await hass.async_block_till_done()
    assert stiege.is_opening is False and stiege.current_cover_position == 100
    assert area.is_opening is False
    assert area.is_closed is False
    assert area.current_cover_position == 100


async def test_scope_app_open_and_close_settle_known_members_and_honor_scope_stop(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Scope open/close on the main bus settles known members on their timers; scope stop cancels them."""
    essen = _make_cover(hass, gateway, where="11", name="Essen Fixglas", travel_time=60.0, position=100)
    stiege = _make_cover(hass, gateway, where="12", name="Stiege oben", travel_time=86.0, position=100)
    area = _make_scope(hass, gateway, "1", [essen, stiege])

    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await area.async_close_cover()
            raw, written = gateway.deliveries[-1]
            assert raw == "*2*2*1##"
            assert essen._stop_task is not None and stiege._stop_task is not None
            assert essen._end_run_from_unknown is False and stiege._end_run_from_unknown is False
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.12
            for c in (area, essen, stiege):
                c.handle_event(OWNEvent.parse("*2*2*1##"))
            clock.now += 88.1
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(essen._stop_task, stiege._stop_task), 1.0)

    assert [essen.current_cover_position, stiege.current_cover_position] == [0, 0]
    assert area.current_cover_position == 0
    assert area.is_closed is True
    assert area.is_closing is False

    # Now open via scope and call area.async_stop_cover() midway: cancels tasks and freezes members.
    await area.async_open_cover()
    _, open_written = gateway.deliveries[-1]
    open_written.set_result(clock.now)
    await _yield()
    clock.now += 0.12
    for c in (area, essen, stiege):
        c.handle_event(OWNEvent.parse("*2*1*1##"))
    clock.now += 30.0
    await area.async_stop_cover()
    assert essen._stop_task is None and stiege._stop_task is None
    _, stop_written = gateway.deliveries[-1]
    stop_written.set_result(clock.now)
    await _yield()
    for m in (essen, stiege):
        m.handle_event(OWNEvent.parse(f"*2*0*{m._where}##"))
    assert essen.current_cover_position == 50
    assert stiege.current_cover_position == 35
    assert area.is_opening is False


async def test_manual_stopwatch_last_run_seconds_preserved_after_early_timer_settle(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Uncalibrated 25 s cover on an 86 s shutter: manual Stop at 86 s records last_run_seconds."""
    # Case A: HA close settles UI at 27 s; user clicks HA Stop at 86 s (write + stop echo).
    cover = _make_cover(hass, gateway, where="21", name="Uncalibrated 86s", travel_time=25.0, position=100)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await cover.async_close_cover()
            _, written = gateway.deliveries[-1]
            written.set_result(clock.now)
            await _yield()
            clock.now += 0.50
            cover.handle_event(OWNEvent.parse("*2*2*21##"))  # motor anchor at t_anchor
            t_anchor = clock.now
            clock.now += 27.0
        finally:
            release.set()
        await asyncio.wait_for(cover._stop_task, 1.0)

    # UI settled to 0% / closed at 27 s, stopwatch anchor latched.
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover._settled_stopwatch == (t_anchor, "close")

    # Shutter keeps moving physically; at t_anchor + 86.0 s user clicks Stop in HA.
    clock.now = t_anchor + 86.0
    await cover.async_stop_cover()
    _, stop_written = gateway.deliveries[-1]
    stop_written.set_result(clock.now)
    await _yield()
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.0 + MOTOR_STOP_DELAY, abs=0.02)
    assert cover.extra_state_attributes["last_run_direction"] == "close"

    # Gateway relays *2*0*21## 40 ms later -> re-anchors last_run_seconds to 86.04 s, keeps 0%.
    clock.now = t_anchor + 86.04
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.04)

    # Case B: Legacy non-Future gateway send() returning None on async_stop_cover() after settle.
    cover_sync = _make_cover(hass, gateway, where="22", name="Sync GW", travel_time=25.0, position=0)
    gateway.send = AsyncMock(return_value=None)
    release_b = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_b)):
        try:
            await cover_sync.async_open_cover()
            clock.now += 0.2
            cover_sync.handle_event(OWNEvent.parse("*2*1*22##"))
            t_anchor_b = clock.now
            clock.now += 27.0
        finally:
            release_b.set()
        await asyncio.wait_for(cover_sync._stop_task, 1.0)
    assert cover_sync.current_cover_position == 100
    clock.now = t_anchor_b + 74.0
    await cover_sync.async_stop_cover()
    assert cover_sync.current_cover_position == 100
    assert cover_sync.extra_state_attributes["last_run_seconds"] == pytest.approx(74.0 + MOTOR_STOP_DELAY, abs=0.02)
    assert cover_sync.extra_state_attributes["last_run_direction"] == "open"

    # Case C: Wall UP settles UI at 27 s; user presses wall Stop at 86 s; later stationary stop > 300 s ignored.
    wall_cover = _make_cover(hass, gateway, where="23", name="Wall Stopwatch", travel_time=25.0, position=0)
    t_utc = dt_util.utcnow()
    t_wall_anchor = clock.now
    wall_cover.handle_event(OWNEvent.parse("*2*1*23##"))
    clock.now += 27.1
    async_fire_time_changed(hass, t_utc + timedelta(seconds=27.1))
    await hass.async_block_till_done()
    assert wall_cover.current_cover_position == 100

    clock.now = t_wall_anchor + 86.0
    wall_cover.handle_event(OWNEvent.parse("*2*0*23##"))
    assert wall_cover.current_cover_position == 100
    assert wall_cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.0)
    assert wall_cover.extra_state_attributes["last_run_direction"] == "open"

    # Settle again, then wait > CALIBRATION_MAX_RUN (300 s): late stationary stop is ignored.
    t_utc2 = dt_util.utcnow()
    wall_cover.handle_event(OWNEvent.parse("*2*2*23##"))
    clock.now += 27.1
    async_fire_time_changed(hass, t_utc2 + timedelta(seconds=27.1))
    await hass.async_block_till_done()
    assert wall_cover.current_cover_position == 0
    clock.now += CALIBRATION_MAX_RUN + 50.0
    wall_cover.handle_event(OWNEvent.parse("*2*0*23##"))
    assert wall_cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.0)
    assert wall_cover.extra_state_attributes["last_run_direction"] == "open"


async def test_venetian_wall_full_run_and_wall_settle_guard_branches(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Venetian wall full run settles both position and tilt; guard branches handle non-loop hass and frozen state."""
    blind = _make_cover(
        hass, gateway, where="31", name="Venetian", travel_time=25.0, slat_tilt=True, slat_time=2.0, position=100
    )
    t0 = dt_util.utcnow()
    blind.handle_event(OWNEvent.parse("*2*2*31##"))
    clock.now += 27.1
    async_fire_time_changed(hass, t0 + timedelta(seconds=27.1))
    await hass.async_block_till_done()
    assert blind.current_cover_position == 0
    assert blind.current_cover_tilt_position == 0
    assert blind.is_closed is True

    # Guard 1: _on_wall_settle is a no-op if _freeze_position already stopped motion without _cancel_wall_settle.
    t1 = dt_util.utcnow()
    blind.handle_event(OWNEvent.parse("*2*1*31##"))
    clock.now += 5.0
    blind._freeze_position(clock.now)
    frozen_pos = blind.current_cover_position
    async_fire_time_changed(hass, t1 + timedelta(seconds=27.1))
    await hass.async_block_till_done()
    assert blind.current_cover_position == frozen_pos

    # Guard 2: cover with mock/non-loop hass skips _arm_wall_settle cleanly.
    no_loop_cover = _make_cover(None, gateway, where="32", name="No Loop", travel_time=25.0, position=50)
    no_loop_cover.hass = SimpleNamespace(loop=MagicMock())  # type: ignore[assignment]
    no_loop_cover.handle_event(OWNEvent.parse("*2*1*32##"))
    assert no_loop_cover._wall_settle_unsub is None
