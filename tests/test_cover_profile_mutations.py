"""Public overrides and explicit shared edits preserve atomic/runtime semantics."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from pytest_socket import socket_enabled  # noqa: F401

from custom_components.myhome.cover_profile_provenance import evidence
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    WS_WRITE,
    ProfileError,
    bind_cover,
    get_store,
    read_profile,
    register_api,
    write_profile,
    ws_write,
)
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


async def shared(hass, plant):
    result = await write_profile(hass, message(plant))
    profile_id = result["assigned_profile_id"]
    await write_profile(hass, message(plant, 1, index=1, action="assign", profile_id=profile_id))
    return profile_id


async def test_overrides_patch_clear_provenance_restart_and_assignment(hass, plant):
    profile_id = await shared(hass, plant)
    store = get_store(hass, plant.entries[0].entry_id)
    result = await write_profile(hass, message(plant, 2, action="overrides", overrides={"opening": 19.25}))
    assert result["effective_opening_time"] == 19.25
    assert result["effective_closing_time"] == 42.5
    original = copy.deepcopy(result["configured"]["opening"]["provenance"])
    assert original["source"] == "manual" and original["origin_entity_id"] == plant.covers[0].entity_id
    assert plant.covers[0]._calibration_source == "panel_override"
    again = await write_profile(hass, message(plant, 3, action="overrides", overrides={"opening": 19.25, "closing": 33}))
    assert again["configured"]["opening"]["provenance"] == original
    # Removing the assignment does not remove personal values or create profiles.
    await write_profile(hass, message(plant, 4, action="assign", profile_id=None))
    assert len(store.data["profiles"]) == 1
    await write_profile(hass, message(plant, 5, action="overrides", overrides={"opening": None}))
    assert plant.covers[0]._travel_time_up == 30
    assert plant.covers[0]._travel_time_down == 33
    hass.data[DATA_KEY].pop(store.entry_id)
    await bind_cover(hass, plant.covers[0])
    store = get_store(hass, store.entry_id)
    assert plant.covers[0]._travel_time_down == 33
    await write_profile(hass, message(plant, 6, action="assign", profile_id=profile_id))
    restored = await write_profile(hass, message(plant, 7, action="overrides", overrides={"closing": None}))
    assert restored["configured"]["closing"]["origin"] == "profile"
    assert restored["effective_closing_time"] == 42.5 and store.data["covers"] == {}
    assert plant.covers[0]._calibration_source == "panel_profile"
    plant.gateways[0].send.assert_not_called()


@pytest.mark.parametrize("values", [{}, {"opening": True}, {"closing": float("nan")}, {"opening": 0},
                                        {"opening": 601}, {"height": 20}, {"opening": {"value": 20, "provenance": {}}}])
async def test_invalid_override_does_not_mutate(hass, plant, values):
    store = get_store(hass, plant.entries[0].entry_id)
    before = copy.deepcopy(store.data)
    with pytest.raises(vol.Invalid):
        await write_profile(hass, message(plant, action="overrides", overrides=values))
    assert store.data == before and plant.covers[0]._travel_time_up == 30


async def test_shared_preview_covers_masked_orphan_and_offline_followers(hass, plant):
    profile_id = await shared(hass, plant)
    await write_profile(hass, message(plant, 2, index=1, action="overrides", overrides={"opening": 18}))
    store = get_store(hass, plant.entries[0].entry_id)
    store.data["assignments"]["removed-internal-id"] = profile_id
    store.covers.pop(plant.covers[1].unique_id)
    before = copy.deepcopy(store.data)
    msg = message(plant, 3, action="preview", profile_id=profile_id,
                  profile={"name": "Shared new", "opening_time": 25, "closing_time": 42.5})
    preview = await write_profile(hass, msg)
    assert store.data == before and plant.covers[0]._travel_time_up == 42.5
    assert len(preview["followers"]) == 3
    masked = preview["followers"][1]
    assert masked["changes"]["opening"] == {"before": 18, "after": 18, "overridden": True, "scaled": False}
    assert not masked["available"]
    assert preview["followers"][2]["entity_id"] is None
    serialized = json.dumps(preview)
    assert "removed-internal-id" not in serialized and plant.covers[0].unique_id not in serialized
    result = await write_profile(hass, {**msg, "action": "update_shared", "confirmation": preview["confirmation"]})
    assert result["revision"] == 4 and len(store.data["profiles"]) == 1
    assert result["effective_closing_time"] == 42.5
    assert store.data["profiles"][profile_id]["provenance"]["closing"] == before["profiles"][profile_id]["provenance"]["closing"]
    assert result["effective_opening_time"] == 25
    await bind_cover(hass, plant.covers[1])
    assert plant.covers[1]._travel_time_up == 18
    assert plant.covers[1]._travel_time_down == 42.5
    assert plant.covers[2]._travel_time_up == 30


async def test_shared_confirmation_rejects_changed_proposal_revision_and_other_target(hass, plant):
    profile_id = await shared(hass, plant)
    msg = message(plant, 2, action="preview", profile_id=profile_id)
    preview = await write_profile(hass, msg)
    confirm = {**msg, "action": "update_shared", "confirmation": preview["confirmation"]}
    for change in ({"confirmation": "invented"}, {"profile": {"name": "Changed", "travel_time": 22}},
                   {"entity_id": plant.covers[1].entity_id}):
        with pytest.raises(ProfileError, match="preview_required"):
            await write_profile(hass, {**confirm, **change})
    for change, error in [({"profile_id": "absent"}, "profile_not_found"),
                          ({"entry_id": plant.entries[1].entry_id}, "target_not_found")]:
        with pytest.raises(ProfileError, match=error):
            await write_profile(hass, {**confirm, **change})
    await write_profile(hass, message(plant, 2, index=1, action="assign", profile_id=None))
    with pytest.raises(ProfileError, match="revision_conflict"):
        await write_profile(hass, confirm)
    with pytest.raises(ProfileError, match="profile_shared"):
        await write_profile(hass, {**msg, "revision": 3, "entity_id": plant.covers[1].entity_id})
    with pytest.raises(vol.Invalid):
        await write_profile(hass, {**msg, "revision": 3, "profile": {}})


@pytest.mark.parametrize("action", ["overrides", "update_shared"])
async def test_write_failure_and_moving_cover_keep_active_timing(hass, plant, action):
    profile_id = await shared(hass, plant)
    store = get_store(hass, plant.entries[0].entry_id)
    msg = message(plant, 2, action="preview", profile_id=profile_id,
                  profile={"name": "Changed", "travel_time": 21}, overrides={"opening": 21})
    preview = await write_profile(hass, msg)
    msg.update(action=action, confirmation=preview["confirmation"])
    before = copy.deepcopy(store.data)
    with patch.object(store.store, "async_save", side_effect=OSError("full")):
        with pytest.raises(OSError):
            await write_profile(hass, msg)
    assert store.data == before and plant.covers[0]._travel_time_up == 42.5
    for cover in plant.covers[:2]:
        cover._attr_is_opening = True
    result = await write_profile(hass, msg)
    assert result["pending"] and result["configured"]["opening"]["value"] == 21
    assert result["effective_opening_time"] == 42.5
    for cover in plant.covers[:2]:
        cover._attr_is_opening = False
        cover._apply_pending_cover_profile()
    assert plant.covers[0]._travel_time_up == 21
    assert plant.covers[1]._travel_time_up == (21 if action == "update_shared" else 42.5)
    persisted = await Store(hass, 7, store.store.key).async_load()
    assert persisted == store.data


async def test_shared_edits_cannot_interrupt_another_followers_native_calibration(hass, plant):
    profile_id = await shared(hass, plant)
    msg = message(plant, 2, action="preview", profile_id=profile_id)
    preview = await write_profile(hass, msg)
    with patch.object(plant.covers[1], "native_calibration_busy", return_value=True):
        with pytest.raises(ProfileError, match="calibration_busy"):
            await write_profile(hass, {**msg, "action": "update_shared", "confirmation": preview["confirmation"]})
    assert get_store(hass, plant.entries[0].entry_id).data["revision"] == 2


async def test_override_restores_native_timing_and_calibration_save_follows_new_profile(hass, plant):
    store = get_store(hass, plant.entries[0].entry_id)
    cover = plant.covers[0]
    store.data["native_fallbacks"][cover._device_id] = {"up": 23, "down": 27}
    await write_profile(hass, message(plant, action="overrides", overrides={"opening": 19}))
    result = await write_profile(hass, message(plant, 1, action="overrides", overrides={"opening": None}))
    assert result["configured"]["opening"]["origin"] == "native_fallback"
    assert result["effective_opening_time"] == 23
    await write_profile(hass, message(plant, 2, action="overrides", overrides={"opening": 19}))
    session = SimpleNamespace(active=True, provenance={key: evidence("guided", cover.unique_id) for key in ("opening", "closing")})
    await write_profile(hass, message(plant, 3), calibration=session)
    assert store.data["covers"] == {} and cover._travel_time_up == 42.5


async def test_websocket_mutations_validation_authorization_and_preview_roundtrip(hass, plant, hass_ws_client):
    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    async def send(index, **msg):
        await client.send_json({"id": index, "type": WS_WRITE, **msg})
        return await client.receive_json()
    first = await send(1, **message(plant))
    profile_id = first["result"]["assigned_profile_id"]
    preview = await send(2, **message(plant, 1, action="preview", profile_id=profile_id))
    assert preview["success"] and preview["result"]["revision"] == 1
    updated = await send(3, **message(plant, 1, action="update_shared", profile_id=profile_id,
                                   confirmation=preview["result"]["confirmation"]))
    assert updated["success"] and updated["result"]["revision"] == 2
    result = await send(4, **message(plant, 2, action="overrides", overrides={"opening": 20}))
    assert result["result"]["effective_opening_time"] == 20
    invalid = await send(5, **message(plant, 3, action="overrides", overrides={"opening": True}))
    assert invalid["error"]["code"] == "invalid_format"
    empty = await send(6, **message(plant, 3, action="overrides", overrides={}))
    assert empty["error"]["code"] == "invalid_profile"
    await client.close()
    connection = SimpleNamespace(user=SimpleNamespace(is_admin=False), send_error=MagicMock())
    with pytest.raises(Unauthorized):
        ws_write(hass, connection, {"id": 7, **message(plant, action="overrides", overrides={"opening": 20})})
    # Advanced and disabled targets share the established write guards.
    plant.covers[0]._advanced = True
    with pytest.raises(ProfileError, match="advanced_cover"):
        await write_profile(hass, message(plant, 3, action="overrides", overrides={"opening": 20}))
    plant.covers[0]._advanced = False
    er.async_get(hass).async_update_entity(plant.covers[0].entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    with pytest.raises(ProfileError, match="cover_unavailable"):
        await write_profile(hass, message(plant, 3, action="overrides", overrides={"opening": 20}))
    assert (await read_profile(hass, plant.entries[0].entry_id, plant.covers[0].entity_id))["revision"] == 3


async def test_shared_preview_only_includes_followers_of_the_selected_profile(hass, plant):
    first = await write_profile(hass, message(plant))
    second = await write_profile(hass, message(plant, 1, index=1))
    preview = await write_profile(hass, message(plant, 2, action="preview", profile_id=first["assigned_profile_id"]))
    assert [row["entity_id"] for row in preview["followers"]] == [plant.covers[0].entity_id]
    await write_profile(hass, message(plant, 2, action="update_shared", profile_id=first["assigned_profile_id"],
                                     confirmation=preview["confirmation"]))
    result = await read_profile(hass, plant.entries[0].entry_id, plant.covers[1].entity_id)
    assert result["assigned_profile_id"] == second["assigned_profile_id"]
