"""Gateway-scoped profile management without assigning or moving a cover."""
from __future__ import annotations

import copy
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.core import CoreState

from .const import DOMAIN
from .cover_profile_mutations import shared_preview
from .cover_profile_provenance import DIRECTIONS, evidence
from .cover_profiles import MAX_PROFILES, PROFILE, ProfileError, commit_profiles, get_store, respond

WS_MANAGE = "myhome/cover_profiles/manage"
NAME = vol.All(str, vol.Strip, vol.Length(min=1, max=64))


def gateway(hass: Any, entry_id: str) -> Any:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ProfileError("target_not_found")
    return entry


async def manage_profile(hass: Any, msg: dict[str, Any]) -> dict[str, Any]:
    entry = gateway(hass, msg["entry_id"])
    store = get_store(hass, entry.entry_id)
    async with store.lock:
        await store.load()
        if gateway(hass, entry.entry_id) is not entry:
            raise ProfileError("target_not_found")
        if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.stopped):
            raise ProfileError("cover_unavailable")
        if store.calibration is not None:
            raise ProfileError("calibration_busy")
        if msg["revision"] != store.data["revision"]:
            raise ProfileError("revision_conflict")
        profile_id, action = msg["profile_id"], msg["action"]
        if profile_id not in store.data["profiles"]:
            raise ProfileError("profile_not_found")
        allowed = {"preview": {"profile"}, "update": {"profile", "confirmation"},
                   "preview_assign": {"entity_ids"}, "assign": {"entity_ids", "confirmation"},
                   "duplicate": {"name"}, "delete": set()}
        if action not in allowed or set(msg) - {"id", "type", "entry_id", "profile_id", "revision", "action"} - allowed[action]:
            raise ProfileError("invalid_profile")
        if action in ("preview_assign", "assign"):
            from .cover_profile_assignment import assign_profiles
            return await assign_profiles(hass, store, entry, msg)
        data = copy.deepcopy(store.data)
        previous = data["profiles"][profile_id]
        followers = [unique for unique, assigned in data["assignments"].items() if assigned == profile_id]
        affected = []
        if action in ("preview", "update"):
            profile = PROFILE(msg.get("profile", {}))
            preview = shared_preview(store, None, profile_id, profile)
            if action == "preview":
                return preview
            if msg.get("confirmation") != preview["confirmation"]:
                raise ProfileError("preview_required")
            if any(store.covers[unique].native_calibration_busy() for unique in followers if unique in store.covers):
                raise ProfileError("calibration_busy")
            profile["provenance"] = {
                direction: copy.deepcopy(previous["provenance"][direction])
                if previous[f"{direction}_time"] == profile[f"{direction}_time"]
                else evidence("manual", None) for direction in DIRECTIONS
            }
            data["profiles"][profile_id] = profile
            affected = followers
        elif action == "duplicate":
            name = NAME(msg.get("name"))
            if len(data["profiles"]) >= MAX_PROFILES:
                raise ProfileError("profile_limit")
            profile_id = uuid4().hex
            data["profiles"][profile_id] = {**copy.deepcopy(previous), "name": name}
        else:
            if followers:
                raise ProfileError("profile_in_use")
            del data["profiles"][profile_id]
        await commit_profiles(hass, store, entry.entry_id, data, affected)
        return {"entry_id": entry.entry_id, "revision": data["revision"], "profile_id": profile_id}


@websocket_command({
    vol.Required("type"): WS_MANAGE, vol.Required("entry_id"): str,
    vol.Required("revision"): vol.All(int, vol.Range(min=0)), vol.Required("profile_id"): str,
    vol.Required("action"): vol.In(["preview", "update", "duplicate", "delete", "preview_assign", "assign"]),
    vol.Optional("entity_ids"): vol.All([str], vol.Length(min=1, max=200), vol.Unique()),
    vol.Optional("profile"): PROFILE, vol.Optional("name"): NAME, vol.Optional("confirmation"): str,
})
@require_admin
@async_response
async def ws_manage(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    await respond(hass, connection, msg, manage_profile(hass, msg))
