"""Diagnostics support for MyHOME."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DECODER_COMPANION,
    CONF_DECODER_ENTITY,
    CONF_DECODER_SLOTS,
    CONF_PRIMARY_GATEWAY,
    DOMAIN,
    INTEGRATION_VERSION,
    ROLE_PRIMARY,
    TOPOLOGY_SHARED,
    get_ownd_version,
)
from .data import MyHOMERuntimeData, get_runtime_data
from .device_health import DeviceHealth

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
    CONF_PRIMARY_GATEWAY,
    "primary_mac",
    "secondary_mac",
    "peer_mac",
    "configured_primary",
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
            bus_topology = getattr(gateway_handler, "bus_topology", None)
            if isinstance(bus_topology, str):
                gw_info["bus_topology"] = bus_topology
                gw_info["gateway_role"] = str(getattr(gateway_handler, "gateway_role", "primary"))
                gw_info["is_follower"] = bool(getattr(gateway_handler, "is_follower", False))
                gw_info["is_standby"] = bool(getattr(gateway_handler, "is_standby", False))
                gw_info["failover_active"] = bool(getattr(gateway_handler, "failover_active", False))
                gw_info["primary_gateway"] = REDACTED if getattr(gateway_handler, "primary_gateway_mac", None) else None
                gw_info["delegated_whos"] = list(getattr(gateway_handler, "delegated_whos", set()))
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

        health = getattr(gateway_handler, "device_health", None)
        if isinstance(health, DeviceHealth):
            gw_info["device_faults"] = health.faults

        bus_monitor = getattr(gateway_handler, "bus_monitor", None)
        if bus_monitor is not None:
            bus_monitor_info = {
                "stats": bus_monitor.get_stats(),
                # The whole ring: the startup status sweep alone can exceed 100 frames
                "recent_frames": bus_monitor.get_recent_frames(limit=bus_monitor.maxlen),
            }

    # Configured devices per platform (``runtime.entities`` is never filled)
    platforms_info: dict[str, int] = {}
    for platform_name, devices in (runtime.platforms if runtime is not None else {}).items():
        platforms_info[platform_name] = len(devices)

    topology_inference = _build_topology_inference_diagnostics(hass, entry, profile_info)

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
        "audio": _build_audio_diagnostics(hass, entry, runtime),
        "bus_monitor": bus_monitor_info,
        "topology_inference": topology_inference,
    }


def _build_audio_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: MyHOMERuntimeData | None,
) -> dict[str, Any]:
    """Describe the WHO=16 sound system: zones, decoders, groups and what is parked.

    Rooms and decoders are user-named, so they never appear by entity id: a zone
    is ``zone_<WHERE>`` (its bus address) and a decoder is ``decoder_<slot>``,
    the same alias the options block uses. What a report needs is which zone is
    on which source, who leads which group, and whether the anti-hiss auto-off
    has parked a room, not what the rooms are called.
    """
    if runtime is None:
        return {}

    zones = {
        entity_id: player for entity_id, player in runtime.media_players.items() if hasattr(player, "diagnostics_state")
    }
    alias: dict[str, str] = {entity_id: f"zone_{player.where}" for entity_id, player in zones.items()}
    slots: dict[str, int] = {}
    for slot in range(1, CONF_DECODER_SLOTS + 1):
        decoder = str(entry.options.get(CONF_DECODER_ENTITY.format(slot)) or "").strip()
        if decoder:
            slots[decoder] = slot
            alias[decoder] = f"decoder_{slot}"

    pool = runtime.decoder_pool
    for decoder, companion in (pool.companion_map if pool is not None else {}).items():
        if decoder in slots:
            alias[companion] = f"decoder_{slots[decoder]}_companion"

    def name(entity_id: str | None) -> str | None:
        return None if entity_id is None else alias.get(entity_id, "unknown")

    audio: dict[str, Any] = {
        "zones": {alias[entity_id]: player.diagnostics_state() for entity_id, player in zones.items()},
    }
    if pool is None or not pool.is_configured:
        audio["decoder_pool"] = None
        return audio

    books = pool.books()
    decoders: dict[str, Any] = {}
    for decoder, slot in slots.items():
        state = hass.states.get(decoder)
        decoders[f"decoder_{slot}"] = {
            "source": pool.decoder_source(decoder),
            "pre_gain_pct": pool.get_pre_gain(decoder),
            "stream_incompatible": decoder in pool.stream_incompatible,
            "companion": name(pool.companion_map.get(decoder)),
            "companion_chosen_by_user": bool(str(entry.options.get(CONF_DECODER_COMPANION.format(slot)) or "").strip()),
            "state": state.state if state is not None else None,
            "held_by": name(next((zone for dec, zone in books["assignments"].items() if dec == decoder), None)),
        }
    audio["decoder_pool"] = {
        "decoders": decoders,
        "groups": {name(leader): [name(member) for member in members] for leader, members in books["groups"].items()},
        "environments": {name(zone): environment for zone, environment in books["environments"].items()},
        "unconfirmed": sorted(name(zone) or "unknown" for zone in pool.unconfirmed),
    }
    return audio


def _build_topology_inference_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    profile_info: dict[str, Any],
) -> dict[str, Any]:
    """Assemble structured topology inference audit data."""
    from .topology import (
        entry_delegated_whos,
        entry_mac,
        entry_model,
        entry_primary_mac,
        entry_role,
        entry_topology,
        gateway_supported_whos,
        gateway_tier,
        infer_shared_bus_topology,
    )

    my_mac = entry_mac(entry) or ""
    my_model = entry_model(entry)
    my_tier = gateway_tier(my_model)
    my_whos = gateway_supported_whos(my_model)
    configured_topology = entry_topology(entry)
    configured_role = entry_role(entry)
    configured_primary = entry_primary_mac(entry)
    configured_whos = entry_delegated_whos(entry)

    target_diag: dict[str, Any] = {
        "mac": my_mac,
        "model": my_model,
        "hardware_tier": my_tier,
        "command_queue_delay": profile_info.get("command_queue_delay"),
        "supported_whos": sorted(my_whos),
        "configured_topology": configured_topology,
        "configured_role": configured_role,
        "configured_primary": configured_primary,
        "configured_delegated_whos": sorted(configured_whos),
    }

    evaluations: list[dict[str, Any]] = []
    peer_entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]

    for peer in peer_entries:
        peer_mac = entry_mac(peer) or ""
        peer_model = entry_model(peer)
        peer_tier = gateway_tier(peer_model)
        peer_whos = gateway_supported_whos(peer_model)
        rec = infer_shared_bus_topology(entry, peer)

        is_target_primary = rec.primary_mac == my_mac
        expected_role = ROLE_PRIMARY if is_target_primary else rec.role
        expected_whos = set() if is_target_primary else rec.delegated_whos
        expected_pri_mac = None if is_target_primary else rec.primary_mac

        role_aligned = (configured_role == expected_role) if configured_topology == TOPOLOGY_SHARED else None
        whos_aligned = (configured_whos == expected_whos) if configured_topology == TOPOLOGY_SHARED else None
        primary_aligned = (configured_primary == expected_pri_mac) if (configured_topology == TOPOLOGY_SHARED and not is_target_primary) else None

        evaluations.append({
            "peer_model": peer_model,
            "peer_mac": peer_mac,
            "peer_tier": peer_tier,
            "peer_supported_whos": sorted(peer_whos),
            "peer_topology": entry_topology(peer),
            "peer_role": entry_role(peer),
            "primary_mac": rec.primary_mac,
            "secondary_mac": rec.secondary_mac,
            "recommended_primary_model": my_model if is_target_primary else peer_model,
            "recommended_secondary_model": peer_model if is_target_primary else my_model,
            "recommended_role": rec.role,
            "delegated_whos": sorted(rec.delegated_whos),
            "capability_delta": sorted(rec.capability_delta),
            "audio_coupled": rec.audio_coupled,
            "rationale": rec.rationale,
            "alignment": {
                "configured_shared_bus": configured_topology == TOPOLOGY_SHARED,
                "is_recommended_primary": is_target_primary,
                "role_aligned": role_aligned,
                "delegated_whos_aligned": whos_aligned,
                "primary_aligned": primary_aligned,
            },
        })

    raw_audit: dict[str, Any] = {
        "target_gateway": target_diag,
        "peer_count": len(peer_entries),
        "evaluations": evaluations,
    }
    return async_redact_data(raw_audit, TO_REDACT)
