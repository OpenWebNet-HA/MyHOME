"""Support for MyHome switches (WHO=1 lighting relays and WHO=4 thermo actuators)."""

from __future__ import annotations

import asyncio
import inspect
import time
import types
from collections import defaultdict
from typing import Any, cast

from homeassistant.components.switch import (  # type: ignore[attr-defined, unused-ignore]
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    EntityCategory,
    Platform,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity_registry import RegistryEntry
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData
from OWNd.message import (
    OWNCommand,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)

from .binary_sensor import (
    binary_sensor_canonical_where,
    classify_binary_sensor_registry_entry,
    dry_contact_config_keys,
    resolve_binary_sensor_inverted,
)
from .const import (
    BUS_ROUTING,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_INVERTED_BINARY_SENSORS,
    CONF_MANUFACTURER,
    CONF_TURN_OFF_COMMAND,
    CONF_TURN_ON_COMMAND,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    SIGNAL_BINARY_SENSOR_INVERTED,
    SIGNAL_NEW_BINARY_SENSOR,
    build_timed_turn_on_command,
    normalize_where,
)
from .data import get_runtime_data
from .discovery import (
    Address,
    DeviceContext,
    PlatformDiscovery,
    binary_sensor_only_on_primary,
    default_known_keys,
    devices_with_binary_sensor,
    parse_unique_id,
    prune_entity,
)
from .expose import is_exposed_on_bus
from .gateway import MyHOMEGatewayHandler
from .ignored import IgnoredAddresses
from .myhome_device import MyHOMEEntity
from .services import async_register_turn_on_timed_service
from .thermo_switch import has_thermo_switch_subentry, load_thermo_switch_subentries
from .typing_compat import device_info_to_dict
from .where_grammar import (
    canonical_point_spelling,
    expose_message_where,
    is_central_thermo_where,
    parse_thermo_actuator_where,
    thermo_dim19_status,
    thermo_dim20_actuator_target,
    thermo_dim20_is_active,
    thermo_dim20_level,
    thermo_dim20_whole_zone_target,
    thermo_event_actuator,
    thermo_event_interface,
    thermo_event_zone,
)

PLATFORM = Platform.SWITCH
PARALLEL_UPDATES = 0
# A MyHomeServer1 028206 emulated by the author of #817 reverted an unauthorized #20 write after
# ~0.16 s (bt_thermo_manager; the PR's description is the only record: no committed oracle result
# or trace holds a WHO 4 #20 write on that image). 2.0 s (~12x that figure) absorbs 9600-baud SCS
# bus contention without catching normal user toggles.
THERMO_REVERT_WINDOW_SECONDS = 2.0
# Actuator N of ``Z#N`` (WHO 4 p. 22: "Actuators N of zone Z [Z#N] = [0-99#1-9]").
_ACTUATOR_NUMBERS = frozenset("123456789")
# How long a direct dimension 20 write is waited for: the gateway's ACK or NACK, or nothing.
DIRECT_WRITE_DELIVERY_TIMEOUT = 15.0


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

    who1_discovery = PlatformDiscovery(
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
    )
    startup_entities.extend(who1_discovery.start(listen=False, add=False))

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
            event_type=None,
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

    # ── WHO 25 & WHO 9: per-device 'Invert state' config switches (#826) ──
    configured_bs = runtime.platforms.get("binary_sensor", {})
    ignored_bs = IgnoredAddresses.from_config_entry(config_entry)
    known_invert_switches: set[str] = set()
    invert_entities: list[MyHOMEInvertSwitch] = []

    def _bs_first_cfg(*keys: str) -> dict[str, Any]:
        for k in keys:
            c = configured_bs.get(k)
            if isinstance(c, dict) and c:
                return c
        return {}

    def _create_invert_switch(
        *,
        who: str,
        where: str,
        device_id: str,
        name: str,
        manufacturer: str,
        model: str,
        cfg: dict[str, Any] | None = None,
    ) -> MyHOMEInvertSwitch | None:
        if who not in ("25", "9"):
            return None
        canonical_where = binary_sensor_canonical_where(who, where)
        opt_key = f"{who}-{canonical_where}"
        if opt_key in known_invert_switches:
            return None
        addr = Address.from_device_id(canonical_where)
        who_int = int(who)
        full_where = (
            f"{addr.where}{BUS_ROUTING}{addr.interface}"
            if addr.interface
            else addr.where
        )
        if ignored_bs.is_ignored(who_int, addr.where, addr.interface) or is_exposed_on_bus(
            hass, config_entry, who_int, full_where
        ):
            return None
        known_invert_switches.add(opt_key)
        return MyHOMEInvertSwitch(
            hass=hass,
            config_entry=config_entry,
            name=name,
            device_id=device_id,
            who=who,
            where=canonical_where,
            inverted=resolve_binary_sensor_inverted(config_entry, who, where, cfg),
            manufacturer=manufacturer,
            model=model,
            gateway=runtime.gateway,
        )

    registry, reg_entries = who1_discovery.registry_entries()
    devices_reg: dr.DeviceRegistry | None = None
    for reg_entry in reg_entries:
        if reg_entry.domain != "binary_sensor" or not reg_entry.unique_id:
            continue
        classified = classify_binary_sensor_registry_entry(
            reg_entry, gateway_mac, entry_mac
        )
        if classified is None or classified[0] not in ("25", "9"):
            continue
        bs_who, candidate = classified
        clean_cand = candidate.split("-")[-1]
        if bs_who == "25":
            candidate = Address.from_device_id(candidate).key
            clean_cand = candidate.split("-")[-1]
            bs_cfg = _bs_first_cfg(*dry_contact_config_keys(candidate))
            bs_where = Address.from_config(candidate, bs_cfg).key if bs_cfg else candidate
            norm_w = normalize_where(bs_where) or normalize_where(bs_where.split("-")[-1])
            bs_dev_id = (
                candidate
                if candidate in configured_bs
                else (norm_w or bs_where.split("-")[-1])
            )
            bs_where_addr = Address.from_device_id(bs_where.split("-")[-1])
            bs_norm_base = normalize_where(bs_where_addr.clean_where) or bs_where_addr.clean_where
            default_name = f"Dry Contact {Address(bs_norm_base, bs_where_addr.interface).suffix}"
            default_model = "Dry Contact Interface"
        else:
            bs_cfg = _bs_first_cfg(
                f"9-{candidate}", f"9-{clean_cand}", candidate, clean_cand
            )
            bs_where = str(bs_cfg.get(CONF_WHERE, clean_cand or candidate))
            clean_w = bs_where.split("-")[-1]
            bs_dev_id = clean_w or clean_cand or candidate
            default_name = f"Auxiliary Channel {clean_w}"
            default_model = "Auxiliary Channel"
        reg_dev_id = getattr(reg_entry, "device_id", None)
        device = None
        if isinstance(reg_dev_id, str) and reg_dev_id:
            if devices_reg is None:
                devices_reg = dr.async_get(hass)
            device = devices_reg.async_get(reg_dev_id)
        if device is not None:
            for dom, ident in device.identifiers:
                if dom == DOMAIN and f"-{bs_who}-" in ident:
                    bs_dev_id = ident.split(f"-{bs_who}-", 1)[1]
                    break
        bs_name = str(
            bs_cfg.get(CONF_NAME)
            or (device.name if device is not None and device.name else None)
            or default_name
        )
        bs_mfr = str(
            bs_cfg.get(CONF_MANUFACTURER)
            or (getattr(device, "manufacturer", None) if device is not None else None)
            or "BTicino"
        )
        bs_model = str(
            bs_cfg.get(CONF_DEVICE_MODEL)
            or (getattr(device, "model", None) if device is not None else None)
            or default_model
        )
        inv = _create_invert_switch(
            who=bs_who,
            where=bs_where,
            device_id=bs_dev_id,
            name=bs_name,
            manufacturer=bs_mfr,
            model=bs_model,
            cfg=bs_cfg,
        )
        if inv is not None:
            invert_entities.append(inv)

    # Shared bus (#453): the contacts with a binary sensor on this follower and on its
    # primary, read once for all the myhome.yaml contacts below.
    follower_contacts: tuple[str, set[str]] | None = None
    if registry is not None and runtime.is_follower and runtime.primary_gateway_mac:
        follower_contacts = (
            runtime.primary_gateway_mac,
            devices_with_binary_sensor(hass, registry, config_entry.entry_id, runtime.primary_gateway_mac),
        )

    def _contact_only_on_primary(switch: MyHOMEInvertSwitch) -> bool:
        """Shared bus: this follower's contact lives on its primary gateway (#453)."""
        if follower_contacts is None:
            return False
        primary_mac, with_sensor = follower_contacts
        identifiers = device_info_to_dict(switch.device_info).get("identifiers") or set()
        return any(
            domain == DOMAIN and binary_sensor_only_on_primary(with_sensor, identifier, gateway_mac, primary_mac)
            for domain, identifier in identifiers
        )

    seen_bs_configs: set[int] = set()
    for dev_id, bs_cfg in configured_bs.items():
        if not isinstance(bs_cfg, dict) or id(bs_cfg) in seen_bs_configs:
            continue
        seen_bs_configs.add(id(bs_cfg))
        bs_who = str(bs_cfg.get(CONF_WHO, "25"))
        if bs_who not in ("25", "9"):
            continue
        bs_addr = Address.from_config(str(dev_id), bs_cfg)
        default_model = "Dry Contact" if bs_who == "25" else "Auxiliary Channel"
        default_name = (
            f"Dry Contact {bs_addr.suffix}"
            if bs_who == "25"
            else f"Auxiliary Channel {bs_addr.where}"
        )
        inv = _create_invert_switch(
            who=bs_who,
            where=bs_addr.key,
            device_id=str(dev_id),
            name=str(bs_cfg.get(CONF_NAME) or default_name),
            manufacturer=str(bs_cfg.get(CONF_MANUFACTURER, "BTicino")),
            model=str(bs_cfg.get(CONF_DEVICE_MODEL, default_model)),
            cfg=bs_cfg,
        )
        if inv is None:
            continue
        if _contact_only_on_primary(inv):
            # The binary sensor of this myhome.yaml entry is not created here either
            known_invert_switches.discard(inv._option_key)
            continue
        invert_entities.append(inv)

    if registry is not None:
        active_invert_uids = {s.unique_id for s in invert_entities if s.unique_id}
        for reg_entry in reg_entries:
            uid = reg_entry.unique_id or ""
            if (
                reg_entry.domain == "switch"
                and uid.endswith("-invert")
                and uid not in active_invert_uids
            ):
                prune_entity(hass, registry, reg_entry, config_entry.entry_id)

    startup_entities.extend(invert_entities)

    @callback
    def _async_new_binary_sensor(info: dict[str, Any]) -> None:
        inv = _create_invert_switch(
            who=str(info.get("who", "")),
            where=str(info.get("where", "")),
            device_id=str(info.get("device_id", "")),
            name=str(info.get("name", "")),
            manufacturer=str(info.get("manufacturer", "BTicino")),
            model=str(info.get("model", "Dry Contact Interface")),
        )
        if inv is not None:
            async_add_entities([inv])

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_NEW_BINARY_SENSOR.format(gateway_mac),
            _async_new_binary_sensor,
        )
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

    async_register_turn_on_timed_service(hass)
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
        if self._actuator_locked is not None:
            self._attr_extra_state_attributes["actuator_locked"] = self._actuator_locked

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

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Persist ``actuator_locked`` across restarts saved while ``unavailable``."""
        if self._actuator_locked is not None:
            return RestoredExtraData({"actuator_locked": self._actuator_locked})
        return None

    def _is_own_point_event(self, message: Any) -> bool:
        """Return True if ``message`` is a point-to-point WHO=1 event for this switch's address."""
        if (
            getattr(message, "is_general", False) is True
            or getattr(message, "is_area", False) is True
            or getattr(message, "is_group", False) is True
        ):
            return False
        msg_where = expose_message_where(message)
        if not msg_where or msg_where.startswith("#"):
            return False
        msg_base, msg_sep, msg_iface = msg_where.rpartition("#4#")
        if msg_sep:
            if self._interface is None:
                return False
            if msg_iface.isdigit() and str(self._interface).isdigit():
                if int(msg_iface) != int(str(self._interface)):
                    return False
            elif msg_iface != str(self._interface):
                return False
            target_pt = msg_base
        else:
            if self._interface is not None:
                return False
            target_pt = msg_where
        own_canon = canonical_point_spelling(normalize_where(self._where) or self._where)
        msg_canon = canonical_point_spelling(normalize_where(target_pt) or target_pt)
        return bool(own_canon) and msg_canon == own_canon

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(OWNLightingCommand.status(self._full_where))

    async def async_turn_on_timed(  # pylint: disable=unused-argument
        self,
        duration: float | None = None,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
        brightness: int | None = None,
        brightness_pct: int | None = None,
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

    async def async_turn_on(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn the device on."""
        await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where))

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn the device off."""
        await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))

    @callback
    def handle_event(self, message: OWNLightingEvent) -> None:
        """Handle an event message."""
        if getattr(message, "is_translation", None) is True:
            return
        if getattr(message, "what", None) in (30, 31, "30", "31"):
            return
        if self._actuator_locked is True and (
            getattr(message, "is_general", False) is True
            or getattr(message, "is_area", False) is True
            or getattr(message, "is_group", False) is True
        ):
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
        prev_is_on = self._attr_is_on
        if message.is_on is not None:
            self._attr_is_on = message.is_on
            self._bus_state_complete = True
        if (
            self._actuator_locked is True
            and self._is_own_point_event(message)
            and prev_is_on is not None
            and self._attr_is_on is not None
            and self._attr_is_on != prev_is_on
        ):
            self._clear_actuator_lock()
        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon
        self._publish_state()


