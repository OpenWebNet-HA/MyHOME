"""Profile persistence, gateway isolation and the real timed-cover runtime contract."""
import asyncio
import copy
import json
from datetime import UTC, datetime
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
from custom_components.myhome.cover_profile_export import WS_EXPORT, export_profiles, ws_export
from custom_components.myhome.cover_profile_provenance import unknown_provenance, utc_timestamp
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    PROFILE,
    WS_READ,
    WS_SUBSCRIBE,
    WS_WRITE,
    ProfileError,
    ProfileStorage,
    bind_cover,
    get_store,
    read_profile,
    register_api,
    remove_entry,
    travel_time,
    write_profile,
    ws_read,
    ws_subscribe,
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


async def test_provenance_survives_rename_assignment_copy_and_directional_edits(hass, plant):
    clock = "custom_components.myhome.cover_profile_provenance.dt_util.utcnow"
    with patch(clock, return_value=datetime(2026, 9, 15, 10, tzinfo=UTC)):
        first = await write_profile(hass, message(plant))
    original = copy.deepcopy(first["profiles"][0]["provenance"])
    profile_id = first["assigned_profile_id"]
    assert original["opening"]["source"] == "manual"
    assert original["opening"]["recorded_at"] == "2026-09-15T10:00:00+00:00"
    assert original["opening"]["inherited"] is False
    assert "origin_unique_id" not in original["opening"]
    with patch(clock, return_value=datetime(2026, 9, 16, 10, tzinfo=UTC)):
        renamed = await write_profile(hass, message(plant, 1, profile_id=profile_id,
                                      profile={"name": "Renamed", "travel_time": 42.5}))
        assert renamed["profiles"][0]["provenance"] == original
        edited = await write_profile(hass, message(plant, 2, profile_id=profile_id,
                                     profile={"name": "Renamed", "opening_time": 42.5, "closing_time": 50}))
    changed = edited["profiles"][0]["provenance"]
    assert changed["opening"] == original["opening"]
    assert changed["closing"]["recorded_at"] == "2026-09-16T10:00:00+00:00"
    shared = await write_profile(hass, message(plant, 3, index=1, action="assign", profile_id=profile_id))
    assert shared["profiles"][0]["provenance"]["opening"]["inherited"] is True
    copied = await write_profile(hass, message(plant, 4, index=1, copy_from_profile_id=profile_id,
                                 profile={"name": "Copy", "opening_time": 42.5, "closing_time": 55}))
    copied_profile = next(p for p in copied["profiles"] if p["id"] == copied["assigned_profile_id"])
    assert copied_profile["provenance"]["opening"]["inherited"] is True
    assert copied_profile["provenance"]["opening"]["recorded_at"] == original["opening"]["recorded_at"]
    assert copied_profile["provenance"]["closing"]["inherited"] is False
    assert plant.covers[0]._travel_time_down == 50
    store = get_store(hass, plant.entries[0].entry_id)
    persisted = copy.deepcopy(store.data)
    hass.data[DATA_KEY].pop(plant.entries[0].entry_id)
    await bind_cover(hass, plant.covers[1])
    assert get_store(hass, plant.entries[0].entry_id).data == persisted
    registry = er.async_get(hass)
    registry.async_update_entity(plant.records[0].entity_id, name="Kitchen", new_entity_id="cover.kitchen")
    after = await read_profile(hass, plant.entries[0].entry_id, plant.covers[1].entity_id)
    origin = next(p for p in after["profiles"] if p["id"] == copied["assigned_profile_id"])["provenance"]["opening"]
    assert origin["origin_name"] == "Kitchen"
    assert origin["origin_entity_id"] == "cover.kitchen"
    registry.async_remove("cover.kitchen")
    missing = await read_profile(hass, plant.entries[0].entry_id, plant.covers[1].entity_id)
    origin = next(p for p in missing["profiles"] if p["id"] == copied["assigned_profile_id"])["provenance"]["opening"]
    assert origin["origin_entity_id"] is None and origin["inherited"] is True


