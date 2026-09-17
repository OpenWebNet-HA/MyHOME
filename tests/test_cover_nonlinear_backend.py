"""Nonlinear profiles: persistence, API/runtime parity and atomic edits."""
import copy
import json
from unittest.mock import patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.helpers.storage import Store
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_geometry import motion_from_settings, profile_motion
from custom_components.myhome.cover_profile_catalogue import manage_profile
from custom_components.myhome.cover_profile_export import export_profiles
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    WS_WRITE,
    ProfileError,
    bind_cover,
    get_store,
    read_profile,
    register_api,
    remove_entry,
)
from custom_components.myhome.cover_settings import validate_scaling
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_profiles import plant as plant_fixture
from tests.test_cover_travel_scaling import write

plant = plant_fixture


def nonlinear(**changes):
    return {"name": "Measured geometry", "opening_time": 26, "closing_time": 20,
            "reference_travel_cm": 150,
            "geometry": {"slat_time_s": 2, "opening_roll": 3, "closing_roll": 2}, **changes}


async def test_saved_geometry_scaled_values_overrides_reload_export_and_isolation(hass, plant):
    data = await write(hass, plant, profile=nonlinear())
    profile_id = data["assigned_profile_id"]
    cover = plant.covers[0]
    assert data["model"] == data["configured_model"] == "slat_roll"
    assert data["position_known"] is False and cover.current_cover_position is None
    assert cover._attr_extra_state_attributes["cover_motion_model"] == "slat_roll"
    for item in data["profiles"][0]["geometry_provenance"].values():
        assert item["source"] == "manual" and "origin_unique_id" not in item
    await write(hass, plant, action="travel", travel_cm=400)
    data = await write(hass, plant, action="overrides", overrides={"opening": 40})
    assert data["effective_opening_time"] == 40
    assert data["effective_closing_time"] == pytest.approx(36 + 16 / 3)
    assert data["effective"]["opening"]["scaled"] is False
    assert data["effective"]["slat_time_s"]["value"] == pytest.approx(16 / 3)
    assert data["effective"]["closing_roll"]["value"] == 3
    assert data["effective"] == data["configured"]
    assert motion_from_settings(data["effective"]) == cover._motion.model
    store = get_store(hass, plant.entries[0].entry_id)
    saved = copy.deepcopy(store.data)
    hass.data[DATA_KEY].pop(store.entry_id)
    await bind_cover(hass, cover)
    store = get_store(hass, store.entry_id)
    assert store.data == saved and cover._motion.model.closing_roll == 3
    result = await overview(hass, store.entry_id)
    assert result["capabilities"]["nonlinear"] and result["capabilities"]["nonlinear_calibration"]
    assert result["profiles"][0]["geometry"] == nonlinear()["geometry"]
    assert result["covers"][0]["model"] == "slat_roll"
    assert plant.covers[1]._motion.model is None and plant.covers[2]._motion.model is None
    exported = await export_profiles(hass, store.entry_id)
    assert exported["format_version"] == 5
    assert exported["profiles"][0]["geometry"] == nonlinear()["geometry"]
    assert exported["profiles"][0]["geometry_provenance"]["opening_roll"]["origin_cover_id"]
    assert "origin_unique_id" not in json.dumps(exported)
    assert cover.unique_id not in json.dumps(exported)
    store.covers.pop(cover.unique_id)
    unloaded = await read_profile(hass, store.entry_id, cover.entity_id)
    assert unloaded["effective"] is None and unloaded["model"] is None
    assert unloaded["configured_model"] == "slat_roll" and unloaded["configured"] == data["configured"]
    assert store.data["assignments"][cover.unique_id] == profile_id


