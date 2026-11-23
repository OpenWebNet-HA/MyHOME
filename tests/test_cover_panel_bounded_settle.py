"""Upstream #975 integration with panel profiles and durable native timing edits."""

from unittest.mock import MagicMock, patch

import pytest

from custom_components.myhome.cover_motion import MotionPosition
from tests.test_cover_nonlinear_backend import nonlinear
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_cover_travel_scaling import write

plant = plant_fixture


@pytest.mark.parametrize("opening,expected", [(True, 11), (False, 21)])
async def test_linear_profile_uses_directional_remaining_time(hass, plant, opening, expected):
    cover = plant.covers[0]
    await write(hass, plant, profile={"name": "Asymmetric", "opening_time": 20, "closing_time": 40})
    cover._start_position = cover._run_start_position = 50
    cover._position_drift_bound = 1.0
    assert cover._settle_travel_for(opening) == expected
    # A stale/restored estimate must still get a whole run for re-homing.
    cover._position_drift_bound = None
    assert cover._settle_travel_for(opening) == (20 if opening else 40)


@pytest.mark.parametrize("opening", [True, False])
async def test_nonlinear_profile_keeps_model_duration_with_confirmed_drift(hass, plant, opening):
    cover = plant.covers[0]
    await write(hass, plant, profile=nonlinear(reference_travel_cm=None))
    cover._motion.confirm(50)
    cover._motion.prepare(opening)
    cover._motion.start(100)
    cover._start_position = cover._run_start_position = 50
    cover._position_drift_bound = 0.0
    model = cover._motion.model
    expected = model.duration(MotionPosition.at_height(0.5),
                              MotionPosition.at_height(1 if opening else 0), opening=opening)
    assert expected != pytest.approx(cover._travel_for(opening) / 2)
    assert cover._settle_travel_for(opening) == pytest.approx(expected)
    cover._motion.position = None
    assert cover._settle_travel_for(opening) == cover._travel_for(opening)


async def test_profile_rename_preserves_drift_but_deferred_timing_edit_invalidates_it(hass, plant):
    cover = plant.covers[0]
    profile = {"name": "Timed", "opening_time": 20, "closing_time": 40}
    saved = await write(hass, plant, profile=profile)
    profile_id = saved["assigned_profile_id"]
    cover._position_drift_bound = 0.5
    await write(hass, plant, profile_id=profile_id, profile={**profile, "name": "Renamed"})
    assert cover._position_drift_bound == 0.5
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        cover._start_position = cover._attr_current_cover_position = 50
        await cover.async_open_cover()
        cover._anchor_run(100)
        data = await write(hass, plant, profile_id=profile_id,
                           profile={**profile, "opening_time": 30})
        assert data["pending"]
        assert cover._travel_time_up == 20
        assert cover._position_drift_bound == 0.5
        assert cover._settle_travel_for(True) == 10.5
        cover._freeze_position(102)
    assert cover._travel_time_up == 30
    assert cover._position_drift_bound is None
    assert cover._settle_travel_for(True) == 30


@pytest.mark.parametrize("edit", [{"travel_time": 20}, {"slat_time": 3}])
async def test_failed_native_persistence_preserves_drift_and_active_settle(hass, plant, edit):
    cover = plant.covers[0]
    cover._slat_tilt = True
    cover._position_drift_bound = 0.5
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        cover._start_position = cover._attr_current_cover_position = 50
        await cover.async_open_cover()
        cover._anchor_run(100)
        before = cover._settle_travel_for(True)
        old_task = cover._stop_task
        with patch.object(cover, "_persist_calibration", side_effect=OSError("disk full")), \
             patch.object(cover, "_recompute_active_settle") as recompute:
            with pytest.raises(OSError, match="disk full"):
                await cover.async_set_travel_time(**edit)
            recompute.assert_not_called()
        assert cover._position_drift_bound == 0.5
        assert cover._settle_travel_for(True) == before
        assert cover._stop_task is old_task
        await cover.async_set_travel_time(**edit)
        assert cover._position_drift_bound is None
        assert cover._settle_travel_for(True) == cover._travel_for(True)


async def test_disconnect_invalidates_drift_and_still_interrupts_panel_session(hass, plant):
    cover = plant.covers[0]
    cover._position_drift_bound = 0.5
    session = MagicMock()
    cover._calibration = session
    plant.gateways[0].available = False
    cover._handle_availability_update()
    assert cover._position_drift_bound is None
    session.interrupt.assert_called_once_with("cover_unavailable")
    cover._calibration = None


async def test_panel_measurement_stop_cannot_confirm_old_timing_drift(hass, plant):
    cover = plant.covers[0]
    cover._calibration = MagicMock()
    cover._position_drift_bound = 0.5
    cover._start_position = cover._attr_current_cover_position = 50
    cover._run_started_at = cover._move_start_time = 100
    cover._attr_is_opening = True
    cover._freeze_position(140)
    assert cover._position_drift_bound is None
    cover._strategy.reanchor_on_stop_echo(cover, {
        "run_started_at": 100, "run_start_position": 50,
        "is_opening": True, "is_closing": False,
    }, 140)
    assert cover._position_drift_bound is None
    cover._calibration = None
