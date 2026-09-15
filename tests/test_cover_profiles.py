"""Profile persistence, gateway isolation and the real timed-cover runtime contract."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_socket import socket_enabled  # noqa: F401 (socket plugin disabled in pyproject)

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    WS_READ,
    WS_WRITE,
    ProfileError,
    bind_cover,
    get_store,
    read_profile,
    register_api,
    remove_entry,
    travel_time,
    write_profile,
    ws_read,
    ws_write,
)


@pytest.fixture
async def plant(hass):
    entries, covers, records, gateways = [], [], [], []
    registry = er.async_get(hass)
    for index in range(2):
        mac = f"00:03:50:00:00:0{index + 1}"
        entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac}, state=ConfigEntryState.LOADED)
        entry.add_to_hass(hass)
        entries.append(entry)
        gateway = SimpleNamespace(mac=mac, unique_id=mac, available=True,
                                  device_registry_id=None, send=AsyncMock(), log_id="test")
        gateways.append(gateway)
        for address in (["11", "12"] if index == 0 else ["11"]):
            cover = MyHOMECover(hass, "Test cover", "Test cover", address, "2", address,
                                None, False, "BTicino", "Standard", gateway, travel_time=30)
            record = registry.async_get_or_create("cover", DOMAIN, cover.unique_id, config_entry=entry)
            cover.entity_id = record.entity_id
            cover.hass = hass
            cover.async_write_ha_state = MagicMock()
            cover.async_schedule_update_ha_state = MagicMock()
            cover.async_on_remove = MagicMock()
            await bind_cover(hass, cover)
            covers.append(cover)
            records.append(record)
    return SimpleNamespace(entries=entries, covers=covers, records=records, gateways=gateways)


def message(plant, revision=0, index=0, **extra):
    return {"entry_id": plant.records[index].config_entry_id,
            "entity_id": plant.records[index].entity_id,
            "revision": revision, "action": "save",
            "profile": {"name": "Living room", "travel_time": 42.5}, **extra}


async def test_profile_create_share_copy_reset_and_restart(hass, plant):
    first = await write_profile(hass, message(plant))
    profile_id = first["assigned_profile_id"]
    assert first["effective_travel_time"] == 42.5
    assert first["default_travel_time"] == 30
    assert plant.covers[0].extra_state_attributes["cover_profile"] == "Living room"
    second = await write_profile(hass, message(plant, 1, index=1, action="assign", profile_id=profile_id))
    assert second["profiles"][0]["uses"] == 2
    with pytest.raises(ProfileError, match="profile_shared"):
        await write_profile(hass, message(plant, 2, profile_id=profile_id))
    copied = await write_profile(hass, message(plant, 2, profile={"name": "My copy", "travel_time": 25}))
    assert copied["assigned_profile_id"] != profile_id
    assert plant.covers[1]._travel_time == 42.5
    updated = await write_profile(hass, message(plant, 3, profile_id=copied["assigned_profile_id"],
                                               profile={"name": "Edited", "travel_time": 26}))
    assert updated["effective_travel_time"] == 26
    reset = await write_profile(hass, message(plant, 4, action="assign", profile_id=None))
    assert reset["assigned_profile_id"] is None
    assert plant.covers[0]._travel_time == 30
    entry_id = plant.entries[0].entry_id
    saved = copy.deepcopy(get_store(hass, entry_id).data)
    hass.data[DATA_KEY].pop(entry_id)
    await bind_cover(hass, plant.covers[1])
    assert get_store(hass, entry_id).data == saved
    assert plant.covers[1]._travel_time == 42.5
    assert get_store(hass, plant.entries[1].entry_id).data["revision"] == 0
    for gateway in plant.gateways:
        gateway.send.assert_not_called()


async def test_revision_conflict_is_atomic_and_registry_rename_retains_assignment(hass, plant):
    results = await asyncio.gather(*(write_profile(hass, message(plant)) for _ in range(2)),
                                   return_exceptions=True)
    assert len([result for result in results if isinstance(result, ProfileError)]) == 1
    store = get_store(hass, plant.entries[0].entry_id)
    assert store.data["revision"] == 1
    assert len(store.data["profiles"]) == 1
    renamed = er.async_get(hass).async_update_entity(plant.records[0].entity_id,
                                                   new_entity_id="cover.renamed")
    state = await read_profile(hass, renamed.config_entry_id, renamed.entity_id)
    assert state["assigned_profile_id"] == next(iter(store.data["profiles"]))
    with pytest.raises(ProfileError, match="target_not_found"):
        await write_profile(hass, message(plant, 1))


async def test_write_failure_does_not_publish_revision_or_runtime_changes(hass, plant):
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    # Exercise the real Store wrapper, including HA's normally swallowed WriteError.
    with patch.object(Store, "_async_write_data", side_effect=WriteError("disk full")):
        with pytest.raises(OSError):
            await write_profile(hass, message(plant))
    assert store.data == before
    assert plant.covers[0]._travel_time == 30
    result = await write_profile(hass, message(plant))
    assert result["revision"] == 1


async def test_moving_cover_keeps_original_time_until_stop_and_reset_can_be_pending(hass, plant):
    cover = plant.covers[0]
    cover._attr_current_cover_position = 0
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        await cover.async_open_cover()
    with patch("custom_components.myhome.cover.time.monotonic", return_value=115):
        result = await write_profile(hass, message(plant))
        assert result["pending"] is True
        assert result["effective_travel_time"] == 30
        assert cover.current_cover_position == 50
        # Continued movement events must not apply the new travel model mid-run.
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
        assert cover._travel_time == 30
        cover.handle_event(OWNMessage.parse("*2*0*11##"))
        assert cover.current_cover_position == 50
    assert cover._travel_time == 42.5
    assert cover.extra_state_attributes["cover_profile_pending"] is False
    await cover.async_close_cover()
    await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
    assert cover._travel_time == 42.5
    await cover.async_stop_cover()
    assert cover._travel_time == 30
    assert cover._pending_profile is None
    # Only the explicit movement commands reached the gateway, never a profile write.
    assert plant.gateways[0].send.await_count == 3


async def test_movement_starting_during_storage_write_is_deferred(hass, plant):
    store = get_store(hass, plant.entries[0].entry_id)
    original = store.store.async_save
    async def save(data):
        plant.covers[0].handle_event(OWNMessage.parse("*2*2*11##"))
        await original(data)
    with patch.object(store.store, "async_save", side_effect=save):
        result = await write_profile(hass, message(plant))
    assert result["pending"]
    assert plant.covers[0]._travel_time == 30
    plant.gateways[0].send.assert_not_called()


async def test_lifecycle_unload_during_write_and_storage_removal(hass, plant):
    store = get_store(hass, plant.entries[0].entry_id)
    original = store.store.async_save
    unbind = plant.covers[0].async_on_remove.call_args.args[0]
    async def save(data):
        unbind()
        await original(data)
    with patch.object(store.store, "async_save", side_effect=save):
        result = await write_profile(hass, message(plant))
    assert result["writable"] is False
    assert result["effective_travel_time"] is None
    assert plant.covers[0]._travel_time == 30
    await bind_cover(hass, plant.covers[0])
    assert plant.covers[0]._travel_time == 42.5
    # A late unload callback must not remove a replacement entity instance.
    replacement = MagicMock()
    store.covers[plant.records[0].unique_id] = replacement
    unbind()
    assert store.covers[plant.records[0].unique_id] is replacement
    await remove_entry(hass, plant.entries[0].entry_id)
    assert plant.entries[0].entry_id not in hass.data[DATA_KEY]
    assert await get_store(hass, plant.entries[0].entry_id).store.async_load() is None
    assert plant.entries[1].entry_id in hass.data[DATA_KEY]


async def test_reject_foreign_gateway_entities_unknown_profiles_and_unavailable_covers(hass, plant):
    first = await write_profile(hass, message(plant))
    with pytest.raises(ProfileError, match="target_not_found"):
        await read_profile(hass, plant.entries[1].entry_id, plant.records[0].entity_id)
    with pytest.raises(ProfileError, match="profile_not_found"):
        await write_profile(hass, message(plant, 0, index=2, action="assign",
                                         profile_id=first["assigned_profile_id"]))
    with pytest.raises(ProfileError, match="target_not_found"):
        await read_profile(hass, "missing", plant.records[0].entity_id)
    registry = er.async_get(hass)
    registry.async_update_entity(plant.records[0].entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    with pytest.raises(ProfileError, match="cover_unavailable"):
        await write_profile(hass, message(plant, 1))
    registry.async_update_entity(plant.records[0].entity_id, disabled_by=None)
    plant.gateways[0].available = False
    with pytest.raises(ProfileError, match="cover_unavailable"):
        await write_profile(hass, message(plant, 1))
    plant.gateways[0].available = True
    plant.covers[0]._advanced = True
    state = await read_profile(hass, plant.entries[0].entry_id, plant.records[0].entity_id)
    assert state["reason"] == "advanced_cover"
    with pytest.raises(ProfileError, match="advanced_cover"):
        await write_profile(hass, message(plant, 1))
    await bind_cover(hass, plant.covers[0])  # Advanced runtime does not resolve timed profiles.
    plant.covers[0]._advanced = False
    with patch.object(hass, "state", CoreState.stopping):
        with pytest.raises(ProfileError, match="cover_unavailable"):
            await write_profile(hass, message(plant, 1))
    with patch("custom_components.myhome.cover_profiles.MAX_PROFILES", 1):
        with pytest.raises(ProfileError, match="profile_limit"):
            await write_profile(hass, message(plant, 1))


@pytest.mark.parametrize("value", [0, 601, float("nan"), float("inf"), True, "30"])
def test_invalid_times_cannot_reach_runtime(value):
    with pytest.raises(vol.Invalid):
        travel_time(value)


@pytest.mark.parametrize("handler", [ws_read, ws_write])
@pytest.mark.parametrize("user", [None, SimpleNamespace(is_admin=False)])
async def test_api_requires_admin_before_storage_access(hass, handler, user):
    with pytest.raises(Unauthorized):
        handler(hass, MagicMock(user=user), {"id": 1})
    assert DATA_KEY not in hass.data


async def test_websocket_round_trip_validation_and_errors(hass, plant, hass_ws_client):
    register_api(hass)
    store = get_store(hass, plant.entries[0].entry_id)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
        try:
            async def send(data):
                await client.send_json(data)
                return await client.receive_json()
            result = await send({"id": 1, "type": WS_READ, **{key: value for key, value in message(plant).items()
                                                            if key in ("entry_id", "entity_id")}})
            assert result["result"]["revision"] == 0
            result = await send({"id": 2, "type": WS_WRITE, **message(plant)})
            assert result["result"]["effective_travel_time"] == 42.5
            result = await send({"id": 3, "type": WS_WRITE, **message(plant)})
            assert result["error"]["code"] == "revision_conflict"
            with patch.object(store.store, "async_save", side_effect=OSError("disk")):
                result = await send({"id": 4, "type": WS_WRITE, **message(plant, 1)})
            assert result["error"]["code"] == "storage_error"
            with patch("custom_components.myhome.cover_profiles.PROFILE", side_effect=vol.Invalid("bad")):
                result = await send({"id": 5, "type": WS_WRITE, **message(plant, 1)})
            assert result["error"]["code"] == "invalid_profile"
            msg = message(plant, 1)
            del msg["profile"]
            result = await send({"id": 6, "type": WS_WRITE, **msg})
            assert result["error"]["code"] == "invalid_profile"
        finally:
            await client.close()


async def test_profile_write_preserves_an_already_scheduled_position_stop(hass, plant):
    cover = plant.covers[0]
    cover._attr_current_cover_position = 0
    release = asyncio.Event()
    scheduled = asyncio.Event()
    durations = []

    async def wait_for_stop(seconds):
        durations.append(seconds)
        scheduled.set()
        await release.wait()

    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=wait_for_stop):
        await cover.async_set_cover_position(position=50)
        stop_task = cover._stop_task
        await scheduled.wait()
        await write_profile(hass, message(plant))
        assert cover._stop_task is stop_task
        assert durations == [15.0]  # Half of the original 30-second full travel.
        release.set()
        await stop_task
    assert cover.current_cover_position == 50
    assert cover._travel_time == 42.5
    assert cover._pending_profile is None
    assert plant.gateways[0].send.await_count == 2


async def test_registered_cover_startup_resolves_profile_before_status_request(hass, plant):
    await write_profile(hass, message(plant))
    entry_id = plant.entries[0].entry_id
    hass.data[DATA_KEY].pop(entry_id)
    cover = plant.covers[0]
    cover._travel_time = 30
    gateway = plant.gateways[0]
    gateway.availability_signal = "test_profile_availability"

    async def request_status(command):
        assert cover._travel_time == 42.5
        assert command is not None

    gateway.send_status_request = AsyncMock(side_effect=request_status)
    await cover.async_added_to_hass()
    gateway.send_status_request.assert_awaited_once()
    assert get_store(hass, entry_id).covers[cover.unique_id] is cover
