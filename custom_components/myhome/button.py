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
    CONF_ENTITIES,
    CONF_MAC,
    CONF_NAME,
    EntityCategory,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify

from .const import (
    CONF_BUS_INTERFACE,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0


def _valid_device_address(address: str) -> bool:
    """Accept numeric WHERE and optional bus routing, without rewriting either."""
    return re.fullmatch(r"[0-9]+(?:#4#[0-9]+)?", address) is not None


async def async_setup_entry(hass, config_entry, async_add_entities):
    mac = config_entry.data.get(CONF_MAC)
    if not mac or mac not in hass.data.get(DOMAIN, {}):
        return True
    if PLATFORM not in hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}):
        return True

    _buttons = []
    _configured_buttons = hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]
    gateway = hass.data[DOMAIN][mac].get(CONF_ENTITY)

    known_button_actuators = set()

    def _create_buttons_for_device(dev_id, cfg):
        who = str(cfg.get(CONF_WHO, "1"))
        where = str(cfg.get(CONF_WHERE, dev_id))
        interface = cfg.get(CONF_BUS_INTERFACE) if CONF_BUS_INTERFACE in cfg else cfg.get("interface")
        if not where or str(where).startswith("#"):
            return []

        actuator_key = f"{who}-{where}#4#{interface}" if interface else f"{who}-{where}"
        if actuator_key in known_button_actuators:
            return []
        known_button_actuators.add(actuator_key)

        name = cfg.get(CONF_NAME, f"Device {where}")
        manufacturer = cfg.get(CONF_MANUFACTURER, "BTicino")
        model = cfg.get(CONF_DEVICE_MODEL, "Actuator")
        clean_where = where.split("-")[-1]
        device_where_id = f"{clean_where}#4#{interface}" if interface else str(clean_where)
        device_id = device_where_id

        disable_button = DisableCommandButtonEntity(
            hass=hass,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            interface=interface,
            name=name,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )
        enable_button = EnableCommandButtonEntity(
            hass=hass,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            interface=interface,
            name=name,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )
        return [disable_button, enable_button]

    for _button in list(_configured_buttons.keys()):
        _buttons.extend(_create_buttons_for_device(_button, _configured_buttons[_button]))

    # Discovered actuators are restored by their platforms from the registry.
    # They no longer emit a new-device signal, so rebuild their buttons here too.
    # Use parent actuators rather than stale button entries: a deleted actuator
    # must not be resurrected just because its old buttons remain registered.
    registry = er.async_get(hass)
    registered_entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    mac_prefixes = (f"{gateway.mac}-", f"{mac}-")

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

    configured_platforms = hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {})
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
        """Add lock/unlock buttons dynamically for newly discovered or configured devices."""
        new_btns = _create_buttons_for_device(dev_info.get("device_id"), dev_info)
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
    mac = config_entry.data.get(CONF_MAC)
    if not mac or mac not in hass.data.get(DOMAIN, {}):
        return True
    if PLATFORM not in hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}):
        return True

    _configured_buttons = hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]

    for _button in list(_configured_buttons.keys()):
        del hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM][_button]
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
        )
        self._attr_name = "Lock"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:lock-alert"

        self._attr_entity_category = EntityCategory.CONFIG

        self._attr_unique_id = f"{gateway.mac}-{self._who}-{self._device_id}-disable"
        clean_name = slugify(name) if name else ""
        if not clean_name:
            clean_name = slugify(f"device_{where}") or "device"
        self.entity_id = f"{platform.lower()}.{clean_name}_lock"
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
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES not in device_dict or not isinstance(device_dict[CONF_ENTITIES], dict):
                device_dict[CONF_ENTITIES] = {}
            device_dict[CONF_ENTITIES]["disable"] = self
        except (KeyError, TypeError):
            pass

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES in device_dict and isinstance(device_dict[CONF_ENTITIES], dict) and "disable" in device_dict[CONF_ENTITIES]:
                del device_dict[CONF_ENTITIES]["disable"]
        except (KeyError, TypeError):
            pass

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
        )
        self._attr_name = "Unlock"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:lock-open-variant-outline"

        self._attr_entity_category = EntityCategory.CONFIG

        self._attr_unique_id = f"{gateway.mac}-{self._who}-{self._device_id}-enable"
        clean_name = slugify(name) if name else ""
        if not clean_name:
            clean_name = slugify(f"device_{where}") or "device"
        self.entity_id = f"{platform.lower()}.{clean_name}_unlock"
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
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES not in device_dict or not isinstance(device_dict[CONF_ENTITIES], dict):
                device_dict[CONF_ENTITIES] = {}
            device_dict[CONF_ENTITIES]["enable"] = self
        except (KeyError, TypeError):
            pass

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        try:
            device_dict = self._hass.data[DOMAIN][self._gateway_handler.mac][CONF_PLATFORMS][self._platform][self._device_id]
            if CONF_ENTITIES in device_dict and isinstance(device_dict[CONF_ENTITIES], dict) and "enable" in device_dict[CONF_ENTITIES]:
                del device_dict[CONF_ENTITIES]["enable"]
        except (KeyError, TypeError):
            pass

    async def async_press(self) -> None:
        """Press the button."""
        await self._gateway_handler.send(f"*14*1*{self._full_where}##")
