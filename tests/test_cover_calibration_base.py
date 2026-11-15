"""Basic calibration: the guided session over both runs, then, for a cover with slats, the lift run.

The lift-off is the Stop of the lift run and review follows with the cover where it stopped. Only a repeat closes
it back, or a lift-off refused as too late; then the lift run starts only from the operator's `open`.
"""
import asyncio
import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome.cover_calibration import begin
from custom_components.myhome.cover_motion import CoverMotionModel, MotionPosition
from custom_components.myhome.cover_profiles import ProfileError, get_store, read_profile
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture
RISE, LOWER, STOP = "*2*1*11##", "*2*2*11##", "*2*0*11##"


@pytest.fixture
async def base(hass, plant, request):
    """A guided session started with `slats` (True unless the test says otherwise)."""
    options = getattr(request, "param", {"slats": True})
    queue = []

    def enqueue(message, guard, lock):
        future = hass.loop.create_future()
        queue.append((message, guard, lock, future))
        return future

    for gateway in plant.gateways:
        gateway.async_queue_calibration = enqueue
    connection = MagicMock(subscriptions={})
    request = {"id": 77, "entry_id": plant.entries[0].entry_id,
               "entity_id": plant.records[0].entity_id, "revision": 0, **options}
    clock = [100.0]
    with patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]):
        session = await begin(hass, connection, request)
        yield SimpleNamespace(session=session, connection=connection, request=request,
                              queue=queue, cover=plant.covers[0], clock=clock, plant=plant)
        session.close()
        session.reservation.completed()


async def act(cal, action, **extra):
    return await cal.session.action({"action": action, "sequence": cal.session.sequence, **extra})


def bus(cal, raw):
    cal.cover.handle_event(OWNMessage.parse(raw))


def written(cal):
    """The last queued command passes its guard and is written now; returns its frame."""
    message, guard, _, future = cal.queue[-1]
    assert guard()
    future.set_result(cal.clock[0])
    return str(message)


def fire(handle):
    """A scheduled callback, called at its due time."""
    callback, args = handle._callback, handle._args
    handle.cancel()
    callback(*args)


async def opening(cal):
    """The opening run is under way: the bus reported the motor start 1 s after the command."""
    await act(cal, "open")
    assert cal.queue[-1][1]()
    cal.clock[0] += 1
    bus(cal, RISE)
    assert cal.session.phase == "opening"


async def runs(cal, *, opening_s=21.8, closing_s=21.21):
    """Both full runs, each confirmed by the press at the arrival."""
    await opening(cal)
    cal.clock[0] += opening_s
    await act(cal, "endpoint")
    bus(cal, STOP)
    await act(cal, "close")
    assert cal.queue[-1][1]()
    cal.clock[0] += 1
    bus(cal, LOWER)
    cal.clock[0] += closing_s
    await act(cal, "endpoint")
    bus(cal, STOP)
    assert cal.session.phase == ("confirm_lift" if cal.session.slats else "review")


async def lift_run(cal, lift=4.24):
    """From the rest: the start, the bus's start 1 s later, the lift-off `lift` s after it."""
    await act(cal, "open")
    assert cal.session.phase == "starting_lift"
    assert written(cal) == RISE
    cal.clock[0] += 1
    bus(cal, RISE)
    assert cal.session.phase == "lifting"
    cal.clock[0] += lift
    return await act(cal, "lift")


async def stopped_at_lift_off(cal):
    """The lift-off's Stop written, and its stop status from the bus."""
    assert written(cal) == STOP
    await asyncio.sleep(0)
    assert cal.session.lift_stop == "written"
    cal.clock[0] += .05
    bus(cal, STOP)
    assert cal.session.lift_stop is None


async def reclose(cal, *, by_bus=True):
    """The Stop before the close back written, the pause, the close back; ended by its stop status or its deadline."""
    assert written(cal) == STOP
    await asyncio.sleep(0)
    assert cal.session.back_stage == "stopping"
    fire(cal.session.settle)
    assert (cal.session.phase, cal.session.back_stage) == ("reclosing", "starting")
    assert written(cal) == LOWER
    cal.clock[0] += .6
    bus(cal, LOWER)
    assert cal.session.back_stage == "running"
    cal.clock[0] += 3
    if by_bus:
        bus(cal, STOP)
    else:
        fire(cal.session.deadline)


async def relift(cal):
    """After a repeat's close back: its Stop written, and nothing moves until the operator's `open`."""
    assert (cal.session.phase, cal.session.back_stage, cal.session.settle) == ("confirm_lift", None, None)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    commands = len(cal.queue)
    assert cal.session.phase == "confirm_lift" and len(cal.queue) == commands
    await act(cal, "open")
    assert cal.session.phase == "starting_lift"
    assert len(cal.queue) == commands + 1 and written(cal) == RISE
    cal.clock[0] += 1
    bus(cal, RISE)
    assert cal.session.phase == "lifting"


async def basic(cal, lift=4.24, **times):
    await runs(cal, **times)
    await lift_run(cal, lift)
    await stopped_at_lift_off(cal)
    assert cal.session.phase == "review"


async def test_with_slats_the_lift_run_follows_the_closing_run(base):
    cal = base
    view = cal.session.view()
    assert (view["mode"], view["slats"], view["samples"], view["lift_taken"]) == ("guided", True, {}, False)
    await runs(cal)
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")  # Before the start.
    await act(cal, "open")
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")  # Queued: the bus has not reported the motor start yet.
    assert written(cal) == RISE
    cal.clock[0] += 1
    bus(cal, RISE)
    assert (cal.session.phase, cal.cover._attr_current_cover_position) == ("lifting", 0)
    cal.clock[0] += 4.244
    result = await act(cal, "lift")
    # The press is the Stop; the lift-off is timed from the bus's start, as the full runs.
    assert str(cal.queue[-1][0]) == STOP
    # Review at once, the cover where it stopped; nothing closes it again.
    assert (result["phase"], result["samples"], result["lift_taken"], result["reason"]) == (
        "review", {"lift": 4.24}, True, None)
    assert result["reclose_seconds"] is None and result["elapsed"] is None
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")  # Once.
    commands = len(cal.queue)
    bus(cal, RISE)  # The lift run's own statuses before and after its Stop change nothing.
    bus(cal, STOP)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    bus(cal, RISE)  # Still running after the write: only its stop status ends the wait.
    assert cal.session.lift_stop == "written"
    cal.clock[0] += 2
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.lift_stop, cal.session.settle) == ("review", None, None)
    assert len(cal.queue) == commands  # No close back.
    # At 0 % (the edge on its sill) with the slats open where the model knows slats.
    assert (cal.cover._attr_current_cover_position, cal.cover._attr_is_closed, cal.session.lifted) == (0, True, True)
    assert cal.session.values == {"opening_time": 21.8, "closing_time": 21.21}
    bus(cal, LOWER)  # Once stopped, any movement is foreign, as in every review.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_the_lift_off_stop_without_a_stop_status_ends_its_wait_after_the_echo_window(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    assert cal.session.lift_stop == "written"
    fire(cal.session.deadline)  # A MyHOMEServer1 may relay no stop status at all.
    assert (cal.session.phase, cal.session.lift_stop) == ("review", None)
    bus(cal, RISE)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_the_lift_off_stop_never_written_ends_the_session(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    cal.queue[-1][3].cancel()  # The gateway dropped it: the motor still rises.
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.reason, cal.session.samples) == ("interrupted", "not_delivered", {})


async def test_the_lift_off_stop_not_written_in_time_ends_the_session(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    fire(cal.session.deadline)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "not_stopped")


