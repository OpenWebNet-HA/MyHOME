"""Versioned export of committed travel profiles, independent of live sessions."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .cover_profiles import ProfileError, get_store, respond
from .cover_settings import KEYS, native_provenance

WS_EXPORT = "myhome/cover_profiles/export"


async def export_profiles(hass: Any, entry_id: str) -> Any:
    """Take one consistent saved revision without reading runtime or draft values."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ProfileError("target_not_found")
    store = get_store(hass, entry_id)
    async with store.lock:
        await store.load()
        if hass.config_entries.async_get_entry(entry_id) is not entry:
            raise ProfileError("target_not_found")
        data = store.data
        records = {record.unique_id: record for record in
                   er.async_entries_for_config_entry(er.async_get(hass), entry_id)
                   if record.domain == "cover" and record.platform == DOMAIN}
        origins = {meta["origin_unique_id"] for profile in data["profiles"].values()
                   for meta in profile["provenance"].values() if meta["origin_unique_id"] is not None}
        origins.update(meta["provenance"]["origin_unique_id"] for record in data["covers"].values()
                       for meta in record["overrides"].values()
                       if meta["provenance"]["origin_unique_id"] is not None)
        origins.update(meta["origin_unique_id"] for native in data["native_fallbacks"].values()
                       for meta in (native_provenance(native, direction) for direction in KEYS)
                       if meta["origin_unique_id"] is not None)
        # References are local to this document; never serialize MAC-bearing unique IDs.
        native_ids = {device_id: store.native_identity(device_id) or f"native:{device_id}"
                      for device_id in data["native_fallbacks"]}
        refs = {unique: f"cover-{index}" for index, unique in
                enumerate(sorted(set(data["assignments"]) | origins | set(data["covers"]) | set(native_ids.values())), start=1)}
        covers = []
        for unique, ref in refs.items():
            record = records.get(unique)
            covers.append({"id": ref, "registry_id": record.id if record else None,
                           "entity_id": record.entity_id if record else None,
                           "name": (record.name or record.original_name or record.entity_id) if record else None})
        return {
            "format": "myhome.cover_calibration", "format_version": 3,
            "exported_at": dt_util.utcnow().isoformat(),
            "gateway": {"entry_id": entry_id, "name": entry.title},
            "revision": data["revision"], "covers": covers,
            "profiles": [{"id": profile_id, "name": profile["name"],
                          "opening_time": profile["opening_time"], "closing_time": profile["closing_time"],
                          "provenance": {direction: {"source": meta["source"],
                                                     "recorded_at": meta["recorded_at"],
                                                     "origin_cover_id": refs.get(meta["origin_unique_id"])}
                                         for direction, meta in profile["provenance"].items()}}
                         for profile_id, profile in sorted(data["profiles"].items())],
            "assignments": [{"cover_id": refs[unique], "profile_id": profile_id}
                            for unique, profile_id in sorted(data["assignments"].items())],
            "model": "linear_time", "scaling": "unscaled",
            "native_fallbacks": [{"cover_id": refs[native_ids[device_id]],
                                  "opening_time": value["up"], "closing_time": value["down"],
                                  "source": value.get("source", "unknown"),
                                  "recorded_at": value.get("measured_at"),
                                  "provenance": {direction: {"source": meta["source"],
                                                            "recorded_at": meta["recorded_at"],
                                                            "origin_cover_id": refs.get(meta["origin_unique_id"])}
                                                 for direction in KEYS
                                                 for meta in [native_provenance(value, direction)]}}
                                 for device_id, value in sorted(data["native_fallbacks"].items())],
            "overrides": [{"cover_id": refs[unique], "values": {
                direction: {"value": item["value"], "source": item["provenance"]["source"],
                            "recorded_at": item["provenance"]["recorded_at"],
                            "origin_cover_id": refs.get(item["provenance"]["origin_unique_id"])}
                for direction, item in record["overrides"].items()}}
                          for unique, record in sorted(data["covers"].items())],
        }


@websocket_command({vol.Required("type"): WS_EXPORT, vol.Required("entry_id"): str})
@require_admin
@async_response
async def ws_export(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    await respond(hass, connection, msg, export_profiles(hass, msg["entry_id"]))
