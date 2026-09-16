"""Shared, unscaled timing schema and pure per-key resolution.

Only supported linear timing fields are accepted. Geometry, fitting and shared
mutation APIs will extend this contract separately; absent measurements stay absent.
"""
from __future__ import annotations

import copy
import math
from typing import Any

import voluptuous as vol

from .cover_profile_provenance import EVIDENCE

MODEL = "linear_time"
KEYS = {"opening": "up", "closing": "down"}


def seconds(value: Any) -> float:
    """Reject booleans, nonfinite values and unsupported travel durations."""
    if type(value) not in (int, float) or not math.isfinite(value) or not 1 <= value <= 600:
        raise vol.Invalid("travel_time must be between 1 and 600 seconds")
    return float(value)


OVERRIDE = vol.Schema({vol.Required("value"): seconds, vol.Required("provenance"): EVIDENCE})
COVER_RECORD = vol.Schema({vol.Optional("overrides", default=dict): {
    vol.In(KEYS): OVERRIDE,
}})
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
            default_source: str = "default") -> dict[str, Any]:
    """One resolver for runtime and API; no clock, registry or storage side effects."""
    result = {}
    for direction, native_key in KEYS.items():
        if direction in overrides:
            record = overrides[direction]
            value, origin, provenance = record["value"], "override", record["provenance"]
        elif profile is not None:
            value, origin = profile[f"{direction}_time"], "profile"
            provenance = profile["provenance"][direction]
        elif native is not None:
            value, origin = native[native_key], "native_fallback"
            provenance = native_provenance(native, direction)
        else:
            value, origin = default, default_source
            provenance = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
        result[direction] = {"value": value, "origin": origin,
                             "provenance": copy.deepcopy(provenance), "scaled": False}
    return result