async def test_a_close_after_the_lift_off_is_foreign(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    bus(cal, LOWER)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_the_close_back_lasts_the_rise_with_its_margin_never_more_than_a_full_close(base):
    cal = base
    await basic(cal, closing_s=12.0)
    await act(cal, "repeat")
    # 4.24 s of rise × 1.5 + 2 s, not the full close (12 + max(4, 3) = 16 s).
    assert cal.session.view()["reclose_seconds"] == 8.36
    await reclose(cal, by_bus=False)  # A timer actuator reports its stop only at its timer.
    assert written(cal) == STOP
    assert cal.cover._attr_current_cover_position == 0
    cal.session.risen = 10.0  # A long rise: the full close and its margin is the limit.
    assert cal.session.reclose_seconds() == 16.0


@pytest.mark.parametrize(("lift", "closing_s"), [(15.0, 15.0), (16.0, 15.0)])
async def test_a_lift_off_not_shorter_than_both_runs_is_rejected_and_run_again(base, lift, closing_s):
    cal = base
    await runs(cal, closing_s=closing_s)
    with pytest.raises(ProfileError, match="lift_not_shorter"):
        await lift_run(cal, lift)
    view = cal.session.view()
    assert (view["phase"], view["reason"], view["samples"], view["lift_taken"]) == (
        "reclosing", "lift_not_shorter", {}, False)
    assert view["reclose_seconds"] == 19.0  # The rise was a full one: the full close and its margin.
    assert str(cal.queue[-1][0]) == STOP  # The press is the Stop all the same.
    await reclose(cal)
    view = cal.session.view()
    # Refused: back down, and the operator starts the lift run again.
    assert (view["phase"], view["reason"], view["reclose_seconds"]) == ("confirm_lift", "lift_not_shorter", None)
    assert cal.session.values == {"opening_time": 21.8, "closing_time": closing_s}  # The full runs stay.
    await lift_run(cal, 3.0)
    assert (cal.session.reason, cal.session.phase, cal.session.samples) == (None, "review", {"lift": 3.0})


async def test_repeat_from_review_closes_back_and_waits_for_the_lift_run(base):
    cal = base
    await basic(cal, lift=2.0)
    result = await act(cal, "repeat")
    assert (result["phase"], result["samples"], result["lift_taken"], result["reclose_seconds"]) == (
        "reclosing", {}, False, 5.0)  # 2 s × 1.5 + 2 s.
    assert result["back_stage"] == "stopping"
    assert cal.session.lift_evidence is None and not cal.session.lifted
    await reclose(cal)
    assert written(cal) == STOP  # The close back ends with a Stop.
    await asyncio.sleep(0)
    commands = len(cal.queue)
    bus(cal, LOWER)  # The close back's own statuses may still arrive.
    bus(cal, STOP)
    view = cal.session.view()
    # No movement by itself: the operator's `open` starts the lift run again.
    assert (view["phase"], view["reason"], view["back_stage"], view["reclose_seconds"]) == ("confirm_lift", None, None, None)
    assert len(cal.queue) == commands and cal.session.settle is None
    await act(cal, "open")
    assert (cal.session.phase, cal.session.reason) == ("starting_lift", None)
    assert written(cal) == RISE
    cal.clock[0] += 1
    bus(cal, RISE)
    cal.clock[0] += 2.5
    result = await act(cal, "lift")
    assert (result["phase"], result["samples"]) == ("review", {"lift": 2.5})
    assert cal.session.values == {"opening_time": 21.8, "closing_time": 21.21}


async def test_the_lift_runs_own_statuses_and_a_status_reply_change_nothing_in_the_close_back(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    bus(cal, RISE)  # Before the close back starts: the lift run's own statuses.
    bus(cal, STOP)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == LOWER
    bus(cal, STOP)
    await asked(cal)
    cal.clock[0] += .6
    bus(cal, LOWER)
    cal.clock[0] += .1
    bus(cal, STOP)  # The reply to our status request sent before the movement: no end yet.
    assert (cal.session.phase, cal.session.back_stage) == ("reclosing", "running")
    cal.clock[0] += 2.9
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.back_stage) == ("confirm_lift", None)


async def test_repeat_before_the_lift_offs_stop_is_written(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    lift_off_stop = cal.queue[-1][3]
    await act(cal, "repeat")
    lift_off_stop.set_result(cal.clock[0])  # Written now: it no longer speaks for the session.
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.back_stage, cal.session.lift_stop) == ("reclosing", "stopping", None)
    await reclose(cal)
    await relift(cal)


async def test_after_a_stop_status_the_lift_off_is_not_marked_and_the_run_is_repeated(base):
    cal = base
    await runs(cal)
    await act(cal, "open")
    written(cal)
    bus(cal, RISE)
    cal.clock[0] += 3
    bus(cal, STOP)  # The actuator stopped (a timer, a wall switch's stop) before the press.
    assert (cal.session.phase, cal.session.view()["motor_stopped"]) == ("lifting", True)
    commands = len(cal.queue)
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")
    assert (cal.session.phase, len(cal.queue)) == ("lifting", commands)  # Nothing changed.
    await act(cal, "repeat")  # Stop, then back to the rest, then the lift run again.
    view = cal.session.view()
    assert (view["phase"], view["motor_stopped"], view["reclose_seconds"]) == ("reclosing", False, 6.5)
    await reclose(cal)
    await relift(cal)
    assert cal.session.samples == {}


async def test_repeat_during_the_lift_run_closes_back_what_it_rose(base):
    cal = base
    await runs(cal)
    await act(cal, "open")
    written(cal)
    bus(cal, RISE)
    cal.clock[0] += 1.0
    await act(cal, "repeat")
    assert cal.session.view()["reclose_seconds"] == 3.5  # 1 s × 1.5 + 2 s.
    await reclose(cal, by_bus=False)
    await relift(cal)


async def test_a_rise_before_the_lift_run_starts_again_is_foreign(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    await reclose(cal)
    bus(cal, RISE)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_after_a_repeated_close_back_nothing_waits_on_its_stop(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    await reclose(cal)
    cal.queue[-1][3].cancel()  # The cover is at its rest: no movement depends on that Stop.
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.reason, cal.session.settle) == ("confirm_lift", None, None)


async def test_a_full_queue_when_the_lift_run_starts_again_ends_the_session(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    await reclose(cal, by_bus=False)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    with patch.object(cal.plant.gateways[0], "async_queue_calibration", side_effect=asyncio.QueueFull), \
            pytest.raises(ProfileError, match="command_queue_full"):
        await act(cal, "open")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "command_queue_full")


async def test_no_stop_queued_at_the_end_of_a_repeated_close_back_ends_the_session(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    written(cal)
    bus(cal, LOWER)
    cal.clock[0] += 3
    with patch.object(cal.plant.gateways[0], "async_queue_calibration", side_effect=asyncio.QueueFull):
        bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "stop_queue_full")


async def test_no_movement_by_itself_after_a_close_back(base):
    """Neither after the closing run nor after a repeat's close back does any frame of movement leave."""
    cal = base
    await runs(cal)
    assert cal.session.phase == "confirm_lift" and cal.session.settle is None
    moves = [str(item[0]) for item in cal.queue if str(item[0]) != STOP]
    await asyncio.sleep(0)
    await lift_run(cal)
    await stopped_at_lift_off(cal)
    await act(cal, "repeat")
    await reclose(cal)
    written(cal)
    await asyncio.sleep(0)
    for handle in (cal.session.settle, cal.session.deadline):
        assert handle is None or handle.cancelled()
    assert [str(item[0]) for item in cal.queue if str(item[0]) != STOP] == [*moves, RISE, LOWER]
    assert cal.session.phase == "confirm_lift"


async def test_repeat_only_for_the_lift_run(base):
    cal = base
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat")
    await opening(cal)
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat")  # Not in a full run.
    assert cal.session.phase == "opening"


@pytest.mark.parametrize("base", [{"slats": False}, {}], indirect=True)
async def test_without_slats_there_is_no_lift_run(base):
    cal = base
    assert cal.session.view()["slats"] is False
    await opening(cal)
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")
    assert cal.session.phase == "opening"
    cal.clock[0] += 20
    await act(cal, "endpoint")
    await act(cal, "close")
    cal.queue[-1][1]()
    bus(cal, LOWER)
    cal.clock[0] += 20
    await act(cal, "endpoint")
    assert cal.session.phase == "review"  # Straight to review.
    for action in ("lift", "repeat"):
        with pytest.raises(ProfileError, match="calibration_step"):
            await act(cal, action)


async def test_the_full_runs_take_no_lift_off(base):
    cal = base
    await opening(cal)
    cal.clock[0] += 3
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "lift")  # The opening run is a full run now.
    assert (cal.session.phase, cal.session.samples) == ("opening", {})


@pytest.mark.parametrize("when", ["lifting", "reclosing"])
async def test_stop_during_the_lift_run_or_the_close_back_ends_the_session(base, when):
    cal = base
    await runs(cal)
    if when == "lifting":
        await act(cal, "open")
        written(cal)
        bus(cal, RISE)
    else:
        await lift_run(cal)
        await act(cal, "repeat")
    await act(cal, "stop")
    view = cal.session.view()
    assert (view["phase"], view["reason"], view["samples"], view["values"]) == ("interrupted", "stopped", {}, {})
    assert (cal.session.back_stage, cal.session.lift_evidence, cal.session.settle) == (None, None, None)
    assert (cal.session.risen, cal.session.lift_stop) == (None, None)


async def test_stop_in_review_after_the_lift_off_keeps_the_measurements(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    await act(cal, "stop")
    assert (cal.session.phase, cal.session.samples) == ("review", {"lift": 4.24})


async def test_a_foreign_movement_during_the_close_back_ends_the_session(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    bus(cal, LOWER)  # A close before ours: a wall switch.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_a_close_before_the_command_is_written_is_foreign(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    written(cal)
    await asyncio.sleep(0)
    fire(cal.session.settle)
    bus(cal, LOWER)  # Queued, not yet written.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_a_rise_while_closing_back_ends_the_session(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    written(cal)
    await asyncio.sleep(0)
    fire(cal.session.settle)
    written(cal)
    bus(cal, LOWER)
    bus(cal, RISE)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_a_stop_never_written_ends_the_session(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    cal.queue[-1][3].cancel()  # The gateway dropped it.
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "not_delivered")


async def test_a_stop_written_after_the_session_ended_changes_nothing(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    stop = cal.queue[-1][3]
    await act(cal, "stop")
    stop.set_result(cal.clock[0])
    await asyncio.sleep(0)
    assert (cal.session.phase, cal.session.settle) == ("interrupted", None)
    cal.session.go_back()  # A pause that ran out anyway moves nothing.
    assert str(cal.queue[-1][0]) == STOP


async def test_a_full_queue_ends_the_lift_run(base):
    cal = base
    await runs(cal)
    await act(cal, "open")
    written(cal)
    bus(cal, RISE)
    cal.clock[0] += 2
    with patch.object(cal.plant.gateways[0], "async_queue_calibration", side_effect=asyncio.QueueFull):
        await act(cal, "lift")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "stop_queue_full")


async def test_a_full_queue_for_the_stop_before_the_close_back_ends_the_session(base):
    cal = base
    await basic(cal)
    with patch.object(cal.plant.gateways[0], "async_queue_calibration", side_effect=asyncio.QueueFull):
        await act(cal, "repeat")
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "stop_queue_full")


async def test_a_full_queue_for_the_close_back_ends_the_session(base):
    cal = base
    await basic(cal)
    await act(cal, "repeat")
    written(cal)
    await asyncio.sleep(0)
    with patch.object(cal.plant.gateways[0], "async_queue_calibration", side_effect=asyncio.QueueFull):
        fire(cal.session.settle)  # Never raises out of the timer.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "command_queue_full")


async def reopen(cal, *, by_bus=True):
    """`repeat` of the closing run: its Stop written, the pause, the open back; ended by its stop status or deadline."""
    assert (cal.session.phase, cal.session.back_stage) == ("reopening", "stopping")
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert (cal.session.phase, cal.session.back_stage) == ("reopening", "starting")
    assert written(cal) == RISE
    cal.clock[0] += .6
    bus(cal, RISE)
    assert cal.session.back_stage == "running"
    cal.clock[0] += 1
    if cal.session.direction:
        # A one-direction session (older clients): a stop within the echo window is the gateway's echo of our own
        # command, no end yet. In the basic calibration it is the end stop already reached.
        bus(cal, STOP)
        bus(cal, RISE)
    assert cal.session.phase == "reopening"
    cal.clock[0] += 18
    if by_bus:
        bus(cal, STOP)
    else:
        fire(cal.session.deadline)
    assert str(cal.queue[-1][0]) == STOP  # Written at the end stop either way.
    assert (cal.session.phase, cal.session.back_stage, cal.cover._attr_current_cover_position) == (
        "confirm_open", None, 100)


async def closing_again(cal, closing_s=20.5):
    """From `confirm_open`: the closing run again, confirmed by the press at the arrival."""
    await act(cal, "close")
    assert written(cal) == LOWER
    cal.clock[0] += 1
    bus(cal, LOWER)
    cal.clock[0] += closing_s
    await act(cal, "endpoint")


@pytest.mark.parametrize("by_bus", [True, False])
async def test_repeat_of_the_closing_run_opens_back_and_keeps_the_opening_time(base, by_bus):
    cal = base
    await runs(cal)
    opening = (cal.session.values["opening_time"], dict(cal.session.provenance["opening"]))
    result = await act(cal, "repeat", direction="closing")
    # The opening run is not run again; the cover opens back by itself, nothing is measured.
    assert (result["phase"], result["values"], result["reason"]) == ("reopening", {"opening_time": 21.8}, None)
    assert (result["reopen_seconds"], result["reclose_seconds"]) == (27.25, None)  # 21.8 + max(4, 25 %).
    assert "closing" not in cal.session.provenance
    bus(cal, LOWER)  # Before the open back starts: the closing run's own statuses.
    bus(cal, STOP)
    await reopen(cal, by_bus=by_bus)
    assert cal.session.view()["reopen_seconds"] is None
    await closing_again(cal)
    assert (cal.session.phase, cal.session.values) == ("confirm_lift", {"opening_time": 21.8, "closing_time": 20.5})
    assert (cal.session.values["opening_time"], cal.session.provenance["opening"]) == opening
    await lift_run(cal)
    assert cal.session.phase == "review"


@pytest.mark.parametrize("base", [{"slats": False}, {"direction": "closing"}], indirect=True)
async def test_repeat_of_the_closing_run_from_review(base):
    cal = base
    if cal.session.direction:
        await act(cal, "close")
        written(cal)
        bus(cal, LOWER)
        cal.clock[0] += 22
        await act(cal, "endpoint")
    else:
        await runs(cal)
    assert cal.session.phase == "review"
    kept = cal.session.values["opening_time"]
    await act(cal, "repeat", direction="closing")
    await reopen(cal)
    await closing_again(cal, 19.0)
    assert (cal.session.phase, cal.session.values) == ("review", {"opening_time": kept, "closing_time": 19.0})


async def test_repeat_of_the_closing_run_after_the_lift_off_keeps_it_while_it_is_shorter(base):
    cal = base
    await basic(cal, lift=4.24)
    await act(cal, "repeat", direction="closing")
    assert cal.session.samples == {"lift": 4.24}
    await reopen(cal)
    await closing_again(cal)
    assert (cal.session.phase, cal.session.samples) == ("review", {"lift": 4.24})  # Straight back to review.
    await act(cal, "repeat", direction="closing")
    await reopen(cal)
    await closing_again(cal, 4.0)  # Now no longer longer than the lift-off: it goes, the lift run comes back.
    assert (cal.session.phase, cal.session.samples, cal.session.lift_evidence) == ("confirm_lift", {}, None)


async def test_repeat_of_the_closing_run_only_once_it_is_confirmed(base):
    cal = base
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat", direction="closing")
    await opening(cal)
    cal.clock[0] += 21.8
    await act(cal, "endpoint")
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat", direction="closing")  # In confirm_open: no closing run yet (only the opening one).
    await act(cal, "close")
    written(cal)
    bus(cal, LOWER)
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat", direction="closing")  # During the run: Stop, then a new session.
    cal.clock[0] += 21
    await act(cal, "endpoint")
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat", direction="opening")  # Past the descent: the opening run is repeated with a new session.
    await act(cal, "repeat", direction="closing")
    assert cal.session.phase == "reopening"


@pytest.mark.parametrize("base", [{"direction": "opening"}], indirect=True)
async def test_a_session_measuring_the_opening_run_only_repeats_no_closing_run(base):
    cal = base
    await opening(cal)
    cal.clock[0] += 21
    await act(cal, "endpoint")
    assert cal.session.phase == "review"
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat", direction="closing")


async def test_a_close_while_opening_back_is_foreign(base):
    cal = base
    await runs(cal)
    await act(cal, "repeat", direction="closing")
    written(cal)
    await asyncio.sleep(0)
    fire(cal.session.settle)
    written(cal)
    bus(cal, RISE)
    bus(cal, LOWER)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_a_rise_before_the_open_back_is_foreign(base):
    cal = base
    await runs(cal)
    await act(cal, "repeat", direction="closing")
    bus(cal, RISE)  # A wall switch: ours is not even queued.
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")


async def test_stop_while_opening_back_ends_the_session(base):
    cal = base
    await runs(cal)
    await act(cal, "repeat", direction="closing")
    await act(cal, "stop")
    assert (cal.session.phase, cal.session.reason, cal.session.values) == ("interrupted", "stopped", {})
    assert (cal.session.back_stage, cal.session.settle) == (None, None)


async def test_slats_only_for_the_guided_session_over_both_runs(hass, plant):
    plant.gateways[0].async_queue_calibration = MagicMock()
    request = {"id": 1, "entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id, "revision": 0}
    for extra in ({"slats": True, "direction": "opening"}, {"slats": True, "mode": "automatic"}):
        with pytest.raises(ProfileError, match="invalid_profile"):
            await begin(hass, MagicMock(subscriptions={}), {**request, **extra})
        assert get_store(hass, request["entry_id"]).calibration is None
    session = await begin(hass, MagicMock(subscriptions={}), {**request, "slats": False, "mode": "guided"})
    assert (session.mode, session.slats) == ("guided", False)
    session.close()


UNKNOWN = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
DATES = "custom_components.myhome.cover_profile_provenance.dt_util.utcnow"


def seed(cal, **record):
    """What the cover's record already holds (as loaded from storage: no revision change)."""
    cal.session.store.data["covers"][cal.cover.unique_id] = {"overrides": {}, **record}


def saved(cal):
    store = cal.session.store
    return store.data["profiles"][store.data["assignments"][cal.cover.unique_id]]


async def test_a_basic_profile_with_slats_has_the_lift_off_as_slat_time_and_neutral_rolls(hass, base):
    cal = base
    seed(cal, travel_cm=110.0, check_cm=3, check_measured_cm=52.0, check_expected_cm=55.0,
         check_at="2026-10-01T09:00:00+00:00")
    with patch(DATES, return_value=datetime(2026, 11, 2, 9, tzinfo=UTC)):
        await runs(cal, opening_s=14.26, closing_s=14.03)
    with patch(DATES, return_value=datetime(2026, 11, 2, 9, 5, tzinfo=UTC)):
        await lift_run(cal, 2.69)
    await stopped_at_lift_off(cal)
    with patch(DATES, return_value=datetime(2026, 11, 2, 10, tzinfo=UTC)):
        result = await act(cal, "save", name="Window")
    assert result["phase"] == "saved"
    profile = saved(cal)
    assert (profile["opening_time"], profile["closing_time"]) == (14.26, 14.03)
    assert profile["geometry"] == {"slat_time_s": 2.69, "opening_roll": 1.0, "closing_roll": 1.0}
    unique = cal.cover.unique_id
    assert profile["geometry_provenance"] == {
        # The lift-off is dated by its own run, not by the save.
        "slat_time_s": {"source": "guided", "recorded_at": "2026-11-02T09:05:00+00:00", "origin_unique_id": unique},
        "opening_roll": UNKNOWN, "closing_roll": UNKNOWN}
    assert profile["provenance"]["opening"]["source"] == "guided"
    assert profile["reference_travel_cm"] == 110.0
    # The travel already saved stays (no tape reading), the check of the old times goes.
    assert cal.session.store.data["covers"][unique] == {"overrides": {}, "travel_cm": 110.0}
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert (view["model"], view["travel_cm"]) == ("slat_roll", 110.0)
    assert view["effective"]["slat_time_s"]["value"] == 2.69
    assert view["effective"]["opening_roll"]["provenance"]["source"] == "unknown"
    assert cal.cover._travel_time_up == 14.26


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_a_basic_profile_without_slats_has_a_slat_time_of_0_and_runs_linear(hass, base):
    """'Without slats' is a slat time of 0 (with rolls 1), not a missing geometry."""
    cal = base
    seed(cal, travel_cm=150.0)
    await runs(cal)
    await act(cal, "save", name="Door")
    profile = saved(cal)
    assert profile["geometry"] == {"slat_time_s": 0.0, "opening_roll": 1.0, "closing_roll": 1.0}
    assert profile["geometry_provenance"]["slat_time_s"]["source"] == "guided"
    assert profile["geometry_provenance"]["opening_roll"]["source"] == "unknown"
    assert "slats" not in profile
    assert cal.cover._motion.model is None  # The linear model: the same code as a times-only profile.
    assert (profile["opening_time"], profile["closing_time"]) == (21.8, 21.21)
    assert cal.session.store.data["covers"][cal.cover.unique_id]["travel_cm"] == 150.0
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert view["model"] == "linear_time"


async def test_a_basic_profile_keeps_no_travel_when_there_was_none(hass, base):
    cal = base
    await basic(cal)
    await act(cal, "save", name="Window")
    assert "reference_travel_cm" not in saved(cal)
    assert cal.cover.unique_id not in cal.session.store.data["covers"]  # No record made up with a travel of None.


async def test_no_basic_profile_without_the_lift_off(hass, base):
    cal = base
    await basic(cal)
    cal.session.samples.clear()  # Review comes only after a lift-off: the save still refuses one without it.
    assert cal.session.view()["lift_taken"] is False
    with pytest.raises(ProfileError, match="lift_missing"):
        await act(cal, "save", name="Window")
    assert (cal.session.phase, cal.session.store.data["revision"]) == ("review", 0)
    # The cover's own times are no longer a destination of a calibration.
    with pytest.raises(ProfileError, match="invalid_profile"):
        await act(cal, "save", save_mode="cover")
    assert (cal.session.phase, cal.session.store.data["revision"]) == ("review", 0)


@pytest.mark.parametrize(("lift", "closing_s"), [(15.0, 15.0), (16.0, 15.0)])
async def test_no_basic_profile_when_the_lift_off_is_not_shorter_than_both_runs(hass, base, lift, closing_s):
    cal = base
    await basic(cal, opening_s=21.8, closing_s=closing_s)
    cal.session.samples["lift"] = lift  # The lift run rejects it already; the save checks it again.
    with pytest.raises(ProfileError, match="lift_not_shorter"):
        await act(cal, "save", name="Window")
    assert (cal.session.phase, cal.session.store.data["profiles"]) == ("review", {})


UNKNOWN_EVIDENCE = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
GUIDED = {"source": "guided", "recorded_at": "2026-10-20T08:00:00+00:00", "origin_unique_id": "elsewhere"}
MANUAL = {"source": "manual", "recorded_at": "2026-10-21T08:00:00+00:00", "origin_unique_id": None}


def refined(cal, *, rolls=(1.4, 1.9), evidence=(GUIDED, GUIDED), reference=110.0, source=False, **record):
    """The cover follows a profile with measured (or entered) rolls, as loaded from storage, with a kept check; the
    profile's times were measured on another cover, or with `source` on this one (only then `shared`)."""
    unique = cal.cover.unique_id
    data = cal.session.store.data
    times = {**GUIDED, "origin_unique_id": unique} if source else GUIDED
    data["profiles"]["fine"] = {
        "name": "Fine-tuned", "opening_time": 15.0, "closing_time": 15.0,
        **({"reference_travel_cm": reference} if reference else {}),
        "geometry": {"slat_time_s": 2.6, "opening_roll": rolls[0], "closing_roll": rolls[1]},
        "geometry_provenance": {"slat_time_s": dict(GUIDED), "opening_roll": dict(evidence[0]),
                                "closing_roll": dict(evidence[1])},
        "provenance": {"opening": dict(times), "closing": dict(times)}}
    data["assignments"][unique] = "fine"
    data["covers"][unique] = {"overrides": {}, "check_cm": 1, "check_measured_cm": 82.0,
                              "check_expected_cm": 82.5, "check_at": "2026-10-20T09:00:00+00:00", **record}


async def test_recalibrating_a_refined_cover_keeps_its_rolls_in_the_new_profile(hass, base):
    cal = base
    refined(cal, travel_cm=110.0)
    await basic(cal, lift=2.69)
    await act(cal, "save", name="Window again")
    profile = saved(cal)
    unique = cal.cover.unique_id
    # New times and lift-off, the rolls of the refinement with their evidence.
    assert profile["geometry"] == {"slat_time_s": 2.69, "opening_roll": 1.4, "closing_roll": 1.9}
    assert profile["geometry_provenance"]["opening_roll"] == profile["geometry_provenance"]["closing_roll"] == GUIDED
    assert profile["geometry_provenance"]["slat_time_s"]["origin_unique_id"] == unique
    assert (profile["opening_time"], profile["reference_travel_cm"]) == (21.8, 110.0)
    # Only the check goes: the precision stays 'refined' until a new check.
    assert cal.session.store.data["covers"][unique] == {"overrides": {}, "travel_cm": 110.0}
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert (view["precision"], view["model"]) == ("refined", "slat_roll")


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_recalibrating_a_refined_cover_without_slats_keeps_its_rolls(hass, base):
    cal = base
    refined(cal, rolls=(2.0, 3.0), evidence=(MANUAL, MANUAL), reference=None)
    await runs(cal)
    await act(cal, "save", name="Door again")
    profile = saved(cal)
    assert profile["geometry"] == {"slat_time_s": 0.0, "opening_roll": 2.0, "closing_roll": 3.0}
    assert profile["geometry_provenance"]["slat_time_s"]["source"] == "guided"
    assert profile["geometry_provenance"]["opening_roll"] == MANUAL
    assert "reference_travel_cm" not in profile
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert view["precision"] == "refined"


async def test_recalibrating_a_follower_keeps_the_rolls_it_uses(hass, base):
    cal = base
    refined(cal, rolls=(1.4, 5.0), travel_cm=150.0)  # Scaled from 110 cm to 150 cm.
    await basic(cal)
    await act(cal, "save", name="Tall window")
    geometry = saved(cal)["geometry"]
    assert geometry["opening_roll"] == pytest.approx((1 + (1.4 ** 2 - 1) * 150 / 110) ** .5)
    assert geometry["closing_roll"] == 5.0  # 5.8 as used: the most a profile stores.
    assert saved(cal)["reference_travel_cm"] == 150.0


async def test_recalibrating_a_refined_cover_into_its_profile_stays_refined(hass, base):
    cal = base
    refined(cal, travel_cm=110.0, source=True)
    await basic(cal)
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    unique = cal.cover.unique_id
    assert cal.session.store.data["assignments"][unique] == "fine"
    record = cal.session.store.data["covers"][unique]
    assert "check_cm" not in record and record["overrides"] == {}
    profile = saved(cal)
    # The rolls the tape measured stay; the slat time is the lift-off just measured (4.24 s, the cover at the
    # reference travel); the times are the new ones.
    assert profile["geometry"] == {"slat_time_s": 4.24, "opening_roll": 1.4, "closing_roll": 1.9}
    assert profile["geometry_provenance"]["slat_time_s"]["origin_unique_id"] == unique
    assert profile["geometry_provenance"]["opening_roll"] == GUIDED
    assert profile["opening_time"] == pytest.approx(21.8)
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert view["precision"] == "refined"


async def test_a_basic_calibration_saved_into_a_basic_profile_keeps_its_lift_off(hass, base):
    """Saved into the profile the cover follows, the lift-off measured is not lost."""
    cal = base
    refined(cal, rolls=(1.0, 1.0), evidence=(dict(UNKNOWN_EVIDENCE), dict(UNKNOWN_EVIDENCE)), travel_cm=165.0,
            source=True)  # The reference travel (110 cm) entered from the catalogue, not this cover's.
    await basic(cal, lift=3.3)
    assert cal.session.view()["save_modes"] == ["new", "shared"]
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    profile = saved(cal)
    # Scaled back to the profile's reference travel (110 cm for a 165 cm cover), as the times are.
    assert profile["geometry"] == {"slat_time_s": pytest.approx(2.2), "opening_roll": 1.0, "closing_roll": 1.0}
    assert profile["geometry_provenance"]["slat_time_s"]["origin_unique_id"] == cal.cover.unique_id
    assert profile["geometry_provenance"]["opening_roll"]["source"] == "unknown"
    assert cal.cover._motion.model.slat_time_s == pytest.approx(3.3)


async def test_only_measured_or_entered_rolls_are_kept(hass, base):
    cal = base
    unknown = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
    refined(cal, evidence=(GUIDED, unknown))
    await basic(cal)
    await act(cal, "save", name="Window")
    profile = saved(cal)
    assert (profile["geometry"]["opening_roll"], profile["geometry"]["closing_roll"]) == (1.4, 1.0)
    assert profile["geometry_provenance"]["closing_roll"] == unknown
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert view["precision"] == "basic"  # One roll of two: not a refinement.


@pytest.mark.parametrize(("slat", "opening", "closing"), [(4.24, 21.80, 21.21), (2.69, 14.26, 14.03)])
def test_neutral_rolls_keep_the_edge_linear_in_time_after_the_slats(slat, opening, closing):
    """The fitted ends of the two measured shutters, with the rolls a basic calibration saves."""
    model = CoverMotionModel(opening_time_s=opening, closing_time_s=closing, slat_time_s=slat)
    closed, open_ = MotionPosition(0.0, 0.0), MotionPosition(1.0, 1.0)
    assert model.advance(closed, opening=True, motor_seconds=slat) == MotionPosition(0.0, 1.0)
    assert model.advance(closed, opening=True, motor_seconds=opening) == open_
    for share in (0.1, 0.25, 0.5, 0.75, 0.9):
        up = model.advance(closed, opening=True, motor_seconds=slat + share * (opening - slat))
        down = model.advance(open_, opening=False, motor_seconds=(1 - share) * (closing - slat))
        assert up.height == pytest.approx(share) and down.height == pytest.approx(share)
        assert model.duration(closed, MotionPosition.at_height(share), opening=True) == pytest.approx(
            slat + share * (opening - slat))
    # Every motor second maps to a height in 0..1 (a percentage in 0..100), the slat phase at 0.
    for tenth in range(int(opening * 10) + 20):
        assert 0 <= model.advance(closed, opening=True, motor_seconds=tenth / 10).height <= 1
    half_slats = model.advance(open_, opening=False, motor_seconds=closing - slat / 2)
    assert (half_slats.height, half_slats.slats) == (0.0, pytest.approx(0.5))
    assert model.scaled(reference_travel_cm=110, travel_cm=197).opening_roll == 1.0


@pytest.mark.parametrize(("reference", "travel"), [(None, None), (None, 150.0), (110.0, None), (110.0, 165.0), (200.0, 90.0)])
def test_no_slats_and_rolls_1_resolve_exactly_as_a_times_only_profile(reference, travel):
    """A slat time of 0 with rolls 1 gives the same times and the same (linear) model, scaled or
    not, so every position and percentage is the one a profile without geometry gives."""
    from custom_components.myhome.cover_geometry import DEFAULT_GEOMETRY, motion_from_settings
    from custom_components.myhome.cover_settings import resolve

    unknown = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
    times = {"name": "Door", "opening_time": 21.8, "closing_time": 21.21,
             "provenance": {"opening": dict(unknown), "closing": dict(unknown)},
             **({"reference_travel_cm": reference} if reference else {})}
    linear = resolve(profile=times, native=None, overrides={}, default=None, travel_cm=travel)
    neutral = resolve(profile={**times, "geometry": dict(DEFAULT_GEOMETRY)}, native=None, overrides={}, default=None,
                      travel_cm=travel)
    assert neutral == linear
    assert motion_from_settings(neutral) is None and motion_from_settings(linear) is None


@pytest.mark.parametrize(("opening", "closing"), [(21.8, 21.21), (14.26, 14.03)])
def test_the_model_itself_with_no_slats_and_rolls_1_is_the_linear_one(opening, closing):
    """And the motion model, were it used: the height is the share of the full time, both ways, at every percent."""
    model = CoverMotionModel(opening_time_s=opening, closing_time_s=closing)
    closed, open_ = MotionPosition(0.0, 0.0), MotionPosition(1.0, 1.0)
    for percent in range(101):
        share = percent / 100
        assert model.advance(closed, opening=True, motor_seconds=share * opening).height == pytest.approx(share, abs=1e-12)
        assert model.advance(open_, opening=False, motor_seconds=share * closing).height == pytest.approx(1 - share, abs=1e-12)
        assert model.duration(closed, MotionPosition.at_height(share), opening=True) == pytest.approx(share * opening, abs=1e-9)


@pytest.mark.parametrize("phase", ["reopening", "reclosing"])
async def test_a_run_back_tells_its_waiting_screen_what_it_needs(base, phase):
    """The runs back are the panel's waiting screen with a bar: the stage, the seconds the run may
    last, and the seconds since the bus reported its start; nothing of it before it starts, nothing after."""
    cal = base
    await runs(cal)
    if phase == "reopening":
        await act(cal, "repeat", direction="closing")
        seconds, direction = 27.25, RISE
    else:
        await lift_run(cal, 2.0)
        await stopped_at_lift_off(cal)
        await act(cal, "repeat")
        seconds, direction = 5.0, LOWER
    key = "reopen_seconds" if phase == "reopening" else "reclose_seconds"
    stages = []
    view = cal.session.view()
    stages.append((view["phase"], view["back_stage"], view[key], view["elapsed"]))
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    view = cal.session.view()
    stages.append((view["phase"], view["back_stage"], view[key], view["elapsed"]))
    assert written(cal) == direction
    cal.clock[0] += .6
    bus(cal, direction)
    cal.clock[0] += 1.5
    view = cal.session.view()
    stages.append((view["phase"], view["back_stage"], view[key], view["elapsed"]))
    assert stages == [(phase, "stopping", seconds, None), (phase, "starting", seconds, None),
                      (phase, "running", seconds, 1.5)]
    cal.clock[0] += 2
    bus(cal, STOP)
    view = cal.session.view()
    assert (view["back_stage"], view[key], view["elapsed"]) == (None, None, None)



# ---------------------------------------------------------------------------------------------------------------------
# The actuator's own stop at the end stop, a cover already there, a repeat after the opening run.
# ---------------------------------------------------------------------------------------------------------------------
async def test_an_actuator_that_stops_by_itself_ends_the_run_timed_to_its_stop(base):
    """The first of the press and the bus's stop: the actuator's own stop at the end stop is the end."""
    cal = base
    await opening(cal)
    cal.clock[0] += 18.3
    bus(cal, STOP)  # The evolved actuator at its end stop.
    view = cal.session.view()
    assert (view["phase"], view["values"], view["motor_stopped"]) == ("confirm_open", {"opening_time": 18.3}, False)
    assert cal.session.provenance["opening"]["ended_by"] == "bus_stop"
    assert view["ended_by"] == {"opening": "bus_stop"}  # The panel says the actuator ended it.
    assert str(cal.queue[-1][0]) == STOP and cal.cover._attr_current_cover_position == 100
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "endpoint")  # Already confirmed: a late press changes nothing.


async def test_a_press_that_crossed_the_actuators_stop_is_answered_not_refused(base):
    """The stop's view and the operator's press crossed (the press carries the sequence just before): the run is already
    over; the press gets the view, changes nothing, and is not shown as refused. Only then and only that sequence."""
    cal = base
    await opening(cal)
    before = cal.session.sequence
    cal.clock[0] += 18.3
    bus(cal, STOP)
    arrived = cal.session.view()
    cal.clock[0] += .2
    view = await cal.session.action({"action": "endpoint", "sequence": before})
    assert (view["phase"], view["values"], view["sequence"]) == ("confirm_open", {"opening_time": 18.3}, arrived["sequence"])
    for msg in ({"action": "endpoint", "sequence": before - 1}, {"action": "close", "sequence": before}):
        with pytest.raises(ProfileError, match="calibration_step"):
            await cal.session.action(msg)
    await act(cal, "close")  # Any later transition closes the allowance.
    with pytest.raises(ProfileError, match="calibration_step"):
        await cal.session.action({"action": "endpoint", "sequence": arrived["sequence"] - 1})


async def test_the_view_says_what_ended_each_run(base):
    """`ended_by` per direction, from the provenance of the times in `values`."""
    cal = base
    assert cal.session.view()["ended_by"] == {}
    await opening(cal)
    cal.clock[0] += 18.3
    await act(cal, "endpoint")  # The press before any stop.
    assert cal.session.view()["ended_by"] == {"opening": "press"}
    await act(cal, "close")
    assert cal.queue[-1][1]()
    cal.clock[0] += 1
    bus(cal, LOWER)
    cal.clock[0] += 17.9
    bus(cal, STOP)  # The actuator's own stop at the bottom.
    assert cal.session.view()["ended_by"] == {"opening": "press", "closing": "bus_stop"}


@pytest.mark.parametrize("seconds", [60.4, 120.8])
async def test_a_stop_at_an_actuators_timer_still_waits_for_the_press(base, seconds):
    """A relay timer (60 s, or 120 s with M=1) is not the end stop: the operator's press says where the cover arrived."""
    cal = base
    await opening(cal)
    cal.clock[0] += seconds
    bus(cal, STOP)
    view = cal.session.view()
    assert (view["phase"], view["motor_stopped"], view["stopped_elapsed"]) == ("opening", True, seconds)


@pytest.mark.parametrize("direction", ["opening", "closing"])
async def test_a_run_that_starts_at_its_end_stop_is_refused(base, direction):
    """The actuator's stop 0.3 s after the start: the cover was already there (HA's state was stale)."""
    cal = base
    if direction == "closing":
        await opening(cal)
        cal.clock[0] += 21.8
        await act(cal, "endpoint")
        bus(cal, STOP)
        await act(cal, "close")
        written(cal)
        cal.clock[0] += .6
        bus(cal, LOWER)
    else:
        await opening(cal)
    cal.clock[0] += .3
    bus(cal, STOP)
    view = cal.session.view()
    start = "confirm_closed" if direction == "opening" else "confirm_open"
    assert (view["phase"], view["reason"], view["motor_stopped"]) == (start, "already_at_end", False)
    assert f"{direction}_time" not in view["values"], "nothing measured"
    assert str(cal.queue[-1][0]) == STOP
    # A repeat: back to where the run starts, by itself, then the run again from there.
    await act(cal, "repeat", direction=direction)
    assert cal.session.phase == ("rehoming" if direction == "opening" else "reopening")
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == (LOWER if direction == "opening" else RISE)
    cal.clock[0] += .6
    bus(cal, LOWER if direction == "opening" else RISE)
    view = cal.session.view()
    assert view["back_stage"] == "running" and view["rehome_seconds" if direction == "opening" else "reopen_seconds"] > 4
    cal.clock[0] += 3
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason) == (start, None)


async def test_repeat_of_the_opening_run_closes_back_and_waits_for_it(base):
    """A repeat after the opening run: the cover closes back by itself, then the opening run waits for `open`."""
    cal = base
    await opening(cal)
    cal.clock[0] += 21.8
    await act(cal, "endpoint")
    bus(cal, STOP)
    result = await act(cal, "repeat", direction="opening")
    assert (result["phase"], result["values"], result["rehome_seconds"]) == ("rehoming", {}, 37.5)  # 30 s in use + 25 %.
    assert written(cal) == STOP
    await asyncio.sleep(0)
    commands = len(cal.queue)
    fire(cal.session.settle)
    assert written(cal) == LOWER and len(cal.queue) == commands + 1
    cal.clock[0] += .6
    bus(cal, LOWER)
    cal.clock[0] += 20
    fire(cal.session.deadline)  # A timer actuator: the close back ends at its time.
    assert written(cal) == STOP
    assert (cal.session.phase, cal.cover._attr_current_cover_position) == ("confirm_closed", 0)
    await opening(cal)
    assert cal.session.phase == "opening"


async def test_a_late_status_of_the_run_just_ended_changes_nothing(base):
    """A reply to a status sweep sent just before the actuator's stop says 'rising' after it: still the run's own."""
    cal = base
    await opening(cal)
    cal.clock[0] += 18.3
    bus(cal, STOP)
    cal.clock[0] += .1
    bus(cal, RISE)
    assert cal.session.phase == "confirm_open"
    cal.clock[0] += 2
    bus(cal, RISE)
    assert (cal.session.phase, cal.session.reason) == ("interrupted", "unexpected_movement")



async def test_the_lift_off_rescaled_into_a_refined_profile_from_a_taller_source(hass, base):
    """From a source 165 cm tall on a 110 cm reference (entered from the catalogue), the lift-off goes in rescaled as
    the times. (Only the source updates its profile.)"""
    cal = base
    refined(cal, travel_cm=165.0, source=True)
    await basic(cal, lift=3.3)
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    geometry = saved(cal)["geometry"]
    assert geometry == {"slat_time_s": pytest.approx(2.2), "opening_roll": 1.4, "closing_roll": 1.9}
    assert cal.cover._motion.model.slat_time_s == pytest.approx(3.3), "the cover uses the lift-off it measured"


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_the_basic_calibration_closes_the_cover_itself_before_the_ascent(base):
    """Asked to (`home`), the session closes the cover first, whatever Home Assistant's state says."""
    cal = base
    view = cal.session.view()
    assert (view["phase"], view["back_stage"], view["rehome_seconds"]) == ("rehoming", "stopping", 37.5)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == LOWER
    cal.clock[0] += .6
    bus(cal, LOWER)
    cal.clock[0] += 15
    bus(cal, STOP)  # At the bottom.
    assert (cal.session.phase, cal.cover._attr_current_cover_position) == ("confirm_closed", 0)
    await opening(cal)
    assert cal.session.phase == "opening"


async def homing(cal):
    """`home`: the session's Stop, the pause, the close to the rest, and the bus's movement 0.6 s after it."""
    assert cal.session.phase == "rehoming"
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == LOWER
    cal.clock[0] += .6
    bus(cal, LOWER)
    assert cal.session.back_stage == "running"


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_the_close_to_the_rest_on_a_cover_already_closed_ends_at_the_actuators_stop(base):
    """Already at the bottom, the end-stop actuator stops 0.3 s after the start, inside the echo window: as in the
    full runs it was there already, and the close to the rest is over at once, Stop written, the ascent waiting (no
    wait for the closing time and its margin)."""
    cal = base
    await homing(cal)
    deadline = cal.session.deadline
    commands = len(cal.queue)
    cal.clock[0] += .3
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason, cal.cover._attr_current_cover_position) == ("confirm_closed", None, 0)
    assert deadline.cancelled() and len(cal.queue) == commands + 1 and str(cal.queue[-1][0]) == STOP
    await opening(cal)
    assert cal.session.phase == "opening"


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_a_reply_to_our_status_request_does_not_end_the_close_to_the_rest(base):
    """The reply to a status request of ours sent before the movement (marked by the gateway, and its copy on the
    monitor session) is not the actuator's stop: the close goes on, to the actuator's own stop (here 0.3 s later) or
    its limit."""
    cal = base
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == LOWER
    await asked(cal)
    cal.clock[0] += .05
    bus(cal, LOWER)
    cal.clock[0] += .03
    marked = OWNMessage.parse(STOP)
    marked.myhome_status_reply = True
    cal.cover.handle_event(marked)
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.back_stage) == ("rehoming", "running")
    cal.clock[0] += .27
    bus(cal, STOP)
    assert cal.session.phase == "confirm_closed"


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_a_timer_actuator_still_ends_the_close_to_the_rest_at_its_limit(base):
    """A timer actuator reports no stop at the bottom: the close lasts the closing time in use and its margin, as before."""
    cal = base
    await homing(cal)
    cal.clock[0] += 37.5
    fire(cal.session.deadline)
    assert written(cal) == STOP
    assert cal.session.phase == "confirm_closed"


