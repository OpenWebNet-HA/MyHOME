"""Optional travel geometry: persistence, previews and the actual cover runtime."""
import asyncio
import copy
from unittest.mock import patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_profile_catalogue import manage_profile
from custom_components.myhome.cover_profile_export import export_profiles
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    WS_WRITE,
    ProfileError,
    ProfileStorage,
    bind_cover,
    get_store,
    read_profile,
    register_api,
    remove_entry,
    write_profile,
)
from custom_components.myhome.cover_settings import validate_scaling
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


async def write(hass, plant, *, index=0, **kwargs):
    store = get_store(hass, plant.records[index].config_entry_id)
    return await write_profile(hass, message(plant, store.data["revision"], index=index, **kwargs))


async def geometry(hass, plant):
    data = await write(hass, plant, profile={"name": "Reference", "opening_time": 20,
                                           "closing_time": 40, "reference_travel_cm": 200})
    await write(hass, plant, action="travel", travel_cm=150)
    return data["assigned_profile_id"]


async def test_optional_geometry_scales_only_inherited_times_and_survives_reload(hass, plant):
    profile_id = await geometry(hass, plant)
    cover = plant.covers[0]
    data = await write(hass, plant, action="overrides", overrides={"closing": 19})
    assert data["travel_cm"] == 150 and data["scaling"] == "height"
    assert data["effective"]["opening"]["scaled"]
    assert not data["effective"]["closing"]["scaled"]
    assert cover._travel_time_up == 15 and cover._travel_time_down == 19
    assert data["effective"]["opening"]["provenance"] == data["profiles"][0]["provenance"]["opening"]
    await write(hass, plant, index=1, action="assign", profile_id=profile_id)
    assert plant.covers[1]._travel_time_up == 20  # Missing target travel: unscaled.
    await write(hass, plant, index=1, action="travel", travel_cm=250)
    assert plant.covers[1]._travel_time_up == 25
    assert plant.covers[2]._travel_time_up == 30  # Same address on another gateway.
    store = get_store(hass, plant.entries[0].entry_id)
    saved = copy.deepcopy(store.data)
    hass.data[DATA_KEY].pop(store.entry_id)
    await bind_cover(hass, cover)
    store = get_store(hass, store.entry_id)
    assert store.data == saved and cover._travel_time_up == 15 and cover._travel_time_down == 19
    store.covers.pop(cover.unique_id)
    unloaded = await read_profile(hass, store.entry_id, cover.entity_id)
    assert unloaded["configured"] == data["configured"] and unloaded["effective"] is None
    exported = await export_profiles(hass, store.entry_id)
    assert exported["format_version"] == 5
    assert exported["profiles"][0]["reference_travel_cm"] == 200
    assert next(c for c in exported["covers"] if c["entity_id"] == cover.entity_id)["travel_cm"] == 150


async def test_clear_geometry_or_overrides_retains_other_configuration(hass, plant):
    profile_id = await geometry(hass, plant)
    cover = plant.covers[0]
    await write(hass, plant, action="overrides", overrides={"opening": 12})
    data = await write(hass, plant, action="travel", travel_cm=None)
    assert data["effective_opening_time"] == 12 and data["effective_closing_time"] == 40
    await write(hass, plant, action="travel", travel_cm=150)
    data = await write(hass, plant, action="overrides", overrides={"opening": None})
    assert data["travel_cm"] == 150 and data["effective_opening_time"] == 15
    # Older clients that only rename/edit times cannot silently erase geometry.
    data = await write(hass, plant, profile_id=profile_id,
                       profile={"name": "Renamed", "opening_time": 20, "closing_time": 40})
    assert data["profiles"][0]["reference_travel_cm"] == 200
    data = await write(hass, plant, profile_id=profile_id,
                       profile={"name": "Renamed", "opening_time": 20, "closing_time": 40, "reference_travel_cm": None})
    assert "reference_travel_cm" not in data["profiles"][0]
    assert cover._travel_time_up == 20 and data["travel_cm"] == 150
    await write(hass, plant, action="travel", travel_cm=None)
    assert get_store(hass, plant.entries[0].entry_id).data["covers"] == {}
    # Geometry alone does not change defaults or native fallback times.
    await write(hass, plant, action="assign", profile_id=None)
    await write(hass, plant, action="travel", travel_cm=500)
    assert cover._travel_time_up == 30


