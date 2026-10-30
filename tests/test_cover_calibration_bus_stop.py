"""A stop status from the bus in a run the operator confirms dates the run instead of ending it."""
import pytest
import voluptuous as vol

from custom_components.myhome.cover_profile_provenance import EVIDENCE, PROVENANCE
from custom_components.myhome.cover_profiles import ProfileError, read_profile
from tests.test_cover_calibration_geometry import GAP, LIFT, endpoint, lift, review, start
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture
geometry = geometry_fixture

STOP, RAISE, LOWER = "*2*0*11##", "*2*1*11##", "*2*2*11##"


async def opening_run(cal):
    """Guided: confirm closed, the worker sends Raise, the actuator reports it 2 s later."""
    await act(cal, "open")
    assert cal.queue[-1][1]()
    cal.clock[0] += 2
    bus(cal, RAISE)
    assert cal.session.phase == "opening"


async def closing_run(cal):
    await act(cal, "close")
    assert cal.queue[-1][1]()
    cal.clock[0] += 3
    bus(cal, LOWER)
    assert cal.session.phase == "closing"


def commands(cal):
    return [str(item[0]) for item in cal.queue]


# Guided timing (base session).

async def test_stop_before_the_confirmation_dates_the_run_and_the_press_records_it(hass, calibration):
    cal = calibration
    await opening_run(cal)
    sequence = cal.session.sequence
    cal.clock[0] += 21.004
    bus(cal, STOP)  # The actuator's own end-of-travel stop.
    view = cal.session.view()
    assert (view["phase"], view["reason"], view["motor_stopped"], view["stopped_elapsed"]) == ("opening", None, True, 21.0)
    assert cal.session.sequence == sequence + 1  # Published to every reader.
    assert cal.session.values == {} and cal.session.deadline is not None and not cal.session.deadline.cancelled()
    cal.clock[0] += 1.7  # The operator's reaction is not added.
    bus(cal, STOP)  # A repeated stop status changes nothing.
    assert cal.session.stopped_elapsed == 21.0 and cal.session.sequence == sequence + 1
    await act(cal, "endpoint")
    assert cal.session.values == {"opening_time": 21.0}
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"
    view = cal.session.view()
    assert (view["phase"], view["motor_stopped"], view["stopped_elapsed"]) == ("confirm_open", False, None)
    assert cal.cover.current_cover_position == 100
    assert commands(cal)[-1] == STOP  # Stop is still written, as after every confirmation.


async def test_confirmation_before_the_stop_keeps_the_press_time(hass, calibration):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 20.5
    await act(cal, "endpoint")
    cal.clock[0] += .8
    bus(cal, STOP)  # Arrives after the confirmation: ignored.
    assert cal.session.phase == "confirm_open" and cal.session.values == {"opening_time": 20.5}
    assert cal.session.provenance["opening"]["ended_by"] == "press"
    assert cal.session.view()["motor_stopped"] is False


async def test_both_orders_in_one_measurement_save_their_evidence(hass, calibration):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 20.5
    await act(cal, "endpoint")
    bus(cal, STOP)
    await closing_run(cal)
    cal.clock[0] += 40
    bus(cal, STOP)
    cal.clock[0] += 2
    await act(cal, "endpoint")
    assert cal.session.phase == "review"
    assert cal.session.values == {"opening_time": 20.5, "closing_time": 40.0}
    await act(cal, "save", name="Measured")
    profile = (await read_profile(hass, cal.session.entry_id, cal.cover.entity_id))["profiles"][0]
    assert {direction: meta["ended_by"] for direction, meta in profile["provenance"].items()} == {
        "opening": "press", "closing": "bus_stop"}
    assert cal.cover._travel_time_down == 40


@pytest.mark.parametrize("frame", [LOWER, RAISE], ids=["opposite", "same_direction"])
async def test_movement_after_the_stop_still_interrupts(calibration, frame):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 10
    bus(cal, STOP)
    bus(cal, frame)  # A wall switch or a scenario moves the cover again.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")
    assert cal.session.values == {} and cal.session.view()["motor_stopped"] is False
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "endpoint")


async def test_opposite_movement_without_a_stop_still_interrupts(calibration):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 20.5
    await act(cal, "endpoint")
    await closing_run(cal)
    bus(cal, RAISE)
    assert cal.session.reason == "unexpected_movement"


async def test_no_confirmation_after_the_stop_ends_in_the_travel_timeout(calibration):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 18
    bus(cal, STOP)
    cal.session.deadline._run()
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "travel_timeout")
    assert cal.session.values == {} and cal.session.stopped_elapsed is None


