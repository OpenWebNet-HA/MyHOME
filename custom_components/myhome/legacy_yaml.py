"""Legacy myhome.yaml loader and normalizer for backward compatibility."""
from __future__ import annotations

import copy
import os
from collections.abc import Sequence
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.json import json_bytes
from homeassistant.util.json import json_loads
from homeassistant.util.yaml import loader as yaml_loader

from . import validate
from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_EXPOSE,
    CONF_FILE_PATH,
    CONF_MEMBERS,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
    DOMAIN,
    LOGGER,
    PLATFORMS,
)
from .repairs import async_create_legacy_yaml_imported_issue
from .where_grammar import canonical_point_address


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


# The validated myhome.yaml platforms of a gateway, kept in its entry options so its
# devices keep their configuration once myhome.yaml is gone or unreadable (#733).
CONF_MIGRATED_YAML_PLATFORMS = "migrated_yaml_platforms"


def _apply_yaml_platforms(
    yaml_platforms: dict[str, dict[str, dict[str, Any]]],
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Populate configured_platforms from validated YAML platform mappings."""
    # One memo for the whole copy: a device the validator files under several keys
    # (climate zone aliases, the button platform) stays one object.
    memo: dict[int, Any] = {}
    for plat, devices in yaml_platforms.items():
        if plat in configured_platforms and isinstance(devices, dict):
            for d_id, d_cfg in devices.items():
                runtime_cfg = copy.deepcopy(d_cfg, memo)
                configured_platforms[plat][d_id] = runtime_cfg
                if isinstance(runtime_cfg, dict):
                    who, dash, clean_id = d_id.partition("-")
                    if dash and who.isdigit():
                        configured_platforms[plat][clean_id] = runtime_cfg
                    iface = runtime_cfg.get(CONF_BUS_INTERFACE) or runtime_cfg.get("bus_interface")
                    # A routed device never claims the bare key: that is the local bus's (#408)
                    routing = f"{BUS_ROUTING}{iface}" if iface is not None else ""
                    if "where" in runtime_cfg:
                        configured_platforms[plat][f"{runtime_cfg['where']}{routing}"] = runtime_cfg
                    if CONF_ZONE in runtime_cfg or "zone" in runtime_cfg:
                        z_val = str(runtime_cfg.get(CONF_ZONE) or runtime_cfg.get("zone"))
                        configured_platforms[plat][f"{z_val}{routing}"] = runtime_cfg
                        clean_z = z_val.split("#")[-1]
                        configured_platforms[plat][f"{clean_z}{routing}"] = runtime_cfg
                        if not routing:
                            configured_platforms[plat][f"zone_{clean_z}"] = runtime_cfg


def _snapshot(
    yaml_platforms: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """The validated platforms as plain JSON, the way entry options store them."""
    return cast(
        "dict[str, dict[str, Any]]",
        json_loads(json_bytes({p: d for p, d in yaml_platforms.items() if isinstance(d, dict)})),
    )


def _restore_shared_devices(
    snapshot: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Make equal device dicts of a stored snapshot one object again.

    JSON storage turns the aliases of a device into separate copies; the live
    myhome.yaml path hands out one object per device.
    """
    seen: dict[bytes, Any] = {}
    restored: dict[str, dict[str, Any]] = {}
    for plat, devices in snapshot.items():
        restored[plat] = {
            d_id: seen.setdefault(json_bytes(d_cfg), d_cfg) if isinstance(d_cfg, dict) else d_cfg
            for d_id, d_cfg in devices.items()
        }
    return restored


def canonicalise_imported_platforms(
    snapshot: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The stored copy with WHO 1 / WHO 2 point addresses in their canonical spelling (#147).

    A copy taken by an earlier beta holds ``where: "0004"`` under the key ``1-0004``,
    the spelling the validator rewrites to ``04`` today; applied as stored it would
    recreate the ``0004`` entity next to the migrated ``04`` one on every boot. The
    ``where``, the ``members`` and the device key are rewritten in a new copy (the
    stored one is not touched). When the copy already has a device under the canonical
    key, that one stays and the 4-digit twin is dropped with a WARNING. Returns the new
    copy and one line per rewritten device: ``plat: old -> new`` for a moved key, or
    ``plat: key members [old] -> [new]`` when only a scope cover's members moved.
    """
    rewritten: list[str] = []
    result: dict[str, dict[str, Any]] = {}
    for plat, devices in snapshot.items():
        new_devices: dict[str, Any] = {}
        for d_id, cfg in devices.items():
            who = str(cfg.get(CONF_WHO, "")) if isinstance(cfg, dict) else ""
            if who not in ("1", "2") or not isinstance(cfg.get(CONF_WHERE), str):
                new_devices[d_id] = cfg
                continue
            where = cfg[CONF_WHERE]
            canonical = canonical_point_address(where)
            members = cfg.get(CONF_MEMBERS)
            new_members = (
                [canonical_point_address(str(m)) for m in members] if isinstance(members, list) else members
            )
            if canonical == where and new_members == members:
                new_devices[d_id] = cfg
                continue
            new_cfg = {**cfg, CONF_WHERE: canonical}
            if new_members != members:
                new_cfg[CONF_MEMBERS] = new_members
            old_prefix = f"{who}-{where}"
            new_id = f"{who}-{canonical}{d_id[len(old_prefix):]}" if d_id.startswith(old_prefix) else d_id
            if new_id != d_id and (new_id in devices or new_id in new_devices):
                LOGGER.warning(
                    "Imported copy of myhome.yaml, %s: dropping '%s' (where '%s'), the copy already "
                    "has '%s' under the canonical spelling",
                    plat, d_id, where, new_id,
                )
                continue
            new_devices[new_id] = new_cfg
            if new_id != d_id:
                rewritten.append(f"{plat}: {d_id} -> {new_id}")
            else:
                rewritten.append(f"{plat}: {d_id} members {members} -> {new_members}")
        result[plat] = new_devices
    return result, rewritten


def migrate_legacy_yaml_to_entry_options(
    hass: HomeAssistant,
    entry: ConfigEntry,
    yaml_platforms: dict[str, dict[str, dict[str, Any]]],
) -> bool:
    """Keep a copy of the validated myhome.yaml platforms in the entry options.

    The first copy raises the ``legacy_yaml_imported`` notice. Every later boot that
    reads myhome.yaml refreshes the copy when the file changed, so removing the file
    keeps its latest content rather than the one of the upgrade. Returns whether the
    options changed.
    """
    # The validator already spells point addresses canonically; this guards the copy.
    snapshot, _ = canonicalise_imported_platforms(_snapshot(yaml_platforms))
    previous = entry.options.get(CONF_MIGRATED_YAML_PLATFORMS)
    if snapshot == previous or (previous is None and not any(snapshot.values())):
        return False
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MIGRATED_YAML_PLATFORMS: snapshot}
    )
    if previous is None:
        gw_name = str(entry.data.get(CONF_NAME) or entry.data.get(CONF_MAC) or entry.entry_id)
        async_create_legacy_yaml_imported_issue(hass, entry.entry_id, gw_name)
    else:
        LOGGER.info(
            "myhome.yaml changed for gateway %s; updated the imported copy",
            entry.data.get(CONF_MAC),
        )
    return True


def _apply_imported_copy(
    hass: HomeAssistant,
    entry: ConfigEntry,
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Fall back to the copy of myhome.yaml imported into the entry options.

    A copy with 4-digit area-0 points is moved to the canonical spelling before it
    is applied, and the moved copy is stored, so the registry migration that follows
    and the platforms see one and the same address (#147).
    """
    imported = entry.options.get(CONF_MIGRATED_YAML_PLATFORMS)
    if not isinstance(imported, dict) or not any(imported.values()):
        return
    imported, rewritten = canonicalise_imported_platforms(imported)
    if rewritten:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_MIGRATED_YAML_PLATFORMS: imported}
        )
        LOGGER.info(
            "Imported copy of myhome.yaml for gateway %s: %d point address(es) moved to the "
            "canonical spelling (%s)",
            entry.data.get(CONF_MAC), len(rewritten), "; ".join(rewritten),
        )
    _apply_yaml_platforms(_restore_shared_devices(imported), configured_platforms)
    LOGGER.info(
        "Using the imported copy of myhome.yaml for gateway %s: the file is missing, "
        "unreadable or has no section for this gateway",
        entry.data.get(CONF_MAC),
    )


def _same_mac(first: Any, second: Any) -> bool:
    """Whether two MAC spellings (colons, hyphens, dots, raw hex) name the same gateway.

    ``dr.format_mac`` never raises: a value that is no MAC comes back as it went in, so two
    equal malformed strings still compare equal. Such a section key never gets this far
    anyway: ``validate.config_schema`` rejects it first with the "Failed to parse" error.
    """
    return dr.format_mac(str(first)) == dr.format_mac(str(second))


def _log_mac_mismatch(hass: HomeAssistant, entry: ConfigEntry, keys: list[Any]) -> None:
    """Warn when myhome.yaml has gateway sections but none under this gateway's MAC (#106).

    Sections that belong to another configured gateway are not a mismatch.
    """
    # Disabled entries count as well: the entry still exists and owns its section, so a
    # file keyed only by a disabled gateway stays at DEBUG. This differs from the
    # top-level-platforms path of _load_live_yaml, which counts enabled gateways only.
    # Entries without a MAC are skipped rather than compared against "None".
    other_macs: list[str] = []
    for other in hass.config_entries.async_entries(DOMAIN):
        if other is entry or not isinstance(getattr(other, "data", None), dict):
            continue
        other_mac = other.data.get(CONF_MAC)
        if other_mac is not None:
            other_macs.append(str(other_mac))
    orphans = [k for k in keys if not any(_same_mac(k, mac) for mac in other_macs)]
    if not orphans:
        LOGGER.debug(
            "myhome.yaml has no section for gateway %s; its sections belong to other gateways",
            entry.data.get(CONF_MAC),
        )
        return
    LOGGER.warning(
        "myhome.yaml has no section for gateway %s; sections found under %s match no configured "
        "gateway. Rename the section to this gateway's MAC to load it",
        entry.data.get(CONF_MAC),
        ", ".join(str(k) for k in orphans),
    )


async def load_legacy_myhome_yaml(
    hass: HomeAssistant,
    entry: ConfigEntry,
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
    platforms: Sequence[Platform | str] = PLATFORMS,
    configured_expose: list[dict[str, Any]] | None = None,
) -> None:
    """Load legacy myhome.yaml if present for seamless backward-compatibility.

    When the file yields no section for this gateway (missing, empty, unparsable,
    invalid, or without this gateway's MAC), the copy imported into the entry
    options takes its place.
    """
    if not await _load_live_yaml(
        hass, entry, configured_platforms, platforms, configured_expose
    ):
        _apply_imported_copy(hass, entry, configured_platforms)


async def _load_live_yaml(
    hass: HomeAssistant,
    entry: ConfigEntry,
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
    platforms: Sequence[Platform | str],
    configured_expose: list[dict[str, Any]] | None = None,
) -> bool:
    """Apply this gateway's section of myhome.yaml; whether the file had one."""
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
        return False

    if parse_err is not None:
        LOGGER.error(
            "Failed to parse myhome.yaml from %s: %s", resolved_path, parse_err
        )
        return False

    if resolved_path is None or not raw_yaml or not isinstance(raw_yaml, dict):
        return False

    try:
        # Support single-gateway config without MAC address header at root level
        if any(plat in raw_yaml for plat in platforms) or CONF_EXPOSE in raw_yaml:
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
                return False

        # Ensure every gateway has mac and every device has where set if omitted
        for gw_key, gw_val in raw_yaml.items():
            if isinstance(gw_val, dict):
                if CONF_MAC not in gw_val:
                    gw_val[CONF_MAC] = str(gw_key)
                for plat, devs in gw_val.items():
                    # climate devices are addressed by `zone` (default "#0") and expose
                    # entries use named slugs (or WHO=13 with no `where`).
                    if plat in ("climate", CONF_EXPOSE) or not isinstance(devs, dict):
                        continue
                    for d_key, d_val in devs.items():
                        if isinstance(d_val, dict) and "where" not in d_val and "zone" not in d_val:
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
        if mac_key is None:
            _log_mac_mismatch(hass, entry, list(_validated))
            return False
        if configured_expose is not None:
            yaml_expose = _validated[mac_key].get(CONF_EXPOSE, [])
            if isinstance(yaml_expose, list):
                configured_expose.extend(yaml_expose)
        yaml_platforms = _validated[mac_key].get(CONF_PLATFORMS, {})
        _apply_yaml_platforms(yaml_platforms, configured_platforms)
        migrate_legacy_yaml_to_entry_options(hass, entry, yaml_platforms)
        LOGGER.info(
            "Loaded legacy myhome.yaml configuration for gateway %s (%s platforms)",
            entry.data[CONF_MAC],
            len(yaml_platforms),
        )
        return True
    except Exception as e:
        LOGGER.error(
            "Failed to parse myhome.yaml from %s: %s", resolved_path, e
        )
        return False
