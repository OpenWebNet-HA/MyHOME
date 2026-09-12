"""Support for MyHome switches (light modules used for controlled outlets, relays)."""

from __future__ import annotations

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
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
        self.entity_id = f"{platform.lower()}.{name.lower().replace(' ', '_')}_lock"
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
        self.entity_id = f"{platform.lower()}.{name.lower().replace(' ', '_')}_unlock"
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
