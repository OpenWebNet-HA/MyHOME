"""Shared-store migration and runtime/API parity on real HA storage and covers."""
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from aiohttp.resolver import ThreadedResolver
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from OWNd.message import OWNMessage
from pytest_socket import socket_enabled  # noqa: F401 (socket plugin disabled)

from custom_components.myhome.const import CONF_COVER_TRAVEL_TIMES
from custom_components.myhome.cover_profile_export import export_profiles
from custom_components.myhome.cover_profile_provenance import evidence, unknown_provenance
from custom_components.myhome.cover_profiles import (
    DATA_KEY,
    STORED,
    WS_OVERVIEW,
    ProfileError,
    bind_cover,
    get_store,
    read_profile,
    register_api,
    remove_entry,
    write_profile,
)
from custom_components.myhome.cover_settings import migrate
from custom_components.myhome.cover_settings_api import overview
from tests.test_cover_profiles import message
from tests.test_cover_profiles import plant as plant_fixture

plant = plant_fixture


async def seed_legacy(hass, plant, version=4):
    entry, cover = plant.entries[0], plant.covers[0]
    plant.gateways[0].config_entry = entry
    native = {str(cover._device_id): {"up": 24.5, "down": 18.5, "source": "manual", "measured_at": "2026-09-16T12:00:00+00:00"},
              "99": {"up": 19.0, "down": 17.0}}
    hass.config_entries.async_update_entry(entry, options={"keep": "unchanged", CONF_COVER_TRAVEL_TIMES: native})
    legacy = {"revision": 12, "profiles": {"legacy": {"name": "Legacy", "opening_time": 35.0, "closing_time": 25.0,
                                                       "provenance": unknown_provenance()}},
              "assignments": {cover.unique_id: "legacy"}}
    if version == 1:
        legacy["profiles"]["legacy"] = {"name": "Legacy", "travel_time": 35.0}
    await Store(hass, version, f"myhome.cover_profiles.{entry.entry_id}").async_save(legacy)
    hass.data[DATA_KEY].pop(entry.entry_id)
    return entry, cover, legacy, native


@pytest.mark.parametrize("version", [1, 2, 3, 4])
async def test_migration_preserves_profile_fallback_evidence_and_original_copy(hass, plant, version):
    entry, cover, legacy, native = await seed_legacy(hass, plant, version)
    await bind_cover(hass, cover)
    store = get_store(hass, entry.entry_id)
    assert store.data["revision"] == 12
    assert store.data["covers"] == {}  # Native values never become overriding measurements.
    assert store.data["native_fallbacks"] == native
    assert cover._travel_time_up == 35
    assert cover._travel_time_down == (35 if version == 1 else 25)
    assert await Store(hass, version, f"{store.store.key}.pre_shared").async_load() == legacy
    assert entry.options[CONF_COVER_TRAVEL_TIMES] == native
    original = copy.deepcopy(store.data)
    # Old options are a backup, never an authority after the first successful migration.
    hass.config_entries.async_update_entry(entry, options={CONF_COVER_TRAVEL_TIMES: {"11": {"up": 2, "down": 3}}})
    hass.data[DATA_KEY].pop(entry.entry_id)
    await bind_cover(hass, cover)
    store = get_store(hass, entry.entry_id)
    assert store.data == original
    reset = await write_profile(hass, message(plant, 12, action="assign", profile_id=None))
    assert reset["effective_opening_time"] == 24.5
    assert reset["effective_closing_time"] == 18.5
    assert reset["effective"]["opening"]["origin"] == "native_fallback"
    assert reset["effective"]["opening"]["provenance"]["source"] == "manual"
    await cover.async_reset_travel_time()
    assert cover._travel_time_up == 25
    hass.data[DATA_KEY].pop(entry.entry_id)
    await bind_cover(hass, cover)
    assert cover._travel_time_up == 25  # The old option cannot resurrect a reset value.
    assert get_store(hass, entry.entry_id).data["native_fallbacks"]["99"] == native["99"]
    plant.gateways[0].send.assert_not_called()


@pytest.mark.parametrize("bad", [{"11": {"up": float("nan"), "down": 20}}, {"11": {"up": 20}}, []])
async def test_invalid_migration_keeps_original_and_does_not_publish(hass, plant, bad):
    entry, cover, legacy, _ = await seed_legacy(hass, plant)
    hass.config_entries.async_update_entry(entry, options={CONF_COVER_TRAVEL_TIMES: bad})
    with pytest.raises(vol.Invalid):
        await bind_cover(hass, cover)
    assert not get_store(hass, entry.entry_id).loaded
    assert await Store(hass, 4, f"myhome.cover_profiles.{entry.entry_id}").async_load() == legacy
    assert cover._travel_time_up == 30