async def test_operator_stop_after_the_motor_stop_still_discards_the_run(calibration):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 18
    bus(cal, STOP)
    await act(cal, "stop")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "stopped")
    assert cal.session.view()["motor_stopped"] is False


@pytest.mark.parametrize("seconds", [59, 60.2, 65])
async def test_a_timer_actuator_stop_is_the_time_saved_when_not_confirmed_earlier(calibration, seconds):
    # The relay of a timer actuator (F411, 60 s by default) reports its release as the stop.
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += seconds
    bus(cal, STOP)
    assert cal.session.phase == "opening"
    cal.clock[0] += 3
    await act(cal, "endpoint")
    assert cal.session.values == {"opening_time": seconds}
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"


async def test_a_stop_that_is_not_a_stop_status_still_interrupts(calibration):
    cal = calibration
    await opening_run(cal)
    cal.clock[0] += 10
    bus(cal, "*#2*11*10*10*50*001*0##")  # Advanced "stopped at 50 %" (state 10), not a standard stop status.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_stop")


def test_evidence_without_ended_by_stays_valid_and_unknown_values_are_refused():
    old = {"source": "guided", "recorded_at": "2026-09-15T11:00:00+00:00", "origin_unique_id": "abc"}
    assert EVIDENCE(old) == old
    assert PROVENANCE({"opening": old, "closing": {**old, "ended_by": "bus_stop"}})
    with pytest.raises(vol.Invalid):
        EVIDENCE({**old, "ended_by": "guess"})


# Guided geometry.

async def test_geometry_home_stop_lets_the_confirmation_continue_without_waiting(hass, geometry):
    cal = geometry
    await start(cal)
    assert (cal.session.step, cal.session.phase) == ("home", "closing")
    cal.clock[0] += 9
    bus(cal, STOP)
    view = cal.session.view()
    assert (view["phase"], view["motor_stopped"], view["stopped_elapsed"]) == ("closing", True, 9.0)
    await act(cal, "endpoint")
    # The bus already reported the stop: no second stop status is awaited.
    assert (cal.session.step, cal.session.phase) == ("lift", "briefing")
    assert str(cal.queue[-1][0]) == STOP and cal.session.values == {}
    assert cal.cover.current_cover_position == 0
    cal.queue[-1][3].set_result(cal.clock[0])  # Its delivery, or a stop status after it, changes nothing.
    bus(cal, STOP)
    assert (cal.session.step, cal.session.phase, cal.session.view()["motor_stopped"]) == ("lift", "briefing", False)


async def test_geometry_full_runs_take_the_bus_stop_or_the_press(hass, geometry):
    cal = geometry
    await lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    await endpoint(cal, 8)  # Reset, confirmed before its stop.
    assert cal.session.step == "opening"
    await start(cal)
    cal.clock[0] += 22
    bus(cal, STOP)  # The motor stops by itself at the top.
    cal.clock[0] += 2.5
    await act(cal, "endpoint")
    assert cal.session.phase == "reading" and cal.session.values == {"opening_time": 22}
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"
    await act(cal, "reading", reading_cm=200)
    await endpoint(cal, 20)  # Full descent confirmed before its stop.
    assert cal.session.values["closing_time"] == 20
    assert cal.session.provenance["closing"]["ended_by"] == "press"
    assert (cal.session.step, cal.session.phase) == ("half_open", "briefing")


async def test_geometry_lift_stop_before_the_press_still_interrupts(geometry):
    cal = geometry
    await endpoint(cal, 9)
    await start(cal)
    assert (cal.session.step, cal.session.phase) == ("lift", "opening")
    cal.clock[0] += LIFT - .5
    bus(cal, STOP)  # The edge-lifted press never came: a genuine early stop.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_stop")


async def test_geometry_opposite_movement_after_the_stop_still_interrupts(geometry):
    cal = geometry
    await start(cal)
    cal.clock[0] += 9
    bus(cal, STOP)
    assert cal.session.motor_stopped
    bus(cal, RAISE)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_geometry_timer_actuator_stop_is_saved_as_the_opening_time(geometry):
    cal = geometry
    await lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    await endpoint(cal, 8)
    await start(cal)
    cal.clock[0] += 60
    bus(cal, STOP)  # The relay releases after 60 s; nobody pressed earlier.
    cal.clock[0] += 4
    await act(cal, "endpoint")
    assert cal.session.values == {"opening_time": 60}
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"


