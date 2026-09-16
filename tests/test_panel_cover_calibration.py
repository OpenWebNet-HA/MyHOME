"""Guided measurement: real cover events, persistence and connection ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState
from homeassistant.exceptions import Unauthorized
from OWNd.message import OWNMessage
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_calibration import (
    WS_ACTION,
    WS_START,
    begin,
    register_api,
    ws_action,
    ws_start,
)
from custom_components.myhome.cover_profiles import ProfileError, get_store, write_profile
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


@pytest.fixture
async def calibration(hass, plant, request):
    mode = getattr(request, "param", "guided")
    queue = []
    for gateway in plant.gateways:
        gateway.async_queue_calibration = lambda message, guard, lock: queue.append((message, guard, lock))
    connection = MagicMock(subscriptions={}, user=SimpleNamespace(is_admin=True))
    request = {"id": 77, "entry_id": plant.entries[0].entry_id,
               "entity_id": plant.records[0].entity_id, "revision": 0, "mode": mode}
    clock = [100.0]
    with patch("custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]):
        session = await begin(hass, connection, request)
        yield SimpleNamespace(session=session, connection=connection, request=request,
                              queue=queue, cover=plant.covers[0], clock=clock, plant=plant)
        session.close()


async def act(cal, action, **extra):
    return await cal.session.action({"action": action, "sequence": cal.session.sequence, **extra})


def bus(cal, raw):
    cal.cover.handle_event(OWNMessage.parse(raw))


async def measured(cal):
    await act(cal, "open")
    assert cal.cover.current_cover_position == 0  # Operator confirmed the starting endpoint.
    assert cal.queue[-1][1]()  # The worker is now about to send, not just enqueue.
    cal.clock[0] += 2
    bus(cal, "*2*1*11##")
    cal.clock[0] += 20.5
    await act(cal, "endpoint")
    await act(cal, "close")
    assert cal.queue[-1][1]()
    cal.clock[0] += 3
    bus(cal, "*2*2*11##")
    cal.clock[0] += 40.5
    await act(cal, "endpoint")


async def test_measurement_uses_bus_start_and_explicit_endpoints_before_save(hass, calibration):
    cal = calibration
    assert cal.queue == []
    assert cal.session.phase == "confirm_closed"
    await measured(cal)
    assert cal.session.values == {"opening_time": 20.5, "closing_time": 40.5}
    assert cal.cover.current_cover_position == 0
    assert cal.cover._travel_time_up == cal.cover._travel_time_down == 30
    assert cal.session.store.data["revision"] == 0
    assert cal.session.phase == "review"
    result = await act(cal, "save", name="Measured bedroom")
    assert result["phase"] == "saved"
    assert cal.session.store.data["revision"] == 1
    assert cal.cover._travel_time_up == 20.5
    assert cal.cover._travel_time_down == 40.5
    assert cal.session.store.calibration is None
    assert cal.cover._calibration is None
    assert len(cal.queue) == 4  # Open, Stop, Close, Stop; Save sends nothing.
    cal.plant.gateways[0].send.assert_not_called()


async def test_guided_evidence_records_endpoint_dates_and_survives_later_save(hass, calibration):
    from datetime import UTC, datetime

    from custom_components.myhome.cover_profiles import read_profile

    cal = calibration
    clock = "custom_components.myhome.cover_profile_provenance.dt_util.utcnow"
    with patch(clock, return_value=datetime(2026, 9, 15, 11, tzinfo=UTC)):
        await measured(cal)
    assert cal.session.store.data["profiles"] == {}
    with patch(clock, return_value=datetime(2026, 9, 16, 12, tzinfo=UTC)):
        await act(cal, "save", name="Measured")
    result = await read_profile(hass, cal.session.entry_id, cal.cover.entity_id)
    for meta in result["profiles"][0]["provenance"].values():
        assert meta["source"] == "guided"
        assert meta["recorded_at"] == "2026-09-15T11:00:00+00:00"
        assert meta["origin_entity_id"] == cal.cover.entity_id
        assert meta["inherited"] is False


@pytest.mark.parametrize("action", ["open", "close", "endpoint", "save"])
async def test_stale_steps_and_invalid_phase_never_move_or_save(calibration, action):
    cal = calibration
    with pytest.raises(ProfileError, match="calibration_step"):
        await cal.session.action({"action": action, "sequence": -1})
    if action != "open":
        with pytest.raises(ProfileError, match="calibration_step"):
            await act(cal, action)
    assert cal.queue == []
    assert cal.session.store.data["revision"] == 0


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_profile_writes_and_second_tab_blocked_while_session_active(hass, calibration):
    cal = calibration
    with pytest.raises(ProfileError, match="calibration_busy"):
        await begin(hass, MagicMock(), cal.request)
    with pytest.raises(ProfileError, match="calibration_busy"):
        await write_profile(hass, {**cal.request, "action": "assign", "profile_id": None})
    other = MagicMock()
    ws_action(hass, other, {"id": 1, "entry_id": cal.request["entry_id"], "session_id": cal.session.id, "action": "stop"})
    await hass.async_block_till_done()
    other.send_error.assert_called_once()
    assert cal.queue == []


@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_queued_movement_invalid_after_disconnect_and_stop_has_expiry(calibration):
    cal = calibration
    await act(cal, "run" if cal.session.mode == "automatic" else "open")
    pending = cal.queue[-1]
    cal.connection.subscriptions[77]()  # HA unsubscribes on socket close.
    assert not pending[1]()
    assert cal.session.store.calibration is None
    assert cal.session.reason == "cancelled"
    stop = cal.queue[-1]
    assert str(stop[0]) == "*2*0*11##"
    assert stop[1]()
    cal.clock[0] += 31
    assert not stop[1]()
    assert cal.session.store.data["profiles"] == {}


async def test_start_timeout_and_expired_queue_guard(calibration):
    cal = calibration
    await act(cal, "open")
    queued = cal.queue[-1]
    cal.clock[0] += 11
    assert not queued[1]()
    cal.session.deadline._run()
    assert cal.session.reason == "start_timeout"
    assert cal.session.phase == "interrupted"
    assert cal.session.stop_requested


async def test_travel_and_heartbeat_timeout_discard_values(calibration):
    cal = calibration
    await act(cal, "open")
    cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    assert cal.session.view()["elapsed"] == 0
    await act(cal, "heartbeat")
    cal.session.deadline._run()
    assert cal.session.reason == "travel_timeout"
    assert cal.session.values == {}
    assert cal.session.provenance == {}
    cal.session.lease._run()
    assert cal.session.reason == "heartbeat_timeout"
    assert cal.cover._calibration is None


@pytest.mark.parametrize("event,reason", [("*2*2*11##", "unexpected_movement"), ("*2*0*11##", "unexpected_stop")])
async def test_external_bus_interference_invalidates_measurement(calibration, event, reason):
    cal = calibration
    await act(cal, "open")
    cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    bus(cal, "*2*1*11##")  # Repeated movement feedback does not restart the clock.
    bus(cal, event)
    assert cal.session.reason == reason
    assert cal.session.values == {}
    assert cal.session.stop_requested


async def test_bus_movement_before_dispatch_or_between_legs_invalidates(calibration):
    cal = calibration
    await act(cal, "open")
    bus(cal, "*2*0*11##")  # No start feedback yet: don't accept as an endpoint.
    assert cal.session.phase == "starting_open"
    bus(cal, "*2*1*11##")  # Our job has not been dispatched.
    assert cal.session.reason == "unexpected_movement"
    assert not cal.queue[0][1]()


async def test_unexpected_motion_while_waiting_for_confirmation(calibration):
    bus(calibration, "*2*2*11##")
    assert calibration.session.reason == "unexpected_movement"


@pytest.mark.parametrize("method,kwargs", [("async_open_cover", {}), ("async_close_cover", {}),
                                          ("async_stop_cover", {}), ("async_set_cover_position", {"position": 50})])
async def test_normal_ha_commands_interrupt_without_blocking_operator(calibration, method, kwargs):
    cal = calibration
    await act(cal, "open")
    pending = cal.queue[-1]
    await getattr(cal.cover, method)(**kwargs)
    assert cal.session.reason == "external_command"
    assert not pending[1]()
    cal.cover._cancel_stop_task()


async def test_stop_bypasses_step_check_and_can_be_retried(calibration):
    cal = calibration
    await act(cal, "open")
    await cal.session.action({"action": "stop", "sequence": -1})
    assert cal.session.reason == "stopped"
    count = len(cal.queue)
    await act(cal, "stop")
    assert len(cal.queue) == count + 1
    await act(cal, "cancel")
    assert cal.session.phase == "cancelled"


async def test_save_failure_preserves_review_and_does_not_create_profile(calibration):
    cal = calibration
    await measured(cal)
    with patch.object(cal.session.store.store, "async_save", side_effect=OSError("full")):
        with pytest.raises(OSError):
            await act(cal, "save", name="Measured")
    assert cal.session.phase == "review"
    assert cal.session.values["opening_time"] == 20.5
    assert cal.session.store.data["profiles"] == {}
    with pytest.raises(vol.Invalid):  # Name validation runs in the profile store.
        await act(cal, "save", name="")
    assert cal.session.phase == "review"
    await act(cal, "save", name="Retry")
    assert cal.session.phase == "saved"


async def test_cancel_during_accepted_save_does_not_claim_to_rollback(calibration):
    cal = calibration
    await measured(cal)
    original = cal.session.store.store.async_save
    async def save(data):
        cal.session.close()
        await original(data)
    with patch.object(cal.session.store.store, "async_save", side_effect=save):
        await act(cal, "save", name="Accepted")
    assert cal.session.store.data["revision"] == 1
    assert cal.session.phase == "saved"


async def test_failed_save_after_disconnect_does_not_restore_closed_session(calibration):
    cal = calibration
    await measured(cal)
    async def fail(_):
        cal.session.close()
        raise OSError("full")
    with patch.object(cal.session.store.store, "async_save", side_effect=fail):
        with pytest.raises(OSError):
            await act(cal, "save", name="Accepted")
    assert cal.session.store.calibration is None
    assert cal.session.store.data["revision"] == 0


async def test_invalid_duration_stops_and_discards(calibration):
    cal = calibration
    await act(cal, "open")
    cal.queue[-1][1]()
    bus(cal, "*2*1*11##")
    with pytest.raises(ProfileError, match="invalid_profile"):
        await act(cal, "endpoint")
    assert cal.session.reason == "invalid_measurement"
    assert cal.session.stop_requested


async def test_full_queue_does_not_claim_stop_was_sent(calibration):
    cal = calibration
    with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=asyncio.QueueFull):
        with pytest.raises(ProfileError, match="command_queue_full"):
            await act(cal, "open")
        await act(cal, "stop")
        assert not cal.session.stop_requested
        assert cal.session.reason == "stop_queue_full"


async def test_unavailable_or_unloaded_cover_ends_measurement(calibration):
    cal = calibration
    cal.plant.gateways[0].available = False
    with pytest.raises(ProfileError, match="cover_unavailable"):
        await act(cal, "open")
    assert cal.session.reason == "cover_unavailable"
    cal.session.close()


async def test_availability_callback_and_unload_release_session(calibration):
    cal = calibration
    cal.plant.gateways[0].available = False
    cal.cover._handle_availability_update()
    assert cal.session.reason == "cover_unavailable"
    with patch("custom_components.myhome.myhome_device.MyHOMEEntity.async_will_remove_from_hass", new=AsyncMock()):
        await cal.cover.async_will_remove_from_hass()
    assert cal.session.store.calibration is None


async def test_start_refusals_and_expired_internal_save(hass, calibration):
    cal = calibration
    cal.session.close()
    with pytest.raises(ProfileError, match="calibration_expired"):
        await write_profile(hass, {**cal.request, "action": "assign"}, calibration=cal.session)
    with pytest.raises(ProfileError, match="revision_conflict"):
        await begin(hass, cal.connection, {**cal.request, "revision": 9})
    with patch.object(hass, "state", CoreState.stopping):
        with pytest.raises(ProfileError, match="cover_unavailable"):
            await begin(hass, cal.connection, cal.request)
    cal.cover._advanced = True
    with pytest.raises(ProfileError, match="advanced_cover"):
        await begin(hass, cal.connection, cal.request)
    cal.cover._advanced = False
    cal.cover._attr_is_opening = True
    with pytest.raises(ProfileError, match="calibration_moving"):
        await begin(hass, cal.connection, cal.request)
    cal.cover._attr_is_opening = False


@pytest.mark.parametrize("handler", [ws_start, ws_action])
async def test_calibration_api_requires_admin(hass, handler):
    with pytest.raises(Unauthorized):
        handler(hass, MagicMock(user=SimpleNamespace(is_admin=False)), {"id": 1})


@pytest.mark.parametrize("mode", ["guided", "automatic"])
async def test_websocket_session_subscription_actions_and_disconnect(hass, plant, hass_ws_client, mode):
    register_api(hass)
    queued = []
    plant.gateways[0].async_queue_calibration = lambda *args: queued.append(args)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
        try:
            request = {"entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id, "revision": 0, "mode": mode}
            await client.send_json({"id": 1, "type": WS_START, **request})
            assert (await client.receive_json())["success"]
            event = (await client.receive_json())["event"]
            assert event["phase"] == ("confirm_automatic" if mode == "automatic" else "confirm_closed")
            await client.send_json({"id": 2, "type": WS_ACTION, "entry_id": request["entry_id"],
                                    "session_id": event["session_id"], "action": "close", "sequence": event["sequence"]})
            assert (await client.receive_json())["error"]["code"] == "calibration_step"
            await client.send_json({"id": 3, "type": WS_ACTION, "entry_id": request["entry_id"],
                                    "session_id": event["session_id"], "action": "heartbeat"})
            assert (await client.receive_json())["result"]["phase"] == ("confirm_automatic" if mode == "automatic" else "confirm_closed")
            await client.send_json({"id": 4, "type": WS_START, **request})
            assert (await client.receive_json())["error"]["code"] == "calibration_busy"
        finally:
            await client.close()
    await hass.async_block_till_done()
    assert get_store(hass, plant.entries[0].entry_id).calibration is None
    assert str(queued[-1][0]) == "*2*0*11##"


@pytest.mark.parametrize("shutdown_first", [True, False])
@pytest.mark.parametrize("calibration", ["guided", "automatic"], indirect=True)
async def test_shutdown_and_repeated_cleanup_remove_listener_only_once(hass, calibration, caplog, shutdown_first):
    cal = calibration
    await act(cal, "run" if cal.session.mode == "automatic" else "open")
    move_guard = cal.queue[0][1]
    if not shutdown_first:
        cal.session.close()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert "Unable to remove unknown job listener" not in caplog.text
    assert cal.session.reason == ("shutdown" if shutdown_first else "cancelled")
    state = cal.session.view()
    cal.connection.subscriptions[77]()  # Socket cleanup after shutdown/cancel.
    cal.session.close()  # Entity unload or another cleanup path.
    assert cal.session.view() == state
    assert "Unable to remove unknown job listener" not in caplog.text
    assert cal.session.shutdown is None
    assert cal.session.lease.cancelled()
    assert cal.session.deadline.cancelled()
    assert not move_guard()
    assert len(cal.queue) == 2  # One queued Open, one Stop; no duplicate Stop.
    assert cal.session.store.calibration is None
    assert cal.cover._calibration is None


def automatic_run(cal, direction, duration):
    assert cal.queue[-1][1]()
    cal.clock[0] += 2  # Queue wait is excluded from the measurement.
    bus(cal, "*2*1*11##" if direction == "open" else "*2*2*11##")
    cal.clock[0] += duration
    bus(cal, "*2*0*11##")


def automatic_next(cal):
    cal.clock[0] += 1
    handle = cal.session.settle
    callback = handle._callback
    handle.cancel()
    callback()


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_automatic_three_runs_review_save_provenance_and_export(hass, calibration):
    from custom_components.myhome.cover_profile_export import export_profiles

    cal = calibration
    assert cal.session.phase == "confirm_automatic" and cal.queue == []
    assert cal.session.view()["mode"] == "automatic"
    await act(cal, "run")
    automatic_run(cal, "open", 0.5)  # Initial positioning may be a partial run.
    assert cal.session.values == {} and cal.session.phase == "settling"
    automatic_next(cal)
    automatic_run(cal, "close", 22.5)
    assert cal.session.values == {"closing_time": 22.5}
    automatic_next(cal)
    automatic_run(cal, "open", 20.5)
    assert cal.session.phase == "review"
    assert cal.session.values == {"closing_time": 22.5, "opening_time": 20.5}
    assert cal.cover._travel_time_up == cal.cover._travel_time_down == 30
    assert (await export_profiles(hass, cal.session.entry_id))["profiles"] == []
    assert len(cal.queue) == 3  # Exactly open/close/open, no automatic persistence or Stop.
    with patch.object(cal.session.store.store, "async_save", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            await act(cal, "save", name="Automatic")
    assert cal.session.phase == "review" and cal.session.store.data["revision"] == 0
    await act(cal, "save", name="Automatic")
    assert cal.session.phase == "saved" and len(cal.queue) == 3
    assert (cal.cover._travel_time_up, cal.cover._travel_time_down) == (20.5, 22.5)
    result = await export_profiles(hass, cal.session.entry_id)
    assert result["format_version"] == 2
    assert all(meta["source"] == "automatic" and meta["recorded_at"]
               for meta in result["profiles"][0]["provenance"].values())


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
@pytest.mark.parametrize("duration,reason", [(59, "automatic_cutoff"), (61.5, "automatic_cutoff"),
                                             (65, "automatic_cutoff"), (181, "automatic_invalid")])
async def test_automatic_rejects_cutoff_and_excessive_runs_before_another_move(calibration, duration, reason):
    cal = calibration
    await act(cal, "run")
    automatic_run(cal, "open", duration)
    assert cal.session.phase == "interrupted" and cal.session.reason == reason
    assert cal.session.values == cal.session.provenance == {}
    assert len(cal.queue) == 2 and cal.session.stop_requested  # Open + defensive Stop.
    assert cal.session.store.data["revision"] == 0


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
@pytest.mark.parametrize("duration", [58, 66])
async def test_automatic_accepts_runs_outside_factory_cutoff_without_claiming_physical_endpoints(calibration, duration):
    cal = calibration
    await act(cal, "run")
    automatic_run(cal, "open", duration)
    automatic_next(cal)
    automatic_run(cal, "close", duration)
    automatic_next(cal)
    automatic_run(cal, "open", duration)
    assert cal.session.phase == "review"
    assert cal.session.store.data["profiles"] == {}


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_automatic_short_measured_run_discards_previous_evidence(calibration):
    cal = calibration
    await act(cal, "run")
    automatic_run(cal, "open", 5)
    automatic_next(cal)
    automatic_run(cal, "close", 20)
    automatic_next(cal)
    automatic_run(cal, "open", 0.5)
    assert cal.session.reason == "automatic_invalid"
    assert cal.session.values == cal.session.provenance == {}


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_automatic_feedback_required_echo_ignored_and_stop_timeout(calibration):
    cal = calibration
    await act(cal, "run")
    assert cal.queue[-1][1]()
    bus(cal, "*2*0*11##")  # Pre-movement echo cannot anchor or complete a run.
    assert cal.session.phase == "starting_open"
    bus(cal, "*2*1*11##")
    cal.clock[0] += 0.1
    bus(cal, "*2*0*11##")
    assert cal.session.phase == "opening" and cal.session.started_at == 100
    assert cal.session.travel_seconds == 180
    cal.session.deadline._run()
    assert cal.session.reason == "automatic_timeout" and cal.session.stop_requested


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
@pytest.mark.parametrize("action", ["stop", "cancel"])
async def test_automatic_cancel_during_settle_or_queue_never_restarts(calibration, action):
    cal = calibration
    await act(cal, "run")
    guard = cal.queue[-1][1]
    automatic_run(cal, "open", 5)
    handle = cal.session.settle
    callback = handle._callback
    await act(cal, action)
    assert handle.cancelled()
    before = len(cal.queue)
    callback()  # A late callback is harmless even after ownership was released.
    assert len(cal.queue) == before
    assert not guard()
    assert cal.session.values == {}


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
@pytest.mark.parametrize("change", ["offline", "removed", "shutdown", "queue_full", "opposite", "position"])
async def test_automatic_next_run_revalidates_and_unexpected_feedback_interrupts(hass, calibration, change):
    cal = calibration
    await act(cal, "run")
    if change == "opposite":
        assert cal.queue[-1][1]()
        bus(cal, "*2*2*11##")
    elif change == "position":
        cal.session.on_event(SimpleNamespace(current_position=50))
    else:
        automatic_run(cal, "open", 5)
        if change == "offline":
            cal.plant.gateways[0].available = False
            automatic_next(cal)
        elif change == "removed":
            with patch("custom_components.myhome.cover_calibration_automatic.target", side_effect=ProfileError("target_not_found")):
                automatic_next(cal)
        elif change == "shutdown":
            with patch.object(hass, "state", CoreState.stopping):
                automatic_next(cal)
        else:
            with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=asyncio.QueueFull):
                automatic_next(cal)
    assert cal.session.phase == "interrupted"
    assert cal.session.store.data["profiles"] == {}


@pytest.mark.parametrize("calibration", ["automatic"], indirect=True)
async def test_automatic_rejects_manual_actions_and_requires_sequence(calibration):
    cal = calibration
    for action in ("open", "close", "endpoint", "save"):
        with pytest.raises(ProfileError, match="calibration_step"):
            await act(cal, action)
    with pytest.raises(ProfileError, match="calibration_step"):
        await cal.session.action({"action": "run", "sequence": -1})
    await act(cal, "run")
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "run")
    cal.session.deadline._run()
    assert cal.session.reason == "start_timeout"
    assert not cal.queue[0][1]()


async def test_guided_session_rejects_automatic_start(calibration):
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(calibration, "run")