async def test_migration_rejects_missing_profile_and_retries_after_write_failure(hass, plant):
    with pytest.raises(vol.Invalid):
        migrate({"profiles": {}, "assignments": {"cover": "missing"}, "revision": 0}, {})
    entry, cover, legacy, _ = await seed_legacy(hass, plant)
    with patch.object(Store, "_async_write_data", side_effect=WriteError("full")):
        with pytest.raises(OSError):
            await bind_cover(hass, cover)
    assert not get_store(hass, entry.entry_id).loaded
    assert await Store(hass, 4, f"myhome.cover_profiles.{entry.entry_id}").async_load() == legacy
    await bind_cover(hass, cover)
    assert cover._travel_time_up == 35


async def test_native_write_is_atomic_revisioned_and_defers_during_motion(hass, plant):
    entry, cover = plant.entries[0], plant.covers[0]
    plant.gateways[0].config_entry = entry
    store = get_store(hass, entry.entry_id)
    before = copy.deepcopy(store.data)
    with patch.object(Store, "_async_write_data", side_effect=WriteError("full")):
        with pytest.raises(OSError):
            await cover.async_set_travel_time(travel_time=40)
    assert store.data == before
    assert cover._travel_time_up == 30
    cover.handle_event(OWNMessage.parse("*2*1*11##"))
    await cover.async_set_travel_time(travel_time_down=40, travel_time_up=50)
    assert store.data["revision"] == 1
    row = (await overview(hass, entry.entry_id))["covers"][0]
    assert row["pending"] and row["effective"]["opening"]["value"] == 30
    assert row["configured"]["opening"]["value"] == 50
    cover._attr_is_opening = False
    cover._move_start_time = None
    cover._apply_pending_cover_profile()
    row = (await overview(hass, entry.entry_id))["covers"][0]
    assert row["effective"] == row["configured"]
    assert cover._travel_time_down == 40
    assert CONF_COVER_TRAVEL_TIMES not in entry.options
    with pytest.raises(ProfileError, match="revision_conflict"):
        await write_profile(hass, message(plant, 0))
    plant.gateways[0].send.assert_not_called()


async def test_overview_runtime_provenance_offline_advanced_and_gateway_isolation(hass, plant):
    entry, cover, _, _ = await seed_legacy(hass, plant)
    await bind_cover(hass, cover)
    store = get_store(hass, entry.entry_id)
    # Exercise the schema/resolver with existing guided evidence.
    data = copy.deepcopy(store.data)
    data["covers"][cover.unique_id] = {"overrides": {"closing": {"value": 28.0, "provenance": evidence("guided", cover.unique_id)}}}
    store.data = STORED(data)
    cover.async_apply_cover_profile(store.profile(cover.unique_id))
    result = await overview(hass, entry.entry_id)
    row = next(r for r in result["covers"] if r["entity_id"] == cover.entity_id)
    assert row["effective"] == row["configured"]
    assert row["effective"]["opening"]["value"] == cover._travel_time_up == 35
    assert row["effective"]["closing"]["value"] == cover._travel_time_down == 28
    assert row["effective"]["closing"]["origin"] == "override"
    assert row["effective"]["closing"]["provenance"]["origin_entity_id"] == cover.entity_id
    assert result["model"] == "linear_time" and result["scaling"] == "unscaled"
    assert result["accuracy"] == {"kind": "not_measured"}
    assert result["capabilities"] == {"height_scaling": False, "nonlinear": False,
                                      "profile_management": True, "override_write": True, "shared_profile_write": True}
    assert len(result["covers"]) == 2
    assert len((await overview(hass, plant.entries[1].entry_id))["covers"]) == 1
    assert "00:03:50" not in json.dumps(result).replace(cover.entity_id, "").replace(plant.covers[1].entity_id, "")
    store.covers.pop(cover.unique_id)
    store.data["covers"].clear()
    store.data["assignments"].clear()
    offline = (await overview(hass, entry.entry_id))["covers"][0]
    assert offline["effective"] is None and not offline["available"]
    assert offline["configured"]["opening"]["value"] == 24.5
    other = plant.covers[1]
    await bind_cover(hass, other)
    other._advanced = True
    advanced = (await read_profile(hass, entry.entry_id, other.entity_id))
    assert advanced["effective"] is None and advanced["configured"] is None
    store.covers.pop(other.unique_id)
    missing = (await overview(hass, entry.entry_id))["covers"][1]
    assert missing["configured"]["opening"]["value"] is None