async def test_the_run_back_of_a_repeat_ends_at_an_early_stop_too(base):
    """A repeat on the descent: the session opens the cover back; an actuator stop 0.3 s after the start (the cover
    was at the top already) ends the run back at once, as the close to the rest."""
    cal = base
    await runs(cal)
    await act(cal, "repeat", direction="closing")
    assert cal.session.phase == "reopening"
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == RISE
    cal.clock[0] += .6
    bus(cal, RISE)
    cal.clock[0] += .3
    bus(cal, STOP)
    assert (cal.session.phase, cal.cover._attr_current_cover_position) == ("confirm_open", 100)


@pytest.mark.parametrize("base", [{"direction": "closing"}], indirect=True)
async def test_outside_the_basic_calibration_an_early_stop_is_still_the_echo(base):
    """A one-direction guided session (older clients) keeps the echo window on its runs back: an early stop changes
    nothing, the actuator's stop after it ends the run back."""
    cal = base
    await act(cal, "close")
    written(cal)
    bus(cal, LOWER)
    cal.clock[0] += 22
    await act(cal, "endpoint")
    assert cal.session.phase == "review"
    await act(cal, "repeat", direction="closing")
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == RISE
    cal.clock[0] += .6
    bus(cal, RISE)
    cal.clock[0] += .3
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.back_stage) == ("reopening", "running")
    cal.clock[0] += 15
    bus(cal, STOP)
    assert cal.session.phase == "confirm_open"


