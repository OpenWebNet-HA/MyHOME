"""Support for MyHome switches (light modules used for controlled outlets, relays)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

from homeassistant.components.button import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.button import (
    ButtonEntity,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    EntityCategory,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_BUS_INTERFACE,
    CONF_DEVICE_MODEL,
    CONF_MANUFACTURER,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    SERVICE_CALIBRATE_COVER,
)
from .data import get_runtime_data
from .discovery import Address, parse_unique_id
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0


def _valid_device_address(address: str) -> bool:
    """Accept numeric WHERE and optional bus routing, without rewriting either."""
    return re.fullmatch(r"[0-9]+(?:#4#[0-9]+)?", address) is not None


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the buttons of a gateway: lock / unlock per actuator, calibrate per timed cover.

    Buttons have no bus address of their own: they are created for every
    actuator of the other platforms - from the registry at start, from
    ``myhome.yaml``, and from the ``myhome_new_device`` announcements the
    light, switch and cover platforms send when they discover one.
    """
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True
    mac = runtime.mac

    _buttons = []
    _configured_buttons = runtime.platforms[PLATFORM]
    gateway = runtime.gateway

    known_button_actuators = set()
    known_calibration_covers: set[str] = set()

    def _calibration_button_for_cover(device_id, name):
        """One 'Calibrate travel time' button per timed cover, on the cover's device."""
        device_id = str(device_id)
        if not device_id or device_id in known_calibration_covers:
            return []
        known_calibration_covers.add(device_id)
        address = Address.from_device_id(device_id)
        return [
            CalibrateCoverButtonEntity(
                hass=hass,
                platform=PLATFORM,
                device_id=device_id,
                where=address.where,
                interface=address.interface,
                name=name or f"Cover {address.where}",
                gateway=gateway,
            )
        ]

    # Covers already in the entity registry (restored before the cover platform re-announces them)
    try:
        registry = er.async_get(hass)
        device_registry = dr.async_get(hass)
        for reg_entry in er.async_entries_for_config_entry(registry, config_entry.entry_id):
            if reg_entry.domain != "cover" or not reg_entry.unique_id:
                continue
            who, device_id = parse_unique_id(reg_entry.unique_id, gateway.mac, mac)
            if who == "2" and device_id:
                # The cover *is* its device, so its name lives on the device entry.
                device = device_registry.async_get(reg_entry.device_id) if reg_entry.device_id else None
                cover_name = (device.name_by_user or device.name) if device else None
                _buttons.extend(_calibration_button_for_cover(device_id, cover_name or reg_entry.name))
    except Exception as err:  # pragma: no cover - registry unavailable in some test harnesses
        LOGGER.debug("Could not enumerate covers for calibration buttons: %s", err)

    if gateway is not None:
        _buttons.append(CalibrateAllCoversButtonEntity(hass=hass, config_entry=config_entry, gateway=gateway))

    def _create_buttons_for_device(dev_id, cfg):
        """Lock and unlock buttons for one actuator (a yaml entry or an announcement)."""
        who = str(cfg.get(CONF_WHO, "1"))
        address = Address.from_config(dev_id, cfg)
        if not address.where or address.where.startswith("#"):
            return []  # groups / general have no lock

        actuator_key = f"{who}-{address.key}"
        if actuator_key in known_button_actuators:
            return []
        known_button_actuators.add(actuator_key)

        common = dict(
            hass=hass,
            platform=PLATFORM,
            device_id=address.clean_key,
            who=who,
            where=address.where,
            interface=address.interface,
            name=cfg.get(CONF_NAME, f"Device {address.where}"),
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Actuator"),
            gateway=gateway,
        )
        return [DisableCommandButtonEntity(**common), EnableCommandButtonEntity(**common)]

    for _button in list(_configured_buttons.keys()):
        _buttons.extend(_create_buttons_for_device(_button, _configured_buttons[_button]))

    # Discovered actuators are restored by their platforms from the registry.
    # They no longer emit a new-device signal, so rebuild their buttons here too.
    # Use parent actuators rather than stale button entries: a deleted actuator
    # must not be resurrected just because its old buttons remain registered.
    registry = er.async_get(hass)
    registered_entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    # Registry ids may carry the config entry's MAC spelling as well as the gateway's
    mac_prefixes = (f"{gateway.mac}-", f"{config_entry.data.get(CONF_MAC, mac)}-")

    def _registered_id(registered):
        if registered.platform == DOMAIN:
            for prefix in mac_prefixes:
                if registered.unique_id.startswith(prefix):
                    return registered.unique_id[len(prefix):]
        return ""

    # Platform setup order is not guaranteed. Determine sensor/switch ownership
    # before restoring any lights, rather than waiting for light.py's cleanup.
    # Include the interface in every key: equal WHEREs on different buses differ.
    non_light_addresses = set()
    for registered in registered_entries:
        registered_id = _registered_id(registered)
        if registered.domain == "switch" and registered_id.startswith("1-"):
            address = registered_id[2:]
        elif registered.domain in ("sensor", "binary_sensor"):
            if registered_id.startswith("1-"):
                address = registered_id[2:].split("-", 1)[0]
            elif registered_id.endswith(("-motion", "-illuminance")):
                # Legacy sensor IDs omitted WHO; other WHO prefixes remain
                # non-numeric and fail validation below.
                address = registered_id.rsplit("-", 1)[0]
            else:
                continue
        else:
            continue
        if _valid_device_address(address):
            non_light_addresses.add(address)

    configured_platforms = runtime.platforms
    for domain, default_who in (("switch", "1"), ("sensor", "1"), ("binary_sensor", "25")):
        for dev_id, cfg in configured_platforms.get(domain, {}).items():
            if str(cfg.get(CONF_WHO, default_who)) != "1":
                continue
            address = str(cfg.get(CONF_WHERE, dev_id)).removeprefix("1-")
            interface = cfg.get(CONF_BUS_INTERFACE) if CONF_BUS_INTERFACE in cfg else cfg.get("interface")
            if interface is not None and "#4#" not in address:
                address = f"{address}#4#{interface}"
            if _valid_device_address(address):
                non_light_addresses.add(address)

    devices = None
    for registered in registered_entries:
        who = {"light": "1", "switch": "1", "cover": "2"}.get(registered.domain)
        if who is None:
            continue
        registered_id = _registered_id(registered)
        if not registered_id.startswith(f"{who}-"):
            continue
        device_id = registered_id[len(who) + 1:]
        # Do not turn corrupted IDs such as MAC-1-1-06 into command WHERE=1-06.
        if not _valid_device_address(device_id):
            continue
        if registered.domain == "light" and device_id in non_light_addresses:
            continue
        where, _, interface = device_id.partition("#4#")
        default_suffix = f"{where}I{interface}" if interface else where
        if registered.device_id and devices is None:
            devices = dr.async_get(hass)
        device = devices.async_get(registered.device_id) if registered.device_id else None
        _buttons.extend(_create_buttons_for_device(device_id, {
            CONF_WHO: who,
            CONF_WHERE: where,
            CONF_BUS_INTERFACE: interface or None,
            CONF_NAME: (device.name if device else None) or registered.original_name
            or f"{registered.domain.title()} {default_suffix}",
            CONF_MANUFACTURER: (device.manufacturer if device else None) or "BTicino",
            CONF_DEVICE_MODEL: (device.model if device else None) or "Actuator",
        }))

    if _buttons:
        async_add_entities(_buttons)

    @callback
    def _async_new_device_listener(dev_info):
        """Add lock/unlock (and, for covers, calibration) buttons for newly discovered or configured devices."""
        new_btns = _create_buttons_for_device(dev_info.get("device_id"), dev_info)
        if str(dev_info.get("who", "")) == "2":
            new_btns.extend(_calibration_button_for_cover(dev_info.get("device_id"), dev_info.get("name")))
        if new_btns:
            async_add_entities(new_btns)

    if hasattr(config_entry, "async_on_unload"):
        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"myhome_new_device_{mac}",
                _async_new_device_listener,
            )
        )

    return True


