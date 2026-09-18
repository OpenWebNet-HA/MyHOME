"""Gateway-scoped cover profiles with atomic persistence and runtime snapshots.

Explicit geometry enables slat/roll timing; legacy profiles remain linear.
The panel and runtime consume the same shared settings resolver.
"""
from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError

from .const import CONF_COVER_TRAVEL_TIMES, DOMAIN
from .cover_geometry import (
    GEOMETRY,
    GEOMETRY_PROVENANCE,
    NONLINEAR_MODEL,
    model_name,
    public_geometry,
    stamp_geometry,
)
from .cover_profile_provenance import (
    DIRECTIONS,
    PROVENANCE,
    evidence,
    public_provenance,
    unknown_provenance,
)
from .cover_settings import (
    COVER_RECORD,
    KEYS,
    MODEL,
    NATIVE,
    centimetres,
    migrate,
    native_provenance,
    reference_profile,
    resolve,
    seconds,
    set_overrides,
    validate_scaling,
)

DATA_KEY = f"{DOMAIN}_cover_profile_stores"
WS_READ = "myhome/cover_profiles/read"
WS_WRITE = "myhome/cover_profiles/write"
WS_SUBSCRIBE = "myhome/cover_profiles/subscribe"
WS_OVERVIEW = "myhome/cover_profiles/overview"
MAX_PROFILES = 200


def travel_time(value: Any) -> Any:
    """Accept finite seconds, including fractions, without treating booleans as numbers."""
    return seconds(value)


LEGACY_PROFILE = vol.Schema({
    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=64)),
    vol.Required("travel_time"): travel_time,
})

def directional_profile(profile: dict[str, Any]) -> Any:
    """Upgrade a validated legacy profile without changing its motion timing."""
    return {"name": profile["name"], "opening_time": profile["travel_time"],
            "closing_time": profile["travel_time"]}


DIRECTIONAL_PROFILE = vol.Schema({
    vol.Required("name"): vol.All(str, vol.Strip, vol.Length(min=1, max=64)),
    vol.Required("opening_time"): travel_time,
    vol.Required("closing_time"): travel_time,
    vol.Optional("reference_travel_cm"): vol.Any(None, centimetres),
    vol.Optional("geometry"): vol.Any(None, GEOMETRY),
})
PROFILE = vol.Any(DIRECTIONAL_PROFILE, vol.All(LEGACY_PROFILE, directional_profile))
STORED_DIRECTIONAL_PROFILE = DIRECTIONAL_PROFILE.extend({
    vol.Optional("reference_travel_cm"): centimetres,
    vol.Optional("geometry"): GEOMETRY,
    vol.Optional("geometry_provenance"): GEOMETRY_PROVENANCE,
    vol.Optional("provenance", default=unknown_provenance): PROVENANCE,
})
STORED_PROFILE = vol.Any(STORED_DIRECTIONAL_PROFILE,
                         vol.All(LEGACY_PROFILE, directional_profile, STORED_DIRECTIONAL_PROFILE))
LEGACY_STORED = vol.Schema({
    vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Required("profiles"): {str: STORED_PROFILE},
    vol.Required("assignments"): {str: str},
})
STORED = LEGACY_STORED.extend({
    vol.Required("native_fallbacks"): {str: NATIVE},
    vol.Required("covers"): {str: COVER_RECORD},
})
TARGET: dict[str | vol.Marker, Any] = {vol.Required("entry_id"): str, vol.Required("entity_id"): str}


class ProfileError(Exception):
    """A stable error code for the panel to translate."""