async def test_home_is_for_the_basic_calibration_only(hass, plant):
    plant.gateways[0].async_queue_calibration = MagicMock()
    with pytest.raises(ProfileError, match="invalid_profile"):
        await begin(hass, MagicMock(subscriptions={}), {"id": 1, "entry_id": plant.entries[0].entry_id,
                                                        "entity_id": plant.records[0].entity_id, "revision": 0, "home": True})


# ---------------------------------------------------------------------------------------------------------------------
# A stop that answers a status request of ours is not the actuator's stop.
# ---------------------------------------------------------------------------------------------------------------------
async def asked(cal, where="11"):
    """A status request of ours for `where`, queued now (session clock)."""
    from OWNd.message import OWNAutomationCommand

    cal.session.status_requested(OWNAutomationCommand.status(where))


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
@pytest.mark.parametrize("then", ["arrival", "already_there"])
async def test_a_stop_answering_our_status_request_before_the_start_ends_nothing(base, then):
    """Our request sent between the command and the run's anchor, answered 'stopped' (the motor had not started yet)
    after the anchor, read twice (on the command session, marked, and on the monitor session): the run goes on. The
    actuator's own stop still ends it: at the arrival, or right after the start when the cover stood at the end stop
    already (two stops, the reply and the actuator's)."""
    cal = base
    await act(cal, "open")
    assert cal.queue[-1][1]()
    cal.clock[0] += .5
    bus(cal, STOP)  # Before the anchor (here the gateway's interlock): nothing.
    assert cal.session.phase == "starting_open"
    await asked(cal)
    cal.clock[0] += .05
    bus(cal, RISE)  # The anchor.
    cal.clock[0] += .03
    marked = OWNMessage.parse(STOP)
    marked.myhome_status_reply = True
    cal.cover.handle_event(marked)  # The reply as read on the command session, marked by the gateway…
    bus(cal, STOP)  # …and its copy on the monitor session, 0.08 s after the request.
    view = cal.session.view()
    assert (view["phase"], view["reason"], view["motor_stopped"]) == ("opening", None, False)
    if then == "arrival":
        cal.clock[0] += 18.27
        bus(cal, STOP)
        assert (cal.session.phase, cal.session.values) == ("confirm_open", {"opening_time": 18.3})
    else:
        cal.clock[0] += .27
        bus(cal, STOP)
        assert (cal.session.phase, cal.session.reason) == ("confirm_closed", "already_at_end")


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_a_movement_dated_before_it_was_learnt_still_takes_the_reply_for_a_reply(base):
    """The run's anchor may be dated before the session learns of the movement (a command translation standing for a
    status never relayed, dated from its arrival): a request sent in between is still answered as before the start."""
    cal = base
    await act(cal, "open")
    assert cal.queue[-1][1]()
    cal.clock[0] += 1
    await asked(cal)
    cal.clock[0] += .05
    bus(cal, RISE)
    cal.session.started_at -= .9  # Dated from the translation's arrival, before the request.
    cal.clock[0] += .03
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason) == ("opening", None)
    cal.clock[0] += 18
    bus(cal, STOP)
    assert cal.session.phase == "confirm_open"


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
@pytest.mark.parametrize("request_", ["after_the_anchor", "another_cover", "too_old"])
async def test_other_stops_near_a_status_request_still_refuse_a_start_at_the_end_stop(base, request_):
    """Only a reply to our request written before the anchor, within STATUS_REPLY_SECONDS: a request written after
    the anchor is answered with what the motor does (stopped: the truth), one for another cover gets no reply from this
    one, and one answered long ago is forgotten."""
    cal = base
    await act(cal, "open")
    assert cal.queue[-1][1]()
    if request_ == "another_cover":
        await asked(cal, where="12")
    elif request_ == "too_old":
        await asked(cal)
        cal.clock[0] += 1.2
    cal.clock[0] += .5
    bus(cal, RISE)
    if request_ == "after_the_anchor":
        cal.clock[0] += .1
        await asked(cal)
    cal.clock[0] += .2
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason) == ("confirm_closed", "already_at_end")