async def test_version_two_profiles_gain_unknown_evidence_without_changing_values(hass, plant):
    entry_id = plant.entries[0].entry_id
    saved = {"revision": 12, "profiles": {"old": {"name": "Existing", "opening_time": 21, "closing_time": 34}},
             "assignments": {plant.records[0].unique_id: "old"}}
    await Store(hass, 2, f"myhome.cover_profiles.{entry_id}").async_save(saved)
    hass.data[DATA_KEY].pop(entry_id)
    await bind_cover(hass, plant.covers[0])
    store = get_store(hass, entry_id)
    assert store.data["revision"] == 12
    assert store.data["assignments"] == saved["assignments"]
    assert store.data["profiles"]["old"]["provenance"] == unknown_provenance()
    assert (plant.covers[0]._travel_time_up, plant.covers[0]._travel_time_down) == (21, 34)
    await write_profile(hass, message(plant, 12, profile_id="old",
                        profile={"name": "Renamed", "opening_time": 21, "closing_time": 34}))
    assert store.data["profiles"]["old"]["provenance"] == unknown_provenance()


async def test_provenance_cannot_be_supplied_by_client_or_copied_across_gateways(hass, plant):
    with pytest.raises(vol.Invalid):
        await write_profile(hass, message(plant, profile={"name": "Forged", "travel_time": 20,
                            "provenance": unknown_provenance()}))
    first = await write_profile(hass, message(plant))
    profile_id = first["assigned_profile_id"]
    with pytest.raises(ProfileError, match="profile_not_found"):
        await write_profile(hass, message(plant, index=2, copy_from_profile_id=profile_id))
    for extra in ({"profile_id": profile_id}, {"action": "assign"}):
        with pytest.raises(ProfileError, match="invalid_profile"):
            await write_profile(hass, message(plant, 1, copy_from_profile_id=profile_id, **extra))
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    with patch.object(Store, "_async_write_data", side_effect=WriteError("disk full")):
        with pytest.raises(OSError):
            await write_profile(hass, message(plant, 1, profile_id=profile_id,
                                profile={"name": "Failed", "travel_time": 99}))
    assert store.data == before and plant.covers[0]._travel_time_up == 42.5


@pytest.mark.parametrize("value", [None, "invalid", "2026-09-15T12:00:00", "2026-09-15T12:00:00+02:00"])
def test_invalid_provenance_dates_are_rejected(value):
    with pytest.raises(vol.Invalid):
        utc_timestamp(value)


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
    assert plant.covers[1]._travel_time_up == 42.5
    updated = await write_profile(hass, message(plant, 3, profile_id=copied["assigned_profile_id"],
                                               profile={"name": "Edited", "travel_time": 26}))
    assert updated["effective_travel_time"] == 26
    reset = await write_profile(hass, message(plant, 4, action="assign", profile_id=None))
    assert reset["assigned_profile_id"] is None
    assert plant.covers[0]._travel_time_up == 30
    entry_id = plant.entries[0].entry_id
    saved = copy.deepcopy(get_store(hass, entry_id).data)
    hass.data[DATA_KEY].pop(entry_id)
    await bind_cover(hass, plant.covers[1])
    assert get_store(hass, entry_id).data == saved
    assert plant.covers[1]._travel_time_up == 42.5
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
    assert plant.covers[0]._travel_time_up == 30
    result = await write_profile(hass, message(plant))
    assert result["revision"] == 1


async def test_moving_cover_keeps_original_time_until_stop_and_reset_can_be_pending(hass, plant):
    cover = plant.covers[0]
    cover._attr_current_cover_position = 0
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        await cover.async_open_cover()
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
    with patch("custom_components.myhome.cover.time.monotonic", return_value=115):
        result = await write_profile(hass, message(plant))
        assert result["pending"] is True
        assert result["effective_travel_time"] == 30
        assert cover.current_cover_position == 50
        # Continued movement events must not apply the new travel model mid-run.
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
        assert cover._travel_time_up == 30
        cover.handle_event(OWNMessage.parse("*2*0*11##"))
        assert cover.current_cover_position == 50
    assert cover._travel_time_up == 42.5
    assert cover.extra_state_attributes["cover_profile_pending"] is False
    await cover.async_close_cover()
    await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
    assert cover._travel_time_up == 42.5
    await cover.async_stop_cover()
    assert cover._travel_time_up == 30
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
    assert plant.covers[0]._travel_time_up == 30
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
    assert plant.covers[0]._travel_time_up == 30
    await bind_cover(hass, plant.covers[0])
    assert plant.covers[0]._travel_time_up == 42.5
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


