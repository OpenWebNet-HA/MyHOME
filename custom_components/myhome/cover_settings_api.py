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
from .cover_geometry import NONLINEAR_MODEL, model_name, public_geometry
from .cover_profile_assignment import assignment_reason
from .cover_profiles import WS_OVERVIEW, ProfileError, get_store, public_settings, respond
from .cover_settings import (
    CHECK_FIELDS,
    MODEL,
    linear_geometry,
    precision_class,
    slats_state,
    travel_edit,
)
from .typing_compat import as_any


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
            effective = public_settings(store, record, records, active=True)
            covers.append({"entity_id": record.entity_id,
                           "assignment_reason": assignment_reason(entry, record, cover, store),
                           "name": record.name or record.original_name or record.entity_id,
                           "profile_id": store.data["assignments"].get(unique),
                           "travel_cm": store.data["covers"].get(unique, {}).get("travel_cm"),
                           # The verified precision kept at the last calibration save (None: no check).
                           **{key: store.data["covers"].get(unique, {}).get(key) for key in CHECK_FIELDS},
                           "available": bool(cover and cover.available and not record.disabled_by),
                           "advanced": bool(cover and cover._advanced),
                           "pending": bool(cover and cover._pending_profile is not None),
                           "configured": configured,
                           "configured_model": model_name(configured),
                           "model": model_name(cover._effective_cover_settings) if cover else None,
                           "position_known": cover.current_cover_position is not None if cover else False,
                           "overrides": {direction: item for direction, item in (configured or {}).items()
                                         if item["origin"] == "override"},
                           "effective": effective,
                           # What the calibration is (none, basic, refined, verified), so the panel need not infer it.
                           "precision": precision_class(effective, store.data["covers"].get(unique),
                                                        store.data["profiles"].get(store.data["assignments"].get(unique))),
                           # What a travel entered on the card does for this cover (`manual_travel`).
                           "travel_edit": travel_edit(store.data, unique),
                           # What is known of its slats (cover_settings.SLATS_STATES).
                           "slats_state": slats_state(store.data, unique)})
        profiles = []
        for profile_id, profile in store.data["profiles"].items():
            followers = [unique for unique, assigned in store.data["assignments"].items() if assigned == profile_id]
            # Public evidence uses registry identities; removed origins remain unknown.
            from .cover_profile_provenance import public_provenance
            profiles.append({"id": profile_id, "name": profile["name"],
                             "opening_time": profile["opening_time"], "closing_time": profile["closing_time"],
                             "reference_travel_cm": profile.get("reference_travel_cm"),
                             # Whether it has slats, from its slat time (> 0 yes, 0 no; no geometry: None).
                             "slats": profile["geometry"]["slat_time_s"] > 0 if profile.get("geometry") else None,
                             "provenance": public_provenance(profile, records, None),
                             **public_geometry(profile, records),
                             "model": MODEL if linear_geometry(profile) else NONLINEAR_MODEL, "scaling": "height" if profile.get("reference_travel_cm") else "unscaled",
                             "assigned_to": [records[unique].entity_id if unique in records else None for unique in followers]})
        return {"entry_id": entry_id, "revision": store.data["revision"], "schema_version": 1,
                "storage_version": 8, "model": "per_cover", "models": [MODEL, NONLINEAR_MODEL], "scaling": "optional_height",
                "accuracy": {"kind": "not_measured"},
                "capabilities": {"height_scaling": True, "nonlinear": True, "nonlinear_calibration": True, "nonlinear_calibration_modes": ["basic_new_profile"], "independent_calibration_check": False, "geometry_overrides": False,
                                 "profile_assignment": True,
                                 "profile_management": True, "override_write": True, "shared_profile_write": True},
                "profiles": profiles, "covers": covers}


@websocket_command(as_any({vol.Required("type"): WS_OVERVIEW, vol.Required("entry_id"): str}))
@require_admin
@async_response
async def ws_overview(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    await respond(hass, connection, msg, overview(hass, msg["entry_id"]))
