"""Diagnostics support for MyHOME."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DECODER_ENTITY,
    CONF_DECODER_SLOTS,
    INTEGRATION_VERSION,
    get_ownd_version,
)
from .data import get_runtime_data

# A diagnostics download is meant to be attached to a public issue. Secrets go
# without saying; the rest identifies a household - where the gateway lives on
# the LAN, its MAC, the SSDP/UDN identity, the path of the user's config file.
# The bus frames, the model, the firmware and the queue figures are what a bug
# report needs, and they carry none of that.
#
# Scope: these keys are redacted, recursively, in the config entry's ``data``
# and ``options`` only. The gateway, profile, queue, platforms and bus_monitor
# blocks are assembled from named fields below and never pass through the
# redaction, so a frame's ``where`` / ``who`` / ``what`` and the counters stay
# intact. Nothing in the download refers back to a redacted value: ``id`` is
# the gateway's formatted MAC (the same identity as ``mac``), ``friendly_name``
# is the name the gateway advertises over SSDP, and a download describes one
# entry and one gateway - so no anonymized reference is needed to relate them.
# The one user-named value in the options, the media_player behind a decoder
# slot, is the exception: it becomes ``media_player.decoder_<slot>`` so the
# slot -> source / gain mapping stays readable without the room it is named
# after.
TO_REDACT = {
    CONF_PASSWORD,
    "password",
    "pin",
    "token",
    "secret",
    "host",
    "mac",
    "id",
    "UDN",
    "ssdp_location",
    "friendly_name",
    "file_path",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a MyHOME config entry."""
    entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    entry_options = async_redact_data(dict(entry.options), TO_REDACT)
    for slot in range(1, CONF_DECODER_SLOTS + 1):
        key = CONF_DECODER_ENTITY.format(slot)
        if entry_options.get(key):  # an empty slot stays empty: configured or not is diagnostics
            entry_options[key] = f"media_player.decoder_{slot}"

    runtime = get_runtime_data(entry)
    gateway_handler = runtime.gateway if runtime is not None else None

    gw_info: dict[str, Any] = {}
    profile_info: dict[str, Any] = {}
    queue_info: dict[str, Any] = {}
    bus_monitor_info: dict[str, Any] = {}

    if gateway_handler is not None:
        gw = getattr(gateway_handler, "gateway", None)
        if gw is not None:
            gw_info = {
                "model_name": getattr(gw, "model_name", None),
                "manufacturer": getattr(gw, "manufacturer", None),
                "firmware": getattr(gw, "firmware", None),
                "is_connected": getattr(gateway_handler, "is_connected", False),
                "send_workers": len(getattr(gateway_handler, "sending_workers", [])),
            }
            identification = getattr(gateway_handler, "identification", None)
            if callable(identification):
                gw_info["identification"] = async_redact_data(identification(), TO_REDACT)
            if hasattr(gw, "profile") and gw.profile:
                profile = gw.profile
                profile_info = {
                    "name": getattr(profile, "name", "Generic"),
                    "command_queue_delay": getattr(profile, "command_queue_delay", 0.0),
                    "max_queue_size": getattr(profile, "max_queue_size", 250),
                    "keepalive_interval": getattr(profile, "keepalive_interval", 90.0),
                }

        send_buffer = getattr(gateway_handler, "send_buffer", None)
        if send_buffer is not None:
            queue_info = {
                "queue_depth": send_buffer.qsize(),
                "max_size": send_buffer.maxsize,
            }

        bus_monitor = getattr(gateway_handler, "bus_monitor", None)
        if bus_monitor is not None:
            bus_monitor_info = {
                "stats": bus_monitor.get_stats(),
                # The whole ring: the startup status sweep alone can exceed 100 frames
                "recent_frames": bus_monitor.get_recent_frames(limit=bus_monitor.maxlen),
            }

    # Count loaded entities per platform
    platforms_info: dict[str, int] = {}
    entities_dict = runtime.entities if runtime is not None else {}
    for platform_name, entities in entities_dict.items():
        platforms_info[platform_name] = len(entities)

    return {
        "integration_version": INTEGRATION_VERSION,
        "ownd_version": await hass.async_add_executor_job(get_ownd_version),
        "config_entry": {
            # The entry id and the user's title are identity, not diagnostics
            "entry_id": "**REDACTED**",
            "version": entry.version,
            "domain": entry.domain,
            "title": f"{gw_info.get('model_name') or 'MyHOME'} Gateway",
            "data": entry_data,
            "options": entry_options,
        },
        "gateway": gw_info,
        "profile": profile_info,
        "queue": queue_info,
        "platforms": platforms_info,
        "bus_monitor": bus_monitor_info,
    }
