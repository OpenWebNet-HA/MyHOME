"""Discussion #770 (#discussioncomment-19245565): unified full-run settling and PR #834 review scenarios.

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
4. Mid-run stops (HA ``stop_cover``, plain ``*2*0*WHERE##``, or WHO 2 command
   translations ``*2*1000#0*WHERE##`` / ``*2*1000#10…*WHERE##``) cancel the
   settle timer and freeze the interpolated position (or stay ``unknown`` on a
   partial run from ``unknown``).
5. Post-settle stop frames (120 s hardware cutoffs, ``stop_cover`` automations,
   scope stops) never overwrite ``last_run_seconds``; mid-run ``travel_time``
   updates recompute active settle tasks immediately.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.myhome.const import CONF_TRAVEL_TIME
from custom_components.myhome.cover import (
    END_SETTLE_MARGIN,
    MOTOR_STOP_DELAY,
    CalibrationInterrupted,
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
    with (
        patch("custom_components.myhome.cover.time") as mock_time,
        patch("custom_components.myhome.cover_scope.time") as mock_scope_time,
    ):
        mock_time.monotonic.side_effect = lambda: clock.now
        mock_scope_time.monotonic.side_effect = lambda: clock.now
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
    *,
    slat_tilt: bool = False,
    slat_time: float = 2.0,
    family: CoverFamily | None = None,
) -> MyHOMEScopeCover:
    if family is None:
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
            slat_tilt=slat_tilt,
            slat_time=slat_time,
            scope=CoverScope.of(where, members=[m._where for m in members]),
        )
    scope.hass = hass
    scope.entity_id = f"cover.scope_{where}"
    scope.async_write_ha_state = MagicMock()
    scope.async_schedule_update_ha_state = MagicMock()
    for c in (*members, scope):
        if c not in family._covers:
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

    # At 120 s after motor start, F411 M=1 hardware timer emits *2*0*21##: state stays 0% / closed,
    # and last_run_seconds is NOT polluted with the 120 s hardware relay cutoff.
    clock.now += 120.0 - (86.0 + END_SETTLE_MARGIN)
    stiege.handle_event(OWNEvent.parse("*2*0*21##"))
    assert stiege.current_cover_position == 0
    assert stiege.is_closed is True
    assert stiege.is_closing is False
    assert stiege.extra_state_attributes["last_run_seconds"] is None
    assert stiege.extra_state_attributes["last_run_direction"] is None

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

    # Late 120 s *2*0*21## hardware cutoff leaves 0% (closed) intact without overwriting last_run_seconds.
    clock.now += 31.9
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.current_cover_position == 0
    assert cover.is_closed is True
    assert cover.extra_state_attributes["last_run_seconds"] is None

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


async def test_post_settle_stops_do_not_overwrite_last_run_seconds_and_temp_upper_bound_stopwatch(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Post-settle stops never overwrite last_run_seconds; >25 s shutters time via temporary 180 s upper bound."""
    # Case A: Documented Method 3 / Recipe 6 workflow on an 86 s shutter:
    # temporarily raise travel_time to 180 s, close, press Stop at 86.0 s, and re-anchor on *2*0*21## at 86.04 s.
    cover = _make_cover(hass, gateway, where="21", name="Uncalibrated 86s", travel_time=25.0, position=100)
    await cover.async_set_travel_time(travel_time=180.0)
    await cover.async_close_cover()
    _, written = gateway.deliveries[-1]
    written.set_result(clock.now)
    await _yield()
    clock.now += 0.50
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    t_anchor = clock.now

    clock.now = t_anchor + 86.0
    await cover.async_stop_cover()
    _, stop_written = gateway.deliveries[-1]
    stop_written.set_result(clock.now)
    await _yield()
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.0 + MOTOR_STOP_DELAY, abs=0.02)
    assert cover.extra_state_attributes["last_run_direction"] == "close"

    clock.now = t_anchor + 86.04
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.04)
    assert cover.extra_state_attributes["last_run_direction"] == "close"
    await cover.async_set_travel_time(travel_time_down=86.04, travel_time_up=86.04)

    # Now perform a full open run that settles on the 88.04 s timer:
    # subsequent 120 s M=1 cutoff, 200 s HA stop_cover automation, and scope stops must NOT overwrite 86.04 s!
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await cover.async_open_cover()
            _, open_w = gateway.deliveries[-1]
            open_w.set_result(clock.now)
            await _yield()
            clock.now += 0.1
            cover.handle_event(OWNEvent.parse("*2*1*21##"))
            t_open = clock.now
            clock.now = t_open + 86.04 + END_SETTLE_MARGIN
        finally:
            release.set()
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.current_cover_position == 100
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.04)
    assert cover.extra_state_attributes["last_run_direction"] == "close"

    # 1. F411 M=1 120 s hardware cutoff arrives after settle -> keeps 86.04 s ("close").
    clock.now = t_open + 120.0
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.current_cover_position == 100
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.04)
    assert cover.extra_state_attributes["last_run_direction"] == "close"

    # 2. Safety automation calls async_stop_cover() at 200 s -> keeps 86.04 s ("close").
    clock.now = t_open + 200.0
    await cover.async_stop_cover()
    _, auto_stop_w = gateway.deliveries[-1]
    auto_stop_w.set_result(clock.now)
    await _yield()
    clock.now += 0.04
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.04)
    assert cover.extra_state_attributes["last_run_direction"] == "close"

    # 3. Scope stop on settled member -> keeps 86.04 s ("close").
    area = _make_scope(hass, gateway, "2", [cover])
    clock.now += 10.0
    await area.async_stop_cover()
    _, area_stop_w = gateway.deliveries[-1]
    area_stop_w.set_result(clock.now)
    await _yield()
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(86.04)
    assert cover.extra_state_attributes["last_run_direction"] == "close"

    # Case B: Legacy non-Future gateway send() returning None on mid-run async_stop_cover().
    cover_sync = _make_cover(hass, gateway, where="22", name="Sync GW", travel_time=180.0, position=0)
    gateway.send = AsyncMock(return_value=None)
    await cover_sync.async_open_cover()
    clock.now += 0.2
    cover_sync.handle_event(OWNEvent.parse("*2*1*22##"))
    t_anchor_b = clock.now
    clock.now = t_anchor_b + 74.0
    await cover_sync.async_stop_cover()
    assert cover_sync.extra_state_attributes["last_run_seconds"] == pytest.approx(74.0 + MOTOR_STOP_DELAY, abs=0.02)
    assert cover_sync.extra_state_attributes["last_run_direction"] == "open"


