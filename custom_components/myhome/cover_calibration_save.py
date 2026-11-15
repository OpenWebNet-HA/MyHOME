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
from .cover_settings import (
    clear_check,
    profile_source,
    profile_times_changed,
    record_check,
    seconds,
    set_overrides,
)


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
        # A session saves into a profile only; `cover` is no longer one of its destinations.
        if msg["save_mode"] != "shared":
            raise ProfileError("invalid_profile")
        profile_id = data["assignments"].get(entity.unique_id)
        if profile_id not in data["profiles"]:
            raise ProfileError("profile_not_found")
        profile = data["profiles"][profile_id]
        if not profile_source(profile, entity.unique_id):
            raise ProfileError("profile_not_source")  # Only the source updates its profile.
        reference = profile.get("reference_travel_cm")
        travel = data["covers"].get(entity.unique_id, {}).get("travel_cm")
        if reference is not None and travel is None:
            # A cover without a travel of its own uses the profile's reference travel (its times are the profile's
            # as they are, a factor of 1): its measurement describes the reference cover as it is. Only the source
            # gets here, and it has no travel of its own when the reference travel was entered from the catalogue:
            # the factor 1 is still what it uses.
            travel = reference
        # A basic calibration's slat time goes into the profile too (before the times, which it scales).
        shared = session.shared_geometry(profile, travel)
        if shared is not None:
            profile["geometry"], profile["geometry_provenance"] = shared
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
        impact = shared_preview(store, entity, profile_id, proposal, clear_overrides=directions,
                                clears_checks=profile_times_changed(store.data["profiles"].get(profile_id), profile))
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
        # The other covers that follow the profile get other times: their checks no longer apply.
        if profile_times_changed(store.data["profiles"].get(profile_id), profile):
            for unique in affected:
                if unique != entity.unique_id:
                    clear_check(data, unique)
        # Times saved without a check of their own: the measured cover's stored check no longer applies.
        record_check(data, entity.unique_id, getattr(session, "check", None))
        await commit_profiles(hass, store, entry.entry_id, data, affected)
        return data["revision"]