async def test_shared_reference_preview_uses_each_follower_geometry_and_exact_confirmation(hass, plant):
    profile_id = await geometry(hass, plant)
    await write(hass, plant, index=1, action="assign", profile_id=profile_id)
    await write(hass, plant, index=1, action="travel", travel_cm=250)
    await write(hass, plant, index=1, action="overrides", overrides={"closing": 19})
    store = get_store(hass, plant.entries[0].entry_id)
    request = {"entry_id": store.entry_id, "revision": store.data["revision"], "profile_id": profile_id,
               "profile": {"name": "Reference", "opening_time": 20, "closing_time": 40, "reference_travel_cm": 100}}
    preview = await manage_profile(hass, {**request, "action": "preview"})
    first, second = preview["followers"]
    assert first["changes"]["opening"] == {"before": 15, "after": 30, "overridden": False, "scaled": True}
    assert second["changes"]["opening"]["after"] == 50
    assert second["changes"]["closing"] == {"before": 19, "after": 19, "overridden": True, "scaled": False}
    with pytest.raises(ProfileError, match="preview_required"):
        await manage_profile(hass, {**request, "action": "update", "profile": {**request["profile"], "reference_travel_cm": 120},
                                    "confirmation": preview["confirmation"]})
    await manage_profile(hass, {**request, "action": "update", "confirmation": preview["confirmation"]})
    assert plant.covers[0]._travel_time_up == 30 and plant.covers[1]._travel_time_up == 50
    assert plant.covers[1]._travel_time_down == 19
    request["revision"] = store.data["revision"]
    request["profile"]["reference_travel_cm"] = None
    preview = await write(hass, plant, action="preview", profile_id=profile_id, profile=request["profile"])
    assert preview["before"]["reference_travel_cm"] == 100 and preview["after"]["reference_travel_cm"] is None
    assert preview["followers"][0]["changes"]["opening"]["after"] == 20
    await write(hass, plant, action="update_shared", profile_id=profile_id, profile=request["profile"], confirmation=preview["confirmation"])
    assert plant.covers[0]._travel_time_up == plant.covers[1]._travel_time_up == 20


async def test_assignment_preview_and_duplicate_preserve_reference(hass, plant):
    profile_id = await geometry(hass, plant)
    await write(hass, plant, index=1, action="travel", travel_cm=300)
    store = get_store(hass, plant.entries[0].entry_id)
    request = {"entry_id": store.entry_id, "revision": store.data["revision"], "profile_id": profile_id,
               "entity_ids": [plant.covers[1].entity_id]}
    preview = await manage_profile(hass, {**request, "action": "preview_assign"})
    assert preview["targets"][0]["changes"]["closing"]["after"] == 60
    assert preview["targets"][0]["changes"]["closing"]["scaled"]
    await manage_profile(hass, {**request, "action": "assign", "confirmation": preview["confirmation"]})
    assert plant.covers[1]._travel_time_down == 60
    copied = await manage_profile(hass, {"entry_id": store.entry_id, "revision": store.data["revision"],
                                        "profile_id": profile_id, "action": "duplicate", "name": "Copy"})
    assert store.data["profiles"][copied["profile_id"]]["reference_travel_cm"] == 200


@pytest.mark.parametrize("value", [0, -1, 10001, True, "150", float("nan"), float("inf")])
async def test_invalid_dimensions_never_change_committed_data(hass, plant, value):
    await geometry(hass, plant)
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    for request in ({"action": "travel", "travel_cm": value},
                    {"profile": {"name": "Invalid", "opening_time": 20, "closing_time": 40, "reference_travel_cm": value}}):
        with pytest.raises(vol.Invalid):
            await write(hass, plant, **request)
    assert store.data == before and plant.covers[0]._travel_time_up == 15