async def test_old_client_edits_copy_provenance_and_explicit_linear_opt_out(hass, plant):
    data = await write(hass, plant, profile=nonlinear())
    profile_id = data["assigned_profile_id"]
    before = copy.deepcopy(data["profiles"][0]["geometry_provenance"])
    data = await write(hass, plant, profile_id=profile_id,
                       profile={"name": "Rename", "opening_time": 26, "closing_time": 20})
    assert data["profiles"][0]["geometry"] == nonlinear()["geometry"]
    assert data["profiles"][0]["geometry_provenance"] == before
    data = await write(hass, plant, copy_from_profile_id=profile_id,
                       profile={"name": "Copy", "opening_time": 26, "closing_time": 20})
    copied_id = data["assigned_profile_id"]
    assert next(p for p in data["profiles"] if p["id"] == copied_id)["geometry_provenance"] == before
    data = await write(hass, plant, profile_id=copied_id, profile=nonlinear(geometry=None))
    assert data["model"] == "linear_time" and plant.covers[0]._motion.model is None
    copied = next(p for p in data["profiles"] if p["id"] == copied_id)
    assert "geometry" not in copied and "geometry_provenance" not in copied
    assert data["effective_opening_time"] == 26


async def test_geometry_only_shared_and_assignment_previews_are_bound_and_visible(hass, plant):
    data = await write(hass, plant, profile=nonlinear())
    profile_id = data["assigned_profile_id"]
    store = get_store(hass, plant.entries[0].entry_id)
    request = {"entry_id": store.entry_id, "revision": store.data["revision"], "profile_id": profile_id,
               "entity_ids": [plant.covers[1].entity_id]}
    preview = await manage_profile(hass, {**request, "action": "preview_assign"})
    target = preview["targets"][0]
    assert target["model_before"] == "linear_time" and target["model_after"] == "slat_roll"
    assert target["geometry_changes"]["opening_roll"] == {"before": 1, "after": 3, "scaled": False}
    await manage_profile(hass, {**request, "action": "assign", "confirmation": preview["confirmation"]})
    original_evidence = copy.deepcopy(store.data["profiles"][profile_id]["geometry_provenance"])
    proposal = nonlinear(geometry={"slat_time_s": 2, "opening_roll": 4, "closing_roll": 2})
    request = {"entry_id": store.entry_id, "revision": store.data["revision"], "profile_id": profile_id, "profile": proposal}
    preview = await manage_profile(hass, {**request, "action": "preview"})
    assert len(preview["followers"]) == 2
    assert preview["followers"][0]["changes"]["opening"]["before"] == preview["followers"][0]["changes"]["opening"]["after"]
    assert preview["followers"][0]["geometry_changes"]["opening_roll"]["after"] == 4
    with pytest.raises(ProfileError, match="preview_required"):
        await manage_profile(hass, {**request, "action": "update", "confirmation": preview["confirmation"], "profile": nonlinear()})
    await manage_profile(hass, {**request, "action": "update", "confirmation": preview["confirmation"]})
    saved = store.data["profiles"][profile_id]
    assert saved["geometry_provenance"]["closing_roll"] == original_evidence["closing_roll"]
    assert saved["geometry_provenance"]["opening_roll"]["origin_unique_id"] is None
    assert plant.covers[1]._motion.model.opening_roll == 4
    # Single-cover shared preview also reports explicit removal of geometry.
    proposal = nonlinear(geometry=None)
    impact = await write(hass, plant, action="preview", profile_id=profile_id, profile=proposal)
    assert impact["after"]["geometry"] is None
    assert impact["followers"][0]["model_after"] == "linear_time"
    await write(hass, plant, action="update_shared", profile_id=profile_id, profile=proposal, confirmation=impact["confirmation"])
    assert all(c._motion.model is None for c in plant.covers)


@pytest.mark.parametrize("geometry", [True, {}, {"slat_time_s": 2},
    {"slat_time_s": 20, "opening_roll": 2, "closing_roll": 2},
    {"slat_time_s": -1, "opening_roll": 2, "closing_roll": 2},
    {"slat_time_s": 2, "opening_roll": 0.9, "closing_roll": 2},
    {"slat_time_s": 2, "opening_roll": 2, "closing_roll": 5.1},
    {"slat_time_s": 2, "opening_roll": float("nan"), "closing_roll": 2},
    {"slat_time_s": True, "opening_roll": 2, "closing_roll": 2}])