async def test_overview_websocket_auth_and_removed_entry(hass, plant, hass_ws_client):
    register_api(hass)
    with patch("aiohttp.connector.DefaultResolver", ThreadedResolver):
        client = await hass_ws_client(hass)
    entry_id = plant.entries[0].entry_id
    await client.send_json({"id": 1, "type": WS_OVERVIEW, "entry_id": entry_id})
    response = await client.receive_json()
    assert response["success"] and response["result"]["storage_version"] == 6
    await client.send_json({"id": 2, "type": WS_OVERVIEW, "entry_id": "missing"})
    assert (await client.receive_json())["error"]["code"] == "target_not_found"
    # Administrator authorization is exercised separately through the decorated handler below.
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from custom_components.myhome.cover_settings_api import ws_overview
    connection = SimpleNamespace(user=SimpleNamespace(is_admin=False), send_error=MagicMock())
    with pytest.raises(Unauthorized):
        ws_overview(hass, connection, {"id": 3, "entry_id": entry_id})
    await client.close()
    store = get_store(hass, entry_id)
    with patch.object(store, "load", new=AsyncMock()), patch.object(hass.config_entries, "async_get_entry", side_effect=[plant.entries[0], None]):
        with pytest.raises(ProfileError, match="target_not_found"):
            await overview(hass, entry_id)


async def test_export_includes_native_fallbacks_and_override_evidence_and_cleanup(hass, plant):
    entry, cover, _, _ = await seed_legacy(hass, plant)
    await bind_cover(hass, cover)
    store = get_store(hass, entry.entry_id)
    store.data["covers"][cover.unique_id] = {"overrides": {"closing": {"value": 27, "provenance": evidence("manual", cover.unique_id)}}}
    exported = await export_profiles(hass, entry.entry_id)
    assert exported["format_version"] == 3 and len(exported["native_fallbacks"]) == 2
    ref = next(r["id"] for r in exported["covers"] if r["entity_id"] == cover.entity_id)
    assert exported["overrides"][0]["cover_id"] == ref
    assert exported["overrides"][0]["values"]["closing"]["value"] == 27
    assert next(r for r in exported["native_fallbacks"] if r["cover_id"] == ref)["opening_time"] == 24.5
    assert cover.unique_id not in json.dumps(exported)
    await remove_entry(hass, entry.entry_id)
    assert await Store(hass, 4, f"{store.store.key}.pre_shared").async_load() is None


async def test_unbound_native_fallback_and_entry_removal_during_native_write(hass, plant):
    from custom_components.myhome.cover_profiles import write_native_timing
    entry, cover, _, native = await seed_legacy(hass, plant)
    cover._profile_store = None
    assert cover.native_cover_fallback() == native[cover._device_id]
    store = get_store(hass, entry.entry_id)
    with patch.object(store, "load", new=AsyncMock()), patch.object(hass.config_entries, "async_get_entry", return_value=None):
        with pytest.raises(ProfileError, match="target_not_found"):
            await write_native_timing(hass, entry, cover, {"up": 10, "down": 12})
    plant.gateways[0].config_entry = None
    assert cover.native_cover_fallback() is None


async def test_failed_main_migration_write_retains_original_and_backup(hass, plant):
    from custom_components.myhome.cover_profiles import ProfileStorage
    entry, cover, legacy, _ = await seed_legacy(hass, plant)
    writer = ProfileStorage._async_write_data

    async def write(store, *args):
        if not store.key.endswith(".pre_shared"):
            raise OSError("main file cannot be replaced")
        await writer(store, *args)

    with patch.object(ProfileStorage, "_async_write_data", write):
        with pytest.raises(OSError):
            await bind_cover(hass, cover)
    store = get_store(hass, entry.entry_id)
    assert not store.loaded and cover._travel_time_up == 30
    assert await Store(hass, 4, store.store.key).async_load() == legacy
    assert await Store(hass, 4, f"{store.store.key}.pre_shared").async_load() == legacy
    await bind_cover(hass, cover)
    assert cover._travel_time_up == 35


async def test_native_partial_edit_retains_unchanged_direction_evidence(hass, plant):
    entry, cover, _, _ = await seed_legacy(hass, plant)
    await bind_cover(hass, cover)
    await write_profile(hass, message(plant, 12, action="assign", profile_id=None))
    before = await read_profile(hass, entry.entry_id, cover.entity_id)
    await cover.async_set_travel_time(travel_time_down=27, travel_time_up=24.5)
    after = await read_profile(hass, entry.entry_id, cover.entity_id)
    assert after["effective"]["opening"]["provenance"] == before["effective"]["opening"]["provenance"]
    assert after["effective"]["closing"]["provenance"]["recorded_at"] != before["effective"]["closing"]["provenance"]["recorded_at"]
