"""Closed clients cannot release a gateway while their actuator may still move."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.exceptions import ServiceValidationError
from OWNd.message import OWNMessage

from custom_components.myhome.cover_calibration import begin
from custom_components.myhome.cover_calibration_reservation import GUARD_SECONDS
from custom_components.myhome.cover_profiles import ProfileError, read_profile, write_profile
from tests.test_cover_calibration_base import asked
from tests.test_cover_calibration_geometry import GAP, endpoint, lift, start
from tests.test_cover_calibration_geometry import geometry as geometry_fixture
from tests.test_panel_cover_calibration import act, automatic_next, automatic_run, bus
from tests.test_panel_cover_calibration import calibration as calibration_fixture
from tests.test_panel_cover_calibration import plant as plant_fixture

calibration = calibration_fixture
geometry = geometry_fixture
plant = plant_fixture


async def dispatch(cal, *, feedback=True):
    await act(cal, "run" if cal.session.mode == "automatic" else "open")
    assert cal.queue[-1][1]()
    if feedback:
        bus(cal, "*2*1*11##")


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
@pytest.mark.parametrize("feedback", [False, True])
async def test_cancel_keeps_gateway_until_stop_and_invalidates_old_commands(hass, calibration, feedback):
    cal, session = calibration, calibration.session
    session.client_id = "panel"
    cal.plant.gateways[0].config_entry = cal.plant.entries[0]
    await dispatch(cal, feedback=feedback)
    motion_guard = cal.queue[-1][1]
    await act(cal, "cancel")
    assert session.closed and session.store.calibration is session
    assert cal.cover._calibration is session
    assert not motion_guard()
    stop_guard = cal.queue[-1][1]
    assert stop_guard()  # A queued Stop is not Stop feedback.
    assert session.reservation.pending
    view = await read_profile(hass, session.entry_id, cal.cover.entity_id)
    assert view["calibration"]["waiting_for_stop"]
    assert not view["calibration"]["recoverable"]
    assert "attachment" not in view["calibration"]
    with pytest.raises(ProfileError, match="calibration_expired"):
        session.attach(cal.connection, 99, "panel")
    with pytest.raises(ProfileError, match="calibration_busy"):
        await begin(hass, MagicMock(), {**cal.request, "client_id": "panel"})
    with pytest.raises(ProfileError, match="calibration_busy"):
        await write_profile(hass, {**cal.request, "action": "assign", "profile_id": None})
    with pytest.raises(ServiceValidationError, match="Finish its measurement"):
        cal.plant.covers[1]._check_panel_timing_owner()
    other = await begin(hass, MagicMock(), {**cal.request, "entry_id": cal.plant.entries[1].entry_id,
                                           "entity_id": cal.plant.records[2].entity_id})
    other.close()  # Another gateway remains independent.
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending
    assert session.store.calibration is None and cal.cover._calibration is None
    assert not stop_guard()
    next_session = await begin(hass, MagicMock(), cal.request)
    try:
        assert not motion_guard() and not stop_guard()
    finally:
        next_session.close()


async def test_guard_is_full_unknown_travel_and_restarts_on_observed_movement(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    session.close()
    timer = session.reservation.timer
    assert GUARD_SECONDS == 600
    assert timer.when() - cal.cover.hass.loop.time() > 599
    bus(cal, "*2*2*11##")
    assert timer.cancelled()
    timer = session.reservation.timer
    assert timer.when() - cal.cover.hass.loop.time() > 599
    timer._run()
    assert timer.cancelled() and session.store.calibration is None
    assert cal.cover._calibration is None
    assert not session.view()["waiting_for_stop"]


async def test_guard_expiry_does_not_discard_live_review_and_old_stop_expires(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    cal.clock[0] += 20
    await act(cal, "endpoint")
    stop = cal.queue[-1][1]
    assert stop()
    cal.clock[0] += 31
    assert not stop()
    session.reservation.timer._run()
    assert session.store.calibration is session and session.active
    assert session.values["opening_time"] == 20
    session.close()
    assert session.store.calibration is None


async def test_old_endpoint_stop_cannot_interrupt_a_later_direction(calibration):
    cal = calibration
    await dispatch(cal)
    cal.clock[0] += 20
    await act(cal, "endpoint")
    old_stop = cal.queue[-1][1]
    assert old_stop()
    await act(cal, "close")
    assert not old_stop()
    assert cal.queue[-1][1]()
    cal.session.close()
    assert cal.session.reservation.pending
    bus(cal, "*2*0*11##")


@pytest.mark.parametrize("cleanup", ["unload", "shutdown", "closed_shutdown", "queue_full"])
async def test_lifecycle_cleanup_keeps_guard_except_when_runtime_stops(hass, calibration, cleanup):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    if cleanup == "unload":
        with patch("custom_components.myhome.myhome_device.MyHOMEEntity.async_will_remove_from_hass", new=AsyncMock()):
            await cal.cover.async_will_remove_from_hass()
    elif cleanup == "queue_full":
        with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=asyncio.QueueFull):
            session.close()
        assert not session.stop_requested
    else:
        if cleanup == "closed_shutdown":
            session.close()
        with patch.object(hass, "state", CoreState.stopping):
            hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
            await hass.async_block_till_done()
            assert cal.queue[-1][1]()  # Best-effort Stop survives shutdown cleanup.
        assert not session.reservation.pending and session.store.calibration is None
        return
    assert session.closed and session.reservation.pending and session.store.calibration is session
    session.reservation.timer._run()
    assert session.store.calibration is None


async def test_saved_partial_measurement_waits_for_bus_stop(calibration):
    cal, session = calibration, calibration.session
    session.direction = "opening"
    session.values["closing_time"] = 30
    from custom_components.myhome.cover_profile_provenance import unknown_provenance
    session.provenance["closing"] = unknown_provenance()["closing"]
    await dispatch(cal)
    cal.clock[0] += 20
    await act(cal, "endpoint")
    await act(cal, "save", name="Measured")
    assert session.phase == "saved" and session.closed
    assert session.store.calibration is session and session.view()["waiting_for_stop"]
    bus(cal, "*2*0*11##")
    assert session.store.calibration is None


# The actuator's answer to a status request, as the gateway reads it on the command session.

def status_reply(cal, raw="*2*0*11##"):
    """Answered «stopped» around the run's command, it can reach the session while the motor may still run."""
    reply = OWNMessage.parse(raw)
    reply.myhome_status_reply = True
    cal.cover.handle_event(reply)


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_a_status_reply_in_a_run_keeps_the_gateway_for_the_cancel_stop(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    cal.clock[0] += 3
    status_reply(cal)
    assert session.reservation.pending and session.active
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    assert session.closed and session.reservation.pending and session.store.calibration is session
    assert stop_guard()  # The Stop still goes out.
    bus(cal, "*2*0*11##")  # The actuator's own stop frees the gateway, as before.
    assert not session.reservation.pending and session.store.calibration is None
    assert not stop_guard()


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_a_status_reply_after_cancel_does_not_release_the_gateway(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    status_reply(cal)
    assert session.reservation.pending and session.store.calibration is session
    assert cal.cover._calibration is session and session.view()["waiting_for_stop"]
    assert stop_guard()
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None
    assert cal.cover._calibration is None and not stop_guard()


async def test_a_status_reply_in_a_geometry_run_keeps_the_gateway_for_the_cancel_stop(geometry):
    cal, session = geometry, geometry.session
    await lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    await endpoint(cal, 8)
    await start(cal)
    status_reply(cal)
    assert session.reservation.pending
    session.close()
    stop_guard = cal.queue[-1][1]
    assert session.reservation.pending and session.store.calibration is session and stop_guard()
    bus(cal, "*2*0*11##")
    assert session.store.calibration is None


# The same answer as the monitor session reads it: no mark, only one of our status requests heard just before.

async def asked_then_dispatched(cal):
    """Our request to this cover goes out with the run's command, before the bus reports the movement."""
    await act(cal, "run" if cal.session.mode == "automatic" else "open")
    assert cal.queue[-1][1]()
    await asked(cal)
    cal.clock[0] += .05
    bus(cal, "*2*1*11##")


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_an_unmarked_status_reply_in_a_run_keeps_the_gateway_for_the_cancel_stop(calibration):
    cal, session = calibration, calibration.session
    await asked_then_dispatched(cal)
    cal.clock[0] += .2
    before = session.view()
    bus(cal, "*2*0*11##")  # The reply, 0.25 s after the request.
    assert session.reservation.pending and session.active
    if session.mode == "guided":  # The run reads it as before: the reply, not the end of the travel.
        assert (session.phase, session.reason, session.motor_stopped) == (before["phase"], None, False)
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    assert session.closed and session.reservation.pending and session.store.calibration is session
    assert stop_guard()
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")  # The actuator's own stop frees the gateway, as before.
    assert not session.reservation.pending and session.store.calibration is None
    assert not stop_guard()


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_an_unmarked_status_reply_after_cancel_does_not_release_the_gateway(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    cal.clock[0] += 3
    await asked(cal)
    cal.clock[0] += .1
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    cal.clock[0] += .2
    bus(cal, "*2*0*11##")  # The reply to the request sent before the cancel.
    assert session.reservation.pending and session.store.calibration is session
    assert cal.cover._calibration is session and session.view()["waiting_for_stop"]
    assert stop_guard()
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None
    assert cal.cover._calibration is None and not stop_guard()


async def test_an_unmarked_status_reply_in_a_geometry_run_keeps_the_gateway_for_the_cancel_stop(geometry):
    cal, session = geometry, geometry.session
    await lift(cal)
    await act(cal, "reading", reading_cm=GAP)
    await endpoint(cal, 8)
    await start(cal)
    await asked(cal)
    cal.clock[0] += .1
    bus(cal, "*2*0*11##")
    assert session.reservation.pending
    session.close()
    stop_guard = cal.queue[-1][1]
    assert session.reservation.pending and session.store.calibration is session and stop_guard()
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")
    assert session.store.calibration is None


async def test_the_actuators_stop_right_after_a_reply_the_run_has_read_frees_the_gateway(calibration):
    """The run takes the reply for the answer to our request; a stop after it is the actuator's, even this soon."""
    cal, session = calibration, calibration.session
    await asked_then_dispatched(cal)
    cal.clock[0] += .1
    bus(cal, "*2*0*11##")  # The reply: the run reads it and the request is answered.
    assert session.phase == "opening" and session.reservation.pending
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    assert stop_guard()
    cal.clock[0] += .3
    bus(cal, "*2*0*11##")  # Still within the reply time of the request, but it was answered.
    assert not session.reservation.pending and session.store.calibration is None
    assert not stop_guard()


# Each request has one reply: once it has come, the actuator's own stop frees the gateway, however soon.

@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
@pytest.mark.parametrize("marked_first", [False, True])
async def test_a_request_answered_moving_lets_the_actuators_stop_free_the_gateway(calibration, marked_first):
    """A request during the run, answered 'moving' (on both sessions), then the cancel: the real stop, still within the
    reply time of the request, is not taken for its reply."""
    cal, session = calibration, calibration.session
    await dispatch(cal)
    cal.clock[0] += 3
    await asked(cal)
    cal.clock[0] += .1
    if marked_first:
        status_reply(cal, "*2*1*11##")
    bus(cal, "*2*1*11##")  # The reply as the monitor session reads it.
    if not marked_first:
        status_reply(cal, "*2*1*11##")
    assert session.active and session.reservation.pending
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    assert stop_guard()
    cal.clock[0] += .3
    bus(cal, "*2*0*11##")  # The actuator's own stop, 0.4 s after the request.
    assert not session.reservation.pending and session.store.calibration is None
    assert cal.cover._calibration is None and not stop_guard()


async def test_two_requests_each_answered_moving_let_the_actuators_stop_free_the_gateway(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    cal.clock[0] += 3
    await asked(cal)
    cal.clock[0] += .05
    await asked(cal)
    cal.clock[0] += .1
    bus(cal, "*2*1*11##")
    bus(cal, "*2*1*11##")
    await act(cal, "cancel")
    cal.clock[0] += .2
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None


async def test_two_requests_one_reply_so_far_keep_the_gateway_for_the_next_stop(calibration):
    """The stop may be the second reply: the actuator's next stop, or the guard, frees the gateway."""
    cal, session = calibration, calibration.session
    await dispatch(cal)
    cal.clock[0] += 3
    await asked(cal)
    cal.clock[0] += .05
    await asked(cal)
    cal.clock[0] += .1
    bus(cal, "*2*1*11##")  # The reply to the first.
    await act(cal, "cancel")
    cal.clock[0] += .2
    bus(cal, "*2*0*11##")
    assert session.reservation.pending and session.store.calibration is session
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None


async def test_a_request_after_the_cancel_still_has_its_reply_awaited(calibration):
    cal, session = calibration, calibration.session
    await dispatch(cal)
    await act(cal, "cancel")
    cal.clock[0] += .1
    await asked(cal)
    cal.clock[0] += .1
    bus(cal, "*2*0*11##")  # The reply to the request sent after the cancel.
    assert session.reservation.pending and session.store.calibration is session
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None


# An automatic run and the monitor copy of a reply: the run does not take it for its stop.

def automatic_closing(cal):
    """The first automatic run done; the closing run's command written, and our request sent before its movement."""
    automatic_run(cal, "open", 0.5)
    automatic_next(cal)
    assert cal.queue[-1][1]()


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_an_unmarked_status_reply_neither_ends_nor_times_an_automatic_run(calibration):
    cal, session = calibration, calibration.session
    await act(cal, "run")
    automatic_closing(cal)
    await asked(cal)
    cal.clock[0] += .05
    bus(cal, "*2*2*11##")
    cal.clock[0] += .3
    bus(cal, "*2*0*11##")  # The reply, 0.35 s after the request: it would otherwise time a 0.3 s closing run.
    assert (session.phase, session.values, session.reason) == ("closing", {}, None)
    cal.clock[0] += 19.5
    bus(cal, "*2*0*11##")  # The actuator's own stop times the run.
    assert (session.phase, session.values) == ("settling", {"closing_time": 19.8})


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_an_unmarked_status_reply_in_an_automatic_run_then_cancel_keeps_the_gateway(calibration):
    cal, session = calibration, calibration.session
    await asked_then_dispatched(cal)
    cal.clock[0] += .2
    bus(cal, "*2*0*11##")  # The reply: the opening run goes on.
    assert (session.phase, session.run_index, session.settle) == ("opening", 0, None)
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    assert session.closed and session.reservation.pending and stop_guard()
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None and not stop_guard()


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_an_unmarked_status_reply_after_cancelling_an_automatic_run_keeps_the_gateway(calibration):
    cal, session = calibration, calibration.session
    await act(cal, "run")
    automatic_closing(cal)
    cal.clock[0] += 1
    bus(cal, "*2*2*11##")
    cal.clock[0] += 3
    await asked(cal)
    cal.clock[0] += .1
    await act(cal, "cancel")
    stop_guard = cal.queue[-1][1]
    cal.clock[0] += .2
    bus(cal, "*2*0*11##")  # The reply to the request sent before the cancel.
    assert session.reservation.pending and session.store.calibration is session and stop_guard()
    cal.clock[0] += 2
    bus(cal, "*2*0*11##")
    assert not session.reservation.pending and session.store.calibration is None