async def test_invalid_nonlinear_profiles_never_publish_or_move(hass, plant, geometry):
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    with pytest.raises(vol.Invalid):
        await write(hass, plant, profile=nonlinear(geometry=geometry))
    assert store.data == before and plant.covers[0]._motion.model is None
    plant.gateways[0].send.assert_not_called()


async def test_invalid_effective_phase_or_storage_failure_is_atomic_and_unknown_evidence_is_honest(hass, plant):
    data = await write(hass, plant, profile=nonlinear())
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    with pytest.raises(vol.Invalid):
        await write(hass, plant, action="overrides", overrides={"opening": 1})
    with patch.object(store.store, "async_save", side_effect=OSError("full")):
        with pytest.raises(OSError):
            await write(hass, plant, profile_id=data["assigned_profile_id"], profile=nonlinear(geometry=None))
    assert store.data == before and plant.covers[0]._motion.model is not None
    profile = store.data["profiles"][data["assigned_profile_id"]]
    profile.pop("geometry_provenance")
    read = await read_profile(hass, store.entry_id, plant.covers[0].entity_id)
    assert read["configured"]["opening_roll"]["provenance"]["source"] == "unknown"
    data = await write(hass, plant, profile_id=data["assigned_profile_id"], profile=nonlinear())
    assert data["profiles"][0]["geometry_provenance"]["opening_roll"]["source"] == "unknown"
    bad = copy.deepcopy(store.data)
    del bad["profiles"][data["assigned_profile_id"]]["geometry"]
    with pytest.raises(vol.Invalid, match="evidence"):
        validate_scaling(bad)
    assert profile_motion({"opening_time": 20, "closing_time": 30}) is None
    assert motion_from_settings({}) is None


async def test_v7_migration_preserves_exact_data_and_removes_its_backup_on_entry_removal(hass, plant):
    entry, cover = plant.entries[0], plant.covers[0]
    store = get_store(hass, entry.entry_id)
    original = copy.deepcopy(store.data)
    key = store.store.key
    await Store(hass, 7, key).async_save(original)
    hass.data[DATA_KEY].pop(entry.entry_id)
    store = get_store(hass, entry.entry_id)
    with patch('custom_components.myhome.cover_profiles.ProfileStorage.async_save', side_effect=OSError("full")):
        with pytest.raises(OSError):
            await bind_cover(hass, cover)
    assert not store.loaded
    assert await Store(hass, 7, key).async_load() == original
    await bind_cover(hass, cover)
    assert store.data == original and cover._motion.model is None
    assert await Store(hass, 7, key + '.pre_nonlinear').async_load() == original
    assert await Store(hass, 8, key).async_load() == original
    await remove_entry(hass, entry.entry_id)
    assert await Store(hass, 7, key + '.pre_nonlinear').async_load() is None


async def test_geometry_websocket_round_trip_and_schema_rejections(hass, plant, hass_ws_client):
    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    request = {"type": WS_WRITE, "entry_id": plant.entries[0].entry_id,
               "entity_id": plant.covers[0].entity_id, "revision": 0, "action": "save", "profile": nonlinear()}
    try:
        await client.send_json({"id": 1, **request})
        response = await client.receive_json()
        assert response["success"] and response["result"]["model"] == "slat_roll"
        profile_id = response["result"]["assigned_profile_id"]
        for request_id, bad in enumerate([{}, {"slat_time_s": True, "opening_roll": 2, "closing_roll": 2}], start=2):
            await client.send_json({"id": request_id, **request, "revision": 1, "profile_id": profile_id,
                                    "profile": nonlinear(geometry=bad)})
            assert not (await client.receive_json())["success"]
        await client.send_json({"id": 4, **request, "revision": 1, "profile_id": profile_id,
                                "profile": nonlinear(geometry=None)})
        response = await client.receive_json()
        assert response["success"] and response["result"]["model"] == "linear_time"
        assert response["result"]["revision"] == 2
    finally:
        await client.close()
