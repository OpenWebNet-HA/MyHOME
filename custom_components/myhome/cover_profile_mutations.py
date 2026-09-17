"""Per-cover overrides and revision-bound shared profile impact previews."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import voluptuous as vol
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .cover_profile_provenance import DIRECTIONS, evidence
from .cover_profiles import PROFILE, ProfileError, commit_profiles, snapshot
from .cover_settings import KEYS, seconds

OVERRIDE_PATCH = vol.All({vol.In(KEYS): vol.Any(None, seconds)}, vol.Length(min=1))


def shared_preview(store: Any, entity: Any, profile_id: str, profile: dict[str, Any], *, clear_overrides: tuple[str, ...] = ()) -> dict[str, Any]:
    """Include every stored follower, even when its registry/runtime is absent."""
    previous = store.data["profiles"][profile_id]
    records = {record.unique_id: record for record in er.async_entries_for_config_entry(
        er.async_get(store.hass), store.entry_id) if record.domain == "cover" and record.platform == DOMAIN}
    followers = []
    for unique, assigned in store.data["assignments"].items():
        if assigned != profile_id:
            continue
        record, cover = records.get(unique), store.covers.get(unique)
        overrides = store.data["covers"].get(unique, {}).get("overrides", {})
        followers.append({
            "entity_id": record.entity_id if record else None,
            "name": (record.name or record.original_name or record.entity_id) if record else None,
            "available": bool(record and not record.disabled_by and cover and cover.available),
            "changes": {direction: {
                "before": overrides[direction]["value"] if direction in overrides else previous[f"{direction}_time"],
                "after": overrides[direction]["value"] if direction in overrides and not (unique == entity.unique_id and direction in clear_overrides) else profile[f"{direction}_time"],
                "overridden": direction in overrides and not (unique == entity.unique_id and direction in clear_overrides),
                **({"override_removed": direction in overrides and unique == entity.unique_id and direction in clear_overrides} if clear_overrides else {}),
            } for direction in DIRECTIONS},
        })
    # Bind confirmation to the exact proposal, gateway, target and saved revision.
    # Runtime motion/availability can change without invalidating the configuration.
    payload = [store.entry_id, entity.unique_id, store.data["revision"], profile_id, profile, clear_overrides]
    token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {"revision": store.data["revision"], "profile_id": profile_id,
            "before": {key: previous[key] for key in profile}, "after": profile,
            "followers": followers, "confirmation": token}


async def mutate_settings(hass: Any, store: Any, entry: Any, entity: Any, msg: dict[str, Any]) -> Any:
    """Called under the gateway lock after common target/ownership/revision checks."""
    data = copy.deepcopy(store.data)
    action = msg["action"]
    if action == "overrides":
        patch = OVERRIDE_PATCH(msg.get("overrides", {}))
        overrides = data["covers"].get(entity.unique_id, {}).get("overrides", {})
        for direction, value in patch.items():
            if value is None:
                overrides.pop(direction, None)
            elif direction not in overrides or overrides[direction]["value"] != value:
                overrides[direction] = {"value": value, "provenance": evidence("manual", entity.unique_id)}
        if overrides:
            data["covers"][entity.unique_id] = {"overrides": overrides}
        else:
            data["covers"].pop(entity.unique_id, None)
        affected = [entity.unique_id]
    else:
        profile_id = msg.get("profile_id")
        if profile_id not in data["profiles"]:
            raise ProfileError("profile_not_found")
        if data["assignments"].get(entity.unique_id) != profile_id:
            raise ProfileError("profile_shared")
        profile = PROFILE(msg.get("profile", {}))
        preview = shared_preview(store, entity, profile_id, profile)
        if action == "preview":
            return preview
        if msg.get("confirmation") != preview["confirmation"]:
            raise ProfileError("preview_required")
        affected = [unique for unique, assigned in data["assignments"].items() if assigned == profile_id]
        if any(store.covers[unique].native_calibration_busy() for unique in affected if unique in store.covers):
            raise ProfileError("calibration_busy")
        previous = data["profiles"][profile_id]
        profile["provenance"] = {
            direction: copy.deepcopy(previous["provenance"][direction])
            if previous[f"{direction}_time"] == profile[f"{direction}_time"]
            else evidence("manual", entity.unique_id) for direction in DIRECTIONS
        }
        data["profiles"][profile_id] = profile
    await commit_profiles(hass, store, entry.entry_id, data, affected)
    return snapshot(hass, store, entry, entity)
