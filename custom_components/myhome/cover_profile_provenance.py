"""Backend-owned evidence for each saved directional travel time."""
from __future__ import annotations

from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.util import dt as dt_util

DIRECTIONS = ("opening", "closing")


def utc_timestamp(value):
    """Accept timezone-aware UTC dates, never a local or fabricated fallback date."""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise vol.Invalid("Invalid provenance timestamp") from error
    if parsed.utcoffset() != timedelta(0):
        raise vol.Invalid("Provenance timestamp must be UTC")
    return value


EVIDENCE = vol.Any(
    vol.Schema({
        vol.Required("source"): vol.In(("manual", "guided", "automatic")),
        vol.Required("recorded_at"): utc_timestamp,
        vol.Required("origin_unique_id"): vol.All(str, vol.Length(min=1)),
    }),
    vol.Schema({
        vol.Required("source"): "unknown",
        vol.Required("recorded_at"): None,
        vol.Required("origin_unique_id"): None,
    }),
)
PROVENANCE = vol.Schema({vol.Required(direction): EVIDENCE for direction in DIRECTIONS})


def unknown_provenance():
    """Old profiles have no recorded evidence; migration must not invent any."""
    return {direction: {"source": "unknown", "recorded_at": None, "origin_unique_id": None}
            for direction in DIRECTIONS}


def evidence(source, unique_id):
    """Record the backend wall clock separately from monotonic duration timing."""
    return {"source": source, "recorded_at": dt_util.utcnow().isoformat(),
            "origin_unique_id": unique_id}


def public_provenance(profile, records, target_unique_id):
    """Resolve names through the registry without exposing internal unique IDs."""
    result = {}
    for direction, value in profile["provenance"].items():
        origin = records.get(value["origin_unique_id"])
        result[direction] = {
            "source": value["source"], "recorded_at": value["recorded_at"],
            "origin_entity_id": origin.entity_id if origin else None,
            "origin_name": (origin.name or origin.original_name or origin.entity_id) if origin else None,
            "inherited": value["origin_unique_id"] is not None and value["origin_unique_id"] != target_unique_id,
        }
    return result
