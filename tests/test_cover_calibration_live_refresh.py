"""The ~1 s position refresh of a timed run, met by a calibration run.

A timed cover republishes its interpolated position about once a second while it moves
(``LIVE_POSITION_REFRESH_INTERVAL``). The cover skips it while its own native calibration
runs, but not while a guided session (base, refinement, check) drives it: the cover still
anchors the session's runs from the bus. These tests fire that refresh between every frame
and every action of whole sessions and show what it changes for the calibration: nothing.
It sends no frame and moves no session field, no anchor and no deadline, and the measured
times, geometry and check stay those of the same session without it. What it does change is
Home Assistant's state: the slider moves during the run, on the times in use (the ones the
session is about to replace).
"""
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.util import dt as dt_util

import tests.test_cover_calibration_check as check_tests
import tests.test_cover_calibration_geometry as geometry_tests
import tests.test_panel_cover_calibration as base_tests
from custom_components.myhome import cover as cover_module
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture
geometry = geometry_fixture

RAISE, LOWER, STOP = "*2*1*11##", "*2*2*11##", "*2*0*11##"


class Refresh:
    """The cover's refresh tickers, fired by hand on the session's clock."""

    def __init__(self, cal):
        self.cal = cal
        self.armed: dict[object, object] = {}
        self.ticks = self.published = 0
        self.positions: list[int | None] = []

    def track(self, hass, action, interval, *, name=None, cancel_on_shutdown=None):
        assert interval == cover_module.LIVE_POSITION_REFRESH_INTERVAL
        key = object()
        self.armed[key] = action

        def unsub():
            self.armed.pop(key, None)

        return unsub

    def state(self):
        cal, session, cover = self.cal, self.cal.session, self.cal.cover
        deadline = session.deadline
        return (len(cal.queue), session.phase, getattr(session, "step", None), session.sequence, dict(session.values),
                dict(getattr(session, "samples", {}) or {}), session.started_at,
                None if deadline is None else (deadline.when(), deadline.cancelled()),
                cover._start_position, cover._move_start_time, cover._run_started_at, cover._motion.position,
                cover._motion.anchor, cover._attr_current_cover_position, cover._attr_is_opening,
                cover._attr_is_closing, cover._attr_is_closed, cover._stop_task, cover._wall_settle_unsub)

    def fire(self):
        cover = self.cal.cover
        for action in list(self.armed.values()):
            before, writes = self.state(), cover.async_schedule_update_ha_state.call_count
            action(dt_util.utcnow())
            self.ticks += 1
            # The refresh changes nothing the session reads or writes, and sends nothing on the bus.
            assert self.state() == before
            if cover.async_schedule_update_ha_state.call_count > writes:
                self.published += 1
                self.positions.append(cover.current_cover_position)


@pytest.fixture
def ticking(request):
    """Run a session with the refresh fired before every bus frame and every panel action."""

    def arm(cal):
        refresh = Refresh(cal)
        original_bus, original_act = base_tests.bus, base_tests.act

        def bus(cal, raw):
            refresh.fire()
            original_bus(cal, raw)
            refresh.fire()

        async def act(cal, action, **extra):
            refresh.fire()
            result = await original_act(cal, action, **extra)
            refresh.fire()
            return result

        cal.cover.platform = SimpleNamespace(config_entry=None)  # published only by a cover on a platform
        clock = SimpleNamespace(monotonic=lambda: cal.clock[0], time=time.time)
        patches = [patch.object(cover_module, "async_track_time_interval", side_effect=refresh.track),
                   patch.object(cover_module, "time", clock)]
        for module in (base_tests, geometry_tests, check_tests):
            patches += [patch.object(module, "bus", bus), patch.object(module, "act", act)]
        for item in patches:
            item.start()
            request.addfinalizer(item.stop)
        return refresh

    return arm


async def test_a_base_run_is_refreshed_and_measures_the_same(hass, calibration, ticking):
    cal = calibration
    refresh = ticking(cal)
    await base_tests.act(cal, "open")
    assert cal.queue[-1][1]()
    cal.clock[0] += 2
    base_tests.bus(cal, RAISE)
    # The guided run is anchored by the cover, which arms its refresh: unlike its native calibration.
    assert cal.session.phase == "opening" and not cal.cover._calibrating and refresh.armed
    cal.clock[0] += 6
    refresh.fire()
    # On the cover's travel time in use (30 s), not on the time being measured.
    assert refresh.positions[-1] == 20
    cal.clock[0] += 14.5
    await base_tests.act(cal, "endpoint")
    base_tests.bus(cal, STOP)
    assert not refresh.armed  # the confirmed end stop ends it at its next tick
    await base_tests.act(cal, "close")
    assert cal.queue[-1][1]()
    cal.clock[0] += 3
    base_tests.bus(cal, LOWER)
    for _ in range(40):
        cal.clock[0] += 1
        refresh.fire()
    cal.clock[0] += .5
    await base_tests.act(cal, "endpoint")
    base_tests.bus(cal, STOP)
    assert cal.session.phase == "review"
    assert cal.session.values == {"opening_time": 20.5, "closing_time": 40.5}
    assert [str(item[0]) for item in cal.queue] == [RAISE, STOP, LOWER, STOP]
    assert refresh.published > 2 and not refresh.armed


async def test_a_refinement_and_its_check_are_refreshed_and_measure_the_same(hass, geometry, ticking):
    cal = geometry
    refresh = ticking(cal)
    view = await check_tests.verdict(cal)
    assert refresh.ticks and refresh.published
    # The same session without the refresh (tests.test_cover_calibration_geometry, ..._check).
    assert cal.session.values == {"opening_time": 22, "closing_time": 20}
    assert cal.session.geometry == pytest.approx({"slat_time_s": 2, "opening_roll": 2, "closing_roll": 3})
    assert view["passed"] is True and view["deviation_cm"] == 0
    assert view["expected_cm"] == pytest.approx(cal.session.check.expected_cm)
    sent = [str(item[0]) for item in cal.queue]
    assert set(sent) <= {RAISE, LOWER, STOP} and sent.count(STOP) == len(sent) / 2
    assert not refresh.armed