@pytest.mark.parametrize("handler", [ws_read, ws_write, ws_subscribe, ws_export])
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
            profile_id = result["result"]["assigned_profile_id"]
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
            result = await send({"id": 7, "type": WS_WRITE, **message(plant, 1, action="delete", profile_id=profile_id)})
            assert result["error"]["code"] == "profile_in_use"
            result = await send({"id": 8, "type": WS_WRITE, **message(plant, 1, action="assign", profile_id=None)})
            assert result["success"]
            result = await send({"id": 9, "type": WS_WRITE, **message(plant, 2, action="delete", profile_id=profile_id)})
            assert result["result"]["profiles"] == []
            result = await send({"id": 10, "type": WS_WRITE, **message(plant, 3, profile={
                "name": "Directional", "opening_time": 22.5, "closing_time": 44.5})})
            assert result["result"]["effective_opening_time"] == 22.5
            assert result["result"]["effective_closing_time"] == 44.5
            result = await send({"id": 11, "type": WS_EXPORT, "entry_id": plant.entries[0].entry_id})
            assert result["result"]["format_version"] == 2
            assert result["result"]["profiles"][0]["closing_time"] == 44.5
            result = await send({"id": 12, "type": WS_EXPORT, "entry_id": "missing"})
            assert result["error"]["code"] == "target_not_found"
            with patch.object(store, "load", side_effect=OSError("disk")):
                result = await send({"id": 13, "type": WS_EXPORT, "entry_id": plant.entries[0].entry_id})
                assert result["error"]["code"] == "storage_error"
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
        assert durations == pytest.approx([15.0], abs=0.02)  # Half of the original 30-second full travel.
        release.set()
        await stop_task
    assert cover.current_cover_position == 50
    assert cover._travel_time_up == 42.5
    assert cover._pending_profile is None
    assert plant.gateways[0].send.await_count == 2


async def test_registered_cover_startup_resolves_profile_before_status_request(hass, plant):
    await write_profile(hass, message(plant))
    entry_id = plant.entries[0].entry_id
    hass.data[DATA_KEY].pop(entry_id)
    cover = plant.covers[0]
    cover._travel_time_up = 30
    gateway = plant.gateways[0]
    gateway.availability_signal = "test_profile_availability"

    async def request_status(command):
        assert cover._travel_time_up == 42.5
        assert command is not None

    gateway.send_status_request = AsyncMock(side_effect=request_status)
    await cover.async_added_to_hass()
    gateway.send_status_request.assert_awaited_once()
    assert get_store(hass, entry_id).covers[cover.unique_id] is cover


async def test_version_one_store_migrates_without_changing_assignments_or_timing(hass, plant):
    entry_id = plant.entries[0].entry_id
    unique_id = plant.records[0].unique_id
    legacy = {"revision": 7, "profiles": {"old": {"name": "Legacy", "travel_time": 32.5}},
              "assignments": {unique_id: "old"}}
    await Store(hass, 1, f"myhome.cover_profiles.{entry_id}").async_save(legacy)
    hass.data[DATA_KEY].pop(entry_id)
    await bind_cover(hass, plant.covers[0])
    store = get_store(hass, entry_id)
    assert store.data == {**legacy, "profiles": {"old": {
        "name": "Legacy", "opening_time": 32.5, "closing_time": 32.5,
        "provenance": unknown_provenance()}}}
    assert plant.covers[0]._travel_time_up == plant.covers[0]._travel_time_down == 32.5
    assert await ProfileStorage(hass, 4, store.store.key).async_load() == store.data
    with pytest.raises(NotImplementedError):
        await store.store._async_migrate_func(5, 1, legacy)
    plant.gateways[0].send.assert_not_called()