async def test_venetian_wall_full_run_and_dynamic_discovery_wall_settle(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Venetian wall full run settles position & tilt; dynamic discovery on moving frame arms settle on add."""
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

    # Guard 2 (Optional #1): cover dynamically discovered on a moving frame (*2*1*32##) before hass is attached
    # skips _arm_wall_settle while hass is None, preserves moving state on restore, and arms settle on add!
    from homeassistant.core import State

    discovered = _make_cover(None, gateway, where="32", name="Discovered Moving", travel_time=25.0, position=None)
    discovered.handle_event(OWNEvent.parse("*2*1*32##"))
    assert discovered.is_opening is True
    assert discovered._wall_settle_unsub is None

    await discovered.async_restore_last_state(State("cover.discovered_moving", "closed", {"current_position": 0}))
    assert discovered.is_opening is True and discovered._attr_is_closed is False
    assert discovered._run_start_position == 0

    await discovered.async_restore_last_state(State("cover.discovered_moving", "closed", {}))
    assert discovered.is_opening is True and discovered._attr_is_closed is False
    assert discovered._run_start_position == 0

    simple_disc = _make_cover(None, gateway, where="33", name="Simple Disc", travel_time=0.0, position=None)
    simple_disc.handle_event(OWNEvent.parse("*2*1*33##"))
    await simple_disc.async_restore_last_state(State("cover.simple_disc", "closed", {}))
    assert simple_disc.is_closed is False

    discovered.hass = hass
    discovered.async_on_remove = MagicMock()
    with patch("custom_components.myhome.cover.MyHOMEEntity.async_added_to_hass", new=AsyncMock()):
        await discovered.async_added_to_hass()
    assert discovered._wall_settle_unsub is not None
    t2 = dt_util.utcnow()
    clock.now += 27.1
    async_fire_time_changed(hass, t2 + timedelta(seconds=27.1))
    await hass.async_block_till_done()
    assert discovered.is_opening is False
    assert discovered.current_cover_position == 100


async def test_scope_stop_freezes_members_without_relayed_stop_and_inside_echo_window(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Scope async_stop_cover() freezes timed members even without relayed stop frames or inside ECHO_WINDOW."""
    essen = _make_cover(hass, gateway, where="11", name="Essen Fixglas", travel_time=60.0, position=0)
    fresh = _make_cover(hass, gateway, where="12", name="Fresh Unknown", travel_time=86.0, position=None)
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        native = MyHOMECover(
            hass=hass,
            name="Native",
            entity_name=None,
            device_id="13",
            who="2",
            where="13",
            interface=None,
            advanced=True,
            manufacturer="BTicino",
            model="LN4660M2",
            gateway=gateway,
        )
    native.hass = hass
    native.entity_id = "cover.native"
    native.async_write_ha_state = MagicMock()
    native.async_schedule_update_ha_state = MagicMock()
    area = _make_scope(hass, gateway, "1", [essen, fresh, native])

    # 1. Silent gateway (F453/F453AV / M=4): no *2*0*1## or *2*0*11## ever arrives on the event session.
    await area.async_open_cover()
    _, open_written = gateway.deliveries[-1]
    open_written.set_result(clock.now)
    await _yield()
    clock.now += 30.0
    await area.async_stop_cover()
    _, stop_written = gateway.deliveries[-1]
    stop_written.set_result(clock.now)
    await _yield()

    assert essen.is_opening is False
    assert essen.current_cover_position == 50
    assert fresh.is_opening is False
    assert fresh.current_cover_position is None
    assert fresh.is_closed is None
    assert area.is_opening is False
    assert area.current_cover_position == 50

    # 2. Quick open_cover -> stop_cover inside ECHO_WINDOW (0.3 s after open write, before any *2*1*1## echo):
    # relayed stop frames must NOT be swallowed as open echoes ("Ignoring stop echo").
    clock.now += 5.0
    await area.async_close_cover()
    _, close_written = gateway.deliveries[-1]
    close_written.set_result(clock.now)
    await _yield()
    assert essen._pending_cmd == "close" and fresh._pending_cmd == "close"

    clock.now += 0.30  # still inside 1.0 s ECHO_WINDOW!
    await area.async_stop_cover()
    assert essen._pending_cmd == "stop" and fresh._pending_cmd == "stop"
    _, quick_stop_written = gateway.deliveries[-1]
    quick_stop_written.set_result(clock.now)
    await _yield()
    clock.now += 0.05
    for c in (area, essen, fresh):
        c.handle_event(OWNEvent.parse("*2*0*1##"))
    essen.handle_event(OWNEvent.parse("*2*0*11##"))
    assert essen.is_closing is False
    assert fresh.is_closing is False
    assert fresh.current_cover_position is None
    assert area.is_closing is False

    # 3. Failed scope stop send() closes member echo windows cleanly.
    gateway.send = AsyncMock(side_effect=HomeAssistantError("gateway offline"))
    with pytest.raises(HomeAssistantError, match="gateway offline"):
        await area.async_stop_cover()
    assert essen._pending_cmd is None and fresh._pending_cmd is None


async def test_scope_stop_echo_preserves_member_reanchor_and_handles_early_echo(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Relayed *2*0*<scope>## keeps member stop echo open for *2*0*<member>## and handles pre-write echoes."""
    essen = _make_cover(hass, gateway, where="11", name="Essen Fixglas", travel_time=60.0, position=0)
    stiege = _make_cover(hass, gateway, where="12", name="Stiege oben", travel_time=100.0, position=0)
    area = _make_scope(hass, gateway, "1", [essen, stiege])

    # 1. Echoed *2*0*1## arrives after stop_written, followed by per-actuator *2*0*11## 600 ms later.
    await area.async_open_cover()
    _, open_written = gateway.deliveries[-1]
    open_written.set_result(clock.now)
    await _yield()
    for c in (area, essen, stiege):
        c.handle_event(OWNEvent.parse("*2*1*1##"))
    t_start = clock.now
    clock.now = t_start + 24.0
    await area.async_stop_cover()
    _, stop_written = gateway.deliveries[-1]
    stop_written.set_result(clock.now)
    await _yield()
    assert essen.current_cover_position == 40  # provisional at 24 s / 60 s

    # Unrelated general stop *2*0*0## on a different scope cover is not consumed as area 1's own echo.
    other_area = _make_scope(hass, gateway, "2", [])
    other_area._begin_command("stop")
    assert other_area._handle_echo(OWNEvent.parse("*2*0*0##"), clock.now) is False

    # Gateway echoes *2*0*1## on relay_scope: area consumes its own echo; members keep echo window open!
    clock.now = t_start + 24.05
    for c in (area, essen, stiege):
        c.handle_event(OWNEvent.parse("*2*0*1##"))
    assert area._pending_cmd is None
    assert essen._pending_cmd == "stop" and stiege._pending_cmd == "stop"

    # Actuator 11 stops slightly later (at t_start + 30.0 s, still inside stop_written's 1.0 s? Use +0.9 s -> 30.0 s):
    clock.now = t_start + 24.9
    essen._travel_time_up = 49.8  # 24.9 / 49.8 = 50% (vs provisional 24.08 / 49.8 = 48%)
    essen.handle_event(OWNEvent.parse("*2*0*11##"))
    assert essen._pending_cmd is None
    assert essen.current_cover_position == 50  # re-anchored on exact *2*0*11## arrival!
    assert stiege.is_opening is False  # stiege never emitted *2*0*12## (MH201) yet stayed frozen!

    # 2. Pre-write echo: *2*0*1## arrives BEFORE stop_written resolves.
    essen._travel_time_up = 60.0
    essen._attr_current_cover_position = essen._start_position = 0
    stiege._attr_current_cover_position = stiege._start_position = 100
    essen.handle_event(OWNEvent.parse("*2*1*11##"))
    clock.now += 15.0
    await area.async_stop_cover()
    _, pre_stop_written = gateway.deliveries[-1]
    assert not pre_stop_written.done()
    for c in (area, essen, stiege):
        c.handle_event(OWNEvent.parse("*2*0*1##"))
    assert essen.is_opening is False
    assert essen.current_cover_position == 25
    # Resolving stop_written afterwards does not overwrite the provisional freeze.
    clock.now += 0.2
    pre_stop_written.set_result(clock.now)
    await _yield()
    assert essen.current_cover_position == 25

    # 3. Point-to-point pre-write stop echo + routed interface scope stop echo (*2*0*0#4#02##).
    pt = _make_cover(hass, gateway, where="21", name="Point PreWrite", travel_time=40.0, position=0)
    pt.handle_event(OWNEvent.parse("*2*1*21##"))
    clock.now += 10.0
    await pt.async_stop_cover()
    _, pt_stop_written = gateway.deliveries[-1]
    assert not pt_stop_written.done()
    pt.handle_event(OWNEvent.parse("*2*0*21##"))
    assert pt.is_opening is False and pt.current_cover_position == 25
    pt_stop_written.set_result(clock.now + 0.05)
    await _yield()
    assert pt.current_cover_position == 25

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        routed_gen = MyHOMEScopeCover(
            hass=hass,
            name="Gen Bus 02",
            entity_name=None,
            device_id="0#4#02",
            who="2",
            where="0",
            interface="02",
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=gateway,
            travel_time=25,
            scope=CoverScope.of("0", "02"),
        )
    routed_gen.hass = hass
    routed_gen.entity_id = "cover.gen_bus_02"
    routed_gen.async_write_ha_state = MagicMock()
    routed_gen.async_schedule_update_ha_state = MagicMock()
    await routed_gen.async_stop_cover()
    _, rg_written = gateway.deliveries[-1]
    rg_written.set_result(clock.now)
    await _yield()
    routed_gen.handle_event(OWNEvent.parse("*2*0*0#4#02##"))
    assert routed_gen._pending_cmd is None


async def test_external_wall_command_interrupting_calibration_arms_wall_settle(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """External wall UP/DOWN interrupting async_calibrate() arms _wall_settle_unsub across all phases (#770)."""
    # 1a. Wall DOWN (*2*2*21##) arrives BEFORE up_written resolves on the bus, then up_written resolves.
    cover = _make_cover(hass, gateway, where="21", name="Calib Shutter", travel_time=60.0, position=50)
    t0 = dt_util.utcnow()
    cal_task = asyncio.create_task(cover.async_calibrate())
    await _yield()
    _, up_written = gateway.deliveries[-1]
    assert not up_written.done()

    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    assert cover._wall_settle_unsub is not None
    assert cover.is_closing is True
    up_written.set_result(clock.now)
    with pytest.raises(CalibrationInterrupted, match="external close"):
        await asyncio.wait_for(cal_task, 1.0)
    assert cover._calibrating is False
    assert cover.is_closing is True
    assert cover._wall_settle_unsub is not None

    # At 60 + 2 s, cover settles to 0% (closed) without waiting for 120 s / missing stop frame.
    clock.now += 60.0 + END_SETTLE_MARGIN + 0.1
    async_fire_time_changed(hass, t0 + timedelta(seconds=62.1))
    await hass.async_block_till_done()
    assert cover.is_closing is False
    assert cover.is_closed is True
    assert cover.current_cover_position == 0

    # 1b. Wall DOWN arrives AFTER up_written resolves while awaiting motor_started echo.
    cal_task_echo = asyncio.create_task(cover.async_calibrate())
    await _yield()
    _, up_written_echo = gateway.deliveries[-1]
    up_written_echo.set_result(clock.now)
    await _yield()
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    with pytest.raises(CalibrationInterrupted, match="external close"):
        await asyncio.wait_for(cal_task_echo, 1.0)
    assert cover.is_closing is True
    assert cover._wall_settle_unsub is not None
    cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 1c. Unwritten calibration command failing, cancelling, or timing out AFTER wall interruption
    # must NOT abort the new wall run and must raise CalibrationInterrupted (not HomeAssistantError).
    for mode in ("exception", "cancel", "timeout"):
        with patch("custom_components.myhome.cover.WRITE_TIMEOUT", 0.01 if mode == "timeout" else 5.0):
            cal_fail = asyncio.create_task(cover.async_calibrate())
            await _yield()
            _, unwritten = gateway.deliveries[-1]
            cover.handle_event(OWNEvent.parse("*2*2*21##"))
            assert cover.is_closing is True
            assert cover._wall_settle_unsub is not None
            if mode == "exception":
                unwritten.set_exception(HomeAssistantError("send dropped"))
            elif mode == "cancel":
                unwritten.cancel()
            with pytest.raises(CalibrationInterrupted, match="external close"):
                await asyncio.wait_for(cal_fail, 1.0)
            assert cover.is_closing is True
            assert cover._wall_settle_unsub is not None
            cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 2. Interrupted by wall UP (*2*1*21##) during closing phase of calibration.
    async def _fast_sleep(delay: float, *args, **kwargs) -> None:
        clock.now += delay
        await _REAL_SLEEP(0)

    t1 = dt_util.utcnow()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_fast_sleep):
        cal_task_2 = asyncio.create_task(cover.async_calibrate())
        await _yield()
        _, w_up = gateway.deliveries[-1]
        w_up.set_result(clock.now)
        await _yield()
        clock.now += 0.1
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        await _yield()
        clock.now += 30.0
        cover.handle_event(OWNEvent.parse("*2*0*21##"))  # up run finishes cleanly
        await _yield(6)
        raw_down, w_down = gateway.deliveries[-1]
        assert raw_down == "*2*2*21##"
        w_down.set_result(clock.now)
        await _yield()
        clock.now += 0.1
        cover.handle_event(OWNEvent.parse("*2*2*21##"))  # down run starts
        await _yield()
        clock.now += 5.0
        cover.handle_event(OWNEvent.parse("*2*1*21##"))  # wall UP interrupts down run!
        assert cover._wall_settle_unsub is not None
        with pytest.raises(CalibrationInterrupted, match="external open"):
            await asyncio.wait_for(cal_task_2, 1.0)

    clock.now += 60.0 + END_SETTLE_MARGIN + 0.1
    async_fire_time_changed(hass, t1 + timedelta(seconds=130.0))
    await hass.async_block_till_done()
    assert cover.is_opening is False
    assert cover.is_closed is False
    assert cover.current_cover_position == 100

    # 3. Interrupted by wall DOWN (*2*2*21##) during CALIBRATION_SETTLE pause between up and down runs:
    # must NOT wipe _calibration_interrupted or send a second calibration command!
    settle_entered = asyncio.Event()
    release_settle = asyncio.Event()

    async def _pause_between_runs(delay: float, *args, **kwargs) -> None:
        if delay >= 1.0:
            settle_entered.set()
            await release_settle.wait()
            return
        await _REAL_SLEEP(0)

    t2 = dt_util.utcnow()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_pause_between_runs):
        cal_task_3 = asyncio.create_task(cover.async_calibrate())
        await _yield()
        _, w_up_3 = gateway.deliveries[-1]
        w_up_3.set_result(clock.now)
        await _yield()
        clock.now += 0.1
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        await _yield()
        clock.now += 30.0
        cover.handle_event(OWNEvent.parse("*2*0*21##"))  # up run finishes -> enters CALIBRATION_SETTLE
        await asyncio.wait_for(settle_entered.wait(), 1.0)
        deliveries_before_wall = len(gateway.deliveries)

        cover.handle_event(OWNEvent.parse("*2*2*21##"))  # external wall close during CALIBRATION_SETTLE
        assert cover._wall_settle_unsub is not None
        assert cover.is_closing is True

        release_settle.set()
        with pytest.raises(CalibrationInterrupted, match="external close"):
            await asyncio.wait_for(cal_task_3, 1.0)
        assert len(gateway.deliveries) == deliveries_before_wall  # no calibration *2*2*21## queued!
        assert cover._wall_settle_unsub is not None

    clock.now += 60.0 + END_SETTLE_MARGIN + 0.1
    async_fire_time_changed(hass, t2 + timedelta(seconds=62.1))
    await hass.async_block_till_done()
    assert cover.is_closing is False
    assert cover.current_cover_position == 0


async def test_scope_stop_cover_halts_member_slat_tilt_pulses_and_mixed_scope_members(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Scope async_stop_cover() settles independent/cross-scope slat tilt pulses and mixed non-tilting members."""
    # 1. Independent member slat tilt pulse stopped by scope async_stop_cover() without relayed *2*0*11##.
    blind_1 = _make_cover(hass, gateway, where="11", name="Blind 11", travel_time=40.0, slat_tilt=True, slat_time=2.0, position=0)
    shutter_2 = _make_cover(hass, gateway, where="12", name="Shutter 12", travel_time=60.0, slat_tilt=False, position=0)
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        adv_3 = MyHOMECover(
            hass=hass,
            name="Adv 13",
            entity_name=None,
            device_id="13",
            who="2",
            where="13",
            interface=None,
            advanced=True,
            manufacturer="BTicino",
            model="F411U2",
            gateway=gateway,
            travel_time=40,
        )
    adv_3.hass = hass
    adv_3.entity_id = "cover.adv_13"
    adv_3.async_write_ha_state = MagicMock()
    adv_3.async_schedule_update_ha_state = MagicMock()

    family = CoverFamily()
    area = _make_scope(hass, gateway, "1", [blind_1, shutter_2, adv_3], slat_tilt=True, slat_time=2.0, family=family)

    release_pulse = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_pulse)):
        await blind_1.async_open_cover_tilt()
        _, tilt_written = gateway.deliveries[-1]
        tilt_written.set_result(clock.now)
        await _yield()
        blind_1.handle_event(OWNEvent.parse("*2*1*11##"))
        assert blind_1._is_tilting is True
        assert blind_1._stop_task is not None

        clock.now += 0.5  # 0.5 s / 2.0 s = 25% slat tilt
        await area.async_stop_cover()
        _, area_stop_written = gateway.deliveries[-1]
        assert not area_stop_written.done()
        assert blind_1._pulse_stop_written is area_stop_written
        assert blind_1._stop_task is None

        area_stop_written.set_result(clock.now)
        await _yield()
        release_pulse.set()

    assert blind_1._is_tilting is False
    assert blind_1.current_cover_tilt_position == 25

    # 2. Cross-scope tilt pulse: group (#1) tilts blind_1 & blind_2; general (0) stops both via seen_leaders.
    blind_2 = _make_cover(hass, gateway, where="14", name="Blind 14", travel_time=40.0, slat_tilt=True, slat_time=2.0, position=0)
    blind_1._attr_current_cover_tilt_position = 0
    for b in (blind_1, blind_2):
        b._device_config = lambda: {"groups": [1]}  # type: ignore[method-assign]
    cross_family = CoverFamily()
    group = _make_scope(hass, gateway, "#1", [blind_1, blind_2], slat_tilt=True, slat_time=2.0, family=cross_family)
    general = _make_scope(hass, gateway, "0", [blind_1, blind_2], slat_tilt=True, slat_time=2.0, family=cross_family)

    release_group_pulse = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_group_pulse)):
        await group.async_open_cover_tilt()
        _, grp_tilt_written = gateway.deliveries[-1]
        grp_tilt_written.set_result(clock.now)
        await _yield()
        group.handle_event(OWNEvent.parse("*2*1*#1##"))
        assert group._is_tilting is True
        assert blind_1._tilt_follows is group and blind_2._tilt_follows is group

        clock.now += 1.0  # 1.0 s / 2.0 s = 50% slat tilt
        await general.async_stop_cover()
        _, gen_stop_written = gateway.deliveries[-1]
        gen_stop_written.set_result(clock.now)
        await _yield()
        release_group_pulse.set()

    assert group._is_tilting is False
    assert blind_1._is_tilting is False and blind_2._is_tilting is False
    assert blind_1.current_cover_tilt_position == 50
    assert blind_2.current_cover_tilt_position == 50

    # 3. Mixed scope: area is tilting blind_1 while non-Venetian shutter_2 is moving from an open command.
    blind_1._attr_current_cover_tilt_position = 0
    release_mixed_pulse = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_mixed_pulse)):
        await area.async_open_cover_tilt()
        _, mixed_tilt_written = gateway.deliveries[-1]
        mixed_tilt_written.set_result(clock.now)
        await _yield()
        area.handle_event(OWNEvent.parse("*2*1*1##"))
        assert area._is_tilting is True and blind_1._tilt_follows is area

        shutter_2.handle_event(OWNEvent.parse("*2*1*12##"))  # wall UP starts shutter_2
        assert shutter_2.is_opening is True
        assert shutter_2._wall_settle_unsub is not None

        clock.now += 15.0  # shutter_2 moved 15 s / 60 s = 25%
        await area.async_stop_cover()
        _, mixed_stop_written = gateway.deliveries[-1]
        mixed_stop_written.set_result(clock.now)
        await _yield()
        release_mixed_pulse.set()

    assert area._is_tilting is False
    assert blind_1._is_tilting is False
    assert shutter_2.is_opening is False
    assert shutter_2.current_cover_position == 25


async def test_reset_travel_time_demotion_cancels_pending_app_and_wall_settle_timers(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Resetting/updating travel times cancels zero-travel settles and re-arms mid-run wall settles."""
    cover = _make_cover(hass, gateway, where="21", name="Simple Relay", travel_time=0.0, position=None)
    assert cover.assumed_state is True
    cover._device_config = lambda: {CONF_TRAVEL_TIME: 0}  # type: ignore[method-assign]

    # Promote via async_set_travel_time -> TimedCurtainStrategy (40 s).
    await cover.async_set_travel_time(travel_time=40.0)
    assert cover.assumed_state is False

    # 1. App open_cover arms _stop_task; async_reset_travel_time demotes to simple and cancels _stop_task.
    await cover.async_open_cover()
    _, open_written = gateway.deliveries[-1]
    open_written.set_result(clock.now)
    await _yield()
    assert cover._stop_task is not None

    await cover.async_reset_travel_time()
    assert cover.assumed_state is True
    assert cover._stop_task is None
    assert cover._start_position is None
    assert cover.current_cover_position is None

    # 2. Re-promote, arm _wall_settle_unsub from wall DOWN, then reset travel time mid-run.
    await cover.async_set_travel_time(travel_time=40.0)
    t0 = dt_util.utcnow()
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    assert cover._wall_settle_unsub is not None

    await cover.async_reset_travel_time()
    assert cover.assumed_state is True
    assert cover._wall_settle_unsub is None
    clock.now += 45.0
    async_fire_time_changed(hass, t0 + timedelta(seconds=45.0))
    await hass.async_block_till_done()
    assert cover.current_cover_position is None
    assert cover.assumed_state is True

    # 3. Direct assumed_state / zero-travel guards inside _settle_run_to_end and _on_wall_settle callbacks.
    await cover.async_set_travel_time(travel_time=40.0)
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        try:
            await cover.async_open_cover()
            _, w2 = gateway.deliveries[-1]
            w2.set_result(clock.now)
            await _yield()
            task_ref = cover._stop_task
            assert task_ref is not None
            cover._strategy = cover._strategy.demoted()
            cover._attr_assumed_state = True
            cover._attr_current_cover_position = None
        finally:
            release.set()
        await asyncio.wait_for(task_ref, 1.0)
    assert cover.current_cover_position is None

    await cover.async_set_travel_time(travel_time=40.0)
    t1 = dt_util.utcnow()
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    cover._strategy = cover._strategy.demoted()
    cover._attr_assumed_state = True
    cover._attr_current_cover_position = None
    clock.now += 45.0
    async_fire_time_changed(hass, t1 + timedelta(seconds=45.0))
    await hass.async_block_till_done()
    assert cover.current_cover_position is None

    # 4. Venetian cover with travel_time: 0 in YAML (VenetianTiltStrategy.demoted() returns self):
    # async_reset_travel_time cancels curtain settle timers while preserving active slat tilt pulses.
    venetian = _make_cover(hass, gateway, where="31", name="Venetian Zero", travel_time=0.0, slat_tilt=True, slat_time=2.0)
    venetian._device_config = lambda: {CONF_TRAVEL_TIME: 0}  # type: ignore[method-assign]
    await venetian.async_set_travel_time(travel_time=40.0)
    await venetian.async_open_cover()
    _, ven_open_w = gateway.deliveries[-1]
    ven_open_w.set_result(clock.now)
    await _yield()
    assert venetian._stop_task is not None
    await venetian.async_reset_travel_time()
    assert venetian._stop_task is None
    assert venetian._travel_for(True) == 0.0
    clock.now += 2.0  # past ECHO_WINDOW of ven_open_w

    # Setting travel_time while already moving without _stop_task arms _wall_settle_unsub immediately!
    await venetian.async_set_travel_time(travel_time=40.0)
    assert venetian._wall_settle_unsub is not None
    venetian.handle_event(OWNEvent.parse("*2*0*31##"))

    t_ven = dt_util.utcnow()
    venetian.handle_event(OWNEvent.parse("*2*1*31##"))
    assert venetian._wall_settle_unsub is not None
    await venetian.async_reset_travel_time()
    assert venetian._wall_settle_unsub is None
    clock.now += 45.0
    async_fire_time_changed(hass, t_ven + timedelta(seconds=45.0))
    await hass.async_block_till_done()
    assert venetian.current_cover_position is None
    venetian.handle_event(OWNEvent.parse("*2*0*31##"))

    # Direct zero-travel completion guards on VenetianTiltStrategy (assumed_state is False).
    await venetian.async_set_travel_time(travel_time=40.0)
    release_ven_settle = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_ven_settle)):
        try:
            await venetian.async_open_cover()
            _, ven_w2 = gateway.deliveries[-1]
            ven_w2.set_result(clock.now)
            await _yield()
            ven_task = venetian._stop_task
            assert ven_task is not None
            venetian._travel_time_up = 0.0
        finally:
            release_ven_settle.set()
        await asyncio.wait_for(ven_task, 1.0)
    assert venetian.current_cover_position is None
    clock.now += 2.0
    venetian.handle_event(OWNEvent.parse("*2*0*31##"))

    await venetian.async_set_travel_time(travel_time=40.0)
    t_ven2 = dt_util.utcnow()
    venetian.handle_event(OWNEvent.parse("*2*1*31##"))
    venetian._travel_time_up = 0.0
    clock.now += 45.0
    async_fire_time_changed(hass, t_ven2 + timedelta(seconds=45.0))
    await hass.async_block_till_done()
    assert venetian.current_cover_position is None
    venetian.handle_event(OWNEvent.parse("*2*0*31##"))

    # Active slat tilt pulse on Venetian cover is NOT cancelled when resetting travel_time to 0.
    venetian._attr_current_cover_position = venetian._start_position = 0
    venetian._attr_current_cover_tilt_position = 0
    release_ven_tilt = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_ven_tilt)):
        await venetian.async_set_travel_time(travel_time=40.0)
        await venetian.async_open_cover_tilt()
        _, ven_tilt_w = gateway.deliveries[-1]
        ven_tilt_w.set_result(clock.now)
        await _yield()
        assert venetian._is_tilting is True and venetian._stop_task is not None
        await venetian.async_reset_travel_time()
        assert venetian._is_tilting is True and venetian._stop_task is not None
        await venetian.async_stop_cover_tilt()
        _, ven_stop_w = gateway.deliveries[-1]
        ven_stop_w.set_result(clock.now)
        await _yield()
        release_ven_tilt.set()

    # 5. Mid-wall-run async_set_travel_time / async_reset_travel_time re-arms _wall_settle_unsub
    # for remaining travel + END_SETTLE_MARGIN (subtracting elapsed motor time).
    timed = _make_cover(hass, gateway, where="41", name="Timed Rearm", travel_time=80.0, position=0)
    timed._device_config = lambda: {CONF_TRAVEL_TIME: 50}  # type: ignore[method-assign]
    timed.handle_event(OWNEvent.parse("*2*1*41##"))  # armed for 82 s
    clock.now += 10.0
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=10.0))
    await hass.async_block_till_done()
    await timed.async_set_travel_time(travel_time=20.0)  # remaining: 20 + 2 - 10 = 12 s
    t_rearm = dt_util.utcnow()
    clock.now += 11.0
    async_fire_time_changed(hass, t_rearm + timedelta(seconds=11.0))
    await hass.async_block_till_done()
    assert timed.is_opening is True  # 1.0 s still remaining before 22 s total
    clock.now += 1.5
    async_fire_time_changed(hass, t_rearm + timedelta(seconds=12.5))
    await hass.async_block_till_done()
    assert timed.is_opening is False and timed.current_cover_position == 100

    timed.handle_event(OWNEvent.parse("*2*2*41##"))  # armed for 22 s
    clock.now += 5.0
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5.0))
    await hass.async_block_till_done()
    await timed.async_reset_travel_time()  # resets to YAML 50 s -> remaining: 50 + 2 - 5 = 47 s
    t_reset = dt_util.utcnow()
    clock.now += 20.0
    async_fire_time_changed(hass, t_reset + timedelta(seconds=20.0))
    await hass.async_block_till_done()
    assert timed.is_closing is True  # past old 22 s timer!
    clock.now += 27.5
    async_fire_time_changed(hass, t_reset + timedelta(seconds=47.5))
    await hass.async_block_till_done()
    assert timed.is_closing is False and timed.current_cover_position == 0

    # 6. Failed stop_written Future clears _pending_cmd cleanly so the real stop still freezes position.
    uncal = _make_cover(hass, gateway, where="51", name="Uncal Stop", travel_time=86.0, position=0)
    t_sw = clock.now
    uncal.handle_event(OWNEvent.parse("*2*1*51##"))
    clock.now = t_sw + 20.0
    await uncal.async_stop_cover()
    _, failed_stop_w = gateway.deliveries[-1]
    failed_stop_w.set_exception(HomeAssistantError("bus write failed"))
    await _yield()
    assert uncal._pending_cmd is None

    clock.now = t_sw + 43.0
    uncal.handle_event(OWNEvent.parse("*2*0*51##"))
    assert uncal.current_cover_position == 50
    assert uncal.extra_state_attributes["last_run_seconds"] == pytest.approx(43.0, abs=0.02)


