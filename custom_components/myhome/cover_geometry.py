"""Validated optional profile geometry and the shared nonlinear resolver adapter."""
from __future__ import annotations

import copy
import math
from typing import Any

import voluptuous as vol

from .cover_motion import CoverMotionModel
from .cover_profile_provenance import EVIDENCE, evidence, public_provenance

GEOMETRY_KEYS = ("slat_time_s", "opening_roll", "closing_roll")
NONLINEAR_MODEL = "slat_roll"
DEFAULT_GEOMETRY = {"slat_time_s": 0.0, "opening_roll": 1.0, "closing_roll": 1.0}
UNKNOWN = {"source": "unknown", "recorded_at": None, "origin_unique_id": None}


def geometry_number(value: Any, *, roll: bool = False) -> float:
    minimum, maximum = (1, 5) if roll else (0, 600)
    if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
        raise vol.Invalid("Invalid slat time or roll coefficient")
    return float(value)


GEOMETRY = vol.Schema({vol.Required(key): (lambda value: geometry_number(value, roll=True))
                       if key.endswith("roll") else geometry_number for key in GEOMETRY_KEYS})
GEOMETRY_PROVENANCE = vol.Schema({vol.Required(key): EVIDENCE for key in GEOMETRY_KEYS})


def merge_geometry(profile: dict[str, Any], previous: dict[str, Any] | None) -> None:
    """Old clients retain geometry; explicit null opts back into linear timing."""
    if "geometry" not in profile and previous and "geometry" in previous:
        profile["geometry"] = copy.deepcopy(previous["geometry"])
    if profile.get("geometry", False) is None:
        profile.pop("geometry")


def stamp_geometry(profile: dict[str, Any], previous: dict[str, Any] | None, unique: str | None) -> None:
    """Only edited keys acquire new manual evidence, never synthetic measurements."""
    if "geometry" in profile:
        profile["geometry_provenance"] = {
            key: copy.deepcopy(previous.get("geometry_provenance", {}).get(key, UNKNOWN))
            if previous and previous.get("geometry", {}).get(key) == profile["geometry"][key]
            else evidence("manual", unique) for key in GEOMETRY_KEYS
        }


def profile_motion(profile: dict[str, Any], overrides: dict[str, Any] | None = None,
                   travel_cm: float | None = None) -> CoverMotionModel | None:
    """Scale inherited geometry before applying personal directional motor times."""
    if "geometry" not in profile:
        return None
    try:
        model = CoverMotionModel(opening_time_s=profile["opening_time"], closing_time_s=profile["closing_time"],
                                 **GEOMETRY(profile["geometry"]))
        return model.scaled(reference_travel_cm=profile.get("reference_travel_cm"), travel_cm=travel_cm,
                            overrides={f"{key}_time_s": item["value"] for key, item in (overrides or {}).items()})
    except ValueError as error:
        raise vol.Invalid(str(error)) from error


def motion_from_settings(settings: dict[str, Any]) -> CoverMotionModel | None:
    if "slat_time_s" not in settings:
        return None
    return CoverMotionModel(opening_time_s=settings["opening"]["value"], closing_time_s=settings["closing"]["value"],
                             **{key: settings[key]["value"] for key in GEOMETRY_KEYS})


def model_name(settings: dict[str, Any] | None) -> str:
    return NONLINEAR_MODEL if settings and "slat_time_s" in settings else "linear_time"


def public_geometry(profile: dict[str, Any], records: Any, unique: Any = None) -> dict[str, Any]:
    if "geometry" not in profile:
        return {}
    return {"geometry": copy.deepcopy(profile["geometry"]), "geometry_provenance": public_provenance(
        {"provenance": {key: profile.get("geometry_provenance", {}).get(key, UNKNOWN) for key in GEOMETRY_KEYS}}, records, unique)}


def geometry_impact(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """An additive preview section, including explicit return to linear defaults."""
    if "slat_time_s" not in before and "slat_time_s" not in after:
        return {}
    return {"model_before": model_name(before), "model_after": model_name(after),
            "geometry_changes": {key: {"before": before.get(key, {}).get("value", DEFAULT_GEOMETRY[key]),
                                       "after": after.get(key, {}).get("value", DEFAULT_GEOMETRY[key]),
                                       "scaled": after.get(key, {}).get("scaled", False)} for key in GEOMETRY_KEYS}}


def reference_timing(profile: dict[str, Any], travel: float, measured: float) -> float:
    """Undo profile scaling for a timing-only shared measurement, not its geometry."""
    ratio = travel / profile["reference_travel_cm"]
    if "geometry" not in profile:
        return float(measured * profile["reference_travel_cm"] / travel)
    geometry = profile["geometry"]
    roll = geometry["closing_roll"]
    effective_roll = math.sqrt(1 + (roll * roll - 1) * ratio)
    scale = ratio * (roll + 1) / (effective_roll + 1)
    return float(geometry["slat_time_s"] + (measured - geometry["slat_time_s"] * ratio) / scale)