@pytest.mark.parametrize("direction", ["opening_time", "closing_time"])
@pytest.mark.parametrize("value", [0, 601, float("nan"), float("inf"), True, "30"])
def test_directional_times_reject_invalid_values(direction, value):
    with pytest.raises(vol.Invalid):
        PROFILE({"name": "Invalid", "opening_time": 20, "closing_time": 40, direction: value})


async def test_directional_profile_restart_and_command_reversal(hass, plant):
    cover = plant.covers[0]
    await write_profile(hass, message(plant, profile={"name": "Two times", "opening_time": 20, "closing_time": 40}))
    hass.data[DATA_KEY].pop(plant.entries[0].entry_id)
    await bind_cover(hass, cover)
    assert cover.extra_state_attributes["opening_time"] == 20
    assert cover.extra_state_attributes["closing_time"] == 40
    cover._attr_current_cover_position = 0
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        await cover.async_open_cover()
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
    with patch("custom_components.myhome.cover.time.monotonic", return_value=110):
        assert cover.current_cover_position == 50
        await cover.async_close_cover()
        cover.handle_event(OWNMessage.parse("*2*2*11##"))
        assert cover._start_position == 50
    with patch("custom_components.myhome.cover.time.monotonic", return_value=120):
        assert cover.current_cover_position == 25
        await cover.async_stop_cover()
        assert cover.current_cover_position == 25
    result = await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
    assert result["effective_opening_time"] == result["effective_closing_time"] == 30


async def test_bus_reversal_and_pending_directional_edit_use_original_times(hass, plant):
    cover = plant.covers[0]
    first = await write_profile(hass, message(plant, profile={"name": "Two times", "opening_time": 20, "closing_time": 40}))
    cover._attr_current_cover_position = 100
    with patch("custom_components.myhome.cover.time.monotonic", return_value=100):
        cover.handle_event(OWNMessage.parse("*2*2*11##"))
    with patch("custom_components.myhome.cover.time.monotonic", return_value=110):
        assert cover.current_cover_position == 75
        edited = await write_profile(hass, message(plant, 1, profile_id=first["assigned_profile_id"],
                                                   profile={"name": "Changed", "opening_time": 30, "closing_time": 60}))
        assert edited["pending"]
        assert edited["effective_closing_time"] == 40
        cover.handle_event(OWNMessage.parse("*2*1*11##"))
        assert cover._start_position == 75
    with patch("custom_components.myhome.cover.time.monotonic", return_value=112):
        assert cover.current_cover_position == 85
        cover.handle_event(OWNMessage.parse("*2*0*11##"))
        assert cover.current_cover_position == 85
    assert (cover._travel_time_up, cover._travel_time_down) == (30, 60)
    with patch("custom_components.myhome.cover.time.monotonic", return_value=120):
        cover.handle_event(OWNMessage.parse("*2*2*11##"))
    with patch("custom_components.myhome.cover.time.monotonic", return_value=126):
        cover.handle_event(OWNMessage.parse("*2*0*11##"))
        assert cover.current_cover_position == 75


@pytest.mark.parametrize(("start", "target", "duration"), [(0, 50, 10), (100, 50, 20)])
async def test_directional_scheduled_stop_keeps_time_during_profile_reset(hass, plant, start, target, duration):
    cover = plant.covers[0]
    await write_profile(hass, message(plant, profile={"name": "Two times", "opening_time": 20, "closing_time": 40}))
    cover._attr_current_cover_position = start
    release, scheduled = asyncio.Event(), asyncio.Event()
    durations = []
    async def timer(seconds):
        durations.append(seconds)
        scheduled.set()
        await release.wait()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=timer):
        await cover.async_set_cover_position(position=target)
        task = cover._stop_task
        await scheduled.wait()
        await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
        assert durations == pytest.approx([duration], abs=0.02)
        assert (cover._travel_time_up, cover._travel_time_down) == (20, 40)
        release.set()
        await task
    assert cover.current_cover_position == target
    assert (cover._travel_time_up, cover._travel_time_down) == (30, 30)