class ProfileStorage(Store[dict[str, Any]]):
    """Surface write failures: HA's default Store logs them and returns success."""

    async def _async_migrate_func(self, old_major_version: Any, old_minor_version: Any, old_data: Any) -> Any:
        if old_major_version == 7:
            data = STORED(old_data)
            await ProfileStorage(self.hass, 7, f"{self.key}.pre_nonlinear", atomic_writes=True).async_save(old_data)
            return data
        if old_major_version == 6:
            data = STORED(old_data)
            await ProfileStorage(self.hass, 6, f"{self.key}.pre_travel", atomic_writes=True).async_save(old_data)
            return data
        if old_major_version == 5:
            data = STORED(old_data)
            await ProfileStorage(self.hass, 5, f"{self.key}.pre_catalogue", atomic_writes=True).async_save(old_data)
            return data
        if old_major_version not in (1, 2, 3, 4):
            raise NotImplementedError
        data = migrate(LEGACY_STORED(old_data), self.native_options())
        # Save the exact old payload/version before HA replaces the main file.
        await ProfileStorage(self.hass, old_major_version, f"{self.key}.pre_shared", atomic_writes=True).async_save(old_data)
        return STORED(data)

    def native_options(self) -> Any:
        entry_id = self.key.removeprefix(f"{DOMAIN}.cover_profiles.")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        return entry.options.get(CONF_COVER_TRAVEL_TIMES, {}) if entry else {}

    async def _async_write_data(self, *args: Any) -> None:
        try:
            await super()._async_write_data(*args)
        except (WriteError, SerializationError) as error:
            raise OSError("Could not persist cover profiles") from error