async def test_who2_command_translation_stops_on_myhomeserver1_and_f461(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """WHO 2 *2*1000#0* / *2*1000#10…* stops halt runs (#645), re-anchor on *2*0*, and never discover phantoms."""
    from custom_components.myhome.discovery import PlatformDiscovery

    # 1. MyHomeServer1 drops plain *2*0*21## (#645): *2*1000#0*21## stops the run and cancels _wall_settle_unsub.
    cover = _make_cover(hass, gateway, where="21", name="MHS1 Shutter", travel_time=40.0, position=0)
    t0 = dt_util.utcnow()
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    assert cover._wall_settle_unsub is not None
    clock.now += 20.0
    cover.handle_event(OWNEvent.parse("*2*1000#0*21##"))
    assert cover.is_opening is False
    assert cover._wall_settle_unsub is None
    assert cover.current_cover_position == 50
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(20.0)

    # Settle timer never fires later even without plain *2*0*21##.
    async_fire_time_changed(hass, t0 + timedelta(seconds=43.0))
    await hass.async_block_till_done()
    assert cover.current_cover_position == 50

    # Follow-up wall command 0.4 s later (still inside ECHO_WINDOW): leading *2*1000#0*21## closes
    # the stop echo window so pre-direction *2*0*21## does not re-anchor and *2*2*21## starts closing!
    clock.now += 0.4
    assert cover._pending_cmd == "stop" and cover._stop_translation_seen is True
    cover.handle_event(OWNEvent.parse("*2*1000#0*21##"))
    assert cover._pending_cmd is None
    clock.now += 0.05
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover.current_cover_position == 50
    clock.now += 0.05
    cover.handle_event(OWNEvent.parse("*2*2*21##"))
    assert cover.is_closing is True

    # Advanced stop translation *2*1000#10#001#1*21## also halts the closing run;
    # and a direction translation *2*1000#1*21## inside ECHO_WINDOW closes the stale stop window too!
    clock.now += 10.0
    cover.handle_event(OWNEvent.parse("*2*1000#10#001#1*21##"))
    assert cover.is_closing is False
    assert cover.current_cover_position == 25
    assert cover._stop_translation_seen is True
    clock.now += 0.3
    cover.handle_event(OWNEvent.parse("*2*1000#1*21##"))
    assert cover._pending_cmd is None
    assert cover.is_opening is False  # #1 translation alone never starts a run (WHO 14 / absent safety)

    # 2. Normal gateway (F461 / F454): *2*1000#0*21## freezes provisionally, then *2*0*21## 100 ms later re-anchors!
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    t_run = clock.now
    clock.now = t_run + 9.0  # 25% + 22.5% = 48% provisionally
    cover.handle_event(OWNEvent.parse("*2*1000#0*21##"))
    assert cover.current_cover_position == 48
    assert cover._pending_cmd == "stop"
    clock.now = t_run + 10.0  # exact 25% + 25% = 50% on actuator *2*0*21##!
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover._pending_cmd is None
    assert cover.current_cover_position == 50
    assert cover.extra_state_attributes["last_run_seconds"] == pytest.approx(10.0)

    # 3. HA async_stop_cover() where RX *2*1000#0*21## arrives BEFORE stop_written Future resolves (Trace 3 #645).
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    t_ha = clock.now
    clock.now = t_ha + 9.2
    await cover.async_stop_cover()
    _, ha_stop_w = gateway.deliveries[-1]
    assert not ha_stop_w.done()
    cover.handle_event(OWNEvent.parse("*2*1000#0*21##"))
    assert cover.is_opening is False
    assert cover._pending_cmd == "stop"  # kept open for *2*0*21##!
    ha_stop_w.set_result(clock.now + 0.02)
    await _yield()
    clock.now = t_ha + 10.0  # +0.78 s after write (< 1.0 s ECHO_WINDOW) -> re-anchors to 75%!
    cover.handle_event(OWNEvent.parse("*2*0*21##"))
    assert cover._pending_cmd is None
    assert cover.current_cover_position == 75

    # 4. Scope wall translated stop (*2*1000#0*1##) + memberless scope _run_timeout cancellation.
    m1 = _make_cover(hass, gateway, where="11", name="Member 11", travel_time=40.0, position=0)
    area = _make_scope(hass, gateway, "1", [m1])
    for c in (area, m1):
        c.handle_event(OWNEvent.parse("*2*1*1##"))
    t_area = clock.now
    clock.now = t_area + 10.0
    for c in (area, m1):
        c.handle_event(OWNEvent.parse("*2*1000#0*1##"))
    assert m1.is_opening is False and m1.current_cover_position == 25
    # Relay of *2*0*1## keeps m1's stop window open so *2*0*11## re-anchors!
    clock.now = t_area + 10.05
    m1.handle_event(OWNEvent.parse("*2*0*1##"))
    assert m1._pending_cmd == "stop"
    clock.now = t_area + 20.0  # impossible on real bus (>1.5 s), test within ECHO_WINDOW (+0.8 s -> 27%):
    clock.now = t_area + 10.8
    m1.handle_event(OWNEvent.parse("*2*0*11##"))
    assert m1._pending_cmd is None and m1.current_cover_position == 27

    empty_group = _make_scope(hass, gateway, "#3", [])
    empty_group.handle_event(OWNEvent.parse("*2*1*#3##"))
    assert empty_group._run_timeout is not None
    clock.now += 5.0
    empty_group.handle_event(OWNEvent.parse("*2*1000#0*#3##"))
    assert empty_group.is_opening is False and empty_group._run_timeout is None

    # 5. Slat-tilting advanced & timed blinds stop on *2*1000#0*, while non-tilting advanced cover ignores it.
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        adv_blind = MyHOMECover(
            hass=hass,
            name="Adv Blind",
            entity_name=None,
            device_id="35",
            who="2",
            where="35",
            interface=None,
            advanced=True,
            manufacturer="BTicino",
            model="F411U2",
            gateway=gateway,
            slat_tilt=True,
            slat_time=2.0,
        )
    adv_blind.hass = hass
    adv_blind.entity_id = "cover.adv_blind"
    adv_blind.async_write_ha_state = MagicMock()
    adv_blind.async_schedule_update_ha_state = MagicMock()
    adv_blind._attr_current_cover_tilt_position = 0
    adv_blind._attr_is_opening = True
    adv_blind.handle_event(OWNEvent.parse("*2*1000#0*35##"))
    assert adv_blind._attr_is_opening is True  # ignored when advanced cover is NOT tilting!
    adv_blind._attr_is_opening = False

    release_tilt = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release_tilt)):
        await adv_blind.async_open_cover_tilt()
        _, tw = gateway.deliveries[-1]
        tw.set_result(clock.now)
        await _yield()
        adv_blind.handle_event(OWNEvent.parse("*2*1*35##"))
        assert adv_blind._is_tilting is True
        clock.now += 2.0  # past ECHO_WINDOW
        adv_blind._tilt_start_time = clock.now - 1.0
        adv_blind.handle_event(OWNEvent.parse("*2*1000#0*35##"))
        assert adv_blind._is_tilting is False
        assert adv_blind.current_cover_tilt_position == 50
        release_tilt.set()

    # 6. Unknown-start HA run stopped by *2*1000#0* cancels _stop_task and stays unknown.
    unk = _make_cover(hass, gateway, where="26", name="Unknown Stop", travel_time=40.0, position=None)
    await unk.async_open_cover()
    _, uw = gateway.deliveries[-1]
    uw.set_result(clock.now)
    await _yield()
    unk.handle_event(OWNEvent.parse("*2*1*26##"))
    assert unk._end_run_from_unknown is True and unk._stop_task is not None
    clock.now += 5.0
    unk.handle_event(OWNEvent.parse("*2*1000#0*26##"))
    assert unk._stop_task is None
    assert unk.is_opening is False
    assert unk.current_cover_position is None

    # 7. Active async_calibrate() interrupted by external *2*1000#0* raises CalibrationInterrupted.
    cal_cover = _make_cover(hass, gateway, where="27", name="Calib Stop", travel_time=40.0, position=50)
    cal_task = asyncio.create_task(cal_cover.async_calibrate())
    await _yield()
    _, cw = gateway.deliveries[-1]
    cw.set_result(clock.now)
    await _yield()
    clock.now += 0.1
    cal_cover.handle_event(OWNEvent.parse("*2*1*27##"))
    await _yield()
    clock.now += 5.0
    cal_cover.handle_event(OWNEvent.parse("*2*1000#0*27##"))
    with pytest.raises(CalibrationInterrupted, match="external stop"):
        await asyncio.wait_for(cal_task, 1.0)
    assert cal_cover._settles_on_timer is True

    # 8. PlatformDiscovery(route_translations=True) routes to known cover 21 without discovering phantom 99.
    from OWNd.message import OWNAutomationEvent

    router = FrameRouter()
    config_entry = MagicMock()
    config_entry.data = {"mac": gateway.mac}
    config_entry.options = {}
    config_entry.runtime_data = MagicMock(
        mac=gateway.mac,
        router=router,
        platforms={},
        is_follower=False,
        delegated_away_whos=set(),
    )
    created: list[str] = []
    disc = PlatformDiscovery(
        hass,
        config_entry,
        MagicMock(),
        platform="cover",
        who="2",
        event_type=OWNAutomationEvent,
        build=lambda ctx: created.append(ctx.address.key) or MagicMock(),
        route_translations=True,
    )
    disc.known.add("21")
    seen_21: list[str] = []
    router.subscribe("2", ["21"], lambda m: seen_21.append(str(m)))
    disc.handle_message(OWNEvent.parse("*2*1000#0*99##"))  # unknown address -> no phantom created!
    assert created == []
    disc.handle_message(OWNEvent.parse("*2*1000#0*21##"))  # known address -> routed!
    assert seen_21 == ["*2*1000#0*21##"]


