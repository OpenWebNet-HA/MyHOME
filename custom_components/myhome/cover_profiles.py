"""Gateway-scoped travel-time profiles for the existing timed-cover runtime.

The storage/resolution split follows Interstellar0verdrive's calibration-store
approach. This contract supports a linear model with separate opening and closing
times; it does not claim compatibility with the fork's roll/height calibration.
"""
from __future__ import annotations

import asyncio
import copy
import math
from uuid import uuid4

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

from .const import DOMAIN

DATA_KEY = f"{DOMAIN}_cover_profile_stores"
WS_READ = "myhome/cover_profiles/read"
WS_WRITE = "myhome/cover_profiles/write"
MAX_PROFILES = 200


def travel_time(value):
    """Accept finite seconds, including fractions, without treating booleans as numbers."""
    if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600:
        raise vol.Invalid("travel_time must be between 1 and 600 seconds")
    return float(value)


LEGACY_PROFILE = vol.Schema({
    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=64)),
    vol.Required("travel_time"): travel_time,
})

def directional_profile(profile):
    """Upgrade a validated legacy profile without changing its motion timing."""
    return {"name": profile["name"], "opening_time": profile["travel_time"],
            "closing_time": profile["travel_time"]}


PROFILE = vol.Any(vol.Schema({
    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=64)),
    vol.Required("opening_time"): travel_time,
    vol.Required("closing_time"): travel_time,
}), vol.All(LEGACY_PROFILE, directional_profile))
STORED = vol.Schema({
    vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Required("profiles"): {str: PROFILE},
    vol.Required("assignments"): {str: str},
})
TARGET = {vol.Required("entry_id"): str, vol.Required("entity_id"): str}


class ProfileError(Exception):
    """A stable error code for the panel to translate."""


class ProfileStorage(Store):
    """Surface write failures: HA's default Store logs them and returns success."""

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        if old_major_version != 1:
            raise NotImplementedError
        return STORED(old_data)

    async def _async_write_data(self, *args):
        try:
            await super()._async_write_data(*args)
        except (WriteError, SerializationError) as error:
            raise OSError("Could not persist cover profiles") from error


class CoverProfileStore:
    """Persist a complete mutation before publishing its new revision in memory."""

    def __init__(self, hass, entry_id):
        self.store = ProfileStorage(
            hass, 2, f"{DOMAIN}.cover_profiles.{entry_id}", atomic_writes=True
        )
        self.lock = asyncio.Lock()
        self.loaded = False
        self.data = {"revision": 0, "profiles": {}, "assignments": {}}
        self.covers = {}
        self.calibration = None
        self.calibration_command_lock = asyncio.Lock()

    async def load(self):
        """Caller holds lock; invalid storage must not silently overwrite saved data."""
        if not self.loaded:
            saved = await self.store.async_load()
            if saved is not None:
                self.data = STORED(saved)
            self.loaded = True

    def profile(self, unique_id):
        """Resolve an explicit assignment, otherwise retain YAML/runtime defaults."""
        profile_id = self.data["assignments"].get(unique_id)
        profile = self.data["profiles"].get(profile_id)
        return {"id": profile_id, **profile} if profile else None


def get_store(hass, entry_id):
    """Share one store and lock per config entry, outside gateway runtime data."""
    stores = hass.data.setdefault(DATA_KEY, {})
    if entry_id not in stores:
        stores[entry_id] = CoverProfileStore(hass, entry_id)
    return stores[entry_id]


def target(hass, entry_id, entity_id):
    """Resolve only a native MyHOME cover belonging to the exact requested gateway."""
    entry = hass.config_entries.async_get_entry(entry_id)
    entity = er.async_get(hass).async_get(entity_id)
    if (entry is None or entry.domain != DOMAIN or entity is None
            or entity.config_entry_id != entry_id or entity.platform != DOMAIN
            or entity.domain != "cover"):
        raise ProfileError("target_not_found")
    return entry, entity


def snapshot(hass, store, entry, entity):
    """An allowlisted view; never expose gateway credentials or runtime objects."""
    cover = store.covers.get(entity.unique_id)
    writable = bool(entry.state == ConfigEntryState.LOADED and entry.disabled_by is None
                    and entity.disabled_by is None and cover and cover.available)
    reason = None if writable else "cover_unavailable"
    if cover and cover._advanced:
        writable, reason = False, "advanced_cover"
    assigned = store.profile(entity.unique_id)
    records = {record.unique_id: record for record in
               er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
               if record.domain == "cover" and record.platform == DOMAIN}
    def assignments(profile_id):
        return [{"entity_id": records[unique].entity_id if unique in records else None,
                 "name": (records[unique].name or records[unique].original_name
                          or records[unique].entity_id) if unique in records else None}
                for unique, value in store.data["assignments"].items() if value == profile_id]
    return {
        "entry_id": entry.entry_id, "entity_id": entity.entity_id,
        "revision": store.data["revision"], "assigned_profile_id": assigned["id"] if assigned else None,
        "profiles": [{"id": key, **value,
                      "uses": list(store.data["assignments"].values()).count(key),
                      "assigned_to": assignments(key)}
                     for key, value in store.data["profiles"].items()],
        "writable": writable, "reason": reason,
        "default_travel_time": cover._default_travel_time if cover else None,
        "effective_travel_time": cover._travel_time if cover else None,
        "effective_opening_time": cover._travel_time if cover else None,
        "effective_closing_time": cover._closing_time if cover else None,
        "pending": bool(cover and cover._pending_profile is not None),
    }