async def test_the_session_listens_to_the_status_requests_while_it_lasts(hass, plant):
    """The session hears of our WHO 2 status requests from its cover's gateway handler until it is released."""
    gateway = plant.gateways[0]
    gateway.automation_status_listeners = listeners = set()
    gateway.async_queue_calibration = MagicMock()
    session = await begin(hass, MagicMock(subscriptions={}), {
        "id": 9, "entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id, "revision": 0})
    assert listeners == {session.status_requested}
    session.close()
    session.reservation.completed()
    assert listeners == set()


# ---------------------------------------------------------------------------------------------------------------------
# The profile is updated only from its source.
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("whose", ["source", "follower", "by_hand"])
async def test_only_the_source_updates_its_profile(hass, base, whose):
    """`shared` only where the cover is the source of the profile it follows (its times' origin); a follower of a
    profile measured on another cover, or of one made by hand (no origin), saves a new profile: `shared` is refused with
    `profile_not_source` and nothing is written."""
    cal = base
    refined(cal, travel_cm=110.0, source=whose == "source")
    if whose == "by_hand":
        cal.session.store.data["profiles"]["fine"]["provenance"] = {
            direction: dict(MANUAL) for direction in ("opening", "closing")}
    await basic(cal)
    assert cal.session.view()["save_modes"] == (["new", "shared"] if whose == "source" else ["new"])
    if whose == "source":
        return
    before = copy.deepcopy(cal.session.store.data)
    for verb in ("preview_save", "save"):
        with pytest.raises(ProfileError, match="profile_not_source"):
            await act(cal, verb, save_mode="shared", confirmation="any")
    assert cal.session.store.data == before and cal.session.phase == "review"
    # The save it has: a profile of its own, named after the cover; the profile followed stays as it was.
    await act(cal, "save")
    data = cal.session.store.data
    assert data["assignments"][cal.cover.unique_id] != "fine"
    assert saved(cal)["name"] == cal.session.profile_name({})
    assert data["profiles"]["fine"] == before["profiles"]["fine"]


async def test_the_save_itself_refuses_a_cover_no_longer_the_source(hass, base):
    """Checked again under the store's lock: the profile's times taken from another cover meanwhile (here, rewritten
    in storage) leave this cover a follower, and `shared` is refused even with a valid preview."""
    from custom_components.myhome.cover_calibration_save import save_measurement

    cal = base
    refined(cal, travel_cm=110.0, source=True)
    await basic(cal)
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    cal.session.store.data["profiles"]["fine"]["provenance"] = {"opening": dict(GUIDED), "closing": dict(GUIDED)}
    with pytest.raises(ProfileError, match="profile_not_source"):
        await save_measurement(cal.session, {"save_mode": "shared", "confirmation": preview["confirmation"]})


# ---------------------------------------------------------------------------------------------------------------------
# Edges of the runs and of the save into the profile followed.
# ---------------------------------------------------------------------------------------------------------------------
async def test_an_actuators_stop_past_the_longest_travel_is_no_measurement(base):
    """A stop status after more than 600 s, not at a relay timer, is no possible travel time: the run is not timed by
    it and the session ends with `invalid_measurement`."""
    cal = base
    await opening(cal)
    cal.clock[0] += 700
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason, cal.session.values) == ("interrupted", "invalid_measurement", {})


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_a_status_neither_moving_nor_stopped_changes_nothing_in_a_run_back(base):
    cal = base
    await homing(cal)
    cal.clock[0] += 3
    bus(cal, "*2*10*11##")
    assert (cal.session.phase, cal.session.back_stage, cal.session.reason) == ("rehoming", "running", None)


async def test_a_repeat_names_only_a_run_of_the_calibration(base):
    cal = base
    await runs(cal)
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "repeat", direction="sideways")
    assert cal.session.phase == "confirm_lift"


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_without_slats_a_save_into_a_refined_profile_keeps_its_whole_geometry(hass, base):
    """The profile's rolls were measured or entered and no lift-off was measured here: its slat time and rolls stay."""
    cal = base
    refined(cal, rolls=(2.0, 3.0), evidence=(MANUAL, MANUAL), reference=None, source=True)
    before = copy.deepcopy(cal.session.store.data["profiles"]["fine"])
    await runs(cal)
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    profile = saved(cal)
    assert (profile["geometry"], profile["geometry_provenance"]) == (before["geometry"], before["geometry_provenance"])
    assert (profile["opening_time"], profile["closing_time"]) == (21.8, 21.21)


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_without_slats_a_save_into_a_basic_profile_sets_its_slat_time_to_zero(hass, base):
    cal = base
    refined(cal, evidence=(dict(UNKNOWN_EVIDENCE), dict(UNKNOWN_EVIDENCE)), reference=None, source=True)
    await runs(cal)
    preview = (await act(cal, "preview_save", save_mode="shared"))["save_preview"]
    await act(cal, "save", save_mode="shared", confirmation=preview["confirmation"])
    profile = saved(cal)
    assert profile["geometry"] == {"slat_time_s": 0.0, "opening_roll": 1.4, "closing_roll": 1.9}
    assert profile["geometry_provenance"]["slat_time_s"]["source"] == "guided"
    assert profile["geometry_provenance"]["opening_roll"] == UNKNOWN_EVIDENCE