async def test_geometry_check_return_to_the_end_stop_takes_the_bus_stop(hass, geometry):
    cal = geometry
    await review(cal)
    await act(cal, "check")
    await start(cal)
    assert (cal.session.step, cal.session.phase) == ("home", "closing")
    cal.clock[0] += 20
    bus(cal, STOP)
    assert cal.session.phase == "closing" and cal.session.check_interrupted is None
    await act(cal, "endpoint")
    assert (cal.session.step, cal.session.phase) == ("check", "briefing")
    # A check run is stopped by the session: a stop before that is still early.
    await start(cal)
    bus(cal, STOP)
    view = cal.session.view()
    assert (view["phase"], view["check_interrupted"], view["motor_stopped"]) == ("review", "unexpected_stop", False)


# A press that crossed the stop's view in flight (sequence just before the stop).

async def test_a_press_sent_before_the_stop_view_arrived_is_accepted(hass, calibration):
    cal = calibration
    await opening_run(cal)
    before = cal.session.sequence  # What the panel holds when the operator presses.
    cal.clock[0] += 21
    bus(cal, STOP)
    cal.clock[0] += .3
    await cal.session.action({"action": "endpoint", "sequence": before})
    assert cal.session.phase == "confirm_open" and cal.session.values == {"opening_time": 21.0}
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"


async def test_a_press_with_the_old_sequence_before_the_stop_keeps_the_rule(calibration):
    cal = calibration
    await opening_run(cal)
    stale = cal.session.sequence - 1
    cal.clock[0] += 20.5
    with pytest.raises(ProfileError, match="calibration_step"):  # No stop yet: nothing to cross.
        await cal.session.action({"action": "endpoint", "sequence": stale})
    await act(cal, "endpoint")  # Pressed first with the current sequence: the press time.
    bus(cal, STOP)
    assert cal.session.values == {"opening_time": 20.5}
    assert cal.session.provenance["opening"]["ended_by"] == "press"


async def test_only_endpoint_and_only_the_sequence_just_before_the_stop_cross_it(calibration):
    cal = calibration
    await opening_run(cal)
    before = cal.session.sequence
    cal.clock[0] += 21
    bus(cal, STOP)
    for msg in ({"action": "endpoint", "sequence": before - 1}, {"action": "save", "sequence": before}):
        with pytest.raises(ProfileError, match="calibration_step"):
            await cal.session.action(msg)
    cal.session.emit()  # Any later transition (an ownership change, say) closes the allowance.
    with pytest.raises(ProfileError, match="calibration_step"):
        await cal.session.action({"action": "endpoint", "sequence": before})
    assert cal.session.phase == "opening" and cal.session.motor_stopped
    await act(cal, "endpoint")
    assert cal.session.values == {"opening_time": 21.0}


async def test_geometry_press_sent_before_the_stop_view_arrived_is_accepted(geometry):
    cal = geometry
    await start(cal)
    before = cal.session.sequence
    cal.clock[0] += 9
    bus(cal, STOP)
    await cal.session.action({"action": "endpoint", "sequence": before})
    assert (cal.session.step, cal.session.phase) == ("lift", "briefing")


# The gateway relaying a stop right after our own direction command.

async def test_an_echo_stop_right_after_the_start_does_not_date_the_run(hass, calibration):
    cal = calibration
    await opening_run(cal)
    sequence = cal.session.sequence
    cal.clock[0] += .05
    bus(cal, STOP)  # Relayed around our Raise: an echo, not the end of the travel.
    assert (cal.session.phase, cal.session.motor_stopped, cal.session.sequence) == ("opening", False, sequence)
    cal.clock[0] += 19.95
    bus(cal, STOP)  # The real end stop dates the run.
    assert cal.session.stopped_elapsed == 20.0
    cal.clock[0] += 1.5
    await act(cal, "endpoint")
    assert cal.session.values == {"opening_time": 20.0}
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"


async def test_a_stop_just_after_the_echo_window_dates_the_run(calibration):
    from custom_components.myhome.cover_motion import ECHO_WINDOW

    cal = calibration
    await opening_run(cal)
    cal.clock[0] += ECHO_WINDOW
    bus(cal, STOP)
    assert cal.session.motor_stopped and cal.session.stopped_elapsed == ECHO_WINDOW


async def test_geometry_echo_stop_right_after_the_start_does_not_date_the_run(geometry):
    cal = geometry
    await lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    await endpoint(cal, 8)
    await start(cal)
    cal.clock[0] += .05
    bus(cal, STOP)
    assert cal.session.phase == "opening" and not cal.session.motor_stopped
    cal.clock[0] += 21.95
    bus(cal, STOP)
    cal.clock[0] += 2
    await act(cal, "endpoint")
    assert cal.session.values == {"opening_time": 22}

