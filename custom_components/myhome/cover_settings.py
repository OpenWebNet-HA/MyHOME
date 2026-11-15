"""Shared per-key resolution for linear timing and explicit profile geometry.

The same validated values drive storage previews, the API and timed covers.
Personal directional times remain unscaled; absent geometry remains linear.
"""
from __future__ import annotations

import copy
import math
from typing import Any

import voluptuous as vol
from homeassistant.util import dt as dt_util

from .cover_geometry import DEFAULT_GEOMETRY, GEOMETRY_KEYS, UNKNOWN, merge_geometry, profile_motion
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
    """Changing timing overrides must preserve the cover's own travel and verified check."""
    record = data["covers"].setdefault(unique, {})
    record["overrides"] = overrides
    if not overrides and "travel_cm" not in record and "check_cm" not in record and "slats" not in record:
        data["covers"].pop(unique)


# The verified precision of the cover's calibration, kept in its record at save: the gap at the check to a tenth of a
# centimetre (absolute; records saved before kept it whole), the height read and the height expected, and when it was
# saved. Absent: no check.
CHECK_FIELDS = ("check_cm", "check_measured_cm", "check_expected_cm", "check_at")


def record_check(data: dict[str, Any], unique: str, check: Any) -> None:
    """A calibration save: keep its completed check, or clear the one of the times it replaces."""
    deviation, measured = getattr(check, "deviation_cm", None), getattr(check, "measured_cm", None)
    if (isinstance(deviation, bool) or not isinstance(deviation, (int, float)) or not math.isfinite(deviation)
            or measured is None or getattr(check, "expected_cm", None) is None):
        clear_check(data, unique)
        return
    data["covers"].setdefault(unique, {"overrides": {}}).update({
        "check_cm": round(abs(float(deviation)), 1), "check_measured_cm": float(measured),
        "check_expected_cm": float(check.expected_cm), "check_at": dt_util.utcnow().isoformat()})


# What a profile gives its followers' times: an edit of any of these gives them other times (and their checks go).
PROFILE_TIMING = ("opening_time", "closing_time", "geometry", "reference_travel_cm")


def profile_times_changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """Whether a profile write changes the times of the covers that follow it."""
    return any((before or {}).get(key) != (after or {}).get(key) for key in PROFILE_TIMING)


# What the panel says of a cover's calibration, derived from what is stored, never stored itself. The same order as the
# panel's status: `none` without a measured time (guided or automatic), then `verified` with a check kept at the last
# save, `refined` when both rolls were measured (guided) or entered (manual), `basic` otherwise: times, with or without
# the slat time of a lift run, rolls unknown or absent. A basic save clears the check: `verified` goes back to `basic`.
MEASURED_SOURCES = ("guided", "automatic")
ROLL_SOURCES = ("guided", "manual")


def precision_class(settings: dict[str, Any] | None, record: dict[str, Any] | None) -> str:
    """`none`, `basic`, `refined` or `verified`, from resolved settings (stored or public) and the cover's record."""
    def source(key: str) -> Any:
        return ((settings or {}).get(key) or {}).get("provenance", {}).get("source")
    if not any(source(direction) in MEASURED_SOURCES for direction in KEYS):
        return "none"
    check = (record or {}).get("check_cm")
    if isinstance(check, (int, float)) and not isinstance(check, bool):
        return "verified"
    if all(source(key) in ROLL_SOURCES for key in ("opening_roll", "closing_roll")):
        return "refined"
    return "basic"


def profile_source(profile: dict[str, Any] | None, unique: str) -> bool:
    """The cover is the source of its profile, the cover the profile took its times from (the
    origin of its opening time, as `travel_edit` and `slats_state` read it). Only the source updates the profile; a
    cover that follows a profile measured on another one, or a profile made by hand (no origin), saves a new one."""
    return profile is not None and profile["provenance"]["opening"]["origin_unique_id"] == unique


def travel_edit(data: dict[str, Any], unique: str) -> str:
    """What a curtain travel entered for this cover does (`manual_travel`), derived from what is stored.

    `reference`: the cover's profile was measured on it (the origin of its opening time): the travel becomes the
    profile's reference travel too, and the covers that follow it adapt their times to theirs. `scaled`: it follows a
    profile with a reference travel: its times adapt to the travel entered. `display`: it follows a profile without
    one, or none: the travel only gives positions in centimetres, and the times stay.
    """
    profile = data["profiles"].get(data["assignments"].get(unique))
    if profile is None:
        return "display"
    if profile_source(profile, unique):
        return "reference"
    return "scaled" if profile.get("reference_travel_cm") is not None else "display"


def cover_timing(data: dict[str, Any], unique: str) -> dict[str, Any]:
    """The values a cover's times and geometry resolve to from stored data alone (no native record, no default)."""
    record = data["covers"].get(unique, {})
    return {key: item["value"] for key, item in resolve(
        profile=data["profiles"].get(data["assignments"].get(unique)), native=None,
        overrides=record.get("overrides", {}), default=None, travel_cm=record.get("travel_cm")).items()}