class CoverProfileStore:
    """Persist a complete mutation before publishing its new revision in memory."""

    def __init__(self, hass: Any, entry_id: str) -> None:
        self.hass, self.entry_id = hass, entry_id
        self.store = ProfileStorage(
            hass, 8, f"{DOMAIN}.cover_profiles.{entry_id}", atomic_writes=True
        )
        self.lock = asyncio.Lock()
        self.loaded = False
        self.data: dict[str, Any] = {"revision": 0, "profiles": {}, "assignments": {},
                                    "covers": {}, "native_fallbacks": {}}
        self.covers: dict[str, Any] = {}
        self.calibration = None
        self.calibration_command_lock = asyncio.Lock()

    async def load(self) -> None:
        """Caller holds lock; invalid storage must not silently overwrite saved data."""
        if not self.loaded:
            saved = await self.store.async_load()
            if saved is None:
                saved = migrate({"revision": 0, "profiles": {}, "assignments": {}}, self.store.native_options())
                await self.store.async_save(saved)
            self.data = STORED(saved)
            validate_scaling(self.data)
            self.loaded = True

    def profile(self, unique_id: str) -> Any:
        """Resolve an explicit assignment, otherwise retain YAML/runtime defaults."""
        profile_id = self.data["assignments"].get(unique_id)
        profile = self.data["profiles"].get(profile_id)
        return {"id": profile_id, **profile} if profile else None

    def native_identity(self, device_id: str) -> str | None:
        """Use the same identity rule as MyHOMEEntity, scoped to this config entry."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        mac = entry.data.get("mac") if entry else None
        return f"{mac}-2-{device_id.removeprefix('2-')}" if mac else None

    def native_for(self, unique_id: str) -> Any:
        for device_id, values in self.data["native_fallbacks"].items():
            if self.native_identity(device_id) == unique_id:
                return values
        return None


def get_store(hass: Any, entry_id: str) -> Any:
    """Share one store and lock per config entry, outside gateway runtime data."""
    stores = hass.data.setdefault(DATA_KEY, {})
    if entry_id not in stores:
        stores[entry_id] = CoverProfileStore(hass, entry_id)
    return stores[entry_id]


def target(hass: Any, entry_id: str, entity_id: str) -> Any:
    """Resolve only a native MyHOME cover belonging to the exact requested gateway."""
    entry = hass.config_entries.async_get_entry(entry_id)
    entity = er.async_get(hass).async_get(entity_id)
    if (entry is None or entry.domain != DOMAIN or entity is None
            or entity.config_entry_id != entry_id or entity.platform != DOMAIN
            or entity.domain != "cover"):
        raise ProfileError("target_not_found")
    return entry, entity


def snapshot(hass: Any, store: Any, entry: Any, entity: Any) -> Any:
    """An allowlisted view; never expose gateway credentials or runtime objects."""
    cover = store.covers.get(entity.unique_id)
    writable = bool(entry.state == ConfigEntryState.LOADED and entry.disabled_by is None
                    and entity.disabled_by is None and cover and cover.available)
    reason = None if writable else "cover_unavailable"
    if cover and cover._advanced:
        writable, reason = False, "advanced_cover"
    assigned = store.profile(entity.unique_id)
    travel = store.data["covers"].get(entity.unique_id, {}).get("travel_cm")
    records = {record.unique_id: record for record in
               er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
               if record.domain == "cover" and record.platform == DOMAIN}
    def assignments(profile_id: Any) -> Any:
        return [{"entity_id": records[unique].entity_id if unique in records else None,
                 "name": (records[unique].name or records[unique].original_name
                          or records[unique].entity_id) if unique in records else None}
                for unique, value in store.data["assignments"].items() if value == profile_id]
    return {
        "entry_id": entry.entry_id, "entity_id": entity.entity_id,
        "calibration": ({key: value for key, value in store.calibration.view().items()
                         if key != "attachment"} if store.calibration and (store.calibration.client_id or store.calibration.closed) else None),
        "revision": store.data["revision"], "assigned_profile_id": assigned["id"] if assigned else None,
        "model": model_name(cover._effective_cover_settings) if cover else None,
        "configured_model": NONLINEAR_MODEL if assigned and "geometry" in assigned else MODEL,
        "nonlinear": True, "position_known": cover.current_cover_position is not None if cover else False,
        "scaling": "height" if travel is not None and assigned and assigned.get("reference_travel_cm") else "unscaled",
        "travel_cm": travel, "height_scaling": True, "accuracy": {"kind": "not_measured"},
        "profiles": [{"id": key, **value, **public_geometry(value, records, entity.unique_id), "model": NONLINEAR_MODEL if "geometry" in value else MODEL, "scaling": "height" if value.get("reference_travel_cm") else "unscaled",
                      "provenance": public_provenance(value, records, entity.unique_id),
                      "uses": list(store.data["assignments"].values()).count(key),
                      "assigned_to": assignments(key)}
                     for key, value in store.data["profiles"].items()],
        "writable": writable, "reason": reason,
        "default_travel_time": cover._default_travel_time if cover else None,
        "effective_travel_time": cover._travel_time_up if cover else None,
        "effective_opening_time": cover._travel_time_up if cover else None,
        "effective_closing_time": cover._travel_time_down if cover else None,
        "pending": bool(cover and cover._pending_profile is not None),
        "configured": public_settings(store, entity, records, active=False),
        "effective": public_settings(store, entity, records, active=True),
    }


def public_settings(store: Any, entity: Any, records: Any, *, active: bool) -> Any:
    """Project the resolver/runtime snapshot without exposing registry unique IDs."""
    cover = store.covers.get(entity.unique_id)
    if cover and cover._advanced:
        return None
    if active:
        values = cover._effective_cover_settings if cover else None
    elif cover:
        values = cover.resolve_cover_settings(store.profile(entity.unique_id))
    else:
        values = resolve(profile=store.profile(entity.unique_id), native=store.native_for(entity.unique_id),
                         overrides=store.data["covers"].get(entity.unique_id, {}).get("overrides", {}),
                         travel_cm=store.data["covers"].get(entity.unique_id, {}).get("travel_cm"),
                         default=None, default_source="unavailable")
    if values is None:
        return None
    provenance = public_provenance({"provenance": {
        direction: item["provenance"] for direction, item in values.items()
    }}, records, entity.unique_id)
    return {direction: {**item, "provenance": provenance[direction]}
            for direction, item in values.items()}


async def read_profile(hass: Any, entry_id: str, entity_id: str) -> Any:
    entry, entity = target(hass, entry_id, entity_id)
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.load()
        return snapshot(hass, store, entry, entity)


async def write_profile(hass: Any, msg: dict[str, Any], *, calibration: Any=None) -> Any:
    """Persist validated edits; global changes require a revision-bound preview."""
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
        if store.covers[entity.unique_id].native_calibration_busy():
            raise ProfileError("calibration_busy")
        if store.calibration and (store.calibration.active or store.calibration.reservation.pending) and store.calibration is not calibration:
            raise ProfileError("calibration_busy")
        if calibration is not None and not calibration.active:
            raise ProfileError("calibration_expired")
        if msg["revision"] != store.data["revision"]:
            raise ProfileError("revision_conflict")
        if msg["action"] in ("travel", "overrides", "preview", "update_shared"):
            from .cover_profile_mutations import mutate_settings

            return await mutate_settings(hass, store, entry, entity, msg)
        data = copy.deepcopy(store.data)
        profile_id = msg.get("profile_id")
        if profile_id is not None and profile_id not in data["profiles"]:
            raise ProfileError("profile_not_found")
        if msg["action"] == "save":
            profile = PROFILE(msg["profile"])
            source_id = msg.get("copy_from_profile_id")
            if source_id is not None and profile_id is not None:
                raise ProfileError("invalid_profile")
            if source_id is not None and source_id not in data["profiles"]:
                raise ProfileError("profile_not_found")
            previous = data["profiles"].get(source_id or profile_id)
            profile = reference_profile(profile, previous)
            if profile_id is not None:
                if (data["assignments"].get(entity.unique_id) != profile_id
                        or list(data["assignments"].values()).count(profile_id) > 1):
                    raise ProfileError("profile_shared")
            else:
                if len(data["profiles"]) >= MAX_PROFILES:
                    raise ProfileError("profile_limit")
                profile_id = uuid4().hex
            if calibration is not None:
                profile["provenance"] = copy.deepcopy(PROVENANCE(calibration.provenance))
            else:
                profile["provenance"] = {
                    direction: copy.deepcopy(previous["provenance"][direction])
                    if previous and previous[f"{direction}_time"] == profile[f"{direction}_time"]
                    else evidence("manual", entity.unique_id)
                    for direction in DIRECTIONS
                }
            stamp_geometry(profile, previous, entity.unique_id)
            if calibration is not None and calibration.geometry_provenance is not None:
                profile["geometry_provenance"] = copy.deepcopy(GEOMETRY_PROVENANCE(calibration.geometry_provenance))
                data["covers"].setdefault(entity.unique_id, {})["travel_cm"] = calibration.measured_travel
            data["profiles"][profile_id] = profile
            if calibration is not None:
                # The measured cover follows the new profile; old overrides must
                # not silently mask the measurement just accepted in review.
                set_overrides(data, entity.unique_id, {})
        elif "copy_from_profile_id" in msg:
            raise ProfileError("invalid_profile")
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
        await commit_profiles(hass, store, entry_id, data,
                              [entity.unique_id] if msg["action"] != "delete" else [])
        return snapshot(hass, store, entry, entity)


async def commit_profiles(hass: Any, store: Any, entry_id: str, data: Any, affected: Any) -> None:
    """Caller holds the store lock and has validated the entire mutation."""
    validate_scaling(data)
    data["revision"] += 1
    await store.store.async_save(data)
    store.data = data
    async_dispatcher_send(hass, f"{WS_SUBSCRIBE}:{entry_id}", {
        "entry_id": entry_id, "revision": data["revision"], "kind": "changed",
    })
    # Unloaded covers restore on bind; moving covers retain pending semantics.
    for unique_id in affected:
        cover = store.covers.get(unique_id)
        if cover is not None:
            cover.async_apply_cover_profile(store.profile(unique_id))
            cover.async_write_ha_state()


async def write_native_timing(hass: Any, entry: Any, cover: Any, result: Any) -> None:
    """Native services share the same transaction and never rewrite legacy options."""
    store = get_store(hass, entry.entry_id)
    async with store.lock:
        await store.load()
        if hass.config_entries.async_get_entry(entry.entry_id) is not entry:
            raise ProfileError("target_not_found")
        cover._check_panel_timing_owner(lock_held=True)
        data = copy.deepcopy(store.data)
        if result is None:
            data["native_fallbacks"].pop(str(cover._device_id), None)
        else:
            previous = data["native_fallbacks"].get(str(cover._device_id))
            record = NATIVE(result)
            record["provenance"] = {
                direction: native_provenance(previous, direction)
                if previous and previous[key] == record[key] and result.get("source") != "measured"
                else native_provenance(record, direction)
                for direction, key in KEYS.items()
            }
            data["native_fallbacks"][str(cover._device_id)] = record
        await commit_profiles(hass, store, entry.entry_id, data, [])
        cover._profile_store = store


async def bind_cover(hass: Any, cover: Any) -> None:
    """Restore a profile before the cover queries its initial bus state."""
    entity = er.async_get(hass).async_get(cover.entity_id)
    if entity is None or entity.platform != DOMAIN or not entity.config_entry_id:
        return
    store = get_store(hass, entity.config_entry_id)
    async with store.lock:
        await store.load()
        store.covers[entity.unique_id] = cover
        cover._profile_store = store
        if not cover._advanced:
            cover.async_apply_cover_profile(store.profile(entity.unique_id))

    @callback
    def unbind() -> None:
        if store.covers.get(entity.unique_id) is cover:
            store.covers.pop(entity.unique_id)

    cover.async_on_remove(unbind)


async def remove_entry(hass: Any, entry_id: str) -> None:
    """Delete this gateway's stored assignments when its config entry is removed."""
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.store.async_remove()
        await Store(hass, 1, f"{store.store.key}.pre_shared").async_remove()
        await Store(hass, 5, f"{store.store.key}.pre_catalogue").async_remove()
        await Store(hass, 6, f"{store.store.key}.pre_travel").async_remove()
        await Store(hass, 7, f"{store.store.key}.pre_nonlinear").async_remove()
        if store.calibration:
            store.calibration.close("cover_unavailable")
        async_dispatcher_send(hass, f"{WS_SUBSCRIBE}:{entry_id}", {
            "entry_id": entry_id, "revision": store.data["revision"], "kind": "removed",
        })
        hass.data[DATA_KEY].pop(entry_id, None)


