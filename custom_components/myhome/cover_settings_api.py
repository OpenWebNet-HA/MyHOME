"""Gateway overview for the shared cover backend; registry names remain native."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .cover_profiles import WS_OVERVIEW, ProfileError, get_store, public_settings, respond
from .cover_settings import MODEL


async def overview(hass: Any, entry_id: str) -> dict[str, Any]:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ProfileError("target_not_found")
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.load()
        if hass.config_entries.async_get_entry(entry_id) is not entry:
            raise ProfileError("target_not_found")
        records = {r.unique_id: r for r in er.async_entries_for_config_entry(er.async_get(hass), entry_id)
                   if r.domain == "cover" and r.platform == DOMAIN}
        covers = []
        for unique, record in records.items():
            cover = store.covers.get(unique)
            configured = public_settings(store, record, records, active=False)
            covers.append({"entity_id": record.entity_id,
                           "name": record.name or record.original_name or record.entity_id,
                           "profile_id": store.data["assignments"].get(unique),
                           "available": bool(cover and cover.available and not record.disabled_by),
                           "advanced": bool(cover and cover._advanced),
                           "pending": bool(cover and cover._pending_profile is not None),
                           "configured": configured,
                           "overrides": {direction: item for direction, item in (configured or {}).items()
                                         if item["origin"] == "override"},
                           "effective": public_settings(store, record, records, active=True)})
        profiles = []
        for profile_id, profile in store.data["profiles"].items():
            followers = [unique for unique, assigned in store.data["assignments"].items() if assigned == profile_id]
            # Public evidence uses registry identities; removed origins remain unknown.
            from .cover_profile_provenance import public_provenance
            profiles.append({"id": profile_id, "name": profile["name"],
                             "opening_time": profile["opening_time"], "closing_time": profile["closing_time"],
                             "provenance": public_provenance(profile, records, None),
                             "model": MODEL, "scaling": "unscaled",
                             "assigned_to": [records[unique].entity_id if unique in records else None for unique in followers]})
        return {"entry_id": entry_id, "revision": store.data["revision"], "schema_version": 1,
                "storage_version": 6, "model": MODEL, "scaling": "unscaled",
                "accuracy": {"kind": "not_measured"},
                "capabilities": {"height_scaling": False, "nonlinear": False,
                                 "profile_management": True, "override_write": True, "shared_profile_write": True},
                "profiles": profiles, "covers": covers}


@websocket_command({vol.Required("type"): WS_OVERVIEW, vol.Required("entry_id"): str})
@require_admin
@async_response
async def ws_overview(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    await respond(hass, connection, msg, overview(hass, msg["entry_id"]))
