"""Diagnostics support for MyHOME."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_CODE, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import (
    BUS_ROUTING,
    CONF_DECODER_COMPANION,
    CONF_DECODER_ENTITY,
    CONF_DECODER_SLOTS,
    CONF_FILE_PATH,
    CONF_PIN_CODE,
    CONF_PRIMARY_GATEWAY,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_TUNER_PICTURES,
    DOMAIN,
    INTEGRATION_VERSION,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TOPOLOGY_SHARED,
    get_ownd_version,
)
from .data import MyHOMERuntimeData, get_runtime_data
from .device_health import DeviceHealth
from .legacy_yaml import CONF_MIGRATED_YAML_PLATFORMS

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
    CONF_PIN_CODE,
    CONF_CODE,
    "token",
    "secret",
    "host",
    "mac",
    "id",
    "UDN",
    "ssdp_location",
    "friendly_name",
    "file_path",
    CONF_FILE_PATH,
    CONF_TUNER_PICTURES,
    CONF_PRIMARY_GATEWAY,
    "primary_mac",
    "secondary_mac",
    "peer_mac",
    "configured_primary",
}

_SAFE_DEVICE_KEY_RE = re.compile(r"^(?:\d+-)?#?\d+(?:#\d+)*(?:#4#\d+)?$|^zone_\d+$")
_WHO_PREFIX_RE = re.compile(r"^(\d+)-")
_PLATFORM_DEFAULT_WHO: dict[str, str] = {
    "alarm_control_panel": "5",
    "binary_sensor": "25",
    "climate": "4",
    "cover": "2",
    "light": "1",
    "lock": "6",
    "media_player": "16",
    "switch": "1",
}


def _unique_key(plat_out: Mapping[str, Any], base: str) -> str:
    """Return ``base`` if unused in ``plat_out``, otherwise ``{base}_{n}``."""
    if base not in plat_out:
        return base
    suffix = 2
    while f"{base}_{suffix}" in plat_out:
        suffix += 1
    return f"{base}_{suffix}"


def _device_identity(
    cfg: Mapping[str, Any],
    *,
    platform_name: str = "",
    raw_key: str = "",
) -> tuple[str, str, str, str, str] | None:
    """Return canonical (who, where_or_zone, interface, device_class, subentry_id) for a device dict."""
    if "where" not in cfg and "zone" not in cfg:
        return None
    base_addr = str(cfg["where"] if "where" in cfg else cfg["zone"])
    iface = str(cfg.get("interface") or cfg.get("bus_interface") or "")
    if not iface and BUS_ROUTING in base_addr:
        base_addr, _, iface = base_addr.rpartition(BUS_ROUTING)
    raw_who = str(cfg.get("who") or "")
    if not raw_who:
        prefix_match = _WHO_PREFIX_RE.match(raw_key)
        if prefix_match is not None:
            raw_who = prefix_match.group(1)
        elif "zone" in cfg:
            raw_who = "4"
        else:
            raw_who = _PLATFORM_DEFAULT_WHO.get(platform_name, "")
    return (
        raw_who,
        base_addr,
        iface,
        str(cfg.get("class") or cfg.get("device_class") or ""),
        str(cfg.get("subentry_id") or ""),
    )


def _count_unique_devices(devices: Mapping[str, Any], platform_name: str = "") -> int:
    """Count distinct configured devices in a platform mapping, ignoring lookup aliases."""
    seen_ids: set[int] = set()
    seen_identities: set[tuple[str, str, str, str, str]] = set()
    count = 0
    for raw_key, cfg in devices.items():
        if id(cfg) in seen_ids:
            continue
        seen_ids.add(id(cfg))
        if isinstance(cfg, Mapping):
            identity = _device_identity(cfg, platform_name=platform_name, raw_key=str(raw_key))
            if identity is not None:
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
        count += 1
    return count


def _redact_yaml_device_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a migrated YAML device config with household/entity names redacted."""
    redacted = dict(cfg)
    for name_key in ("name", "entity_name", "state_sensor", "subentry_id"):
        if name_key in redacted and redacted[name_key] is not None:
            redacted[name_key] = REDACTED
    access = redacted.get("access")
    if isinstance(access, Mapping):
        clean_access = dict(access)
        for access_key in ("allowed_users", "approvers", "camera", "prewarn_light", "close_block_entity"):
            if access_key in clean_access and clean_access[access_key] not in (None, [], {}):
                clean_access[access_key] = REDACTED
        redacted["access"] = clean_access
    return redacted