def enter_travel(data: dict[str, Any], unique: str, travel_cm: float | None) -> list[str]:
    """Set (or with None remove) the curtain travel entered for this cover; returns the covers it changes.

    As `travel_edit` says: with `reference` the profile's reference travel follows, and removing it removes both;
    otherwise only the cover's own travel, and removing it returns to the profile's. The precision stays: a cover
    whose times this changes loses its check (they described other times), any other keeps it.
    """
    edit = travel_edit(data, unique)
    profile_id = data["assignments"].get(unique)
    changed = ([other for other, assigned in data["assignments"].items() if assigned == profile_id]
               if edit == "reference" else [unique])
    before = {other: cover_timing(data, other) for other in changed}
    own_before = data["covers"].get(unique, {}).get("travel_cm")
    record = data["covers"].setdefault(unique, {"overrides": {}})
    if travel_cm is None:
        record.pop("travel_cm", None)
    else:
        record["travel_cm"] = centimetres(travel_cm)
    if edit == "reference":
        profile = data["profiles"][profile_id]
        if travel_cm is None:
            profile.pop("reference_travel_cm", None)
        else:
            profile["reference_travel_cm"] = record["travel_cm"]
    set_overrides(data, unique, record["overrides"])
    for other in changed:
        if cover_timing(data, other) != before[other]:
            clear_check(data, other)
    if edit == "reference" and record.get("travel_cm") != own_before:
        # The source's check gave the gap in cm on the travel it had: another travel (or none)
        # leaves it describing nothing. Its times stay (they are the profile's, on its own reference).
        clear_check(data, unique)
    return changed


# What is known of a cover's slats. A cover that follows a profile knows it from the profile's slat time (> 0 with,
# 0 without, no geometry: not said): `yes_measured` / `no_measured` where that slat time was measured on this very
# cover, `yes_profile` / `no_profile` otherwise (inherited, read only). A cover without a profile knows only what its
# card says (`slats` in its record, a declaration): `yes` / `no`. `unknown` when nobody said. A declaration stays in
# the record when a profile is assigned (it serves again if the profile is unlinked), but it no longer counts while
# the cover follows one.
SLATS_STATES = ("yes_measured", "no_measured", "yes_profile", "no_profile", "yes", "no", "unknown")


def slats_state(data: dict[str, Any], unique: str) -> str:
    profile = data["profiles"].get(data["assignments"].get(unique))
    if profile is None:
        said = data["covers"].get(unique, {}).get("slats")
        return "unknown" if said is None else "yes" if said else "no"
    geometry = profile.get("geometry")
    if not geometry:
        return "unknown"
    has = "yes" if geometry["slat_time_s"] > 0 else "no"
    evidence_ = profile.get("geometry_provenance", {}).get("slat_time_s", {})
    measured = (profile_source(profile, unique)
                and evidence_.get("source") in MEASURED_SOURCES and evidence_.get("origin_unique_id") == unique)
    return f"{has}_measured" if measured else f"{has}_profile"


def enter_slats(data: dict[str, Any], unique: str, slats: bool | None) -> None:
    """What the card says of the cover's slats (None: not said). Only for a cover that follows no profile; one
    that does inherits them from its profile (refused with `slats_profile`, or `slats_measured` where measured here)."""
    from .cover_profiles import ProfileError
    state = slats_state(data, unique)
    if state.endswith("_measured"):
        raise ProfileError("slats_measured")
    if data["assignments"].get(unique) in data["profiles"]:
        raise ProfileError("slats_profile")
    record = data["covers"].setdefault(unique, {"overrides": {}})
    if slats is None:
        record.pop("slats", None)
    else:
        record["slats"] = slats
    set_overrides(data, unique, record.get("overrides", {}))


def clear_check(data: dict[str, Any], unique: str) -> None:
    """New times without a check of their own: the stored check no longer describes this cover."""
    record = data["covers"].get(unique)
    if record is None or "check_cm" not in record:
        return
    for key in CHECK_FIELDS:
        record.pop(key, None)
    set_overrides(data, unique, record.get("overrides", {}))


OVERRIDE = vol.Schema({vol.Required("value"): seconds, vol.Required("provenance"): EVIDENCE})
COVER_RECORD = vol.Schema({vol.Optional("overrides", default=dict): {
    vol.In(KEYS): OVERRIDE,
}, vol.Optional("travel_cm"): centimetres,
    # Absent in older records (no check); whole in those saved before the gap kept its tenth.
    vol.Optional("check_cm"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("check_measured_cm"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("check_expected_cm"): vol.All(vol.Coerce(float), vol.Range(min=0)),
    vol.Optional("check_at"): str,
    # Whether the cover has slats, as said on its card; absent: not said.
    vol.Optional("slats"): bool})
# Keep native evidence verbatim in storage. Public responses allowlist its fields.
NATIVE_EVIDENCE = vol.Schema({vol.Required("source"): str,
                             vol.Required("recorded_at"): vol.Any(str, None),
                             vol.Required("origin_unique_id"): vol.Any(str, None)})
_TIMED_NATIVE = vol.Schema({vol.Required("up"): seconds, vol.Required("down"): seconds,
                    vol.Optional("provenance"): {vol.Required(direction): NATIVE_EVIDENCE for direction in KEYS}},
                    extra=vol.ALLOW_EXTRA)


# A native blind may store only slat timing or motor pulse, without travel calibration.
NATIVE = vol.Any(_TIMED_NATIVE, vol.Schema({
    vol.Optional("slat_time"): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=10)),
    vol.Optional("min_motor_pulse"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
}))

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


def linear_geometry(profile: dict[str, Any]) -> bool:
    """No geometry, or a geometry that moves the edge exactly as the linear model (no slats, rolls 1)."""
    return profile.get("geometry") in (None, DEFAULT_GEOMETRY)


def resolve(*, profile: dict[str, Any] | None, native: dict[str, Any] | None,
            overrides: dict[str, Any], default: float | None,
            default_source: str = "default", travel_cm: float | None = None) -> dict[str, Any]:
    """One resolver for runtime and API; no clock, registry or storage side effects."""
    result = {}
    # A slat time of 0 with rolls 1 is the linear model exactly: resolved as linear, so the cover runs the same
    # code and numbers as a profile without geometry.
    motion = profile_motion(profile, overrides, travel_cm) if profile and not linear_geometry(profile) else None
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
        elif native is not None and native_key in native:
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