async def test_mid_run_set_and_reset_travel_time_recomputes_app_and_scope_settle_tasks(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """Mid-run async_set_travel_time / async_reset_travel_time recomputes active _settle_run_to_end tasks."""
    sleep_delays: list[float] = []
    step_events: list[asyncio.Event] = []

    async def _stepped_sleep(delay: float, *args, **kwargs) -> None:
        if delay <= 0:
            await _REAL_SLEEP(0)
            return
        sleep_delays.append(delay)
        ev = asyncio.Event()
        step_events.append(ev)
        await ev.wait()

    # 1. Lengthening mid-run (25 s -> 86 s at t+10 s): wakes sleep(27), re-sleeps for remaining 78 s!
    cover = _make_cover(hass, gateway, where="21", name="Recompute Up", travel_time=25.0, position=0)
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_stepped_sleep):
        await cover.async_open_cover()
        _, w1 = gateway.deliveries[-1]
        w1.set_result(clock.now)
        await _yield()
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        await _yield(6)
        assert sleep_delays[-1] == pytest.approx(27.0)

        clock.now += 10.0
        await cover.async_set_travel_time(travel_time=86.0)
        await _yield(8)
        assert cover.is_opening is True  # did NOT settle at 10 s!
        assert sleep_delays[-1] == pytest.approx(78.0)  # 86 + 2 - 10 = 78 s

        clock.now += 78.0
        step_events[-1].set()
        assert cover._stop_task is not None
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.is_opening is False and cover.current_cover_position == 100

    # 2. Shortening mid-run below elapsed (86 s -> reset to 25 s default at t+30 s): settles immediately!
    cover._device_config = lambda: {}  # type: ignore[method-assign]
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_stepped_sleep):
        await cover.async_close_cover()
        _, w2 = gateway.deliveries[-1]
        w2.set_result(clock.now)
        await _yield()
        cover.handle_event(OWNEvent.parse("*2*2*21##"))
        await _yield(6)
        assert sleep_delays[-1] == pytest.approx(88.0)

        clock.now += 30.0
        await cover.async_reset_travel_time()  # back to 25 s default -> 27 - 30 = -3 <= 0!
        assert cover._stop_task is not None
        await asyncio.wait_for(cover._stop_task, 1.0)
    assert cover.is_closing is False and cover.current_cover_position == 0

    # 3. Wall run started AFTER a completed HA _stop_task (_stop_task.done() is True):
    # mid-run async_set_travel_time still re-arms _wall_settle_unsub!
    assert cover._stop_task is not None and cover._stop_task.done()
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    first_unsub = cover._wall_settle_unsub
    assert first_unsub is not None
    clock.now += 5.0
    await cover.async_set_travel_time(travel_time=50.0)
    assert cover._wall_settle_unsub is not None and cover._wall_settle_unsub is not first_unsub
    cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 4. Scope run member recomputes its _stop_task when member.async_set_travel_time is called mid-run.
    member = _make_cover(hass, gateway, where="11", name="Scope Member", travel_time=60.0, position=0)
    area = _make_scope(hass, gateway, "1", [member])
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_stepped_sleep):
        await area.async_open_cover()
        _, ws = gateway.deliveries[-1]
        ws.set_result(clock.now)
        await _yield()
        for c in (area, member):
            c.handle_event(OWNEvent.parse("*2*1*1##"))
        await _yield(6)
        clock.now += 20.0
        await member.async_set_travel_time(travel_time=15.0)  # 17 - 20 <= 0 -> settles immediately!
        assert member._stop_task is not None
        await asyncio.wait_for(member._stop_task, 1.0)
    assert member.is_opening is False and member.current_cover_position == 100
    assert area.is_opening is False and area.current_cover_position == 100


