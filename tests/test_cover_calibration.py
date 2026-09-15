"""Guided measurement: real cover events, persistence and connection ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
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
async def calibration(hass, plant):
    queue = []
    for gateway in plant.gateways:
        gateway.async_queue_calibration = lambda message, guard, lock: queue.append((message, guard, lock))
    connection = MagicMock(subscriptions={}, user=SimpleNamespace(is_admin=True))
    request = {"id": 77, "entry_id": plant.entries[0].entry_id,
               "entity_id": plant.records[0].entity_id, "revision": 0}
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
    assert cal.cover._travel_time == cal.cover._closing_time == 30
    assert cal.session.store.data["revision"] == 0
    assert cal.session.phase == "review"
    result = await act(cal, "save", name="Measured bedroom")
    assert result["phase"] == "saved"
    assert cal.session.store.data["revision"] == 1
    assert cal.cover._travel_time == 20.5
    assert cal.cover._closing_time == 40.5
    assert cal.session.store.calibration is None
    assert cal.cover._calibration is None
    assert len(cal.queue) == 4  # Open, Stop, Close, Stop; Save sends nothing.
    cal.plant.gateways[0].send.assert_not_called()


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


async def test_queued_movement_invalid_after_disconnect_and_stop_has_expiry(calibration):
    cal = calibration
    await act(cal, "open")
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


async def test_websocket_session_subscription_actions_and_disconnect(hass, plant, hass_ws_client):
    register_api(hass)
    queued = []
    plant.gateways[0].async_queue_calibration = lambda *args: queued.append(args)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
        try:
            request = {"entry_id": plant.entries[0].entry_id, "entity_id": plant.records[0].entity_id, "revision": 0}
            await client.send_json({"id": 1, "type": WS_START, **request})
            assert (await client.receive_json())["success"]
            event = (await client.receive_json())["event"]
            assert event["phase"] == "confirm_closed"
            await client.send_json({"id": 2, "type": WS_ACTION, "entry_id": request["entry_id"],
                                    "session_id": event["session_id"], "action": "close", "sequence": event["sequence"]})
            assert (await client.receive_json())["error"]["code"] == "calibration_step"
            await client.send_json({"id": 3, "type": WS_ACTION, "entry_id": request["entry_id"],
                                    "session_id": event["session_id"], "action": "heartbeat"})
            assert (await client.receive_json())["result"]["phase"] == "confirm_closed"
            await client.send_json({"id": 4, "type": WS_START, **request})
            assert (await client.receive_json())["error"]["code"] == "calibration_busy"
        finally:
            await client.close()
    await hass.async_block_till_done()
    assert get_store(hass, plant.entries[0].entry_id).calibration is None
    assert str(queued[-1][0]) == "*2*0*11##"