def _redact_migrated_yaml_platforms(migrated: Mapping[str, Any]) -> dict[str, Any]:
    """Deduplicate alias keys and redact user-chosen names in migrated_yaml_platforms."""
    out: dict[str, Any] = {}
    for platform_name, devices in migrated.items():
        plat_str = str(platform_name)
        if not isinstance(devices, Mapping):
            out[plat_str] = devices
            continue
        seen_ids: set[int] = set()
        seen_identities: set[tuple[str, str, str, str, str]] = set()
        plat_out: dict[str, Any] = {}
        for raw_key, cfg in devices.items():
            key_str = str(raw_key)
            if not isinstance(cfg, Mapping):
                safe_key = (
                    _unique_key(plat_out, key_str)
                    if _SAFE_DEVICE_KEY_RE.match(key_str)
                    else _unique_key(plat_out, "value")
                )
                plat_out[safe_key] = REDACTED if isinstance(cfg, str) else cfg
                continue
            if id(cfg) in seen_ids:
                continue
            seen_ids.add(id(cfg))
            identity = _device_identity(cfg, platform_name=plat_str, raw_key=key_str)
            if identity is not None:
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                who, base_addr, iface, klass, _ = identity
                addr_with_routing = f"{base_addr}{BUS_ROUTING}{iface}" if iface else base_addr
                canon_key = f"{who}-{addr_with_routing}" if who else addr_with_routing
                if canon_key in plat_out and klass:
                    canon_key = f"{canon_key}-{klass}"
                canon_key = _unique_key(plat_out, canon_key)
            else:
                canon_key = _unique_key(
                    plat_out,
                    key_str if _SAFE_DEVICE_KEY_RE.match(key_str) else f"device_{len(plat_out) + 1}",
                )
            plat_out[canon_key] = _redact_yaml_device_config(cfg)
        out[plat_str] = plat_out
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a MyHOME config entry."""
    raw_options = dict(entry.options)
    if isinstance(raw_options.get(CONF_MIGRATED_YAML_PLATFORMS), Mapping):
        raw_options[CONF_MIGRATED_YAML_PLATFORMS] = _redact_migrated_yaml_platforms(
            raw_options[CONF_MIGRATED_YAML_PLATFORMS]
        )
    entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    entry_options = async_redact_data(raw_options, TO_REDACT)
    for slot in range(1, CONF_DECODER_SLOTS + 1):
        key = CONF_DECODER_ENTITY.format(slot)
        if entry_options.get(key):  # an empty slot stays empty: configured or not is diagnostics
            entry_options[key] = f"media_player.decoder_{slot}"
        comp_key = CONF_DECODER_COMPANION.format(slot)
        if entry_options.get(comp_key):
            entry_options[comp_key] = f"media_player.decoder_{slot}_companion"
    for slot in range(1, CONF_SOURCE_SLOTS + 1):
        src_key = CONF_SOURCE_NAME.format(slot)
        if str(entry_options.get(src_key) or "").strip():
            entry_options[src_key] = f"Source {slot}"

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
                # OWNd GatewayProfile fields; keepalive None means OS TCP keepalive only.
                profile_info = {
                    "name": getattr(profile, "model_name", "Generic"),
                    "display_name": getattr(profile, "display_name", None),
                    "command_queue_delay": getattr(profile, "command_queue_delay", 0.0),
                    "max_queue_size": getattr(profile, "max_queue_size", 250),
                    "keepalive_interval": getattr(profile, "event_keepalive_interval", None),
                    "keepalive_summary": getattr(profile, "keepalive_summary", None),
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
        platforms_info[platform_name] = (
            _count_unique_devices(devices, str(platform_name))
            if isinstance(devices, Mapping)
            else len(devices)
        )

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
        "expose": (
            runtime.expose_manager.as_diagnostics()
            if runtime is not None and runtime.expose_manager is not None
            else []
        ),
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
        entry_firmware,
        entry_mac,
        entry_model,
        entry_primary_mac,
        entry_role,
        entry_topology,
        gateway_delegatable_whos,
        gateway_supported_whos,
        gateway_tier,
        infer_shared_bus_topology,
    )

    my_mac = entry_mac(entry) or ""
    my_model = entry_model(entry)
    my_fw = entry_firmware(entry)
    my_tier = gateway_tier(my_model)
    my_whos = gateway_supported_whos(my_model, my_fw)
    my_delegatable = gateway_delegatable_whos(my_model, my_fw)
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
        "delegatable_whos": sorted(my_delegatable),
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
        peer_fw = entry_firmware(peer)
        peer_tier = gateway_tier(peer_model)
        peer_whos = gateway_supported_whos(peer_model, peer_fw)
        peer_delegatable = gateway_delegatable_whos(peer_model, peer_fw)
        rec = infer_shared_bus_topology(entry, peer)

        is_target_primary = rec.primary_mac == my_mac
        expected_role = ROLE_PRIMARY if is_target_primary else rec.role
        expected_whos = set() if is_target_primary else rec.delegated_whos
        expected_pri_mac = None if is_target_primary else rec.primary_mac

        role_aligned = (configured_role == expected_role) if configured_topology == TOPOLOGY_SHARED else None
        whos_aligned = (configured_whos == expected_whos) if configured_topology == TOPOLOGY_SHARED else None
        whos_supported = (
            (configured_whos <= my_delegatable)
            if (configured_topology == TOPOLOGY_SHARED and configured_role == ROLE_SECONDARY)
            else None
        )
        primary_aligned = (configured_primary == expected_pri_mac) if (configured_topology == TOPOLOGY_SHARED and not is_target_primary) else None

        evaluations.append({
            "peer_model": peer_model,
            "peer_mac": peer_mac,
            "peer_tier": peer_tier,
            "peer_supported_whos": sorted(peer_whos),
            "peer_delegatable_whos": sorted(peer_delegatable),
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
                "delegated_whos_supported": whos_supported,
                "primary_aligned": primary_aligned,
            },
        })

    raw_audit: dict[str, Any] = {
        "target_gateway": target_diag,
        "peer_count": len(peer_entries),
        "evaluations": evaluations,
    }
    return async_redact_data(raw_audit, TO_REDACT)