async def test_settles_on_timer_flag_and_calibration_hub_stop_restoration(
    hass: HomeAssistant, gateway, clock: Clock, fake_time
) -> None:
    """_settles_on_timer disables wall/app timer settling while lowered and is restored on calibration stop."""
    cover = _make_cover(hass, gateway, where="21", name="Wizard Target", travel_time=30.0, position=None)
    assert cover._settles_on_timer is True

    # 1. Lowering _settles_on_timer during a wall run cancels _wall_settle_unsub without stopping the motor.
    cover.handle_event(OWNEvent.parse("*2*1*21##"))
    assert cover._wall_settle_unsub is not None
    cover._settles_on_timer = False
    assert cover._settles_on_timer is False
    assert cover._wall_settle_unsub is None
    assert cover.is_opening is True
    cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 2. While _settles_on_timer is False, neither HA open_cover nor wall open arms a settle timer.
    await cover.async_open_cover()
    _, w1 = gateway.deliveries[-1]
    w1.set_result(clock.now)
    await _yield()
    assert cover._stop_task is None
    cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 3. Lowering _settles_on_timer mid-HA-run wakes and exits _settle_run_to_end without settling or stopping motor.
    cover._settles_on_timer = True
    release = asyncio.Event()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=_controlled_sleep(release)):
        await cover.async_open_cover()
        _, w2 = gateway.deliveries[-1]
        w2.set_result(clock.now)
        await _yield()
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        await _yield(6)
        assert cover._stop_task is not None and cover._end_run_from_unknown is True
        cover._settles_on_timer = False
        await asyncio.wait_for(cover._stop_task, 1.0)
        release.set()
    assert cover.is_opening is True
    assert cover.current_cover_position is None
    assert cover._end_run_from_unknown is False
    cover.handle_event(OWNEvent.parse("*2*0*21##"))

    # 4. CoverCalibrationHub.async_stop() restores active._settles_on_timer = True.
    hub = cover.calibration_hub
    hub.active_cover = cover
    cover._calibrating = True
    assert cover._settles_on_timer is False
    stopped = await hub.async_stop()
    assert stopped == 1
    assert cover._settles_on_timer is True