async def test_delete_protects_assignments_and_is_atomic_persistent_and_gateway_scoped(hass, plant):
    first = await write_profile(hass, message(plant))
    profile_id = first["assigned_profile_id"]
    assert first["profiles"][0]["assigned_to"][0]["entity_id"] == plant.records[0].entity_id
    delete = dict(action="delete", profile_id=profile_id)
    with pytest.raises(ProfileError, match="profile_in_use"):
        await write_profile(hass, message(plant, 1, **delete))
    with pytest.raises(ProfileError, match="profile_not_found"):
        await write_profile(hass, message(plant, 1, action="delete", profile_id=None))
    with pytest.raises(ProfileError, match="profile_not_found"):
        await write_profile(hass, message(plant, 0, index=2, **delete))
    cover = plant.covers[0]
    await cover.async_close_cover()
    await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
    assert cover._pending_profile == (None,)
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    with patch.object(Store, "_async_write_data", side_effect=WriteError("full")):
        with pytest.raises(OSError):
            await write_profile(hass, message(plant, 2, **delete))
    assert store.data == before
    with pytest.raises(ProfileError, match="revision_conflict"):
        await write_profile(hass, message(plant, 1, **delete))
    result = await write_profile(hass, message(plant, 2, **delete))
    assert result["profiles"] == []
    assert result["assigned_profile_id"] is None
    assert result["revision"] == 3
    assert cover._pending_profile == (None,)
    assert cover._travel_time_down == 42.5
    plant.gateways[0].send.assert_awaited_once()  # Only the explicit close command.
    hass.data[DATA_KEY].pop(plant.entries[0].entry_id)
    restored = get_store(hass, plant.entries[0].entry_id)
    await restored.load()
    assert restored.data == {"revision": 3, "profiles": {}, "assignments": {}}
    await cover.async_stop_cover()
    assert cover._travel_time_down == 30


async def test_delete_racing_an_assignment_cannot_remove_an_assigned_profile(hass, plant):
    first = await write_profile(hass, message(plant))
    profile_id = first["assigned_profile_id"]
    await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
    results = await asyncio.gather(
        write_profile(hass, message(plant, 2, index=1, action="assign", profile_id=profile_id)),
        write_profile(hass, message(plant, 2, action="delete", profile_id=profile_id)),
        return_exceptions=True,
    )
    assert isinstance(results[1], ProfileError)
    state = get_store(hass, plant.entries[0].entry_id).data
    assert all(value in state["profiles"] for value in state["assignments"].values())
    # Missing registry rows remain counted, never silently orphaned by a delete.
    er.async_get(hass).async_remove(plant.records[1].entity_id)
    read = await read_profile(hass, plant.entries[0].entry_id, plant.records[0].entity_id)
    assert read["profiles"][0]["assigned_to"] == [{"entity_id": None, "name": None}]
    with pytest.raises(ProfileError, match="profile_in_use"):
        await write_profile(hass, message(plant, 3, action="delete", profile_id=profile_id))


