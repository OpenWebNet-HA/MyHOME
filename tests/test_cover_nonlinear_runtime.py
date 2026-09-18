"""Real timed-cover commands use the frozen nonlinear model and bus anchors."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNMessage

from custom_components.myhome.cover_motion import CoverMotionModel, MotionPosition
from custom_components.myhome.cover_motion_runtime import CoverMotionTracker
from tests.test_cover_nonlinear_backend import nonlinear
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_cover_travel_scaling import write

plant = plant_fixture


async def configured(hass, plant):
    data = await write(hass, plant, profile=nonlinear(reference_travel_cm=None))
    return plant.covers[0], data["assigned_profile_id"]


def test_tracker_retains_physical_progress_through_write_wait_echo_and_reversal():
    tracker = CoverMotionTracker()
    assert tracker.sample(0) is None
    with pytest.raises(ValueError, match="unknown"):
        tracker.remaining(MotionPosition.at_height(0.5))
    model = CoverMotionModel(26, 20, 2, 3, 2)
    tracker.configure(model)
    tracker.confirm(0)
    tracker.prepare(True)
    tracker.start(100)
    assert tracker.sample(100.5) == MotionPosition(0, 0.25)
    tracker.prepare(False)
    # The old movement continues while the opposite frame is still queued.
    assert tracker.sample(101) == MotionPosition(0, 0.5)
    tracker.start(101)
    tracker.start(101.1)  # Motor-start echo refines the anchor, not the start state.
    assert tracker.sample(101.6).slats == pytest.approx(0.25)
    assert tracker.remaining(MotionPosition.at_height(0)) == 1
    tracker.stop(102.1)
    assert tracker.position == MotionPosition(0, 0)
    tracker.confirm(50)
    tracker.prepare(True)
    tracker.start(200)
    assert tracker.remaining(MotionPosition.at_height(0.25)) == 0  # Queued target already passed.
    tracker.configure(None)
    assert tracker.position is None


async def test_unknown_state_needs_full_run_and_restart_does_not_invent_slat_position(hass, plant):
    cover, _ = await configured(hass, plant)
    assert cover.current_cover_position is None and not cover.is_closed
    with pytest.raises(HomeAssistantError, match="full opening"):
        await cover.async_set_cover_position(position=50)
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100):
        await cover.async_set_cover_position(position=100)
        cover._anchor_run(100)
    with patch('custom_components.myhome.cover.time.monotonic', return_value=125):
        assert cover.current_cover_position is None
    with patch('custom_components.myhome.cover.time.monotonic', return_value=126):
        assert cover.current_cover_position == 100
        cover._freeze_position(126)
    await cover.async_restore_last_state(SimpleNamespace(state='closed', attributes={'current_position': 0}))
    assert cover.current_cover_position is None
    with patch('custom_components.myhome.cover.time.monotonic', return_value=200):
        await cover.async_set_cover_position(position=0)
        cover._anchor_run(200)
        cover._freeze_position(205)  # An early Stop is not an endpoint confirmation.
        assert cover.current_cover_position is None
        await cover.async_close_cover()
        cover._anchor_run(210)
        cover._freeze_position(230)
        assert cover.current_cover_position == 0 and cover.is_closed


async def test_runtime_stop_and_reversal_inside_slats_and_external_status(hass, plant):
    cover, _ = await configured(hass, plant)
    cover._motion.confirm(0)
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100):
        await cover.async_open_cover()
        cover._anchor_run(100)
        cover._freeze_position(100.5)
        assert cover.current_cover_position == 0 and not cover.is_closed
        assert cover._motion.position.slats == 0.25
        cover._end_echo_window()
        cover.handle_event(OWNMessage.parse('*2*2*11##'))
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100.25):
        assert cover.current_cover_position == 0
        cover._end_echo_window()
        cover.handle_event(OWNMessage.parse('*2*1*11##'))
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100.5):
        cover.handle_event(OWNMessage.parse('*2*0*11##'))
        assert cover._motion.position.slats == pytest.approx(0.25)
    # A genuine position report supplies an explicit state even for a timed cover.
    event = OWNMessage.parse('*2*0*11##')
    with patch.object(type(event), 'current_position', new_callable=lambda: property(lambda _: 75)):
        cover.handle_event(event)
    assert cover.current_cover_position == 75


async def test_actual_target_timer_keeps_model_and_accounts_for_delayed_stop_write(hass, plant):
    cover, profile_id = await configured(hass, plant)
    cover._motion.confirm(0)
    old = cover._motion.model
    clock = [100.0]
    scheduled, release = asyncio.Event(), asyncio.Event()
    durations = []
    async def wait(seconds):
        durations.append(seconds)
        scheduled.set()
        await release.wait()
        clock[0] += seconds
    async def anchor(_):
        cover._anchor_run(clock[0])
        return clock[0]
    async def send(command):
        future = asyncio.get_running_loop().create_future()
        # Stop sits in the queue for another second after the requested target.
        if str(command) == '*2*0*11##':
            clock[0] += 1
        future.set_result(clock[0])
        return future
    plant.gateways[0].send = AsyncMock(side_effect=send)
    with patch('custom_components.myhome.cover.time.monotonic', side_effect=lambda: clock[0]), \
         patch.object(cover, '_await_motion_anchor', side_effect=anchor), \
         patch('custom_components.myhome.cover.asyncio.sleep', side_effect=wait):
        await cover.async_set_cover_position(position=50)
        await scheduled.wait()
        run = cover._stop_task
        expected_duration = old.duration(MotionPosition.at_height(0), MotionPosition.at_height(0.5), opening=True)
        assert durations == pytest.approx([expected_duration])
        data = await write(hass, plant, profile_id=profile_id,
                           profile=nonlinear(opening_time=40, reference_travel_cm=None,
                                             geometry={"slat_time_s": 3, "opening_roll": 2, "closing_roll": 2}))
        assert data["pending"] and cover._motion.model == old
        assert data["effective"]["opening_roll"]["value"] == 3
        assert data["configured"]["opening_roll"]["value"] == 2
        release.set()
        await run
        await asyncio.get_running_loop().run_in_executor(None, lambda: None)
        assert cover.current_cover_position == round(old.advance(MotionPosition.at_height(0), opening=True,
                                                                 motor_seconds=expected_duration + 1).height * 100)
        assert cover._motion.model.opening_time_s == 40
        assert cover._pending_profile is None


async def test_failed_direction_keeps_preexisting_motion_and_zero_height_can_still_close(hass, plant):
    cover, _ = await configured(hass, plant)
    cover._motion.confirm(0)
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100):
        await cover.async_open_cover()
        cover._anchor_run(100)
        failed = asyncio.get_running_loop().create_future()
        failed.set_exception(OSError('not delivered'))
        plant.gateways[0].send.return_value = failed
        with pytest.raises(HomeAssistantError):
            await cover.async_close_cover()
        assert cover.is_opening and cover._motion.opening
        cover._freeze_position(100.5)
        assert not cover.is_closed and cover.current_cover_position == 0
        plant.gateways[0].send.return_value = None
        async def anchor(_):
            cover._anchor_run(100)
            return 100
        async def wait(seconds):
            assert seconds == 0.5
            cover._motion.stop(100 + seconds)
        with patch.object(cover, '_await_motion_anchor', side_effect=anchor), \
             patch('custom_components.myhome.cover.asyncio.sleep', side_effect=wait):
            await cover.async_set_cover_position(position=0)
            await cover._stop_task
        assert cover.is_closed
        plant.gateways[0].send.reset_mock()
        await cover.async_set_cover_position(position=0)
        plant.gateways[0].send.assert_not_called()


async def test_model_removal_is_deferred_and_failed_first_command_does_not_claim_motion(hass, plant):
    cover, profile_id = await configured(hass, plant)
    failed = asyncio.get_running_loop().create_future()
    failed.set_exception(OSError('no connection'))
    plant.gateways[0].send.return_value = failed
    with pytest.raises(HomeAssistantError):
        await cover.async_open_cover()
    assert not cover.is_opening and cover._motion.anchor is None
    plant.gateways[0].send.return_value = None
    cover._motion.confirm(100)
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100):
        await cover.async_close_cover()
        cover._anchor_run(100)
        data = await write(hass, plant, profile_id=profile_id, profile=nonlinear(geometry=None, reference_travel_cm=None))
        assert data["pending"] and data["model"] == "slat_roll" and data["configured_model"] == "linear_time"
        assert data["effective"]["slat_time_s"]["value"] == 2
        cover._freeze_position(105)
        assert cover._motion.model is None
        assert cover.current_cover_position == round(CoverMotionModel(26, 20, 2, 3, 2).advance(
            MotionPosition.at_height(1), opening=False, motor_seconds=5).height * 100)


async def test_homing_without_echo_includes_motor_start_delay_and_endpoint_request_stops_running_motor(hass, plant):
    from custom_components.myhome.cover import MOTOR_START_DELAY

    cover, _ = await configured(hass, plant)
    written = asyncio.get_running_loop().create_future()
    written.set_result(100)
    plant.gateways[0].send.return_value = written
    with patch('custom_components.myhome.cover.time.monotonic', return_value=100):
        await cover.async_open_cover()
        await asyncio.sleep(0)
        assert cover._motion.anchor == 100 + MOTOR_START_DELAY
    with patch('custom_components.myhome.cover.time.monotonic', return_value=126):
        assert cover.current_cover_position is None
    with patch('custom_components.myhome.cover.time.monotonic', return_value=127):
        assert cover.current_cover_position == 100 and cover.is_opening
        plant.gateways[0].send.return_value = None
        await cover.async_set_cover_position(position=100)
        assert str(plant.gateways[0].send.call_args.args[0]) == '*2*0*11##'
        assert not cover.is_opening and cover.current_cover_position == 100
