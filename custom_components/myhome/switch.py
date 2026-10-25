"""Support for MyHome switches (WHO=1 lighting relays and WHO=4 thermo actuators)."""

from __future__ import annotations

import inspect
import time
import types
from collections import defaultdict
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.switch import (  # type: ignore[attr-defined, unused-ignore]
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    Platform,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import RegistryEntry
from OWNd.message import (
    MESSAGE_TYPE_ACTION,
    OWNCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)

from .const import (
    BUS_ROUTING,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_MANUFACTURER,
    CONF_TURN_OFF_COMMAND,
    CONF_TURN_ON_COMMAND,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    SERVICE_TURN_ON_TIMED,
    build_timed_turn_on_command,
)
from .data import get_runtime_data
from .discovery import (
    Address,
    DeviceContext,
    PlatformDiscovery,
    default_known_keys,
    parse_unique_id,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity
from .thermo_switch import has_thermo_switch_subentry, load_thermo_switch_subentries
from .typing_compat import as_any
from .where_grammar import (
    parse_thermo_actuator_where,
    thermo_event_actuator,
    thermo_event_interface,
    thermo_event_zone,
)

PLATFORM = Platform.SWITCH
PARALLEL_UPDATES = 0
# Emulated MyHomeServer1 (028206) bt_thermo_manager reverts unauthorized #20 writes in ~0.16 s;
# 2.0 s (~12x margin) absorbs 9600-baud SCS bus contention without catching normal user toggles.
THERMO_REVERT_WINDOW_SECONDS = 2.0


def _supports_config_subentry_id(cb: Any) -> bool:
    """Return True if ``async_add_entities`` accepts ``config_subentry_id``."""
    target = inspect.unwrap(getattr(cb, "func", cb))
    code = getattr(target, "__code__", None)
    if not isinstance(code, types.CodeType):
        return False
    n_args = code.co_argcount + code.co_kwonlyargcount
    return "config_subentry_id" in code.co_varnames[:n_args]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the switches of a gateway: WHO=1 relays and WHO=4 thermo actuators.

    WHO=1 switches are lighting actuators configured in ``myhome.yaml`` (discovery
    and routing of WHO=1 frames stay with the light platform). WHO=4 thermo
    switches represent probe-less thermo actuator / towel-warmer relays (e.g.
    BTicino ``F430/2`` or ``F430/4``) configured via UI subentries or ``myhome.yaml``.
    """
    runtime = get_runtime_data(config_entry)
    if runtime is None:
        return True
    load_thermo_switch_subentries(config_entry, runtime.platforms)
    if PLATFORM not in runtime.platforms:
        return True

    entry_mac = str(config_entry.data.get(CONF_MAC, runtime.mac))
    gateway_mac = str(runtime.gateway.mac)
    startup_entities: list[Entity] = []

    def who1_registry_address(entry: RegistryEntry) -> Address | None:
        who, device_id = parse_unique_id(entry.unique_id or "", gateway_mac, entry_mac)
        if who not in (None, "1"):
            return None
        return Address.from_device_id(device_id)

    def build_who1(ctx: DeviceContext) -> MyHOMESwitch:
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

    startup_entities.extend(
        PlatformDiscovery(
            hass,
            config_entry,
            async_add_entities,
            platform=PLATFORM,
            who="1",
            event_type=None,
            build=build_who1,
            announce=True,
            accept=lambda ctx: str(ctx.cfg.get(CONF_WHO) or "1") == "1",
            registry_address=who1_registry_address,
            reject_registry_entry=corrupted,
            known_keys=known_keys,
            yaml_device_id=lambda address: address.clean_key,
        ).start(listen=False, add=False)
    )

    # ── WHO 4: probe-less thermo actuator / towel-warmer switches ───────
    def who4_registry_address(entry: RegistryEntry) -> Address | None:
        who, device_id = parse_unique_id(entry.unique_id or "", gateway_mac, entry_mac)
        if who != "4":
            return None
        base = Address.from_device_id(device_id)
        return Address(f"4-{base.clean_where}", base.interface)

    def stale_thermo_switch(entry: RegistryEntry, ctx: DeviceContext) -> bool:
        if str(ctx.cfg.get(CONF_WHO) or "") == "4":
            return False
        # Preserve registry customizations if a subentry at this address is temporarily invalid.
        return not has_thermo_switch_subentry(
            config_entry,
            ctx.address.clean_where,
            ctx.address.interface,
            subentry_id=getattr(entry, "config_subentry_id", None),
        )

    def build_thermo(ctx: DeviceContext) -> MyHOMEThermoSwitch | None:
        cfg = ctx.cfg
        try:
            norm_where, zone, actuator, norm_if = parse_thermo_actuator_where(
                ctx.address.clean_where, ctx.address.interface
            )
        except ValueError:
            return None
        suffix = f"{norm_where}I{norm_if}" if norm_if else norm_where
        name_val = cfg.get(CONF_NAME)
        name = str(name_val) if name_val else f"Thermo Actuator {suffix}"
        raw_entity_name = cfg.get(CONF_ENTITY_NAME)
        entity_name = str(raw_entity_name) if raw_entity_name is not None else None
        raw_icon = cfg.get(CONF_ICON)
        icon = str(raw_icon) if raw_icon is not None else None
        raw_icon_on = cfg.get(CONF_ICON_ON)
        icon_on = str(raw_icon_on) if raw_icon_on is not None else None
        device_class = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class") or SwitchDeviceClass.SWITCH
        manufacturer = str(cfg.get(CONF_MANUFACTURER, "BTicino"))
        model = str(cfg.get(CONF_DEVICE_MODEL, "Thermo Actuator"))
        device_id = f"{norm_where}{BUS_ROUTING}{norm_if}" if norm_if else norm_where
        raw_sub_id = cfg.get("subentry_id")
        subentry_id = str(raw_sub_id) if raw_sub_id else None
        return MyHOMEThermoSwitch(
            hass=hass,
            name=name,
            entity_name=entity_name,
            icon=icon,
            icon_on=icon_on,
            device_id=device_id,
            where=norm_where,
            zone=zone,
            actuator=actuator,
            interface=norm_if,
            device_class=str(device_class),
            manufacturer=manufacturer,
            model=model,
            turn_on_command=cfg.get(CONF_TURN_ON_COMMAND),
            turn_off_command=cfg.get(CONF_TURN_OFF_COMMAND),
            gateway=runtime.gateway,
            subentry_id=subentry_id,
        )

    def thermo_known_keys(ctx: DeviceContext) -> list[str]:
        _nw, zone, _act, iface = parse_thermo_actuator_where(
            ctx.address.clean_where, ctx.address.interface
        )
        zone_key = f"{zone}{BUS_ROUTING}{iface}" if iface else zone
        return [
            k
            for k in (
                ctx.key,
                f"4-{ctx.address.clean_key}",
                ctx.config_id or "",
                f"thermo_switch:{zone_key}",
            )
            if k
        ]

    def thermo_bus_address(message: Any) -> Address | None:
        zone = thermo_event_zone(message)
        if zone is None:
            return None
        return Address(f"4-{zone}", thermo_event_interface(message))

    def thermo_route_keys(message: Any, address: Address | None) -> list[str]:
        zone = thermo_event_zone(message)
        if zone is None:
            return []
        iface = thermo_event_interface(message)
        zone_key = f"{zone}{BUS_ROUTING}{iface}" if iface else zone
        return [f"thermo_switch:{zone_key}"]

    startup_entities.extend(
        PlatformDiscovery(
            hass,
            config_entry,
            async_add_entities,
            platform=PLATFORM,
            who="4",
            event_type=OWNHeatingEvent,
            build=build_thermo,
            announce=False,
            accept=lambda ctx: ctx.source != "bus" and str(ctx.cfg.get(CONF_WHO) or "") == "4",
            registry_address=who4_registry_address,
            reject_registry_entry=stale_thermo_switch,
            known_keys=thermo_known_keys,
            address=thermo_bus_address,
            route_keys=thermo_route_keys,
            yaml_device_id=lambda address: f"4-{address.clean_key}",
        ).start(listen=True, add=False)
    )

    if startup_entities:
        if _supports_config_subentry_id(async_add_entities):
            by_subentry: defaultdict[str | None, list[Entity]] = defaultdict(list)
            for ent in startup_entities:
                by_subentry[getattr(ent, "_subentry_id", None)].append(ent)
            add_cb = cast(Any, async_add_entities)
            for sub_id, group in by_subentry.items():
                if sub_id is None:
                    async_add_entities(group)
                else:
                    add_cb(group, config_subentry_id=sub_id)
        else:
            async_add_entities(startup_entities)

    platform = entity_platform.current_platform.get()
    if platform is not None:
        platform.async_register_entity_service(
            SERVICE_TURN_ON_TIMED,
            as_any({
                vol.Optional("duration"): vol.Coerce(float),
                vol.Optional("hours", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=255)),
                vol.Optional("minutes", default=0): vol.All(vol.Coerce(int), vol.Range(min=0, max=59)),
                vol.Optional("seconds", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=59)),
            }),
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
        if message.is_on is not None:
            self._attr_is_on = message.is_on
        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon
        self._publish_state()


class MyHOMEThermoSwitch(MyHOMEEntity, SwitchEntity):
    """Switch entity for a probe-less WHO=4 thermo actuator / towel-warmer relay."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        entity_name: str | None,
        icon: str | None,
        icon_on: str | None,
        device_id: str,
        where: str,
        zone: str,
        actuator: str | None,
        interface: str | None,
        device_class: str | None,
        manufacturer: str,
        model: str,
        turn_on_command: str | None,
        turn_off_command: str | None,
        gateway: MyHOMEGatewayHandler,
        subentry_id: str | None = None,
    ) -> None:
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who="4",
            where=where,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            entity_name=entity_name,
        )
        self._zone = zone
        self._actuator = actuator
        self._interface = interface
        self._subentry_id = subentry_id
        self._full_where = (
            f"{self._where}{BUS_ROUTING}{self._interface}"
            if self._interface is not None
            else self._where
        )
        self._turn_on_command = turn_on_command
        self._turn_off_command = turn_off_command
        self._actuator_states: dict[str, bool] = {}
        self._zone_active = False
        self._active_source = "actuators"
        self._pending_direct_write: tuple[bool, float, bool] | None = None
        self._revert_warned = False

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
        interface_refused = self._interface is not None and (
            getattr(gateway, "interface_zone_request_refused", False) is True
        )
        self._poll_on_add = not interface_refused and not callable(
            getattr(gateway, "watch_zone_actuators", None)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return WHO=4 thermo actuator addressing attributes."""
        attrs: dict[str, Any] = {"zone": int(self._zone)}
        if self._actuator is not None:
            attrs["actuator"] = int(self._actuator)
        elif self._actuator_states:
            attrs["actuators"] = {
                act: "on" if active else "off"
                for act, active in sorted(self._actuator_states.items())
            }
        if self._interface is not None:
            attrs["Int"] = self._interface
        return attrs

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore relay state, per-actuator map, and custom icons across restarts."""
        await super().async_restore_last_state(last_state)
        raw_actuators = last_state.attributes.get("actuators")
        if self._actuator is None and isinstance(raw_actuators, dict) and not self._actuator_states:
            for act, val in raw_actuators.items():
                if val in ("on", True):
                    self._actuator_states[str(act)] = True
                elif val in ("off", False):
                    self._actuator_states[str(act)] = False
        if (
            self._attr_is_on is not None
            and self._off_icon is not None
            and self._on_icon is not None
        ):
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon

    async def async_added_to_hass(self) -> None:
        """Subscribe to gateway Dimension 20 polling and reconnect resyncs."""
        await super().async_added_to_hass()
        watch = getattr(self._gateway_handler, "watch_zone_actuators", None)
        interface_refused = self._interface is not None and (
            getattr(self._gateway_handler, "interface_zone_request_refused", False)
            is True
        )
        if callable(watch) and not interface_refused:
            self.async_on_remove(watch(self._full_where))

    async def async_update(self) -> None:
        """Request Dimension 20 actuator status from gateways that forward it."""
        if getattr(self._gateway_handler, "actuator_request_refused", False) is True:
            return
        if (
            self._interface is not None
            and getattr(self._gateway_handler, "interface_zone_request_refused", False)
            is True
        ):
            return
        cmd = cast(OWNCommand, OWNCommand.parse(f"*#4*{self._full_where}*20##"))
        await self._gateway_handler.send_status_request(cmd)

    async def async_turn_on_timed(
        self,
        duration: float | None = None,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
    ) -> None:
        """Reject WHO=1 hardware timers on WHO=4 thermo actuators."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="thermo_switch_no_timer",
            translation_placeholders={"name": self._device_name},
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the thermo actuator on via custom trigger or direct Dimension 20 write."""
        if "timer" in kwargs or "duration" in kwargs:
            await self.async_turn_on_timed()
        if self._turn_on_command:
            cmd = cast(OWNCommand, OWNCommand.parse(self._turn_on_command))
            await self._gateway_handler.send(cmd)
            return
        self._pending_direct_write = (True, time.monotonic(), False)
        cmd = cast(OWNCommand, OWNCommand.parse(f"*#4*{self._full_where}*#20*1##"))
        await self._gateway_handler.send(cmd)

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn the thermo actuator off via custom trigger or direct Dimension 20 write."""
        if self._turn_off_command:
            cmd = cast(OWNCommand, OWNCommand.parse(self._turn_off_command))
            await self._gateway_handler.send(cmd)
            return
        self._pending_direct_write = (False, time.monotonic(), False)
        cmd = cast(OWNCommand, OWNCommand.parse(f"*#4*{self._full_where}*#20*0##"))
        await self._gateway_handler.send(cmd)

    def _check_direct_write_revert(self, new_state: bool) -> None:
        """Detect MyHomeServer1 bt_thermo_manager immediate revert after a #20 write."""
        if self._pending_direct_write is None:
            return
        target, sent_at, confirmed = self._pending_direct_write
        elapsed = time.monotonic() - sent_at
        if elapsed > THERMO_REVERT_WINDOW_SECONDS:
            self._pending_direct_write = None
            return
        if new_state == target:
            self._pending_direct_write = (target, sent_at, True)
            return
        if confirmed:
            self._pending_direct_write = None
            if not self._revert_warned:
                self._revert_warned = True
                LOGGER.warning(
                    "%s Thermo actuator %s (%s) reverted to %s %.2fs after direct WHO=4 "
                    "Dimension 20 write. On MyHomeServer1 (Home + Project) 'Towel Warmer' "
                    "(BNTR) objects, bt_thermo_manager locks the relay state unless "
                    "triggered via a Home + Project Scenario/Automation (e.g. CEN+ "
                    "'*25*21#1*21##' / '*25*21#2*21##'). Configure 'turn_on_command' and "
                    "'turn_off_command' on this thermo switch.",
                    self._gateway_handler.log_id,
                    self._full_where,
                    self._device_name,
                    "ON" if new_state else "OFF",
                    elapsed,
                )

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Update switch state from WHO=4 Dimension 20 / Dimension 19 events."""
        if getattr(message, "is_translation", None) is True:
            return
        if thermo_event_interface(message) != self._interface:
            return
        if getattr(message, "message_type", None) != MESSAGE_TYPE_ACTION:
            return
        if thermo_event_zone(message) != self._zone:
            return

        dimension = getattr(message, "dimension", None)
        if dimension == 20:
            act_id = thermo_event_actuator(message)
            if self._actuator is not None and act_id != self._actuator:
                return
            is_active = bool(message.is_active())
            self._actuator_states[act_id] = is_active
            self._active_source = "actuators"
            new_state = (
                is_active
                if self._actuator is not None
                else any(self._actuator_states.values())
            )
            self._bus_state_complete = True
        elif dimension == 19 and self._actuator is None:
            is_active = bool(
                message.is_active()
                or message.is_heating()
                or message.is_cooling()
                or getattr(message, "cooling_fan_on", None)
                or getattr(message, "fan_on", None)
            )
            self._zone_active = is_active
            self._active_source = "zone"
            if not is_active:
                self._actuator_states = dict.fromkeys(self._actuator_states, False)
            new_state = self._zone_active
            self._bus_state_complete = True
        else:
            return

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._check_direct_write_revert(new_state)
        self._attr_is_on = new_state
        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon
        self._publish_state()