async def test_profile_subscriptions_publish_only_commits_and_isolate_gateways(hass, plant, hass_ws_client):
    """Two real sockets reconcile changes, and unsubscribe/disconnect remove listeners."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    register_api(hass)
    entry_id = plant.entries[0].entry_id
    store = get_store(hass, entry_id)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        first, second = await hass_ws_client(hass), await hass_ws_client(hass)
        try:
            for client in (first, second):
                await client.send_json({"id": 1, "type": WS_SUBSCRIBE, "entry_id": entry_id})
                assert (await client.receive_json())["success"]
                assert (await client.receive_json())["event"] == {
                    "entry_id": entry_id, "revision": 0, "kind": "ready"}
            await write_profile(hass, message(plant, index=2))  # Other gateway: no event.
            with patch.object(store.store, "async_save", side_effect=OSError("full")):
                with pytest.raises(OSError):
                    await write_profile(hass, message(plant))
            result = await write_profile(hass, message(plant))
            profile_id = result["assigned_profile_id"]
            for client in (first, second):
                assert (await client.receive_json())["event"] == {
                    "entry_id": entry_id, "revision": 1, "kind": "changed"}
            # No extra notifications preceded the successful mutation.
            await first.send_json({"id": 2, "type": "unsubscribe_events", "subscription": 1})
            assert (await first.receive_json())["success"]
            await write_profile(hass, message(plant, 1, action="assign", profile_id=None))
            assert (await second.receive_json())["event"]["revision"] == 2
            await write_profile(hass, message(plant, 2, action="delete", profile_id=profile_id))
            assert (await second.receive_json())["event"]["revision"] == 3
            await remove_entry(hass, entry_id)
            assert (await second.receive_json())["event"]["kind"] == "removed"
            await second.close()
            await hass.async_block_till_done()
            async_dispatcher_send(hass, f"{WS_SUBSCRIBE}:{entry_id}", {})
            await first.send_json({"id": 3, "type": "ping"})
            assert (await first.receive_json())["type"] == "pong"
        finally:
            await first.close()
            await second.close()


async def test_subscribe_validation_storage_failure_and_removed_while_loading(hass, plant):
    store = get_store(hass, plant.entries[0].entry_id)
    connection = MagicMock(user=SimpleNamespace(is_admin=True), subscriptions={})
    msg = {"id": 1, "type": WS_SUBSCRIBE, "entry_id": "missing"}
    ws_subscribe(hass, connection, msg)
    await hass.async_block_till_done()
    assert connection.send_error.call_args.args[1] == "target_not_found"
    assert "missing" not in hass.data[DATA_KEY]
    msg["entry_id"] = plant.entries[0].entry_id
    for error, code in [(OSError("disk"), "storage_error"), (vol.Invalid("bad"), "invalid_profile")]:
        with patch.object(store, "load", side_effect=error):
            ws_subscribe(hass, connection, msg)
            await hass.async_block_till_done()
        assert connection.send_error.call_args.args[1] == code
    original = hass.config_entries.async_get_entry
    reads = 0
    def lookup(entry_id):
        nonlocal reads
        reads += 1
        return original(entry_id) if reads == 1 else None
    with patch.object(hass.config_entries, "async_get_entry", side_effect=lookup):
        ws_subscribe(hass, connection, msg)
        await hass.async_block_till_done()
    assert connection.send_error.call_args.args[1] == "target_not_found"
    assert not connection.subscriptions


async def test_subscription_closed_while_waiting_for_store_does_not_leak(hass, plant):
    connection = MagicMock(user=SimpleNamespace(is_admin=True), subscriptions={})
    entry_id = plant.entries[0].entry_id
    store = get_store(hass, entry_id)
    await store.lock.acquire()
    ws_subscribe(hass, connection, {"id": 1, "type": WS_SUBSCRIBE, "entry_id": entry_id})
    await asyncio.sleep(0)
    connection.subscriptions.pop(1)()  # HA socket cleanup before disk/lock is ready.
    store.lock.release()
    await hass.async_block_till_done()
    await write_profile(hass, message(plant))
    connection.send_result.assert_not_called()
    connection.send_event.assert_not_called()
    assert not connection.subscriptions


async def test_export_preserves_saved_profiles_orphans_and_origins_without_runtime_or_secrets(hass, plant):
    first = await write_profile(hass, message(plant, profile={"name": "Kitchen", "opening_time": 25, "closing_time": 31}))
    profile_id = first["assigned_profile_id"]
    await write_profile(hass, message(plant, 1, index=1, action="assign", profile_id=profile_id))
    await write_profile(hass, message(plant, index=2, profile={"name": "Other gateway", "travel_time": 42}))
    await write_profile(hass, message(plant, 2, action="assign", profile_id=None))
    entry_id = plant.entries[0].entry_id
    store = get_store(hass, entry_id)
    # Keep an unassigned legacy profile as well as a shared profile with a removed origin.
    store.data["profiles"]["old"] = {"name": "Unused", "opening_time": 20, "closing_time": 22,
                                     "provenance": unknown_provenance()}
    await store.store.async_save(store.data)
    er.async_get(hass).async_remove(plant.records[0].entity_id)
    plant.gateways[0].available = False
    store.calibration = SimpleNamespace(active=True, values={"opening_time": 99})
    before = copy.deepcopy(store.data)
    result = await export_profiles(hass, entry_id)
    assert result["format"] == "myhome.cover_calibration" and result["format_version"] == 2
    assert result["revision"] == 3
    assert datetime.fromisoformat(result["exported_at"]).utcoffset().total_seconds() == 0
    assert len(result["profiles"]) == 2
    profile = next(p for p in result["profiles"] if p["id"] == profile_id)
    assert (profile["opening_time"], profile["closing_time"]) == (25, 31)
    origin = profile["provenance"]["opening"]
    assert origin["source"] == "manual"
    missing = next(c for c in result["covers"] if c["id"] == origin["origin_cover_id"])
    assert missing == {"id": missing["id"], "registry_id": None, "entity_id": None, "name": None}
    assigned = next(c for c in result["covers"] if c["entity_id"] == plant.records[1].entity_id)
    assert assigned["registry_id"] == plant.records[1].id
    assert result["assignments"] == [{"cover_id": assigned["id"], "profile_id": profile_id}]
    legacy = next(p for p in result["profiles"] if p["id"] == "old")
    assert legacy["provenance"]["closing"] == {"source": "unknown", "recorded_at": None, "origin_cover_id": None}
    serialized = json.dumps(result)
    assert "Other gateway" not in serialized and "origin_unique_id" not in serialized
    assert all(g.mac not in serialized for g in plant.gateways)
    assert store.data == before
    for gateway in plant.gateways:
        gateway.send.assert_not_called()
    # Missing assignments remain representable, independently of a missing origin.
    er.async_get(hass).async_remove(plant.records[1].entity_id)
    orphan = await export_profiles(hass, entry_id)
    assert orphan["assignments"] == result["assignments"]
    assert all(c["entity_id"] is None for c in orphan["covers"])
    result["profiles"][0]["provenance"]["opening"]["source"] = "changed by caller"
    assert store.data == before


async def test_export_empty_gateway_and_removed_entry_during_load(hass, plant):
    entry_id = plant.entries[0].entry_id
    result = await export_profiles(hass, entry_id)
    assert result["profiles"] == result["assignments"] == result["covers"] == []
    store = get_store(hass, entry_id)
    with patch.object(store, "load", side_effect=lambda: None):
        with patch.object(hass.config_entries, "async_get_entry", side_effect=[plant.entries[0], None]):
            with pytest.raises(ProfileError, match="target_not_found"):
                await export_profiles(hass, entry_id)
    alien = MockConfigEntry(domain="other", data={})
    alien.add_to_hass(hass)
    with pytest.raises(ProfileError, match="target_not_found"):
        await export_profiles(hass, alien.entry_id)
    assert alien.entry_id not in hass.data[DATA_KEY]


async def test_export_waits_for_committed_revision_without_publishing_a_write(hass, plant):
    store = get_store(hass, plant.entries[0].entry_id)
    entered, release = asyncio.Event(), asyncio.Event()
    original_save = store.store.async_save
    async def save(data):
        entered.set()
        await release.wait()
        await original_save(data)
    with patch.object(store.store, "async_save", side_effect=save):
        writer = asyncio.create_task(write_profile(hass, message(plant)))
        await entered.wait()
        reader = asyncio.create_task(export_profiles(hass, plant.entries[0].entry_id))
        await asyncio.sleep(0)
        assert not reader.done()
        release.set()
        await writer
        result = await reader
    assert result["revision"] == 1
    assert result["profiles"][0]["opening_time"] == 42.5
    assert store.data["revision"] == 1


async def test_version_three_migration_preserves_recorded_evidence(hass, plant):
    await write_profile(hass, message(plant))
    entry_id = plant.entries[0].entry_id
    before = copy.deepcopy(get_store(hass, entry_id).data)
    await Store(hass, 3, f"myhome.cover_profiles.{entry_id}").async_save(before)
    hass.data[DATA_KEY].pop(entry_id)
    await bind_cover(hass, plant.covers[0])
    assert get_store(hass, entry_id).data == before
    assert plant.covers[0]._travel_time_up == 42.5
