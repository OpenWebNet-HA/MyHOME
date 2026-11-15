"""Upstream full-run timers must coexist with panel calibration and profiles."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.myhome.cover_calibration import ready_cover
from custom_components.myhome.cover_motion import CoverMotionModel
from custom_components.myhome.cover_profiles import get_store, write_profile
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture


@pytest.mark.parametrize("modelled", [False, True])
async def test_guided_run_does_not_arm_the_previous_travel_timer(calibration, modelled):
    """A measurement longer than the old travel remains owned by the operator."""
    cal = calibration
    if modelled:
        cal.cover._motion.configure(CoverMotionModel(30, 30, 2))
    await act(cal, "open")
    assert cal.queue[-1][1]()
    with patch("custom_components.myhome.cover.time.monotonic", side_effect=lambda: cal.clock[0]):
        bus(cal, "*2*1*11##")
        assert not cal.cover._settles_on_timer
        assert cal.cover._wall_settle_unsub is None
        assert cal.cover._stop_task is None
        cal.clock[0] += 45
        bus(cal, "*2*1*11##")
        assert cal.session.phase == "opening"
        assert cal.cover.is_opening
        assert cal.cover._wall_settle_unsub is None
        await act(cal, "endpoint")
        bus(cal, "*2*0*11##")
    assert cal.session.values["opening_time"] == 45
    cal.session.close()
    assert cal.cover._settles_on_timer


async def test_finished_full_run_releases_task_and_allows_guided_calibration(hass, plant):
    cover = plant.covers[0]
    with (
        patch.object(cover, "_await_motion_anchor", AsyncMock(return_value=100)),
        patch("custom_components.myhome.cover.time.monotonic", return_value=200),
    ):
        await cover.async_open_cover()
        task = cover._stop_task
        assert task is not None
        await task
    assert cover.current_cover_position == 100
    assert cover._stop_task is None
    assert not cover.is_opening
    store = get_store(hass, plant.entries[0].entry_id)
    assert ready_cover(hass, store, plant.entries[0].entry_id, cover.entity_id) is cover


async def test_full_run_applies_a_profile_waiting_for_the_end_stop(hass, plant):
    cover = plant.covers[0]
    release = asyncio.Event()

    async def anchor(_written):
        await release.wait()
        return 100

    with (
        patch.object(cover, "_await_motion_anchor", side_effect=anchor),
        patch("custom_components.myhome.cover.time.monotonic", return_value=200),
    ):
        await cover.async_open_cover()
        task = cover._stop_task
        pending = await write_profile(hass, message(plant))
        assert pending["pending"]
        assert cover._travel_time_up == 30
        release.set()
        await task
    assert cover._pending_profile is None
    assert cover._travel_time_up == 42.5
    assert cover.current_cover_position == 100
