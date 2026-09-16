"""Sequential real bus events, gateway ownership and all-or-nothing profile saves."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome import cover_profiles as profiles
from custom_components.myhome.cover_calibration import begin, register_api, ws_action
from custom_components.myhome.cover_calibration_batch import (
    WS_BATCH_START,
    WS_TARGETS,
    begin_batch,
    read_targets,
    ws_batch_start,
    ws_targets,
)
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


@pytest.fixture
async def batch(hass, plant):
    await profiles.write_profile(hass, message(plant))
    queue = []
    for gateway in plant.gateways:
        gateway.async_queue_calibration = lambda *args: queue.append(args)
    connection = MagicMock(subscriptions={}, user=SimpleNamespace(is_admin=True))
    request = {
        "id": 77,
        "entry_id": plant.entries[0].entry_id,
        "revision": 1,
        "entity_ids": [cover.entity_id for cover in plant.covers[:2]],
    }
    clock = [100.0]
    with patch(
        "custom_components.myhome.cover_calibration.monotonic", side_effect=lambda: clock[0]
    ):
        session = await begin_batch(hass, connection, request)
        yield SimpleNamespace(
            session=session,
            plant=plant,
            queue=queue,
            clock=clock,
            connection=connection,
            request=request,
        )
        session.close()


async def action(batch, name, **extra):
    return await batch.session.action({"action": name, "sequence": batch.session.sequence, **extra})


def advance(batch):
    handle = batch.session.settle
    callback = handle._callback
    handle.cancel()
    batch.clock[0] += 1
    callback()


def run(batch, direction, duration):
    assert batch.queue[-1][1]()
    cover = batch.session.cover
    batch.clock[0] += 2
    cover.handle_event(
        OWNMessage.parse(f"*2*{1 if direction == 'open' else 2}*{cover._full_where}##")
    )
    batch.clock[0] += duration
    cover.handle_event(OWNMessage.parse(f"*2*0*{cover._full_where}##"))


async def measure(batch):
    await action(batch, "run")
    for index in range(2):
        run(batch, "open", 5)
        advance(batch)
        run(batch, "close", 22 + index)
        advance(batch)
        run(batch, "open", 20 + index)
        if index == 0:
            assert batch.session.phase == "between_covers"
            advance(batch)


async def test_batch_sequences_selected_covers_and_saves_one_atomic_revision(hass, batch):
    from custom_components.myhome.cover_profile_export import export_profiles

    session = batch.session
    before = copy.deepcopy(session.store.data)
    assert batch.queue == []
    assert batch.plant.covers[1]._calibration is None
    await measure(batch)
    assert session.phase == "review" and session.cover_index == 1
    assert [str(item[0]) for item in batch.queue] == [
        "*2*1*11##",
        "*2*2*11##",
        "*2*1*11##",
        "*2*1*12##",
        "*2*2*12##",
        "*2*1*12##",
    ]
    assert not batch.queue[0][1]()  # Old open guard cannot revive on another cover's open phase.
    assert batch.plant.covers[0]._calibration is None
    assert session.store.data == before
    assert len((await export_profiles(hass, session.entry_id))["profiles"]) == 1
    assert session.view()["results"][0]["values"] == {"closing_time": 22, "opening_time": 20}
    with patch.object(
        session.store.store, "async_save", wraps=session.store.store.async_save
    ) as save:
        result = await action(batch, "save", names=["Kitchen", "Bedroom"])
    save.assert_awaited_once()
    assert result["revision"] == 2 and result["phase"] == "saved"
    assert len(session.store.data["profiles"]) == 3  # The old profile remains untouched.
    for old_id, old_profile in before["profiles"].items():
        assert session.store.data["profiles"][old_id] == old_profile
    for index, cover in enumerate(batch.plant.covers[:2]):
        profile = session.store.profile(cover.unique_id)
        assert profile["opening_time"] == cover._travel_time_up == 20 + index
        assert profile["closing_time"] == cover._travel_time_down == 22 + index
        assert profile["provenance"]["opening"]["origin_unique_id"] == cover.unique_id
        assert profile["provenance"]["closing"]["source"] == "automatic"
        assert cover._calibration is None
    assert len(batch.queue) == 6
    for gateway in batch.plant.gateways:
        gateway.send.assert_not_called()
    persisted = copy.deepcopy(session.store.data)
    hass.data[profiles.DATA_KEY].pop(session.entry_id)
    await profiles.bind_cover(hass, batch.plant.covers[0])
    assert profiles.get_store(hass, session.entry_id).data == persisted


@pytest.mark.parametrize(
    "failure",
    ["disk", "name", "missing_names", "revision", "capacity", "moving", "rebound", "ownership"],
)
async def test_batch_save_failure_never_partially_changes_profiles(hass, batch, failure):
    await measure(batch)
    session = batch.session
    names = ["Kitchen", "Bedroom"]
    patcher = patch.object(session.store.store, "async_save", side_effect=OSError("disk"))
    if failure == "name":
        names[1] = " "
    if failure == "missing_names":
        names.pop()
    if failure == "revision":
        session.revision -= 1
    if failure == "moving":
        batch.plant.covers[0]._attr_is_opening = True
    if failure == "rebound":
        patcher = patch(
            "custom_components.myhome.cover_calibration_batch.ready_cover", return_value=object()
        )
    if failure == "capacity":
        patcher = patch.object(profiles, "MAX_PROFILES", 2)
    if failure == "ownership":
        session.store.calibration = None
    before = copy.deepcopy(session.store.data)
    with (
        patcher
        if failure in {"disk", "capacity", "rebound"}
        else patch.object(session.connection, "send_event")
    ):
        with pytest.raises((profiles.ProfileError, OSError, vol.Invalid)):
            await action(batch, "save", names=names)
    assert session.store.data == before
    assert batch.plant.covers[0]._travel_time_up == 42.5
    assert batch.plant.covers[1]._travel_time_up == 30
    assert len(batch.queue) == 6


@pytest.mark.parametrize("cancel", ["stop", "cancel", "socket", "heartbeat", "shutdown"])
async def test_batch_cancellation_between_covers_discards_every_result_and_next_move(
    hass, batch, cancel
):
    await action(batch, "run")
    run(batch, "open", 5)
    advance(batch)
    run(batch, "close", 22)
    advance(batch)
    run(batch, "open", 20)
    assert len(batch.session.results) == 1
    pending = batch.session.settle
    callback = pending._callback
    if cancel == "socket":
        batch.connection.subscriptions[77]()
    elif cancel == "heartbeat":
        batch.session.lease._run()
    elif cancel == "shutdown":
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await hass.async_block_till_done()
    else:
        await action(batch, cancel)
    assert pending.cancelled()
    callback()
    assert batch.session.results == []
    assert batch.session.store.data["revision"] == 1
    assert all("*12##" not in str(item[0]) for item in batch.queue)
    assert batch.plant.covers[1]._calibration is None


@pytest.mark.parametrize("failure", ["cutoff", "offline", "rebound", "queue_full"])
async def test_batch_failure_stops_the_group_before_save(hass, batch, failure):
    await action(batch, "run")
    run(batch, "open", 5)
    advance(batch)
    run(batch, "close", 22)
    advance(batch)
    run(batch, "open", 20)
    if failure == "offline":
        batch.plant.gateways[0].available = False
        advance(batch)
    elif failure == "rebound":
        with patch(
            "custom_components.myhome.cover_calibration_batch.ready_cover", return_value=object()
        ):
            advance(batch)
    elif failure == "queue_full":
        with patch.object(
            batch.plant.gateways[0], "async_queue_calibration", side_effect=asyncio.QueueFull
        ):
            advance(batch)
    else:
        advance(batch)
        run(batch, "open", 61.5)
    assert batch.session.phase == "interrupted"
    assert batch.session.results == batch.session.view()["results"] == []
    assert batch.session.store.data["revision"] == 1


async def test_batch_keeps_gateway_ownership_and_rejects_other_sockets(hass, batch):
    with pytest.raises(profiles.ProfileError, match="calibration_busy"):
        await begin_batch(hass, batch.connection, batch.request)
    with pytest.raises(profiles.ProfileError, match="calibration_busy"):
        await begin(
            hass, batch.connection, {**batch.request, "entity_id": batch.plant.covers[1].entity_id}
        )
    with pytest.raises(profiles.ProfileError, match="calibration_busy"):
        await profiles.write_profile(
            hass, {**message(batch.plant, 1), "action": "assign", "profile_id": None}
        )
    outsider = MagicMock(user=SimpleNamespace(is_admin=True))
    ws_action(
        hass,
        outsider,
        {
            "id": 2,
            "entry_id": batch.session.entry_id,
            "session_id": batch.session.id,
            "action": "stop",
        },
    )
    await hass.async_block_till_done()
    assert outsider.send_error.call_args.args[1] == "calibration_expired"
    # Another gateway is independent.
    await profiles.write_profile(hass, message(batch.plant, index=2))
    assert batch.session.store.data["revision"] == 1


async def test_batch_selection_validation_before_any_motion(hass, plant):
    msg = {
        "id": 1,
        "entry_id": plant.entries[0].entry_id,
        "revision": 0,
        "entity_ids": [cover.entity_id for cover in plant.covers[:2]],
    }
    connection = MagicMock(subscriptions={})
    for ids in ([], ["x"] * 21):
        with pytest.raises(vol.Invalid):
            await begin_batch(hass, connection, {**msg, "entity_ids": ids})
    for ids, code in [
        ([msg["entity_ids"][0]] * 2, "invalid_selection"),
        ([plant.covers[2].entity_id], "target_not_found"),
        (["cover.missing"], "target_not_found"),
    ]:
        with pytest.raises(profiles.ProfileError, match=code):
            await begin_batch(hass, connection, {**msg, "entity_ids": ids})
    with pytest.raises(profiles.ProfileError, match="revision_conflict"):
        await begin_batch(hass, connection, {**msg, "revision": 1})
    with patch.object(profiles, "MAX_PROFILES", 1):
        with pytest.raises(profiles.ProfileError, match="profile_limit"):
            await begin_batch(hass, connection, msg)
    plant.covers[1]._advanced = True
    with pytest.raises(profiles.ProfileError, match="advanced_cover"):
        await begin_batch(hass, connection, msg)
    assert not connection.subscriptions
    assert profiles.get_store(hass, msg["entry_id"]).calibration is None


async def test_targets_gateway_scope_unavailable_entries_and_removed_during_load(hass, plant):
    entry_id = plant.entries[0].entry_id
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "myhome", "sensor", config_entry=plant.entries[0])
    registry.async_update_entity(plant.covers[0].entity_id, name="Kitchen")
    plant.covers[1]._advanced = True
    data = await read_targets(hass, entry_id)
    assert len(data["targets"]) == 2 and data["revision"] == 0
    assert data["targets"][0]["name"] == "Kitchen"
    assert data["targets"][1]["reason"] == "advanced_cover"
    alien = MockConfigEntry(domain="other", data={})
    alien.add_to_hass(hass)
    for bad in ("missing", alien.entry_id):
        with pytest.raises(profiles.ProfileError, match="target_not_found"):
            await read_targets(hass, bad)
    store = profiles.get_store(hass, entry_id)
    msg = {"id": 1, "entry_id": entry_id, "revision": 0, "entity_ids": [plant.covers[0].entity_id]}
    for operation in (
        lambda: read_targets(hass, entry_id),
        lambda: begin_batch(hass, MagicMock(), msg),
    ):
        with patch(
            "custom_components.myhome.cover_calibration_batch.gateway",
            side_effect=[plant.entries[0], object()],
        ):
            with pytest.raises(profiles.ProfileError, match="target_not_found"):
                await operation()
    with patch.object(store, "load", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            await read_targets(hass, entry_id)


@pytest.mark.parametrize("handler", [ws_targets, ws_batch_start])
@pytest.mark.parametrize("user", [None, SimpleNamespace(is_admin=False)])
async def test_batch_apis_require_admin(hass, handler, user):
    with pytest.raises(Unauthorized):
        handler(hass, MagicMock(user=user), {})
    assert profiles.DATA_KEY not in hass.data


async def test_batch_websocket_start_action_and_disconnect(hass, plant, hass_ws_client):
    register_api(hass)
    queue = []
    plant.gateways[0].async_queue_calibration = lambda *args: queue.append(args)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
        try:
            await client.send_json(
                {"id": 1, "type": WS_TARGETS, "entry_id": plant.entries[0].entry_id}
            )
            assert len((await client.receive_json())["result"]["targets"]) == 2
            msg = {
                "type": WS_BATCH_START,
                "entry_id": plant.entries[0].entry_id,
                "entity_ids": [c.entity_id for c in plant.covers[:2]],
                "revision": 0,
            }
            await client.send_json({"id": 2, **msg, "entity_ids": ["cover.missing"]})
            assert (await client.receive_json())["error"]["code"] == "target_not_found"
            await client.send_json({"id": 3, **msg})
            assert (await client.receive_json())["success"]
            state = (await client.receive_json())["event"]
            assert state["batch"] and state["phase"] == "confirm_automatic"
            assert queue == []
            await client.send_json(
                {
                    "id": 4,
                    "type": "myhome/cover_calibration/action",
                    "entry_id": msg["entry_id"],
                    "session_id": state["session_id"],
                    "sequence": state["sequence"],
                    "action": "run",
                }
            )
            assert (await client.receive_json())["event"]["phase"] == "starting_open"
            assert (await client.receive_json())["result"]["phase"] == "starting_open"
        finally:
            await client.close()
    await hass.async_block_till_done()
    assert not queue[0][1]()
    assert profiles.get_store(hass, plant.entries[0].entry_id).calibration is None