async def async_unload_entry(hass, config_entry):
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    _configured_buttons = runtime.platforms[PLATFORM]

    for _button in list(_configured_buttons.keys()):
        del runtime.platforms[PLATFORM][_button]
    return True


class DisableCommandButtonEntity(ButtonEntity, MyHOMEEntity):
    def __init__(
        self,
        hass,
        platform: str,
        name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        super().__init__(
            hass=hass,
            name=name,
            platform=platform,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            translation_key="lock",
        )
        self._attr_icon = "mdi:lock-alert"

        self._attr_entity_category = EntityCategory.CONFIG

        self._attr_unique_id = f"{gateway.mac}-{self._who}-{self._device_id}-disable"
        self._interface = interface
        self._full_where = (
            f"{self._where}#4#{self._interface}"
            if self._interface is not None
            else self._where
        )

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_availability_listener()
        self._register_entity_ref("disable")

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref("disable")

    async def async_press(self) -> None:
        """Press the button."""
        await self._gateway_handler.send(f"*14*0*{self._full_where}##")


class EnableCommandButtonEntity(ButtonEntity, MyHOMEEntity):
    def __init__(
        self,
        hass,
        platform: str,
        name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        super().__init__(
            hass=hass,
            name=name,
            platform=platform,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            translation_key="unlock",
        )
        self._attr_icon = "mdi:lock-open-variant-outline"

        self._attr_entity_category = EntityCategory.CONFIG

        self._attr_unique_id = f"{gateway.mac}-{self._who}-{self._device_id}-enable"
        self._interface = interface
        self._full_where = (
            f"{self._where}#4#{self._interface}"
            if self._interface is not None
            else self._where
        )

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_availability_listener()
        self._register_entity_ref("enable")

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref("enable")

    async def async_press(self) -> None:
        """Press the button."""
        await self._gateway_handler.send(f"*14*1*{self._full_where}##")


class CalibrateCoverButtonEntity(ButtonEntity, MyHOMEEntity):
    """Measure a timed cover's up/down travel times on the bus (myhome.calibrate_cover)."""

    def __init__(self, hass, platform: str, device_id: str, where: str, interface: str | None, name: str, gateway):
        super().__init__(
            hass=hass,
            name=name,
            platform=platform,
            device_id=device_id,
            who="2",
            where=where,
            manufacturer="BTicino",
            model="Shutter / Cover",
            gateway=gateway,
            translation_key="calibrate_travel_time",
        )
        self._attr_icon = "mdi:ruler-square-compass"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_unique_id = f"{gateway.mac}-2-{device_id}-calibrate"
        self._interface = interface
        self._poll_on_add = False

    async def async_update(self):
        """Buttons have no state to request."""

    async def async_press(self) -> None:
        cover_entity_id = er.async_get(self.hass).async_get_entity_id("cover", DOMAIN, f"{self._gateway_handler.mac}-2-{self._device_id}")
        if not cover_entity_id:
            LOGGER.warning("No cover entity found for %s; cannot calibrate.", self._device_id)
            return
        await self.hass.services.async_call(
            DOMAIN, SERVICE_CALIBRATE_COVER, {"entity_id": cover_entity_id}, blocking=False
        )


class CalibrateAllCoversButtonEntity(ButtonEntity):
    """Run myhome.calibrate_cover for every timed cover of this gateway, one after another."""

    _attr_has_entity_name = True
    _attr_translation_key = "calibrate_all_covers"
    _attr_icon = "mdi:window-shutter-settings"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, hass, config_entry, gateway):
        self.hass = hass
        self._config_entry = config_entry
        self._gateway_handler = gateway
        self._attr_unique_id = f"{gateway.mac}-calibrate-all-covers"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, gateway.unique_id)})

    @property
    def available(self) -> bool:
        return bool(getattr(self._gateway_handler, "available", True))

    def _cover_entity_ids(self) -> list[str]:
        registry = er.async_get(self.hass)
        return sorted(
            e.entity_id
            for e in er.async_entries_for_config_entry(registry, self._config_entry.entry_id)
            if e.domain == "cover" and not e.disabled
        )

    async def async_press(self) -> None:
        entity_ids = self._cover_entity_ids()
        if not entity_ids:
            LOGGER.warning("%s No cover entities to calibrate.", self._gateway_handler.log_id)
            return
        LOGGER.info("%s Calibrating %d covers sequentially.", self._gateway_handler.log_id, len(entity_ids))
        # The entity service runs the covers concurrently; the per-gateway lock serializes them.
        await self.hass.services.async_call(DOMAIN, SERVICE_CALIBRATE_COVER, {"entity_id": entity_ids}, blocking=False)

