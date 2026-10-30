"""Reviewed measurement saves into individual values or an assigned profile."""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from .cover_calibration import ready_cover
from .cover_geometry import reference_timing
from .cover_profile_mutations import shared_preview
from .cover_profile_provenance import DIRECTIONS, PROVENANCE
from .cover_profiles import PROFILE, ProfileError, commit_profiles, target
from .cover_settings import clear_check, profile_times_changed, record_check, seconds, set_overrides


async def save_measurement(session: Any, msg: dict[str, Any], *, preview: bool = False) -> Any:
    """Resolve times and evidence exclusively from the owned backend session."""
    store, hass = session.store, session.hass
    async with store.lock:
        if store.calibration is not session or not session.active:
            raise ProfileError("calibration_expired")
        if session.revision != store.data["revision"]:
            raise ProfileError("revision_conflict")
        entry, entity = target(hass, session.entry_id, session.cover.entity_id)
        if ready_cover(hass, store, entry.entry_id, entity.entity_id) is not session.cover:
            raise ProfileError("cover_unavailable")
        directions = (session.direction,) if session.direction else DIRECTIONS
        values = PROFILE({"name": "Measurement", **session.values})
        provenance = PROVENANCE(session.provenance)
        data = copy.deepcopy(store.data)
        affected = [entity.unique_id]
        if msg["save_mode"] == "cover":
            overrides = data["covers"].setdefault(entity.unique_id, {"overrides": {}})["overrides"]
            for direction in directions:
                overrides[direction] = {"value": values[f"{direction}_time"],
                                        "provenance": copy.deepcopy(provenance[direction])}
        else:
            profile_id = data["assignments"].get(entity.unique_id)
            if profile_id not in data["profiles"]:
                raise ProfileError("profile_not_found")
            profile = data["profiles"][profile_id]
            reference = profile.get("reference_travel_cm")
            travel = data["covers"].get(entity.unique_id, {}).get("travel_cm")
            if reference is not None and travel is None:
                raise ProfileError("profile_reference_required")
            for direction in directions:
                # Measurements are made on this cover; shared times describe the
                # reference cover. Do not scale a measured duration a second time.
                value = values[f"{direction}_time"]
                profile[f"{direction}_time"] = seconds(reference_timing(profile, travel, value)) if reference is not None else value
                profile["provenance"][direction] = copy.deepcopy(provenance[direction])
            proposal = {key: profile[key] for key in ("name", "opening_time", "closing_time")}
            if reference is not None:
                proposal["reference_travel_cm"] = reference
            if "geometry" in profile:
                proposal["geometry"] = copy.deepcopy(profile["geometry"])
            impact = shared_preview(store, entity, profile_id, proposal, clear_overrides=directions)
            # A confirmation from another measurement cannot authorize this save.
            impact["confirmation"] = hashlib.sha256((session.id + impact["confirmation"]).encode()).hexdigest()
            if preview:
                return impact
            if msg.get("confirmation") != impact["confirmation"]:
                raise ProfileError("preview_required")
            affected = [unique for unique, assigned in data["assignments"].items() if assigned == profile_id]
            if any(store.covers[unique].native_calibration_busy() for unique in affected if unique in store.covers):
                raise ProfileError("calibration_busy")
            overrides = data["covers"].get(entity.unique_id, {}).get("overrides", {})
            for direction in directions:
                overrides.pop(direction, None)
            set_overrides(data, entity.unique_id, overrides)
            # R-3: the other covers that follow the profile get other times: their checks no longer apply.
            if profile_times_changed(store.data["profiles"].get(profile_id), profile):
                for unique in affected:
                    if unique != entity.unique_id:
                        clear_check(data, unique)
        # L-73: times saved without a check of their own: the measured cover's stored check no longer applies.
        record_check(data, entity.unique_id, getattr(session, "check", None))
        await commit_profiles(hass, store, entry.entry_id, data, affected)
        return data["revision"]