class MyHOMEThermoSwitch(MyHOMEEntity, SwitchEntity):
    """Switch entity for a probe-less WHO=4 thermo actuator / towel-warmer relay."""

    _unrecorded_attributes = frozenset({"output_level", "actuator_levels"})

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
        # Actuators the map holds only because an aggregate report (``Z#0``) was shown under
        # number 1: no actuator number is proven by it.
        self._aggregate_actuators: set[str] = set()
        self._actuator_levels: dict[str, int] = {}
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
        """Return WHO=4 thermo actuator addressing and proportional output attributes."""
        attrs: dict[str, Any] = {"zone": int(self._zone)}
        if self._actuator is not None:
            attrs["actuator"] = int(self._actuator)
            if self._actuator in self._actuator_levels:
                attrs["output_level"] = self._actuator_levels[self._actuator]
        elif self._actuator_states:
            attrs["actuators"] = {
                act: "on" if active else "off"
                for act, active in sorted(self._actuator_states.items())
            }
            if self._aggregate_actuators:
                # From an aggregate report (``Z#0``): no actuator number proven; saved so a
                # restart does not turn them into write targets.
                attrs["aggregate_actuators"] = sorted(self._aggregate_actuators)
            if self._actuator_levels:
                attrs["actuator_levels"] = {
                    act: level
                    for act, level in sorted(self._actuator_levels.items())
                }
        if self._interface is not None:
            attrs["Int"] = self._interface
        return attrs

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore relay state, per-actuator map, and custom icons across restarts."""
        await super().async_restore_last_state(last_state)
        raw_actuators = last_state.attributes.get("actuators")
        if self._actuator is None and isinstance(raw_actuators, dict):
            raw_aggregate = last_state.attributes.get("aggregate_actuators")
            aggregate = (
                {str(act) for act in raw_aggregate}
                if isinstance(raw_aggregate, (list, tuple))
                else set()
            )
            for act, val in raw_actuators.items():
                act = str(act)
                # A live report is never overwritten; one shown only from an aggregate report is.
                if act in self._actuator_states and act not in self._aggregate_actuators:
                    continue
                if val in ("on", True):
                    self._actuator_states[act] = True
                    if act in aggregate:
                        self._aggregate_actuators.add(act)
                    else:
                        self._aggregate_actuators.discard(act)
                elif val in ("off", False):
                    self._actuator_states[act] = False
                    if act in aggregate:
                        self._aggregate_actuators.add(act)
                    else:
                        self._aggregate_actuators.discard(act)
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
        # By the form the read takes: ``*#4*Z#N*20##`` is forwarded by more gateways than
        # ``*#4*Z*20##``, which an F455 refuses too (``actuator_read_refused``).
        read_refused = getattr(self._gateway_handler, "actuator_read_refused", None)
        refused = read_refused(self._full_where) if callable(read_refused) else None
        if not isinstance(refused, bool):  # a gateway double without the method
            refused = getattr(self._gateway_handler, "actuator_request_refused", False) is True
        if refused:
            return
        if (
            self._interface is not None
            and getattr(self._gateway_handler, "interface_zone_request_refused", False)
            is True
        ):
            return
        cmd = cast(OWNCommand, OWNCommand.parse(f"*#4*{self._full_where}*20##"))
        await self._gateway_handler.send_status_request(cmd)

    async def async_turn_on_timed(  # pylint: disable=unused-argument
        self,
        duration: float | None = None,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
        brightness: int | None = None,
        brightness_pct: int | None = None,
    ) -> None:
        """Reject WHO=1 hardware timers on WHO=4 thermo actuators."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="thermo_switch_no_timer",
            translation_placeholders={"name": self._device_name},
        )

    def _refuse_interface_write(self) -> None:
        """Refuse the direct ``#20`` write of an actuator behind an ``#4#II`` interface.

        The gateways do not route WHO 4 across an interface (``interface_zone_request_refused``,
        own-firmware-oracle ``findings/thermo-zone-requests.md``): the read form of this address
        is cut at ``#`` and sent to the local bus (on an F450, F454, F459, F460, F461,
        MyHomeServer1 and 3488 image ``*#4*2#1#4#01*20##`` puts the bytes of the zone read
        ``*#4*2*20##`` on the local bus, on the MH202 those of zone 4 actuator 1:
        ``thermo-zone-requests.tsv`` row 22 against rows 37 and 23), so the write may
        switch an actuator of the local bus. The climate platform refuses its writes for the
        same reason (#816); the reads of this switch are already withheld. A configured
        ``turn_on_command`` / ``turn_off_command`` is the owner's own frame and is sent.
        """
        if self._interface is None:
            return
        if getattr(self._gateway_handler, "interface_zone_request_refused", False) is not True:
            return
        raise HomeAssistantError(
            f"{self._device_name} is behind interface {self._interface} and the gateway does not route "
            f"WHO 4 across it: `*#4*{self._full_where}*#20*...` would switch an actuator of the local bus",
            translation_domain=DOMAIN,
            translation_key="thermo_switch_interface_write_refused",
            translation_placeholders={
                "name": self._device_name,
                "interface": self._interface,
                "where": self._full_where,
            },
        )

    def _direct_write_frames(self, value: int) -> list[str]:
        """The Dimension 20 writes that switch this entity ON (``1``) or OFF (``0``).

        An actuator switch writes its own address, ``*#4*Z#N*#20*V##``: the only form of
        this write with evidence. Real MyHomeServer1 plants put it on the bus (71 frames in
        the trace corpus under ``tests/fixtures``, every one ``Z#N``), and an F461 2.0.11 and an F455 1.0.76
        acknowledged ``*#4*3#1*#20*1##`` / ``*#4*3#1*#20*0##`` and the actuator followed
        (live, 2026-11-18: ``*#4*3#1*20##`` read ``0``, ``1``, ``0``).

        A whole-zone switch (WHERE ``Z``) wrote ``*#4*Z*#20*V##``. No source has that
        address for dimension 20: the PDF's WHERE is ``Z#N``, ``Z#0`` or ``0#0``, for the
        request only (WHO 4 p. 22, "1.5.8 Actuator status"), no capture and no committed
        oracle row holds a bare-zone write, and an F455 refuses even the bare-zone read
        (``gateway.ZONE_ACTUATOR_REQUEST_REFUSED``). So the zone is written actuator by
        actuator, each one that has reported (``_actuator_states``: dimension 20 frames,
        restored across restarts), in the proven form. Only while no actuator of the zone
        is known does the bare-zone frame still go out, as before; whether any gateway acts
        on it is not known. An aggregate report (``Z#0``) proves no actuator number; saved
        actuators (restored across restarts) count as reported.
        """
        if self._actuator is None:
            actuators = sorted(
                act
                for act in self._actuator_states
                if act in _ACTUATOR_NUMBERS and act not in self._aggregate_actuators
            )
            if actuators:
                routing = f"{BUS_ROUTING}{self._interface}" if self._interface is not None else ""
                return [f"*#4*{self._zone}#{act}{routing}*#20*{value}##" for act in actuators]
        return [f"*#4*{self._full_where}*#20*{value}##"]

    async def async_turn_on(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn the thermo actuator on via custom trigger or direct Dimension 20 write.

        A configured command the gateway refuses is raised like a built one.
        """
        if self._turn_on_command:
            self._raise_refused(await self._deliver_frames([self._turn_on_command]))
            return
        self._refuse_interface_write()
        self._pending_direct_write = (True, time.monotonic(), False)
        await self._send_direct_writes(1)

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn the thermo actuator off via custom trigger or direct Dimension 20 write."""
        if self._turn_off_command:
            self._raise_refused(await self._deliver_frames([self._turn_off_command]))
            return
        self._refuse_interface_write()
        self._pending_direct_write = (False, time.monotonic(), False)
        await self._send_direct_writes(0)

    async def _deliver_frames(self, frames: list[str]) -> list[str]:
        """Queue every frame, then wait once for all of them; return the frames the gateway refused."""
        written: list[tuple[str, asyncio.Future[Any]]] = []
        for frame in frames:
            future: Any = await self._gateway_handler.send(cast(OWNCommand, OWNCommand.parse(frame)))
            if isinstance(future, asyncio.Future):
                written.append((frame, future))
        if written:
            await asyncio.wait(
                [future for _frame, future in written], timeout=DIRECT_WRITE_DELIVERY_TIMEOUT
            )
        return [
            frame
            for frame, future in written
            if future.done() and future.cancelled() and getattr(future, "_gateway_nack", False) is True
        ]

    def _raise_refused(self, refused: list[str]) -> None:
        """Raise for the frames the gateway refused (NACK); return when there is none."""
        if not refused:
            return
        frames = ", ".join(refused)
        raise HomeAssistantError(
            f"{self._device_name}: the gateway refused {frames}",
            translation_domain=DOMAIN,
            translation_key="thermo_switch_write_refused",
            translation_placeholders={"name": self._device_name, "frames": frames},
        )

    async def _send_direct_writes(self, value: int) -> None:
        """Send every direct dimension 20 write, then raise for the ones the gateway refused (NACK).

        Every write is queued before any answer is waited for, so a later service call of this
        entity is queued behind all of them. The whole call waits once, at most
        ``DIRECT_WRITE_DELIVERY_TIMEOUT``; a write nothing answered in that time is not raised.
        A call whose every write was refused disarms its own revert check, not a newer call's.
        A cancelled call disarms its own revert check as well.
        """
        pending = self._pending_direct_write
        frames = self._direct_write_frames(value)
        try:
            refused = await self._deliver_frames(frames)
        except asyncio.CancelledError:
            # A cancelled call no longer watches for a revert of its write (the frames stay
            # queued and may still be refused).
            if (
                pending is not None
                and self._pending_direct_write is not None
                and self._pending_direct_write[1] == pending[1]
            ):
                self._pending_direct_write = None
            raise
        if (
            refused
            and len(refused) == len(frames)
            and pending is not None
            and self._pending_direct_write is not None
            and self._pending_direct_write[1] == pending[1]
        ):
            self._pending_direct_write = None
        self._raise_refused(refused)

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
    def handle_event(self, message: OWNHeatingEvent | OWNHeatingCommand) -> None:
        """Update switch state from WHO=4 Dimension 20 events/writes or Dimension 19 events."""
        if getattr(message, "is_translation", None) is True:
            return
        if thermo_event_interface(message) != self._interface:
            return
        if thermo_event_zone(message) != self._zone:
            return

        dim20_active = thermo_dim20_is_active(message)
        if dim20_active is not None:
            dim20_level = thermo_dim20_level(message)
            act_id = thermo_event_actuator(message)
            names_actuator = thermo_dim20_actuator_target(message) is not None
            if act_id != "0" and not (act_id.isascii() and act_id.isdigit() and 1 <= int(act_id) <= 9):
                return
            if is_central_thermo_where(message):
                # A central unit's WHERE (``#0#N``, ``#Z``) is neither the whole zone nor one of its actuators.
                return
            whole_zone = thermo_dim20_whole_zone_target(message)
            if not whole_zone and not names_actuator:
                # Neither the zone nor one of its actuators (a 3-token WHERE, an interface out of range).
                return
            # A WHERE that names no actuator (``Z#0``, or the bare zone, for which OWNd reports
            # actuator 1) is the whole zone: it proves no actuator number.
            if whole_zone:
                if self._actuator is None and not dim20_active:
                    # The whole zone is off: the whole-zone switch shows its outputs at 0, as after
                    # the zone's inactive dimension 19 report below. One actuator's switch keeps its
                    # level until its own ``Z#N`` frame (#949).
                    self._actuator_levels = dict.fromkeys(self._actuator_levels, 0)
                if self._actuator is not None:
                    self._actuator_states[self._actuator] = dim20_active
                elif self._actuator_states:
                    self._actuator_states = dict.fromkeys(
                        self._actuator_states, dim20_active
                    )
                else:
                    self._actuator_states["1"] = dim20_active
                    self._aggregate_actuators.add("1")
                new_state = dim20_active
            else:
                if self._actuator is not None and act_id != self._actuator:
                    return
                self._actuator_states[act_id] = dim20_active
                self._aggregate_actuators.discard(act_id)
                if dim20_level is not None:
                    self._actuator_levels[act_id] = dim20_level
                elif act_id in self._actuator_levels:
                    if dim20_active:
                        self._actuator_levels.pop(act_id, None)
                    else:
                        self._actuator_levels[act_id] = 0
                new_state = (
                    dim20_active
                    if self._actuator is not None
                    else any(self._actuator_states.values())
                )
            self._active_source = "actuators"
            self._bus_state_complete = True
        elif (
            isinstance(message, OWNHeatingEvent)
            and self._actuator is None
            and (dim19 := thermo_dim19_status(message)) is not None
        ):
            is_active = dim19[2]
            self._zone_active = is_active
            self._active_source = "zone"
            if not is_active:
                self._actuator_states = dict.fromkeys(self._actuator_states, False)
                self._actuator_levels = dict.fromkeys(self._actuator_levels, 0)
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


class MyHOMEInvertSwitch(MyHOMEEntity, SwitchEntity):
    """Configuration switch to invert a WHO=25 dry contact or WHO=9 auxiliary channel (#826)."""

    _poll_on_add = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = None
    _attr_icon = "mdi:swap-horizontal"

    def __init__(
        self,
        hass: HomeAssistant | None,
        config_entry: ConfigEntry,
        name: str,
        device_id: str,
        who: str,
        where: str,
        inverted: bool,
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
            translation_key="invert_state",
        )
        clean_dev_id = str(self._device_id)
        if clean_dev_id.startswith(f"{self._who}-"):
            clean_dev_id = clean_dev_id[len(f"{self._who}-") :]
        self._attr_unique_id = f"{gateway.mac}-{self._who}-{clean_dev_id}-invert"
        self._config_entry = config_entry
        self._canonical_where = binary_sensor_canonical_where(who, where)
        self._option_key = f"{who}-{self._canonical_where}"
        self._attr_is_on = inverted

    @callback
    def _persist_and_apply(self, inverted: bool) -> None:
        """Persist inverted polarity in config entry options and notify binary sensor."""
        target_hass = self.hass or self._hass
        if target_hass is not None:
            current_opts = dict(self._config_entry.options)
            inv_map = dict(current_opts.get(CONF_INVERTED_BINARY_SENSORS) or {})
            inv_map[self._option_key] = inverted
            current_opts[CONF_INVERTED_BINARY_SENSORS] = inv_map
            target_hass.config_entries.async_update_entry(
                self._config_entry, options=current_opts
            )
            async_dispatcher_send(
                target_hass,
                SIGNAL_BINARY_SENSOR_INVERTED.format(str(self._gateway_handler.mac)),
                self._who,
                self._canonical_where,
                inverted,
            )
        self._attr_is_on = inverted
        self._publish_state()

    async def async_turn_on(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Enable state inversion for the companion binary sensor."""
        self._persist_and_apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Disable state inversion for the companion binary sensor."""
        self._persist_and_apply(False)

    async def async_turn_on_timed(  # pylint: disable=unused-argument
        self,
        duration: float | None = None,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
        brightness: int | None = None,
        brightness_pct: int | None = None,
    ) -> None:
        """Reject WHO=1 hardware timers on binary sensor invert configuration switches."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="invert_switch_no_timer",
            translation_placeholders={"name": self._device_name},
        )
