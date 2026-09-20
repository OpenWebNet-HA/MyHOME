"""Shared per-key resolution for linear timing and explicit profile geometry.

The same validated values drive storage previews, the API and timed covers.
Personal directional times remain unscaled; absent geometry remains linear.
"""
from __future__ import annotations

import copy
import math
from typing import Any

import voluptuous as vol

from .cover_geometry import GEOMETRY_KEYS, UNKNOWN, merge_geometry, profile_motion
from .cover_profile_provenance import EVIDENCE

MODEL = "linear_time"
KEYS = {"opening": "up", "closing": "down"}


def seconds(value: Any) -> float:
    """Reject booleans, nonfinite values and unsupported travel durations."""
    if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600:
        raise vol.Invalid("travel_time must be between 1 and 600 seconds")
    return float(value)


def centimetres(value: Any) -> float:
    """Validate an explicitly supplied physical travel, never infer one."""
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.1 <= value <= 10000:
        raise vol.Invalid("travel_cm must be between 0.1 and 10000 centimetres")
    return float(value)


def reference_profile(profile: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Omission retains existing geometry; explicit null removes it."""
    result = copy.deepcopy(profile)
    if "reference_travel_cm" not in result and previous and "reference_travel_cm" in previous:
        result["reference_travel_cm"] = previous["reference_travel_cm"]
    if result.get("reference_travel_cm", False) is None:
        result.pop("reference_travel_cm")
    merge_geometry(result, previous)
    return result


def set_overrides(data: dict[str, Any], unique: str, overrides: dict[str, Any]) -> None:
    """Changing timing overrides must preserve the cover's own travel."""
    record = data["covers"].setdefault(unique, {})
    record["overrides"] = overrides
    if not overrides and "travel_cm" not in record:
        data["covers"].pop(unique)


OVERRIDE = vol.Schema({vol.Required("value"): seconds, vol.Required("provenance"): EVIDENCE})
COVER_RECORD = vol.Schema({vol.Optional("overrides", default=dict): {
    vol.In(KEYS): OVERRIDE,
}, vol.Optional("travel_cm"): centimetres})
# Keep native evidence verbatim in storage. Public responses allowlist its fields.
NATIVE_EVIDENCE = vol.Schema({vol.Required("source"): str,
                             vol.Required("recorded_at"): vol.Any(str, None),
                             vol.Required("origin_unique_id"): vol.Any(str, None)})
NATIVE = vol.Schema({vol.Required("up"): seconds, vol.Required("down"): seconds,
                    vol.Optional("provenance"): {vol.Required(direction): NATIVE_EVIDENCE for direction in KEYS}},
                    extra=vol.ALLOW_EXTRA)


def native_provenance(native: dict[str, Any], direction: str) -> dict[str, Any]:
    """Keep per-key evidence when available, otherwise report the original record."""
    return copy.deepcopy(native.get("provenance", {}).get(direction, {
        "source": native.get("source", "unknown"), "recorded_at": native.get("measured_at"),
        "origin_unique_id": None,
    }))


def migrate(data: dict[str, Any], native: Any) -> dict[str, Any]:
    """Preserve assignments, native fallback and revision without promoting values."""
    result = copy.deepcopy(data)
    if any(profile_id not in result["profiles"] for profile_id in result["assignments"].values()):
        raise vol.Invalid("Cannot migrate an assignment to a missing profile")
    if not isinstance(native, dict):
        raise vol.Invalid("Invalid native cover timings")
    result["native_fallbacks"] = {str(key): NATIVE(value) for key, value in native.items()}
    result["covers"] = {}
    return result


def resolve(*, profile: dict[str, Any] | None, native: dict[str, Any] | None,
            overrides: dict[str, Any], default: float | None,
            default_source: str = "default", travel_cm: float | None = None) -> dict[str, Any]:
    """One resolver for runtime and API; no clock, registry or storage side effects."""
    result = {}
    motion = profile_motion(profile, overrides, travel_cm) if profile else None
    for direction, native_key in KEYS.items():
        scaled = False
        if direction in overrides:
            record = overrides[direction]
            value, origin, provenance = record["value"], "override", record["provenance"]
        elif profile is not None:
            value, origin = profile[f"{direction}_time"], "profile"
            provenance = profile["provenance"][direction]
            if travel_cm is not None and profile.get("reference_travel_cm") is not None:
                value = (getattr(motion, f"{direction}_time_s") if motion else
                         round(seconds(value * centimetres(travel_cm) / centimetres(profile["reference_travel_cm"])), 4))
                scaled = True
        elif native is not None:
            value, origin = native[native_key], "native_fallback"
            provenance = native_provenance(native, direction)
        else:
            value, origin = default, default_source
            provenance = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
        result[direction] = {"value": value, "origin": origin,
                             "provenance": copy.deepcopy(provenance), "scaled": scaled}
    if motion and profile:
        for key in GEOMETRY_KEYS:
            result[key] = {"value": getattr(motion, key), "origin": "profile",
                           "provenance": copy.deepcopy(profile.get("geometry_provenance", {}).get(key, UNKNOWN)),
                           "scaled": travel_cm is not None and profile.get("reference_travel_cm") is not None}
    return result


def validate_scaling(data: dict[str, Any]) -> None:
    """Reject unsupported effective durations before persistence, including orphans."""
    for profile in data["profiles"].values():
        if "geometry_provenance" in profile and "geometry" not in profile:
            raise vol.Invalid("Geometry evidence requires geometry")
        profile_motion(profile)
    for unique, profile_id in data["assignments"].items():
        if profile_id not in data["profiles"]:
            raise vol.Invalid("Missing assigned profile")
        record = data["covers"].get(unique, {})
        resolve(profile=data["profiles"][profile_id], native=None, overrides=record.get("overrides", {}),
                default=None, travel_cm=record.get("travel_cm"))