async def respond(hass: Any, connection: Any, msg: dict[str, Any], operation: Any) -> None:
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


@websocket_command({vol.Required("type"): WS_READ, **TARGET})
@require_admin
@async_response
async def ws_read(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    await respond(hass, connection, msg, read_profile(hass, msg["entry_id"], msg["entity_id"]))


@websocket_command({
    vol.Required("type"): WS_WRITE, **TARGET,
    vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Required("action"): vol.In(["assign", "save", "delete", "overrides", "preview", "update_shared", "travel"]),
    vol.Optional("travel_cm"): vol.Any(None, centimetres),
    vol.Optional("profile_id"): vol.Any(str, None),
    vol.Optional("copy_from_profile_id"): str,
    vol.Optional("profile"): PROFILE,
    vol.Optional("overrides"): {vol.In(KEYS): vol.Any(None, seconds)},
    vol.Optional("confirmation"): str,
})
@require_admin
@async_response
async def ws_write(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    if msg["action"] == "save" and "profile" not in msg:
        connection.send_error(msg["id"], "invalid_profile", "Profile is required")
        return
    await respond(hass, connection, msg, write_profile(hass, msg))


@websocket_command({
    vol.Required("type"): WS_SUBSCRIBE, vol.Required("entry_id"): str,
})
@require_admin
@async_response
async def ws_subscribe(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    """Send revision invalidations, registering before the initial synchronization."""
    entry_id = msg["entry_id"]
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "target_not_found", "Gateway not found")
        return
    store = get_store(hass, entry_id)
    active = True
    unsubscribe: Callable[[], None] | None = None

    @callback
    def cancel() -> None:
        nonlocal active
        active = False
        if unsubscribe is not None:
            unsubscribe()

    # HA can close the socket while this handler is waiting for disk/the lock.
    connection.subscriptions[msg["id"]] = cancel
    try:
        async with store.lock:
            await store.load()
            if not active:
                return
            if hass.config_entries.async_get_entry(entry_id) is not entry:
                raise ProfileError("target_not_found")

            @callback
            def changed(event: Any) -> None:
                connection.send_event(msg["id"], event)

            unsubscribe = async_dispatcher_connect(
                hass, f"{WS_SUBSCRIBE}:{entry_id}", changed
            )
            connection.send_result(msg["id"])
            changed({"entry_id": entry_id, "revision": store.data["revision"], "kind": "ready"})
    except (ProfileError, vol.Invalid, OSError) as error:
        connection.subscriptions.pop(msg["id"], None)
        cancel()
        code = (str(error) if isinstance(error, ProfileError) else
                "invalid_profile" if isinstance(error, vol.Invalid) else "storage_error")
        connection.send_error(msg["id"], code, code)


@callback
def register_api(hass: HomeAssistant) -> None:
    from .cover_profile_catalogue import ws_manage
    from .cover_profile_export import ws_export
    from .cover_settings_api import ws_overview

    websocket_api.async_register_command(hass, ws_manage)
    websocket_api.async_register_command(hass, ws_export)
    websocket_api.async_register_command(hass, ws_overview)
    websocket_api.async_register_command(hass, ws_read)
    websocket_api.async_register_command(hass, ws_write)
    websocket_api.async_register_command(hass, ws_subscribe)
    from .cover_calibration import register_api as register_calibration
    register_calibration(hass)
