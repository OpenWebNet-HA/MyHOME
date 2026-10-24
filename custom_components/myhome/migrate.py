"""Config entry, entity registry, and device registry migration and cleanup helpers."""
from __future__ import annotations

import re
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

from .const import (
    CONF_COVER_TRAVEL_TIMES,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_EXPOSE,
)
from .where_grammar import canonical_point_address, canonical_point_spelling


class GatewayProtocol(Protocol):
    """Protocol for gateway instances inspected during device pruning."""

    @property
    def unique_id(self) -> str | None: ...

    @property
    def id(self) -> str | None: ...


def preserve_legacy_compat_name(
    hass: HomeAssistant,
    entity_id: str,
    registry_entry: er.RegistryEntry | None,
    legacy_name: str | None,
) -> er.RegistryEntry | None:
    """Preserve a pre-v2 entity's full friendly name as ``compat_name`` on HA 2026.11+.

    Legacy ``MAC-WHERE`` registry entries predated ``has_entity_name = True`` and
    stored the user's full friendly name in ``entry.name`` without a device prefix.
    When ``EntityPlatform`` attaches the entity to its v2 device and sets
    ``has_entity_name = True``, Home Assistant 2026.11+ clears ``compat_name`` and
    prefixes ``entry.name`` with the default device name unless ``compat_name`` is
    restored once the entity is registered.
    """
    if (
        not legacy_name
        or registry_entry is None
        or getattr(registry_entry, "compat_name", False) is not None
    ):
        return registry_entry
    return er.async_get(hass)._async_update_entity(
        entity_id,
        compat_name=legacy_name,  # type: ignore[call-arg, unused-ignore]
    )


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
        "lock": "6",
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
                        legacy_name = (
                            reg_entry.name if not reg_entry.has_entity_name else None
                        )
                        entity_registry.async_update_entity(
                            reg_entry.entity_id, new_unique_id=new_unique_id
                        )
                        reloaded_entry = entity_registry.async_get(
                            reg_entry.entity_id
                        )  # reload
                        if reloaded_entry is not None:
                            reg_entry = (
                                preserve_legacy_compat_name(
                                    hass,
                                    reloaded_entry.entity_id,
                                    reloaded_entry,
                                    legacy_name,
                                )
                                or reloaded_entry
                            )
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

    try:
        migrate_canonical_point_addresses(hass, entry)
    except Exception as err:  # pylint: disable=broad-except  # a registry problem must not block the setup
        LOGGER.warning("Could not migrate 4-digit point addresses to their canonical spelling: %s", err)


# Platforms whose unique ids may carry a WHO 1 / WHO 2 point address:
# {mac}-{who}-{where}[#4#{iface}][-{suffix}]. The WHO in the id is the predicate: a WHO 1
# motion / illuminance sensor ({mac}-1-0004-motion) is a point, a WHO 25 CEN button
# ({mac}-25-0004) or a WHO 4 probe ({mac}-4-0004-temperature) is not and does not match.
_POINT_ADDRESS_DOMAINS = ("light", "switch", "cover", "button", "binary_sensor", "sensor")
_POINT_ADDRESS_ID = re.compile(r"^(?P<who>[12])-(?P<where>\d{4})(?P<tail>(?:[#-].*)?)$")


def _canonical_point_id(after_mac: str) -> str | None:
    """``1-0004`` -> ``1-04`` (also ``2-0009#4#01``, ``1-0004-disable``); None when nothing is rewritten."""
    if (match := _POINT_ADDRESS_ID.match(after_mac)) is None:
        return None
    # Silent: the registry spelling is not something the user can fix in myhome.yaml;
    # validate.py warns once about the yaml spelling itself.
    canonical = canonical_point_spelling(match["where"])
    if canonical == match["where"]:
        return None
    return f"{match['who']}-{canonical}{match['tail']}"


