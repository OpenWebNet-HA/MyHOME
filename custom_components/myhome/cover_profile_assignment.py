"""Atomic multi-cover assignment with an exact, gateway-scoped preview."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState

from .cover_geometry import geometry_impact
from .cover_profile_provenance import DIRECTIONS
from .cover_profiles import ProfileError, commit_profiles, target

SELECTION = vol.All([str], vol.Length(min=1, max=200), vol.Unique())


def assignment_reason(entry: Any, record: Any, cover: Any, store: Any) -> str | None:
    """Keep selection eligibility and commit-time validation consistent."""
    if (entry.state != ConfigEntryState.LOADED or entry.disabled_by is not None
            or record.disabled_by is not None or cover is None or not cover.available):
        return "cover_unavailable"
    if cover._advanced:
        return "advanced_cover"
    if store.calibration is not None or cover.native_calibration_busy():
        return "calibration_busy"
    return None


async def assign_profiles(hass: Any, store: Any, entry: Any, msg: dict[str, Any]) -> dict[str, Any]:
    """Called under the catalogue lock after gateway/revision/profile validation."""
    try:
        entity_ids = sorted(SELECTION(msg.get("entity_ids", [])))
    except vol.Invalid as error:
        raise ProfileError("invalid_selection") from error
    profile_id = msg["profile_id"]
    profile = store.data["profiles"][profile_id]
    targets, identities = [], []
    for entity_id in entity_ids:
        _, record = target(hass, entry.entry_id, entity_id)
        cover = store.covers.get(record.unique_id)
        reason = assignment_reason(entry, record, cover, store)
        if reason is not None:
            raise ProfileError(reason)
        previous = store.profile(record.unique_id)
        before = cover.resolve_cover_settings(previous)
        after = cover.resolve_cover_settings({"id": profile_id, **profile})
        identities.append(record.unique_id)
        targets.append({
            "entity_id": entity_id, "name": record.name or record.original_name or entity_id,
            "previous_profile_id": previous["id"] if previous else None,
            "previous_profile_name": previous["name"] if previous else None,
            **geometry_impact(before, after),
            "changes": {direction: {
                "before": before[direction]["value"], "after": after[direction]["value"],
                "overridden": after[direction]["origin"] == "override",
                "scaled": after[direction]["scaled"],
            } for direction in DIRECTIONS},
        })
    # Bind identities as well as entity IDs: registry replacement must invalidate
    # confirmation even if it does not change the profile store's revision.
    proposal = {"entry_id": entry.entry_id, "revision": store.data["revision"],
                "profile_id": profile_id, "profile_name": profile["name"], "targets": targets}
    payload = ["assign", identities, proposal]
    confirmation = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if msg["action"] == "preview_assign":
        return {**proposal, "confirmation": confirmation}
    if msg.get("confirmation") != confirmation:
        raise ProfileError("preview_required")
    data = copy.deepcopy(store.data)
    for unique in identities:
        data["assignments"][unique] = profile_id
    await commit_profiles(hass, store, entry.entry_id, data, identities)
    return {"entry_id": entry.entry_id, "revision": data["revision"],
            "profile_id": profile_id, "entity_ids": entity_ids}