async def test_effective_duration_bounds_and_failed_persistence_are_atomic(hass, plant):
    profile_id = await geometry(hass, plant)
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    for travel in (0.1, 10000):
        with pytest.raises(vol.Invalid):
            await write(hass, plant, action="travel", travel_cm=travel)
    with pytest.raises(vol.Invalid):
        await write(hass, plant, action="travel")
    with patch.object(store.store, "async_save", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            await write(hass, plant, action="travel", travel_cm=100)
    assert store.data == before and plant.covers[0]._travel_time_up == 15
    # Orphan followers must also be validated, even without a runtime instance.
    store.covers.pop(plant.covers[0].unique_id)
    er.async_get(hass).async_remove(plant.covers[0].entity_id)
    with pytest.raises(vol.Invalid):
        await manage_profile(hass, {"entry_id": store.entry_id, "revision": store.data["revision"],
                                    "profile_id": profile_id, "action": "preview",
                                    "profile": {"name": "Invalid", "opening_time": 20, "closing_time": 40, "reference_travel_cm": 0.1}})
    assert store.data == before
    bad = copy.deepcopy(before)
    bad["assignments"]["missing"] = "absent"
    with pytest.raises(vol.Invalid):
        validate_scaling(bad)


async def test_travel_edit_does_not_change_in_flight_stop_timer(hass, plant):
    await geometry(hass, plant)
    cover = plant.covers[0]
    cover._attr_current_cover_position = 0
    release, scheduled = asyncio.Event(), asyncio.Event()
    durations = []
    async def wait(seconds):
        durations.append(seconds)
        scheduled.set()
        await release.wait()
    with patch("custom_components.myhome.cover.asyncio.sleep", side_effect=wait):
        await cover.async_set_cover_position(position=50)
        task = cover._stop_task
        await scheduled.wait()
        result = await write(hass, plant, action="travel", travel_cm=300)
        assert cover._stop_task is task and durations == pytest.approx([7.5], abs=0.02)
        assert result["pending"] and result["configured"]["opening"]["value"] == 30
        assert result["effective"]["opening"]["value"] == 15
        release.set()
        await task
    assert cover.current_cover_position == 50 and cover._travel_time_up == 30
    row = (await overview(hass, plant.entries[0].entry_id))["covers"][0]
    assert row["effective"] == row["configured"] and row["travel_cm"] == 300


async def test_v6_migration_is_lossless_and_keeps_exact_backup(hass, plant):
    await write(hass, plant)
    await write(hass, plant, action="overrides", overrides={"closing": 14})
    entry_id = plant.entries[0].entry_id
    store = get_store(hass, entry_id)
    original = copy.deepcopy(store.data)
    key = store.store.key
    await Store(hass, 6, key).async_save(original)
    hass.data[DATA_KEY].pop(entry_id)
    await bind_cover(hass, plant.covers[0])
    store = get_store(hass, entry_id)
    assert store.data == original
    assert await Store(hass, 6, key + ".pre_travel").async_load() == original
    assert await Store(hass, 8, key).async_load() == original
    assert plant.covers[0]._travel_time_up == 42.5 and plant.covers[0]._travel_time_down == 14
    await remove_entry(hass, entry_id)
    assert await Store(hass, 6, key + ".pre_travel").async_load() is None


@pytest.mark.parametrize("fail_backup", [False, True])
async def test_v6_migration_failure_preserves_original_and_retries(hass, plant, fail_backup):
    entry_id = plant.entries[0].entry_id
    store = get_store(hass, entry_id)
    original, key = copy.deepcopy(store.data), store.store.key
    await Store(hass, 6, key).async_save(original)
    hass.data[DATA_KEY].pop(entry_id)
    writer = ProfileStorage._async_write_data
    async def fail(self, *args):
        if self.key.endswith(".pre_travel") == fail_backup:
            raise OSError("disk full")
        await writer(self, *args)
    with patch.object(ProfileStorage, "_async_write_data", fail):
        with pytest.raises(OSError):
            await bind_cover(hass, plant.covers[0])
    assert await Store(hass, 6, key).async_load() == original
    assert not get_store(hass, entry_id).loaded
    await bind_cover(hass, plant.covers[0])
    assert get_store(hass, entry_id).data == original


async def test_websocket_travel_write_clear_and_revision_conflict(hass, plant, hass_ws_client):
    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    request = {"type": WS_WRITE, "entry_id": plant.entries[0].entry_id,
               "entity_id": plant.covers[0].entity_id, "revision": 0, "action": "travel", "travel_cm": 180.5}
    try:
        await client.send_json({"id": 1, **request})
        response = await client.receive_json()
        assert response["success"] and response["result"]["travel_cm"] == 180.5
        await client.send_json({"id": 2, **request, "travel_cm": 200})
        assert (await client.receive_json())["error"]["code"] == "revision_conflict"
        await client.send_json({"id": 3, **request, "revision": 1, "travel_cm": None})
        assert (await client.receive_json())["result"]["travel_cm"] is None
        for request_id, value in enumerate([True, "200", 0], start=4):
            await client.send_json({"id": request_id, **request, "revision": 2, "travel_cm": value})
            assert not (await client.receive_json())["success"]
        assert get_store(hass, plant.entries[0].entry_id).data["revision"] == 2
    finally:
        await client.close()