async def test_no_save_into_the_profile_followed_without_the_lift_off(hass, base):
    cal = base
    refined(cal, travel_cm=110.0, source=True)
    await basic(cal)
    cal.session.samples.clear()
    before = copy.deepcopy(cal.session.store.data)
    with pytest.raises(ProfileError, match="lift_missing"):
        await act(cal, "preview_save", save_mode="shared")
    assert cal.session.store.data == before and cal.session.phase == "review"


async def test_no_save_into_the_profile_followed_from_a_session_no_longer_open(hass, base):
    from custom_components.myhome.cover_calibration_save import save_measurement

    cal = base
    refined(cal, travel_cm=110.0, source=True)
    await basic(cal)
    store = cal.session.store
    store.calibration = None  # Another session took the store meanwhile, or this one expired.
    with pytest.raises(ProfileError, match="calibration_expired"):
        await save_measurement(cal.session, {"save_mode": "shared"}, preview=True)
    store.calibration = cal.session
    assert cal.session.phase == "review" and store.data["profiles"]["fine"]["opening_time"] == 15.0


# ---------------------------------------------------------------------------------------------------------------------
# A reply read on the command session may come late: it never ends a run back or the wait after the lift-off.
# ---------------------------------------------------------------------------------------------------------------------
def marked_stop(cal):
    marked = OWNMessage.parse(STOP)
    marked.myhome_status_reply = True
    cal.cover.handle_event(marked)


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_a_late_marked_reply_does_not_end_the_close_to_the_rest(base):
    cal = base
    await homing(cal)
    cal.clock[0] += 3
    marked_stop(cal)
    assert (cal.session.phase, cal.session.back_stage) == ("rehoming", "running")
    cal.clock[0] += 2
    bus(cal, STOP)
    assert cal.session.phase == "confirm_closed"


