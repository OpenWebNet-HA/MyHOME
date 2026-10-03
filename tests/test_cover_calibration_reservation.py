"""Closed clients cannot release a gateway while their actuator may still move."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.exceptions import ServiceValidationError

from custom_components.myhome.cover_calibration import begin
from custom_components.myhome.cover_calibration_reservation import GUARD_SECONDS
from custom_components.myhome.cover_profiles import ProfileError, read_profile, write_profile
from tests.test_panel_cover_calibration import act, bus
from tests.test_panel_cover_calibration import calibration as calibration_fixture
from tests.test_panel_cover_calibration import plant as plant_fixture

calibration = calibration_fixture
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
