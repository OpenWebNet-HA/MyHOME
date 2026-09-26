"""Config entry, entity registry, and device registry migration and cleanup helpers."""
from __future__ import annotations

from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)

from .const import DOMAIN, LOGGER


class GatewayProtocol(Protocol):
    """Protocol for gateway instances inspected during device pruning."""

    @property
    def unique_id(self) -> str | None: ...

    @property
    def id(self) -> str | None: ...


def _device_for_identifier(
    device_registry: dr.DeviceRegistry, entry: ConfigEntry, identifier: tuple[str, str]
) -> dr.DeviceEntry | None:
    """Return the entry's device carrying ``identifier``.

    Identifiers are only unique per config entry since core 2026.8, so the
    lookup is scoped to this entry (``async_get_device`` is deprecated).
    """
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if identifier in device.identifiers:
            return device
    return None


def migrate_entry_and_registries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Migrate config entry, entity registry, and device registry to modern canonical identifiers."""
    # Migrating the config entry's unique_id if it was not formatted to the recommended hass standard
    if entry.unique_id != dr.format_mac(entry.unique_id):
        hass.config_entries.async_update_entry(
            entry, unique_id=dr.format_mac(entry.unique_id)
        )
        LOGGER.warning("Migrating config entry unique_id to %s", entry.unique_id)

    entity_registry = er.async_get(hass)
    _mac = dr.format_mac(entry.data[CONF_MAC])

    _domain_to_who = {
        "light": "1",
        "cover": "2",
        "switch": "1",
        "media_player": "16",
        "climate": "4",
    }

    registry_entries = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    for reg_entry in registry_entries:
        parts = reg_entry.unique_id.split("-")
        # Old unique_id format: MAC-WHERE (MAC may be formatted with colons or raw hex)
        is_matching_mac = False
        mac_prefix = parts[0] if parts else ""
        if mac_prefix == _mac or mac_prefix == entry.data[CONF_MAC]:
            is_matching_mac = True
        elif mac_prefix:
            try:
                is_matching_mac = dr.format_mac(mac_prefix) == _mac
            except Exception:
                is_matching_mac = False

        if not is_matching_mac:
            continue

        after_mac = reg_entry.unique_id[len(mac_prefix) + 1 :]

        if reg_entry.domain == "button":
            btn_type = (
                "disable"
                if after_mac.endswith("-disable")
                else "enable"
                if after_mac.endswith("-enable")
                else None
            )
            if btn_type:
                raw_where = after_mac[: -len(btn_type) - 1]
                subparts = raw_where.split("-")
                if len(subparts) == 1:
                    # Missing WHO (2.0b3 unique_id format {mac}-{where}-{btn_type})
                    who = None
                    device_registry = dr.async_get(hass)
                    if reg_entry.device_id:
                        dev = device_registry.async_get(reg_entry.device_id)
                        if dev:
                            for ident in dev.identifiers:
                                if len(ident) == 2 and ident[0] == DOMAIN:
                                    id_parts = str(ident[1]).split("-")
                                    if len(id_parts) >= 3 and id_parts[1].isdigit():
                                        who = id_parts[1]
                                        break
                    if not who:
                        gw_platforms = configured_platforms
                        if "cover" in gw_platforms and (
                            raw_where in gw_platforms["cover"]
                            or f"2-{raw_where}" in gw_platforms["cover"]
                        ):
                            who = "2"
                        else:
                            who = "1"

                    target_unique_id = f"{_mac}-{who}-{raw_where}-{btn_type}"
                    existing_canonical_id = entity_registry.async_get_entity_id(
                        "button", DOMAIN, target_unique_id
                    )
                    if existing_canonical_id and existing_canonical_id != reg_entry.entity_id:
                        try:
                            entity_registry.async_remove(reg_entry.entity_id)
                            LOGGER.info(
                                "Pruned duplicate button entity %s in favor of %s",
                                reg_entry.entity_id,
                                existing_canonical_id,
                            )
                        except Exception as err:
                            LOGGER.warning(
                                "Could not prune duplicate button entity %s: %s",
                                reg_entry.entity_id,
                                err,
                            )
                    else:
                        try:
                            if reg_entry.entity_id.endswith(
                                "_2"
                            ) and not entity_registry.async_get(reg_entry.entity_id[:-2]):
                                entity_registry.async_update_entity(
                                    reg_entry.entity_id,
                                    new_unique_id=target_unique_id,
                                    new_entity_id=reg_entry.entity_id[:-2],
                                )
                            else:
                                entity_registry.async_update_entity(
                                    reg_entry.entity_id,
                                    new_unique_id=target_unique_id,
                                )
                            reloaded_entry = entity_registry.async_get(reg_entry.entity_id)
                            if reloaded_entry is not None:
                                reg_entry = reloaded_entry
                            LOGGER.info(
                                "Migrated button entity %s to canonical unique_id %s",
                                reg_entry.entity_id,
                                target_unique_id,
                            )
                        except ValueError as err:
                            LOGGER.warning(
                                "Could not auto-migrate button entity %s: %s",
                                reg_entry.entity_id,
                                err,
                            )
                elif mac_prefix != _mac:
                    target_unique_id = f"{_mac}-{after_mac}"
                    if not entity_registry.async_get_entity_id(
                        "button", DOMAIN, target_unique_id
                    ):
                        try:
                            entity_registry.async_update_entity(
                                reg_entry.entity_id, new_unique_id=target_unique_id
                            )
                            reloaded_entry = entity_registry.async_get(reg_entry.entity_id)
                            if reloaded_entry is not None:
                                reg_entry = reloaded_entry
                        except ValueError:
                            pass
            continue

        # Other platforms (light, cover, switch, media_player, climate)
        subparts = after_mac.split("-")
        if len(subparts) == 1:
            where_part = subparts[0]
            who = _domain_to_who.get(reg_entry.domain)
            if who:
                new_unique_id = f"{_mac}-{who}-{where_part}"
                if not entity_registry.async_get_entity_id(
                    reg_entry.domain, DOMAIN, new_unique_id
                ):
                    try:
                        entity_registry.async_update_entity(
                            reg_entry.entity_id, new_unique_id=new_unique_id
                        )
                        reloaded_entry = entity_registry.async_get(
                            reg_entry.entity_id
                        )  # reload
                        if reloaded_entry is not None:
                            reg_entry = reloaded_entry
                        LOGGER.info(
                            "Resurrecting orphaned MyHOME entity %s to new unique_id %s",
                            reg_entry.entity_id,
                            new_unique_id,
                        )
                    except ValueError as e:
                        LOGGER.warning(
                            "Could not auto-migrate entity %s to %s: %s",
                            reg_entry.entity_id,
                            new_unique_id,
                            e,
                        )

                # Also migrate matching device in device_registry if present so custom device names and areas are preserved
                device_registry = dr.async_get(hass)
                old_device = (
                    _device_for_identifier(
                        device_registry, entry, (DOMAIN, f"{_mac}-{where_part}")
                    )
                    or _device_for_identifier(
                        device_registry,
                        entry,
                        (DOMAIN, f"{entry.data[CONF_MAC]}-{where_part}"),
                    )
                    or (
                        device_registry.async_get(reg_entry.device_id)
                        if reg_entry.device_id
                        else None
                    )
                )
                if old_device:
                    try:
                        device_registry.async_update_device(
                            old_device.id,
                            new_identifiers={(DOMAIN, f"{_mac}-{who}-{where_part}")},
                        )
                    except Exception as e:
                        LOGGER.warning(
                            "Could not auto-migrate device %s to new identifier: %s",
                            old_device.id,
                            e,
                        )
        elif mac_prefix != _mac:
            new_unique_id = f"{_mac}-{after_mac}"
            if not entity_registry.async_get_entity_id(
                reg_entry.domain, DOMAIN, new_unique_id
            ):
                try:
                    entity_registry.async_update_entity(
                        reg_entry.entity_id, new_unique_id=new_unique_id
                    )
                    reloaded_entry = entity_registry.async_get(reg_entry.entity_id)
                    if reloaded_entry is not None:
                        reg_entry = reloaded_entry
                except ValueError:
                    pass


def prune_stale_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    gateway_device_entry: dr.DeviceEntry | None = None,
    gateway: GatewayProtocol | None = None,
) -> None:
    """Prune orphaned devices with 0 entities from the device registry."""
    try:
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        gateway_dev_id = getattr(gateway_device_entry, "id", None)
        gateway_handler = gateway
        gateway_unique_id = getattr(gateway_handler, "unique_id", None)
        gateway_id = getattr(gateway_handler, "id", None)
        for dev in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
            if dev.id == gateway_dev_id:
                continue
            if gateway_unique_id and (DOMAIN, gateway_unique_id) in dev.identifiers:
                continue
            if gateway_id and (DOMAIN, gateway_id) in dev.identifiers:
                continue
            # Do not prune scenario devices (CEN / CEN+) that intentionally have no entities
            is_scenario_device = (
                (dev.model and ("Scenario Control" in dev.model or dev.model.startswith("CEN")))
                or (dev.name and (dev.name.startswith("CEN") or "Scenario" in dev.name))
                or any(
                    isinstance(ident[1], str)
                    and (
                        "-15-" in ident[1]
                        or ident[1].startswith("cen")
                        or ident[1].startswith("cenplus")
                    )
                    for ident in dev.identifiers
                    if ident[0] == DOMAIN
                )
            )
            if is_scenario_device:
                continue
            dev_entries = er.async_entries_for_device(
                entity_registry, dev.id, include_disabled_entities=True
            )
            if len(dev_entries) == 0:
                LOGGER.info(
                    "Pruning empty orphaned MyHOME device from registry: %s (%s)",
                    dev.name,
                    dev.id,
                )
                device_registry.async_remove_device(dev.id)
    except Exception as err:
        LOGGER.debug("Error during empty device pruning: %s", err)


# Backwards compatibility aliases
async def async_migrate_entry_and_registries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    configured_platforms: dict[str, dict[str, dict[str, Any]]],
) -> None:
    """Async wrapper for migrate_entry_and_registries for backwards compatibility."""
    migrate_entry_and_registries(hass, entry, configured_platforms)


async_prune_stale_devices = prune_stale_devices
