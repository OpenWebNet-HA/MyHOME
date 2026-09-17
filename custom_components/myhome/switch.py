from typing import Any

import voluptuous as vol
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import RegistryEntry
from OWNd.message import (
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_MANUFACTURER,
    LOGGER,
    SERVICE_TURN_ON_TIMED,
    build_timed_turn_on_command,
)
from .data import get_runtime_data
from .discovery import DeviceContext, PlatformDiscovery, default_known_keys
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PLATFORM = Platform.SWITCH
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the switches of a gateway: registry entries first, then myhome.yaml.

    Switches are WHO=1 actuators that *must* be configured (a relay driving a
    socket looks exactly like a light on the bus); discovery of WHO=1 frames is
    the light platform's job, which routes frames for configured switches here.
    """
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    def build(ctx: DeviceContext) -> MyHOMESwitch:
        cfg = ctx.cfg
        name_val = cfg.get(CONF_NAME)
        name = str(name_val) if name_val else f"Switch {ctx.suffix}"
        raw_entity_name = cfg.get(CONF_ENTITY_NAME)
        entity_name = str(raw_entity_name) if raw_entity_name is not None else None
        raw_icon = cfg.get(CONF_ICON)
        icon = str(raw_icon) if raw_icon is not None else None
        raw_icon_on = cfg.get(CONF_ICON_ON)
        icon_on = str(raw_icon_on) if raw_icon_on is not None else None
        device_class = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class") or SwitchDeviceClass.SWITCH
        manufacturer = str(cfg.get(CONF_MANUFACTURER, "BTicino"))
        model = str(cfg.get(CONF_DEVICE_MODEL, "Switch / Relay"))
        return MyHOMESwitch(
            hass=hass,
            name=name,
            entity_name=entity_name,
            icon=icon,
            icon_on=icon_on,
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            interface=ctx.address.interface,
            device_class=str(device_class),
            manufacturer=manufacturer,
            model=model,
            gateway=runtime.gateway,
        )

    def corrupted(entry: RegistryEntry, ctx: DeviceContext) -> bool:
        # Duplicate unique ids like "{mac}-1-1-06" written by earlier versions
        return bool(entry.unique_id and "-1-1-" in entry.unique_id)

    def known_keys(ctx: DeviceContext) -> list[str]:
        # The light platform claims the bare WHERE of a routed switch for it
        # (_ForeignAddresses) and publishes under that spelling too.
        return [*default_known_keys(ctx), ctx.address.where, ctx.address.clean_where]

    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="1", event_type=None, build=build, announce=True,
        reject_registry_entry=corrupted, known_keys=known_keys,
        yaml_device_id=lambda address: address.clean_key,
    ).start(listen=False)

    platform = entity_platform.current_platform.get()
    if platform is not None:
        platform.async_register_entity_service(
            SERVICE_TURN_ON_TIMED,
            {
                vol.Optional("duration"): vol.Coerce(float),
                vol.Optional("hours", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Optional("minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
                vol.Optional("seconds", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=59)),
            },
            "async_turn_on_timed",
        )
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    _configured_switches = runtime.platforms[PLATFORM]
    for _switch in list(_configured_switches.keys()):
        del runtime.platforms[PLATFORM][_switch]

    return True


class MyHOMESwitch(MyHOMEEntity, SwitchEntity):
    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        entity_name: str | None,
        icon: str | None,
        icon_on: str | None,
        device_id: str,
        who: str,
        where: str,
        interface: str | None,
        device_class: str | None,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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
            entity_name=entity_name,
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

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(OWNLightingCommand.status(self._full_where))

    async def async_turn_on_timed(
        self,
        duration: float | None = None,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
    ) -> None:
        """Turn on switch with a hardware-offloaded bus timer."""
        cmd = build_timed_turn_on_command(
            self._full_where,
            duration=duration,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )
        await self._gateway_handler.send(cmd)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        if "timer" in kwargs or "duration" in kwargs:
            raw_dur = kwargs.get("timer", kwargs.get("duration"))
            dur = float(raw_dur) if raw_dur is not None else None
            await self.async_turn_on_timed(
                duration=dur,
                hours=int(kwargs.get("hours", 0)),
                minutes=int(kwargs.get("minutes", 0)),
                seconds=float(kwargs.get("seconds", 0)),
            )
            return
        await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where))

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn the device off."""
        await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))

    @callback
    def handle_event(self, message: OWNLightingEvent) -> None:
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
        self._publish_state()