async def test_a_late_marked_reply_does_not_end_the_wait_after_the_lift_off(base):
    cal = base
    await runs(cal)
    await lift_run(cal)
    assert written(cal) == STOP
    await asyncio.sleep(0)
    marked_stop(cal)
    assert cal.session.lift_stop == "written"
    bus(cal, STOP)
    assert cal.session.lift_stop is None


# ---------------------------------------------------------------------------------------------------------------------
# A reply that comes before the movement answers its request there: the actuator's own stop after it is not taken for it.
# ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_a_reply_before_the_movement_leaves_the_actuators_stop_its_meaning(base):
    """The request shares the queue with the command: its reply (on both sessions) comes before the bus's movement.
    The actuator's stop 0.3 s after the movement, within a second of the request, still says the cover stood at the top
    already, and a press after it saves nothing."""
    cal = base
    await act(cal, "open")
    assert cal.queue[-1][1]()
    await asked(cal)
    cal.clock[0] += .1
    marked_stop(cal)
    bus(cal, STOP)  # Its copy on the monitor session, still before the movement.
    assert cal.session.phase == "starting_open"
    cal.clock[0] += .3
    bus(cal, RISE)
    cal.clock[0] += .3
    bus(cal, STOP)
    assert (cal.session.phase, cal.session.reason) == ("confirm_closed", "already_at_end")
    cal.clock[0] += 4.7
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "endpoint")
    assert "opening_time" not in cal.session.values


