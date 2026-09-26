"""Legacy myhome.yaml loader and normalizer for backward compatibility."""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util.yaml import loader as yaml_loader

from . import validate
from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_FILE_PATH,
    CONF_PLATFORMS,
    CONF_ZONE,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)


def _read_legacy_yaml(
    primary_path: str, fallback_path: str | None = None
) -> tuple[str | None, Any, Exception | None]:
    """Check existence and load YAML off the event loop."""
    target_path = primary_path
    try:
        if not os.path.isfile(target_path) and fallback_path and os.path.isfile(fallback_path):
            target_path = fallback_path
        if not os.path.isfile(target_path):
            return None, None, None
        return target_path, yaml_loader.load_yaml(target_path), None
    except Exception as err:
        return target_path, None, err


async def load_legacy_myhome_yaml(
    hass: HomeAssistant,
    entry: ConfigEntry,
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
    platforms: Sequence[Platform | str] = PLATFORMS,
) -> None:
    """Load legacy myhome.yaml if present for seamless backward-compatibility."""
    _opt_path = entry.options.get(CONF_FILE_PATH) or entry.options.get("file_path")
    primary_path = str(_opt_path) if _opt_path else hass.config.path("myhome.yaml")
    fallback_path = "/config/myhome.yaml" if primary_path != "/config/myhome.yaml" else None

    try:
        resolved_path, raw_yaml, parse_err = await hass.async_add_executor_job(
            _read_legacy_yaml, primary_path, fallback_path
        )
    except Exception as e:
        LOGGER.error(
            "Failed to parse myhome.yaml from %s: %s", primary_path, e
        )
        return

    if parse_err is not None:
        LOGGER.error(
            "Failed to parse myhome.yaml from %s: %s", resolved_path, parse_err
        )
        return

    if resolved_path is None or not raw_yaml or not isinstance(raw_yaml, dict):
        return

    try:
        if raw_yaml and isinstance(raw_yaml, dict):
            # Support single-gateway config without MAC address header at root level
            if any(plat in raw_yaml for plat in platforms):
                configured_gateways = [
                    e
                    for e in hass.config_entries.async_entries(DOMAIN)
                    if not getattr(e, "disabled_by", None)
                ]
                if len(configured_gateways) <= 1:
                    raw_yaml = {entry.data[CONF_MAC]: raw_yaml}
                else:
                    LOGGER.error(
                        "myhome.yaml contains top-level platform configurations without a gateway MAC, "
                        "but %d gateways are configured. Please specify the gateway MAC address header in myhome.yaml.",
                        len(configured_gateways),
                    )
                    raw_yaml = {}

            # Ensure every gateway has mac and every device has where set if omitted
            for gw_key, gw_val in raw_yaml.items():
                if isinstance(gw_val, dict):
                    if CONF_MAC not in gw_val:
                        gw_val[CONF_MAC] = str(gw_key)
                    for plat, devs in gw_val.items():
                        if isinstance(devs, dict):
                            for d_key, d_val in devs.items():
                                if (
                                    isinstance(d_val, dict)
                                    and "where" not in d_val
                                    and "zone" not in d_val
                                ):
                                    d_val["where"] = str(d_key)

            _validated = validate.config_schema(raw_yaml)
            formatted_entry_mac = dr.format_mac(entry.data[CONF_MAC])
            mac_key = None
            if formatted_entry_mac in _validated:
                mac_key = formatted_entry_mac
            elif entry.data[CONF_MAC] in _validated:
                mac_key = entry.data[CONF_MAC]
            else:
                for k in _validated:
                    try:
                        if dr.format_mac(k) == formatted_entry_mac:
                            mac_key = k
                            break
                    except Exception:
                        continue
            if mac_key and mac_key in _validated:
                yaml_platforms = _validated[mac_key].get(CONF_PLATFORMS, {})
                for plat, devices in yaml_platforms.items():
                    if plat in configured_platforms:
                        for d_id, d_cfg in devices.items():
                            configured_platforms[plat][d_id] = d_cfg
                            if isinstance(d_cfg, dict):
                                who, dash, clean_id = d_id.partition("-")
                                if dash and who.isdigit():
                                    configured_platforms[plat][clean_id] = d_cfg
                                iface = d_cfg.get(CONF_BUS_INTERFACE) or d_cfg.get(
                                    "bus_interface"
                                )
                                # A routed device never claims the bare key: that is the local bus's (#408)
                                routing = (
                                    f"{BUS_ROUTING}{iface}"
                                    if iface is not None
                                    else ""
                                )
                                if "where" in d_cfg:
                                    configured_platforms[plat][
                                        f"{d_cfg['where']}{routing}"
                                    ] = d_cfg
                                if CONF_ZONE in d_cfg or "zone" in d_cfg:
                                    z_val = str(
                                        d_cfg.get(CONF_ZONE) or d_cfg.get("zone")
                                    )
                                    configured_platforms[plat][
                                        f"{z_val}{routing}"
                                    ] = d_cfg
                                    clean_z = z_val.split("#")[-1]
                                    configured_platforms[plat][
                                        f"{clean_z}{routing}"
                                    ] = d_cfg
                                    if not routing:
                                        configured_platforms[plat][
                                            f"zone_{clean_z}"
                                        ] = d_cfg
                LOGGER.info(
                    "Loaded legacy myhome.yaml configuration for gateway %s (%s platforms)",
                    entry.data[CONF_MAC],
                    len(yaml_platforms),
                )
    except Exception as e:
        LOGGER.error(
            "Failed to parse myhome.yaml from %s: %s", resolved_path, e
        )
