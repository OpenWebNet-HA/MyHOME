"""Support for MyHome switches (light modules used for controlled outlets, relays)."""
import voluptuous as vol
from homeassistant.components.switch import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.core import callback
from homeassistant.helpers import entity_platform
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
from .discovery import DeviceContext, PlatformDiscovery
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, config_entry, async_add_entities):
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
        return MyHOMESwitch(
            hass=hass,
            name=cfg.get(CONF_NAME) or f"Switch {ctx.suffix}",
            entity_name=cfg.get(CONF_ENTITY_NAME),
            icon=cfg.get(CONF_ICON),
            icon_on=cfg.get(CONF_ICON_ON),
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            interface=ctx.address.interface,
            device_class=cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class") or SwitchDeviceClass.SWITCH,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Switch / Relay"),
            gateway=runtime.gateway,
        )

    def corrupted(entry, ctx: DeviceContext) -> bool:
        # Duplicate unique ids like "{mac}-1-1-06" written by earlier versions
        return "-1-1-" in entry.unique_id

    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="1", event_type=None, build=build, announce=True,
        reject_registry_entry=corrupted,
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


async def async_unload_entry(hass, config_entry):
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

    async def async_update(self):
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
    ):
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

    async def async_turn_on(self, **kwargs):
        """Turn the device on."""
        if "timer" in kwargs or "duration" in kwargs:
            dur = kwargs.get("timer", kwargs.get("duration"))
            return await self.async_turn_on_timed(
                duration=dur,
                hours=kwargs.get("hours", 0),
                minutes=kwargs.get("minutes", 0),
                seconds=kwargs.get("seconds", 0),
            )
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
        self._publish_state()