async def read_profile(hass, entry_id, entity_id):
    entry, entity = target(hass, entry_id, entity_id)
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.load()
        return snapshot(hass, store, entry, entity)


async def write_profile(hass, msg, *, calibration=None):
    """Change one cover; shared profiles are copied explicitly instead of edited globally."""
    entry_id, entity_id = msg["entry_id"], msg["entity_id"]
    # Validate before allocating storage and again after any wait for the lock/load.
    target(hass, entry_id, entity_id)
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.load()
        entry, entity = target(hass, entry_id, entity_id)
        if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.stopped):
            raise ProfileError("cover_unavailable")
        current = snapshot(hass, store, entry, entity)
        if not current["writable"]:
            raise ProfileError(current["reason"])
        if store.calibration and store.calibration.active and store.calibration is not calibration:
            raise ProfileError("calibration_busy")
        if calibration is not None and not calibration.active:
            raise ProfileError("calibration_expired")
        if msg["revision"] != store.data["revision"]:
            raise ProfileError("revision_conflict")
        data = copy.deepcopy(store.data)
        profile_id = msg.get("profile_id")
        if profile_id is not None and profile_id not in data["profiles"]:
            raise ProfileError("profile_not_found")
        if msg["action"] == "save":
            profile = PROFILE(msg["profile"])
            if profile_id is not None:
                if (data["assignments"].get(entity.unique_id) != profile_id
                        or list(data["assignments"].values()).count(profile_id) > 1):
                    raise ProfileError("profile_shared")
            else:
                if len(data["profiles"]) >= MAX_PROFILES:
                    raise ProfileError("profile_limit")
                profile_id = uuid4().hex
            data["profiles"][profile_id] = profile
        if msg["action"] == "delete":
            if profile_id is None:
                raise ProfileError("profile_not_found")
            if profile_id in data["assignments"].values():
                raise ProfileError("profile_in_use")
            del data["profiles"][profile_id]
        elif profile_id is None:
            data["assignments"].pop(entity.unique_id, None)
        else:
            data["assignments"][entity.unique_id] = profile_id
        data["revision"] += 1
        await store.store.async_save(data)
        store.data = data
        # The entity may have unloaded while storage was writing. Its next mount
        # resolves the persisted assignment. Never send a bus command or reload HA.
        cover = store.covers.get(entity.unique_id)
        if cover is not None and msg["action"] != "delete":
            cover.async_apply_cover_profile(store.profile(entity.unique_id))
            cover.async_write_ha_state()
        return snapshot(hass, store, entry, entity)


async def bind_cover(hass, cover):
    """Restore a profile before the cover queries its initial bus state."""
    entity = er.async_get(hass).async_get(cover.entity_id)
    if entity is None or entity.platform != DOMAIN or not entity.config_entry_id:
        return
    store = get_store(hass, entity.config_entry_id)
    async with store.lock:
        await store.load()
        store.covers[entity.unique_id] = cover
        if not cover._advanced:
            cover.async_apply_cover_profile(store.profile(entity.unique_id))

    @callback
    def unbind():
        if store.covers.get(entity.unique_id) is cover:
            store.covers.pop(entity.unique_id)

    cover.async_on_remove(unbind)


async def remove_entry(hass, entry_id):
    """Delete this gateway's stored assignments when its config entry is removed."""
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.store.async_remove()
        hass.data[DATA_KEY].pop(entry_id, None)


async def respond(hass, connection, msg, operation):
    try:
        result = await operation
    except ProfileError as error:
        connection.send_error(msg["id"], str(error), str(error))
    except vol.Invalid:
        connection.send_error(msg["id"], "invalid_profile", "Invalid profile data")
    except OSError:
        connection.send_error(msg["id"], "storage_error", "Could not persist cover profiles")
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): WS_READ, **TARGET})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_read(hass, connection, msg):
    await respond(hass, connection, msg, read_profile(hass, msg["entry_id"], msg["entity_id"]))


@websocket_api.websocket_command({
    vol.Required("type"): WS_WRITE, **TARGET,
    vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Required("action"): vol.In(["assign", "save", "delete"]),
    vol.Optional("profile_id"): vol.Any(str, None),
    vol.Optional("profile"): PROFILE,
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_write(hass, connection, msg):
    if msg["action"] == "save" and "profile" not in msg:
        connection.send_error(msg["id"], "invalid_profile", "Profile is required")
        return
    await respond(hass, connection, msg, write_profile(hass, msg))


@callback
def register_api(hass: HomeAssistant):
    websocket_api.async_register_command(hass, ws_read)
    websocket_api.async_register_command(hass, ws_write)
    from .cover_calibration import register_api as register_calibration
    register_calibration(hass)