def migrate_canonical_point_addresses(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move entities and devices of 4-digit point spellings (``0004``) to the canonical one (``04``).

    myhome.yaml now resolves ``0004`` to ``04`` (#147, #135). The entity created under the old
    spelling keeps its entity_id, name and area under the new unique id; a duplicate that bus
    discovery created under the canonical spelling in this config entry is removed in its favour,
    and only once the rename has succeeded (the registry refuses a rename to a unique id in use,
    so the duplicate is taken out first and put back if the rename fails). A duplicate held by
    another config entry is a collision: nothing is touched and a WARNING says so. Running the
    migration again finds nothing to do.
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    mac = dr.format_mac(entry.data[CONF_MAC])
    prefix = f"{mac}-"

    for reg_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if reg_entry.domain not in _POINT_ADDRESS_DOMAINS or not reg_entry.unique_id.startswith(prefix):
            continue
        if (new_tail := _canonical_point_id(reg_entry.unique_id[len(prefix):])) is None:
            continue
        new_unique_id = f"{prefix}{new_tail}"
        duplicate_id = entity_registry.async_get_entity_id(reg_entry.domain, DOMAIN, new_unique_id)
        duplicate = entity_registry.async_get(duplicate_id) if duplicate_id else None
        if duplicate is not None and duplicate.config_entry_id != entry.entry_id:
            LOGGER.warning(
                "Not migrating %s to unique_id %s: %s of another config entry already holds it",
                reg_entry.entity_id, new_unique_id, duplicate.entity_id,
            )
            continue
        if duplicate is not None:
            # The bus-created twin of this config entry: out of the way for the rename.
            entity_registry.async_remove(duplicate.entity_id)
        try:
            entity_registry.async_update_entity(reg_entry.entity_id, new_unique_id=new_unique_id)
        except ValueError as err:
            LOGGER.warning("Could not migrate %s to unique_id %s: %s", reg_entry.entity_id, new_unique_id, err)
            if duplicate is not None:
                # Put the twin back as it was (the registry restores a removed entity's
                # entity_id, name, icon, area and labels on re-creation).
                entity_registry.async_get_or_create(
                    duplicate.domain,
                    duplicate.platform,
                    duplicate.unique_id,
                    config_entry=entry,
                    device_id=duplicate.device_id,
                    original_name=duplicate.original_name,
                )
            continue
        LOGGER.info("Migrated %s to unique_id %s", reg_entry.entity_id, new_unique_id)
        if duplicate is not None:
            LOGGER.info("Removed %s, a duplicate of %s under the canonical address", duplicate.entity_id, reg_entry.entity_id)

    _migrate_expose_subentries(hass, entry)
    _migrate_cover_travel_times(hass, entry)

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identifiers = set(device.identifiers)
        renamed = {
            ident: (DOMAIN, f"{prefix}{new_tail}")
            for ident in identifiers
            if ident[0] == DOMAIN
            and str(ident[1]).startswith(prefix)
            and (new_tail := _canonical_point_id(str(ident[1])[len(prefix):])) is not None
        }
        if not renamed:
            continue
        twins = [
            other
            for new_ident in renamed.values()
            if (other := _device_for_identifier(device_registry, entry, new_ident)) is not None
            and other.id != device.id
        ]
        # The registry refuses an identifier another device holds, so the bus-created twin
        # goes first: its entities move to the surviving device and it is removed, to come
        # back as it was (same id, area, labels, user-given name) if the rename is refused.
        handed_over: dict[str, list[str]] = {}
        for other in twins:
            handed_over[other.id] = [
                other_entity.entity_id
                for other_entity in er.async_entries_for_device(entity_registry, other.id, include_disabled_entities=True)
            ]
            for entity_id in handed_over[other.id]:
                entity_registry.async_update_entity(entity_id, device_id=device.id)
            device_registry.async_remove_device(other.id)
        try:
            device_registry.async_update_device(
                device.id, new_identifiers=(identifiers - set(renamed)) | set(renamed.values())
            )
        except Exception as err:  # pylint: disable=broad-except
            LOGGER.warning("Could not migrate device %s to the canonical address: %s", device.id, err)
            for other in twins:
                restored = device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id, identifiers=set(other.identifiers)
                )
                for entity_id in handed_over[other.id]:
                    entity_registry.async_update_entity(entity_id, device_id=restored.id)
            continue
        for other in twins:
            # The twin's area and user-given name survive on the configured device.
            carried: dict[str, Any] = {}
            if device.area_id is None and other.area_id is not None:
                carried["area_id"] = other.area_id
            if device.name_by_user is None and other.name_by_user is not None:
                carried["name_by_user"] = other.name_by_user
            if carried:
                device_registry.async_update_device(device.id, **carried)
            LOGGER.info("Removed device %s, a duplicate of %s under the canonical address", other.id, device.id)


def _migrate_expose_subentries(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move WHO 1 / WHO 2 ``expose`` subentries at ``0001``..``0009`` to ``01``..``09`` (#147).

    `normalize_expose_where()` spells them canonically at load time as well; the
    stored data and the title follow so the UI shows the address the bus uses.
    """
    expose_subentries = [
        subentry for subentry in getattr(entry, "subentries", {}).values() if subentry.subentry_type == SUBENTRY_TYPE_EXPOSE
    ]
    taken = {
        (str(sub.data.get(CONF_WHO)), str(sub.data.get(CONF_WHERE))): sub
        for sub in expose_subentries
        if str(sub.data.get(CONF_WHO)) in ("1", "2")
    }
    for subentry in expose_subentries:
        data = dict(subentry.data)
        if str(data.get(CONF_WHO)) not in ("1", "2") or not isinstance(data.get(CONF_WHERE), str):
            continue
        canonical = canonical_point_address(data[CONF_WHERE])
        if canonical == data[CONF_WHERE]:
            continue
        if (holder := taken.get((str(data[CONF_WHO]), canonical))) is not None:
            # Two bindings for one bus address: the canonical one is the one that worked.
            LOGGER.warning(
                "Removing expose subentry %s (%s): %s (%s) already exposes WHO %s WHERE %s",
                subentry.subentry_id, subentry.title, holder.subentry_id, holder.title, data[CONF_WHO], canonical,
            )
            hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
            continue
        title = subentry.title.replace(f"WHERE {data[CONF_WHERE]} ", f"WHERE {canonical} ")
        hass.config_entries.async_update_subentry(
            entry, subentry, data={**data, CONF_WHERE: canonical}, title=title
        )
        LOGGER.info("Migrated expose subentry %s from WHERE %s to %s", subentry.subentry_id, data[CONF_WHERE], canonical)


def _migrate_cover_travel_times(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-key persisted cover calibrations from ``0004`` / ``0004#4#01`` to ``04`` / ``04#4#01`` (#147).

    `entry.options["cover_travel_times"]` is keyed by the cover's device id; once the
    cover is addressed canonically its measured travel times and `slat_time` would be
    orphaned. A record already stored under the canonical key belongs to the cover
    that worked on the bus and wins; the 4-digit one is then dropped.
    """
    stored = entry.options.get(CONF_COVER_TRAVEL_TIMES)
    if not isinstance(stored, dict):
        return
    rekeyed = {key: record for key, record in stored.items() if canonical_point_address(str(key)) == str(key)}
    changed = False
    for key, record in stored.items():
        canonical = canonical_point_address(str(key))
        if canonical == str(key):
            continue
        changed = True
        if canonical in rekeyed:
            LOGGER.info(
                "Dropping the cover calibration stored under %s: %s already has one under the canonical address",
                key, canonical,
            )
            continue
        rekeyed[canonical] = record
        LOGGER.info("Migrated the cover calibration of %s to the canonical address %s", key, canonical)
    if changed:
        hass.config_entries.async_update_entry(entry, options={**entry.options, CONF_COVER_TRAVEL_TIMES: rekeyed})


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
