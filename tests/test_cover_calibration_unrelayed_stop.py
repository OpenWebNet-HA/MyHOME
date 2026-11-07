"""A Stop the gateway acknowledges without relaying its stop status still ends the step.

On 5 October a MyHOMEServer1 relayed only the translation of our Stop (`*2*1000#0*WHERE##`,
dropped by the gateway handler) after the lift-off press, and the step waited 30 s for the
`not_stopped` deadline. Once the Stop is written, no movement status within ECHO_WINDOW of
the write means the motor has stopped.
"""
import asyncio

import pytest

from custom_components.myhome.cover_motion import ECHO_WINDOW
from tests.test_cover_calibration_geometry import LIFT, endpoint, review, start
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus

plant = plant_fixture
geometry = geometry_fixture

STOP, RAISE, LOWER = "*2*0*11##", "*2*1*11##", "*2*2*11##"


def due(cal):
    return cal.session.deadline.when() - cal.session.hass.loop.time()


def commands(cal):
    return [str(item[0]) for item in cal.queue]


async def lift_pressed(cal):
    """Home, the lift-off ascent and the press; the Stop is queued, not written yet."""
    await endpoint(cal, 9)
    await start(cal)
    cal.clock[0] += LIFT - .5
    await act(cal, "lift")
    assert cal.session.phase == "geometry_wait_stop" and commands(cal)[-1] == STOP


async def written(cal, *, elapsed=.5, acknowledged=0.0):
    """The worker writes the Stop `elapsed` s after the press; its future resolves `acknowledged` s later."""
    cal.clock[0] += elapsed
    _, guard, _, future = cal.queue[-1]
    assert guard()
    at = cal.clock[0]
    cal.clock[0] += acknowledged
    future.set_result(at)
    await asyncio.sleep(0)


async def test_the_live_case_ends_the_lift_off_run_after_the_echo_window(geometry):
    cal = geometry
    await lift_pressed(cal)
    assert due(cal) == pytest.approx(30, abs=.1)  # Until the write: `not_stopped`.
    await written(cal)
    sent = len(cal.queue)
    # No stop status and no movement status: only the translation came, and the handler drops it.
    view = cal.session.view()
    assert (view["phase"], view["reason"]) == ("geometry_wait_stop", None)
    assert cal.session.samples["lift"] == pytest.approx(LIFT)  # Taken from the write, as before.
    assert due(cal) == pytest.approx(ECHO_WINDOW, abs=.1)
    cal.clock[0] += ECHO_WINDOW
    cal.session.deadline._run()
    view = cal.session.view()
    assert (view["phase"], view["step"], view["reason"]) == ("reading", "lift", None)
    assert cal.session.samples["lift"] == pytest.approx(LIFT)
    assert len(cal.queue) == sent  # No second Stop.
    # No stop status was seen: the gateway reservation still waits for one or for its guard.
    assert cal.session.reservation.pending


@pytest.mark.parametrize(("acknowledged", "left"), [(1.0, ECHO_WINDOW - 1.0), (2.0, 0.0)])
async def test_the_window_is_measured_from_the_write_not_from_its_acknowledgement(geometry, acknowledged, left):
    cal = geometry
    await lift_pressed(cal)
    await written(cal, acknowledged=acknowledged)
    assert cal.session.samples["lift"] == pytest.approx(LIFT)
    assert due(cal) == pytest.approx(left, abs=.1)


async def test_the_stop_status_inside_the_window_confirms_at_once(geometry):
    cal = geometry
    await lift_pressed(cal)
    await written(cal)
    grace = cal.session.deadline
    cal.clock[0] += .05
    bus(cal, STOP)
    assert cal.session.phase == "reading" and grace.cancelled()
    assert not cal.session.reservation.pending


