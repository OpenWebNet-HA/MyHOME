"""Geometry wizard exercises real cover events, queue delivery, recovery and atomic save."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol

from custom_components.myhome.cover_calibration import CalibrationSession, begin
from custom_components.myhome.cover_calibration_fit import closing_fit, opening_fit, winding
from custom_components.myhome.cover_profiles import ProfileError, read_profile
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus

plant = plant_fixture


@pytest.fixture
async def geometry(hass, plant):
    queue = []

    def enqueue(message, guard, lock):
        future = hass.loop.create_future()
        queue.append((message, guard, lock, future))
        return future

    plant.gateways[0].async_queue_calibration = enqueue
    clock = [100.0]
    connection = MagicMock(subscriptions={})
    request = {"id": 12, "entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id,
               "revision": 0, "mode": "geometry", "client_id": "owner"}
    with patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]):
        session = await begin(hass, connection, request)
        cal = SimpleNamespace(session=session, cover=plant.covers[0], queue=queue, clock=clock, plant=plant,
                              connection=connection, request=request)
        yield cal
        session.close()
        session.reservation.completed()


async def start(cal):
    await act(cal, "next")
    command, guard, _, written = cal.queue[-1]
    cal.clock[0] += 2  # Wait in the queue must never enter a measured duration.
    assert guard()
    written.set_result(cal.clock[0])
    cal.clock[0] += .5
    bus(cal, "*2*2*11##" if "*2*2*" in str(command) else "*2*1*11##")
    await asyncio.sleep(0)


async def stopped(cal, *, elapsed=0, echo_first=False):
    cal.clock[0] += elapsed
    _, guard, _, written = cal.queue[-1]
    assert guard()
    if echo_first:
        bus(cal, "*2*0*11##")
    written.set_result(cal.clock[0])
    await asyncio.sleep(0)
    if not echo_first:
        bus(cal, "*2*0*11##")


async def endpoint(cal, seconds):
    await start(cal)
    cal.clock[0] += seconds
    await act(cal, "endpoint")
    await stopped(cal, elapsed=.4)


async def lift(cal, *, echo_first=False):
    await endpoint(cal, 9)  # Home: deliberately never recorded as a timing.
    assert cal.session.step == "lift"
    await start(cal)
    cal.clock[0] += 1.5
    await act(cal, "lift")
    await stopped(cal, elapsed=.5, echo_first=echo_first)
    assert cal.session.samples["lift"] == 2
    assert cal.session.phase == "reading"


async def half(cal, seconds):
    await start(cal)
    # Invoke the real scheduled callback at its due time, with an extra queue wait.
    cal.clock[0] += seconds - .2
    callback = cal.session.deadline._callback
    cal.session.deadline.cancel()
    callback()
    await stopped(cal, elapsed=.2)
    assert cal.session.phase == "reading"


async def until_open_reading(cal):
    await lift(cal)
    await act(cal, "reading", reading_cm=0)
    await endpoint(cal, 8)  # Reset before timing; not part of the opening time.
    await endpoint(cal, 22)
    await act(cal, "reading", reading_cm=200)
    await endpoint(cal, 20)
    await half(cal, 12)


async def review(cal):
    await until_open_reading(cal)
    await act(cal, "reading", reading_cm=200 * (2 * .5 + .5 ** 2) / 3)
    await endpoint(cal, 7)
    await half(cal, 9)
    await act(cal, "reading", reading_cm=200 * (2 * .5 + 2 * .5 ** 2) / 4)
    assert cal.session.phase == "review"


async def test_basic_measurement_atomic_save_runtime_provenance_and_no_accuracy(hass, geometry):
    cal = geometry
    await review(cal)
    assert cal.session.values == {"opening_time": 22, "closing_time": 20}
    assert cal.session.geometry == pytest.approx({"slat_time_s": 2, "opening_roll": 2, "closing_roll": 3})
    assert cal.session.view()["independent_check"] is False
    assert cal.session.view()["accuracy"] is None
    assert cal.cover._travel_time_up == 30
    before = copy.deepcopy(cal.session.store.data)
    with patch.object(cal.session.store.store, "async_save", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            await act(cal, "save", name="Measured roll")
    assert cal.session.phase == "review" and cal.session.store.data == before
    result = await act(cal, "save", name="Measured roll")
    assert result["phase"] == "saved"
    data = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    profile = data["profiles"][0]
    assert profile["reference_travel_cm"] == 200
    assert cal.session.store.data["covers"][cal.cover.unique_id]["travel_cm"] == 200
    assert profile["geometry"] == pytest.approx({"slat_time_s": 2, "opening_roll": 2, "closing_roll": 3})
    assert all(meta["source"] == "guided" for meta in profile["geometry_provenance"].values())
    assert cal.cover._motion.model.opening_roll == pytest.approx(2)
    assert cal.cover._motion.position.height == pytest.approx(.375)
    assert cal.session.store.data["revision"] == 1
    assert cal.session.store.calibration is None


async def test_lift_waits_for_both_write_and_stop_even_when_echo_arrives_first(geometry):
    await lift(geometry, echo_first=True)
    assert geometry.session.samples["lift"] == 2
    await act(geometry, "repeat")
    assert geometry.session.step == "reset" and geometry.session.after_position == "lift"
    await endpoint(geometry, 3)
    assert geometry.session.step == "lift" and geometry.session.phase == "briefing"


async def test_reading_checkpoints_resume_without_motion_and_repeat_repositions(geometry):
    cal = geometry
    await until_open_reading(cal)
    length = len(cal.queue)
    cal.session.detach(cal.session.attachment)
    assert cal.session.phase == "reading" and len(cal.queue) == length
    cal.session.attach(cal.connection, 13, "owner")
    assert len(cal.queue) == length
    await act(cal, "repeat")
    assert cal.session.after_position == "half_open"
    await endpoint(cal, 8)
    assert cal.session.step == "half_open"
    await half(cal, 12)
    await act(cal, "reading", reading_cm=83.3333333333)
    await endpoint(cal, 7)
    await half(cal, 9)
    await act(cal, "repeat")
    assert cal.session.step == "top" and cal.session.after_position == "half_close"


@pytest.mark.parametrize("action", ["run", "open", "close", "endpoint", "lift", "reading", "repeat", "save"])
async def test_geometry_rejects_wrong_step_and_stale_sequence(geometry, action):
    with pytest.raises(ProfileError):
        await act(geometry, action)
    with pytest.raises(ProfileError):
        await geometry.session.action({"action": "next", "sequence": -1})
    assert not geometry.queue


async def test_cancelled_delivery_discards_session_and_keeps_reservation(geometry):
    cal = geometry
    await act(cal, "next")
    assert cal.queue[-1][1]()
    cal.queue[-1][3].cancel()
    await asyncio.sleep(0)
    assert cal.session.reason == "not_delivered"
    assert cal.session.values == {} and cal.session.geometry == {}
    assert cal.session.reservation.pending
    cal.session.interrupt("late")
    assert cal.session.reason == "not_delivered"


@pytest.mark.parametrize("failure", ["queue", "cancel", "invalid", "closed", "opposite", "timeout"])
async def test_lift_failures_never_create_reading_or_save(geometry, failure):
    cal = geometry
    await endpoint(cal, 4)
    await start(cal)
    cal.clock[0] += 2
    if failure == "queue":
        with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=asyncio.QueueFull):
            await act(cal, "lift")
    else:
        await act(cal, "lift")
        future = cal.queue[-1][3]
        if failure == "cancel":
            future.cancel()
        elif failure == "invalid":
            future.set_result(cal.clock[0] + 601)
        elif failure == "closed":
            cal.session.close()
            future.set_result(cal.clock[0])
        elif failure == "opposite":
            bus(cal, "*2*2*11##")
        else:
            callback, args = cal.session.deadline._callback, cal.session.deadline._args
            cal.session.deadline.cancel()
            callback(*args)
        await asyncio.sleep(0)
    assert cal.session.phase in {"interrupted", "cancelled"}
    assert cal.session.samples == {} and cal.session.geometry == {}
    assert cal.session.store.data["revision"] == 0


async def test_endpoint_stop_queue_failure_and_disconnect(geometry):
    cal = geometry
    await start(cal)
    with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=asyncio.QueueFull):
        await act(cal, "endpoint")
    assert cal.session.reason == "stop_queue_full"


async def test_disconnect_during_motion_stops_discards_and_does_not_resume(geometry):
    await start(geometry)
    geometry.session.detach(geometry.session.attachment)
    assert geometry.session.phase == "interrupted"
    assert str(geometry.queue[-1][0]) == "*2*0*11##"
    assert geometry.session.reservation.pending


async def test_invalid_reading_retains_step_and_samples(geometry):
    cal = geometry
    await lift(cal)
    with pytest.raises(ProfileError, match="invalid_reading"):
        await act(cal, "reading", reading_cm=True)
    await act(cal, "reading", reading_cm=4)
    await endpoint(cal, 2)
    await endpoint(cal, 22)
    with pytest.raises(ProfileError, match="invalid_reading"):
        await act(cal, "reading", reading_cm=3)
    assert cal.session.phase == "reading"
    await act(cal, "repeat")
    assert cal.session.after_position == "opening"


async def test_shared_or_personal_save_not_offered_when_geometry_overrides_unsupported(geometry):
    await review(geometry)
    for mode in ["cover", "shared"]:
        with pytest.raises(ProfileError):
            await act(geometry, "save", save_mode=mode)
    assert geometry.session.view()["save_modes"] == ["new"]
    geometry.session.phase = "saving"
    geometry.session.interrupt("late")
    assert geometry.session.geometry
    geometry.session.phase = "review"


@pytest.mark.parametrize("slat", [0, 2])
def test_fit_recovers_late_lift_gap_and_both_directional_rolls(slat):
    for roll in [1, 1.3, 2, 4.9, 5]:
        height, gap, travel, total = 88.0, 7.0, 200.0, 22.0
        lift_time = slat + (total - slat) * winding(gap / travel, roll)
        at_height = slat + (total - slat) * winding(height / travel, roll)
        assert opening_fit(total, lift_time, gap, travel, at_height, height) == pytest.approx((slat, roll))
        elapsed = (total - slat) * (1 - winding(height / travel, roll))
        assert closing_fit(total, slat, elapsed, height, travel) == pytest.approx(roll)


@pytest.mark.parametrize("args", [(22, 2, 0, 200, 12, 0), (22, 2, 0, 200, 12, 190),
                                  (22, 2, 0, 200, 12, 1), (22, .1, 10, 200, 12, 90)])
def test_opening_fit_rejects_inconsistent_measurements(args):
    with pytest.raises(vol.Invalid):
        opening_fit(*args)


@pytest.mark.parametrize("args", [(20, 21, 9, 75, 200), (20, 2, 9, 1, 200), (20, 2, 9, 190, 200)])
def test_closing_fit_rejects_inconsistent_measurements(args):
    with pytest.raises(vol.Invalid):
        closing_fit(*args)


async def test_timing_only_protocol_rejects_geometry_verbs(geometry):
    cal = geometry
    cal.session.mode = "guided"
    with pytest.raises(ProfileError):
        await act(cal, "reading", reading_cm=2)
    with pytest.raises(ProfileError):
        CalibrationSession.geometry_action(cal.session, {})


async def test_slats_must_fit_both_full_directional_timings(geometry):
    cal = geometry
    await until_open_reading(cal)
    cal.session.values["closing_time"] = 1
    with pytest.raises(ProfileError, match="invalid_reading"):
        await act(cal, "reading", reading_cm=83.3333333333)
    assert cal.session.phase == "reading" and not cal.session.geometry


async def test_repeat_closing_keeps_opening_and_rehomes_at_top(geometry):
    cal = geometry
    await until_open_reading(cal)
    # Repeat is also offered at the briefing immediately after measuring closing.
    cal.session.phase = "briefing"
    assert cal.session.view()["can_repeat"]
    await act(cal, "repeat")
    assert cal.session.step == "top" and cal.session.after_position == "closing"
    assert cal.session.values["opening_time"] == 22
    await endpoint(cal, 7)
    assert cal.session.step == "closing"
    await endpoint(cal, 21)
    assert cal.session.values["closing_time"] == 21


async def test_impossibly_short_endpoint_interrupts_instead_of_storing(geometry):
    cal = geometry
    await lift(cal)
    await act(cal, "reading", reading_cm=0)
    await endpoint(cal, 4)
    await start(cal)
    with pytest.raises(ProfileError, match="invalid_profile"):
        await act(cal, "endpoint")
    assert cal.session.phase == "interrupted" and not cal.session.values
