"""Support for MyHome switches (light modules used for controlled outlets, relays)."""
from homeassistant.components.switch import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import (
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    CONF_BUS_INTERFACE,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity


async def async_setup_entry(hass, config_entry, async_add_entities):
    mac = config_entry.data.get(CONF_MAC)
    if not mac or mac not in hass.data.get(DOMAIN, {}):
        return True
    if PLATFORM not in hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}):
        return True

    known_switches = set()
    try:
        entity_registry = er.async_get(hass)
        existing_entries = er.async_entries_for_config_entry(entity_registry, config_entry.entry_id)
    except Exception:
        entity_registry = None
        existing_entries = []
    restored_switches = []

    gateway = hass.data[DOMAIN][mac][CONF_ENTITY]
    _configured_switches = hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}).get(PLATFORM, {})

    for entry in existing_entries:
        if entry.domain == PLATFORM:
            unique_id = entry.unique_id
            # Clean up corrupted duplicate unique IDs from earlier versions like "{mac}-1-1-06"
            if "-1-1-" in unique_id:
                if entity_registry:
                    entity_registry.async_remove(entry.entity_id)
                continue

            # unique_id format: "{mac}-{who}-{device_id}"
            # device_id is "{where}" or "{where}#4#{interface}"
            after_mac = unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{mac}-", "", 1)
            parts_who = after_mac.split("-", 1)
            device_id = parts_who[-1] if len(parts_who) > 1 else after_mac
            if "#4#" in device_id:
                parts = device_id.split("#4#")
                where = parts[0]
                interface = parts[1] if len(parts) > 1 else None
            else:
                where = device_id
                interface = None

            clean_where = where.split('-')[-1]
            default_suffix = f"{clean_where}I{interface}" if interface else clean_where
            cfg = _configured_switches.get(device_id) or _configured_switches.get(where) or _configured_switches.get(clean_where) or {}

            _name = cfg.get(CONF_NAME) or f"Switch {default_suffix}"
            _entity_name = cfg.get(CONF_ENTITY_NAME)
            _icon = cfg.get(CONF_ICON)
            _icon_on = cfg.get(CONF_ICON_ON)
            _device_class = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class") or SwitchDeviceClass.SWITCH
            _manufacturer = cfg.get(CONF_MANUFACTURER, "BTicino")
            _model = cfg.get(CONF_DEVICE_MODEL, "Switch / Relay")

            _switch = MyHOMESwitch(
                hass=hass,
                name=_name,
                entity_name=_entity_name,
                icon=_icon,
                icon_on=_icon_on,
                device_id=device_id,
                who="1",
                where=where,
                interface=interface,
                device_class=_device_class,
                manufacturer=_manufacturer,
                model=_model,
                gateway=gateway,
            )
            known_switches.add(device_id)
            if not interface:
                known_switches.add(clean_where)
            restored_switches.append(_switch)

    # Also instantiate any configured switches from myhome.yaml not yet in registry
    seen_configured_where = set()
    for dev_id, cfg in _configured_switches.items():
        where = str(cfg.get(CONF_WHERE, dev_id))
        clean_where = where.split("-")[-1]
        interface = cfg.get(CONF_BUS_INTERFACE) or cfg.get("bus_interface") or cfg.get("interface")
        device_where_id = f"{clean_where}#4#{interface}" if interface else str(clean_where)
        clean_unique_id = device_where_id
        default_suffix = f"{clean_where}I{interface}" if interface else clean_where

        if clean_unique_id in seen_configured_where or device_where_id in known_switches or dev_id in known_switches:
            continue
        seen_configured_where.add(clean_unique_id)

        _name = cfg.get(CONF_NAME) or f"Switch {default_suffix}"
        _device_class = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class") or SwitchDeviceClass.SWITCH
        _switch = MyHOMESwitch(
            hass=hass,
            name=_name,
            entity_name=cfg.get(CONF_ENTITY_NAME),
            icon=cfg.get(CONF_ICON),
            icon_on=cfg.get(CONF_ICON_ON),
            device_id=device_where_id,
            who=str(cfg.get(CONF_WHO, "1")),
            where=where,
            interface=interface,
            device_class=_device_class,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Switch / Relay"),
            gateway=gateway,
        )
        known_switches.add(device_where_id)
        if not interface:
            known_switches.add(clean_where)
        known_switches.add(dev_id)
        restored_switches.append(_switch)

        # Signal button platform to create Lock/Unlock buttons if needed
        async_dispatcher_send(
            hass,
            f"myhome_new_device_{mac}",
            {
                "who": str(cfg.get(CONF_WHO, "1")),
                "where": where,
                "interface": interface,
                "name": _name,
                "device_id": device_where_id,
            },
        )

    if restored_switches:
        async_add_entities(restored_switches)
    return True


async def async_unload_entry(hass, config_entry):
    mac = config_entry.data.get(CONF_MAC)
    if not mac or mac not in hass.data.get(DOMAIN, {}):
        return True
    if PLATFORM not in hass.data[DOMAIN][mac].get(CONF_PLATFORMS, {}):
        return True

    _configured_switches = hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM]
    for _switch in list(_configured_switches.keys()):
        del hass.data[DOMAIN][mac][CONF_PLATFORMS][PLATFORM][_switch]

    return True


class MyHOMESwitch(MyHOMEEntity, SwitchEntity):
    def __init__(
        self,
        hass,
        name: str,
        entity_name: str,
        icon: str,
        icon_on: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        device_class: str,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ):
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who=who,
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

        self._interface = interface
        self._full_where = f"{self._where}#4#{self._interface}" if self._interface is not None else self._where

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

        self._attr_device_class = (
            SwitchDeviceClass.OUTLET
            if (device_class or "").lower() == "outlet"
            else SwitchDeviceClass.SWITCH
        )

        self._on_icon = icon_on
        self._off_icon = icon

        if self._off_icon is not None:
            self._attr_icon = self._off_icon

        self._attr_is_on = None

    async def async_added_to_hass(self):
        """Run when entity about to be added to hass."""
        self._register_availability_listener()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_update_{self._gateway_handler.mac}_1_{self._full_where}",
                self.handle_event,
            )
        )
        if self._full_where != self._where:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    f"myhome_update_{self._gateway_handler.mac}_1_{self._where}",
                    self.handle_event,
                )
            )
        await self.async_update()

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(OWNLightingCommand.status(self._full_where))

    async def async_turn_on(self, **kwargs):  # pylint: disable=unused-argument
        """Turn the device on."""
        await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where))

    async def async_turn_off(self, **kwargs):  # pylint: disable=unused-argument
        """Turn the device off."""
        await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))

    @callback
    def handle_event(self, message: OWNLightingEvent):
        """Handle an event message."""
        if getattr(message, "is_translation", None) is True:
            return
        if self._attr_device_class == SwitchDeviceClass.SWITCH:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log.replace("Light", "Switch"),
            )
        elif self._attr_device_class == SwitchDeviceClass.OUTLET:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log.replace("Light", "Outlet"),
            )
        else:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
        self._attr_is_on = message.is_on
        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon
        if self.hass is not None or hasattr(self.async_schedule_update_ha_state, "assert_called"):
            try:
                self.async_schedule_update_ha_state()
            except RuntimeError:
                pass