async def test_an_opposite_movement_inside_the_window_still_interrupts(geometry):
    cal = geometry
    await lift_pressed(cal)
    await written(cal)
    grace = cal.session.deadline
    bus(cal, LOWER)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")
    assert grace.cancelled() and cal.session.samples == {}


@pytest.mark.parametrize("end", ["stop_status", "deadline"])
async def test_a_movement_in_the_run_direction_gives_the_not_stopped_deadline_back(geometry, end):
    cal = geometry
    await lift_pressed(cal)
    await written(cal)
    grace = cal.session.deadline
    bus(cal, RAISE)  # The motor is still reported running after our Stop.
    assert grace.cancelled() and cal.session.phase == "geometry_wait_stop"
    assert due(cal) == pytest.approx(30, abs=.1)
    bus(cal, RAISE)  # A repeat does not push it further.
    if end == "stop_status":
        bus(cal, STOP)
        assert cal.session.phase == "reading"
    else:
        cal.session.deadline._run()
        assert (cal.session.phase, cal.session.reason) == ("interrupted", "not_stopped")


async def test_a_stop_never_written_still_ends_in_not_stopped(geometry):
    cal = geometry
    await lift_pressed(cal)
    cal.clock[0] += 30
    cal.session.deadline._run()  # The worker never wrote it: no window was opened.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "not_stopped")
    assert cal.session.samples == {}


@pytest.mark.parametrize("how", ["stop", "close"])
async def test_stop_or_close_inside_the_window_cancels_it(geometry, how):
    cal = geometry
    await lift_pressed(cal)
    await written(cal)
    grace = cal.session.deadline
    if how == "stop":
        await act(cal, "stop")
        assert (cal.session.phase, cal.session.reason) == ("interrupted", "stopped")
    else:
        cal.session.close()
        assert cal.session.phase == "cancelled"
    assert grace.cancelled()


async def test_the_check_run_ends_after_the_window_with_the_write_time(geometry):
    cal = geometry
    await review(cal)
    await act(cal, "check")
    await endpoint(cal, 9)
    await start(cal)
    planned = cal.session.check.planned_seconds
    cal.clock[0] += planned - .2
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()  # The session stops the check run.
    await written(cal, elapsed=.7)
    assert (cal.session.phase, cal.session.step) == ("geometry_wait_stop", "check")
    assert due(cal) == pytest.approx(ECHO_WINDOW, abs=.1)
    cal.session.deadline._run()
    view = cal.session.view()
    assert (view["phase"], view["step"], view["check_interrupted"]) == ("reading", "check", None)
    assert view["check"]["check_seconds"] == pytest.approx(planned + .5)


async def test_a_confirmed_end_stop_goes_on_after_the_window(geometry):
    cal = geometry
    await start(cal)
    cal.clock[0] += 9
    await act(cal, "endpoint")  # Before any stop status: the Stop is awaited as before.
    assert (cal.session.step, cal.session.phase) == ("home", "geometry_wait_stop")
    await written(cal, elapsed=.4)
    assert due(cal) == pytest.approx(ECHO_WINDOW, abs=.1)
    cal.session.deadline._run()
    assert (cal.session.step, cal.session.phase) == ("lift", "briefing")


async def test_a_confirmed_end_stop_still_takes_a_stop_status_before_the_write(geometry):
    cal = geometry
    await start(cal)
    cal.clock[0] += 9
    await act(cal, "endpoint")
    bus(cal, STOP)  # The status overtakes the acknowledgement of the write.
    assert (cal.session.step, cal.session.phase) == ("lift", "briefing")
    deadline = cal.session.deadline
    await written(cal, elapsed=.4)
    assert cal.session.deadline is deadline and cal.session.phase == "briefing"


async def test_a_confirmed_end_stop_whose_stop_is_not_delivered_still_interrupts(geometry):
    cal = geometry
    await start(cal)
    cal.clock[0] += 9
    await act(cal, "endpoint")
    cal.queue[-1][3].cancel()
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "not_delivered")