@pytest.mark.parametrize("base", [{"slats": True, "home": True}], indirect=True)
async def test_a_reply_before_the_close_to_the_rest_moves_leaves_the_actuators_stop_its_meaning(base):
    cal = base
    assert written(cal) == STOP
    await asyncio.sleep(0)
    fire(cal.session.settle)
    assert written(cal) == LOWER
    await asked(cal)
    cal.clock[0] += .1
    bus(cal, STOP)  # The reply, before the movement.
    cal.clock[0] += .3
    bus(cal, LOWER)
    cal.clock[0] += .3
    bus(cal, STOP)  # Already at the rest: over at once, no wait for the closing time.
    assert cal.session.phase == "confirm_closed"


@pytest.mark.parametrize("base", [{"slats": False}], indirect=True)
async def test_recalibrating_a_cover_refined_to_rolls_of_1_keeps_their_evidence(hass, base):
    """Rolls measured as exactly 1 without slats resolve as the linear model; a new basic profile still keeps them as
    measured, so the cover stays refined."""
    cal = base
    refined(cal, rolls=(1.0, 1.0), reference=None)
    cal.session.store.data["profiles"]["fine"]["geometry"]["slat_time_s"] = 0.0
    await runs(cal)
    await act(cal, "save", name="Door again")
    profile = saved(cal)
    assert profile["geometry"] == {"slat_time_s": 0.0, "opening_roll": 1.0, "closing_roll": 1.0}
    assert profile["geometry_provenance"]["opening_roll"] == profile["geometry_provenance"]["closing_roll"] == GUIDED
    view = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    assert (view["precision"], view["model"]) == ("refined", "linear_time")
