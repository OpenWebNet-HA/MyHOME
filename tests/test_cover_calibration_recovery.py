"""Recover safe checkpoints without resurrecting motion or an old controller."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.resolver import ThreadedResolver
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import Unauthorized
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_calibration import (
    WS_ACTION,
    WS_START,
    begin,
    register_api,
    ws_action,
)
from custom_components.myhome.cover_calibration_batch import begin_batch
from custom_components.myhome.cover_calibration_recovery import WS_RESUME, ws_resume
from custom_components.myhome.cover_profiles import (
    ProfileError,
    get_store,
    read_profile,
    remove_entry,
)
from tests.test_cover_calibration_batch import batch as batch_fixture
from tests.test_cover_calibration_batch import measure
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_panel_cover_calibration import act, bus, measured
from tests.test_panel_cover_calibration import calibration as calibration_fixture

plant = plant_fixture
calibration = calibration_fixture
batch = batch_fixture


@pytest.fixture
async def recovering(hass, calibration):
    cal = calibration
    cal.session.close()
    cal.queue.clear()
    cal.request["client_id"] = "first-controller"
    cal.session = await begin(hass, cal.connection, cal.request)
    yield cal
    cal.session.close()


def disconnect(cal):
    cal.connection.subscriptions[77]()


def fire(handle):
    callback, args = handle._callback, handle._args
    handle.cancel()
    callback(*args)


@pytest.mark.parametrize("checkpoint", ["initial", "half", "review"])
async def test_recovery_preserves_safe_checkpoint_and_evidence_without_commands(hass, recovering, checkpoint):
    cal, session = recovering, recovering.session
    if checkpoint == "review":
        await measured(cal)
    elif checkpoint == "half":
        await act(cal, "open")
        assert cal.queue[-1][1]()
        bus(cal, "*2*1*11##")
        cal.clock[0] += 22
        await act(cal, "endpoint")
    phase, values, evidence = session.phase, dict(session.values), copy.deepcopy(session.provenance)
    before = len(cal.queue)
    old_cleanup = cal.connection.subscriptions[77]
    old_token = session.attachment
    disconnect(cal)
    retention = session.retention
    assert session.store.calibration is session and not session.listener
    assert len(cal.queue) == before
    view = await read_profile(hass, session.entry_id, cal.cover.entity_id)
    assert view["calibration"]["session_id"] == session.id
    assert not view["calibration"]["attached"]
    assert "attachment" not in view["calibration"]
    # Same HA websocket, new editor: old unsubscribe/action must be powerless.
    session.attach(cal.connection, 88, "second-controller")
    old_cleanup()
    assert session.listener and retention.cancelled()
    assert session.attachment != old_token
    assert (session.phase, session.values, session.provenance) == (phase, values, evidence)
    assert len(cal.queue) == before
    for action in ("heartbeat", "stop", "cancel", "detach"):
        ws_action(hass, cal.connection, {"id": 9, "entry_id": session.entry_id,
            "session_id": session.id, "attachment": old_token, "action": action})
        await hass.async_block_till_done()
        assert cal.connection.send_error.call_args.args[1] == "calibration_expired"
    assert session.listener and len(cal.queue) == before
    if checkpoint == "review":
        await act(cal, "save", name="Recovered")
        assert session.store.data["revision"] == 1


@pytest.mark.parametrize("phase", ["starting_open", "opening", "closing", "settling", "between_covers"])
async def test_detaching_during_cycle_discards_measurement_and_invalidates_queued_motion(recovering, phase):
    cal, session = recovering, recovering.session
    await act(cal, "open")
    guard = cal.queue[-1][1]
    session.phase = phase
    session.values["opening_time"] = 22
    session.settle = session.hass.loop.call_later(100, lambda: None)
    disconnect(cal)
    assert session.phase == "interrupted" and session.values == session.provenance == {}
    assert session.settle.cancelled() and session.deadline.cancelled()
    assert not guard()
    assert str(cal.queue[-1][0]) == "*2*0*11##"
    count = len(cal.queue)
    session.attach(cal.connection, 78, "recover")
    assert len(cal.queue) == count
    with pytest.raises(ProfileError, match="calibration_step"):
        await act(cal, "save", name="Cannot save interrupted")


async def test_heartbeat_detaches_once_and_retention_expires_without_motion(recovering):
    cal, session = recovering, recovering.session
    fire(session.lease)
    assert not session.listener and session.phase == "confirm_closed"
    assert cal.connection.send_event.call_args.args[1]["attached"] is False
    retention = session.retention
    disconnect(cal)
    assert session.retention is retention  # Stale cleanup cannot extend recovery.
    fire(retention)
    assert session.store.calibration is None and cal.cover._calibration is None
    assert session.closed and cal.queue == []
    with pytest.raises(ProfileError, match="calibration_expired"):
        session.attach(cal.connection, 90, "too-late")


@pytest.mark.parametrize("cleanup", ["cancel", "unload", "shutdown", "remove"])
async def test_detached_session_releases_gateway_on_explicit_lifecycle_end(hass, recovering, cleanup):
    cal, session = recovering, recovering.session
    disconnect(cal)
    retention = session.retention
    if cleanup == "cancel":
        session.attach(cal.connection, 80, "cancel")
        await act(cal, "cancel")
    elif cleanup == "unload":
        with patch("custom_components.myhome.myhome_device.MyHOMEEntity.async_will_remove_from_hass", new=AsyncMock()):
            await cal.cover.async_will_remove_from_hass()
    elif cleanup == "remove":
        await remove_entry(hass, session.entry_id)
    else:
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    session.close()
    assert session.closed and retention.cancelled()
    assert session.store.calibration is None and cal.cover._calibration is None


async def test_detached_review_save_failure_remains_recoverable(hass, recovering):
    cal, session = recovering, recovering.session
    await measured(cal)
    values = dict(session.values)
    async def fail(_data):
        disconnect(cal)
        raise OSError("disk")
    with patch.object(session.store.store, "async_save", side_effect=fail):
        with pytest.raises(OSError):
            await act(cal, "save", name="Retry")
    assert session.phase == "review" and session.values == values and not session.listener
    session.attach(cal.connection, 80, "retry")
    await act(cal, "save", name="Retry")
    assert session.store.data["revision"] == 1


async def test_accepted_save_completes_after_detach(recovering):
    cal, session = recovering, recovering.session
    await measured(cal)
    original = session.store.store.async_save
    async def save(data):
        disconnect(cal)
        await original(data)
    with patch.object(session.store.store, "async_save", side_effect=save):
        await act(cal, "save", name="Accepted")
    assert session.closed and session.phase == "saved" and session.retention.cancelled()
    assert session.store.data["revision"] == 1 and session.store.calibration is None


async def test_replayed_start_recovers_only_its_detached_session(hass, recovering):
    cal, session = recovering, recovering.session
    with pytest.raises(ProfileError, match="calibration_busy"):
        await begin(hass, cal.connection, cal.request)
    disconnect(cal)
    with pytest.raises(ProfileError, match="calibration_busy"):
        await begin(hass, cal.connection, {**cal.request, "client_id": "other"})
    assert await begin(hass, cal.connection, cal.request) is session
    assert cal.queue == [] and session.listener


async def test_batch_review_survives_recovery_and_replay(hass, batch):
    batch.session.close()
    batch.queue.clear()
    batch.request["client_id"] = "batch-controller"
    batch.session = await begin_batch(hass, batch.connection, batch.request)
    await measure(batch)
    results = copy.deepcopy(batch.session.results)
    disconnect(batch)
    assert await begin_batch(hass, batch.connection, batch.request) is batch.session
    assert batch.session.results == results
    assert len(batch.queue) == 6
    await batch.session.action({"action": "save", "sequence": batch.session.sequence, "names": ["One", "Two"]})
    assert batch.session.store.data["revision"] == 2


async def test_resume_endpoint_rejects_missing_stale_legacy_and_live_sessions(hass, recovering):
    cal, session = recovering, recovering.session
    request = {"id": 90, "entry_id": session.entry_id, "session_id": session.id, "client_id": "resume"}
    for changes in ({"entry_id": "missing"}, {"session_id": "stale"}, {}):
        ws_resume(hass, cal.connection, {**request, **changes})
        await hass.async_block_till_done()
        assert cal.connection.send_error.call_args.args[1] == ("calibration_busy" if not changes else "calibration_expired")
    session.client_id = None
    with pytest.raises(ProfileError, match="calibration_expired"):
        session.attach(cal.connection, 90, "legacy")
    session.close()
    ws_resume(hass, cal.connection, request)
    await hass.async_block_till_done()
    assert cal.connection.send_error.call_args.args[1] == "calibration_expired"


def test_resume_requires_admin(hass):
    with pytest.raises(Unauthorized):
        ws_resume(hass, MagicMock(user=SimpleNamespace(is_admin=False)), {"id": 1})


async def test_real_websocket_disconnect_resume_and_attachment_authorization(hass, plant, hass_ws_client):
    register_api(hass)
    queue = []
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    entry_id = plant.entries[0].entry_id
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        first = await hass_ws_client(hass)
        await first.send_json({"id": 1, "type": WS_START, "entry_id": entry_id,
            "entity_id": plant.records[0].entity_id, "revision": 0, "client_id": "browser-one"})
        assert (await first.receive_json())["success"]
        state = (await first.receive_json())["event"]
        await first.close()
        await hass.async_block_till_done()
        session = get_store(hass, entry_id).calibration
        assert session and not session.listener
        second = await hass_ws_client(hass)
        await second.send_json({"id": 1, "type": WS_RESUME, "entry_id": entry_id,
            "session_id": state["session_id"], "client_id": "browser-two"})
        assert (await second.receive_json())["success"]
        resumed = (await second.receive_json())["event"]
        assert resumed["phase"] == state["phase"] and resumed["attachment"] != state["attachment"]
        action = {"type": WS_ACTION, "entry_id": entry_id, "session_id": session.id, "action": "heartbeat"}
        await second.send_json({"id": 2, **action})
        assert (await second.receive_json())["error"]["code"] == "calibration_expired"
        await second.send_json({"id": 3, **action, "attachment": resumed["attachment"]})
        assert (await second.receive_json())["success"]
        await second.send_json({"id": 4, **action, "action": "detach", "attachment": resumed["attachment"]})
        assert not (await second.receive_json())["event"]["attached"]
        assert (await second.receive_json())["success"]
        await second.close()
        assert queue == []
        session.close()


async def test_close_releases_all_ownership_even_if_stop_queue_unexpectedly_fails(recovering):
    cal, session = recovering, recovering.session
    with patch.object(cal.cover._gateway_handler, "async_queue_calibration", side_effect=RuntimeError("broken queue")):
        with pytest.raises(RuntimeError, match="broken queue"):
            session.close()
    assert session.closed and not session.listener and session.lease.cancelled()
    assert session.store.calibration is None and cal.cover._calibration is None
