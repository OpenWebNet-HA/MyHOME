"""Support for MyHome switches (light modules used for controlled outlets, relays)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from OWNd.message import OWNCommand

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    STATE_UNAVAILABLE,
    EntityCategory,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_component import DATA_INSTANCES
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .command_delivery import async_send_confirmed
from .const import (
    CONF_BUS_INTERFACE,
    CONF_DEVICE_MODEL,
    CONF_MANUFACTURER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SOURCE_TUNER,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    SERVICE_CALIBRATE_COVER,
)
from .cover import is_native_point_cover
from .cover_calibration import (
    CalibrationCutoff,
    CalibrationInterrupted,
    get_calibration_hub,
)
from .cover_scope import CoverScope
from .data import MyHOMEConfigEntry, get_runtime_data
from .discovery import (
    Address,
    actuator_only_on_primary,
    parse_unique_id,
    prune_orphaned_companions,
)
from .expose import is_exposed_on_bus
from .myhome_device import MyHOMEEntity
from .repairs import (
    async_create_cover_cutoff_issue,
    async_delete_cover_cutoff_issue,
    cover_calibration_notification_id,
    resolve_cover_label,
)
from .sound_source import source_address, tuner_source, uses_who22_commands

PARALLEL_UPDATES = 0
PLATFORM = Platform.BUTTON


def _valid_device_address(address: str) -> bool:
    """Accept numeric WHERE and optional bus routing, without rewriting either."""
    return re.fullmatch(r"[0-9]+(?:#4#[0-9]+)?", address) is not None


def _is_non_calibratable_cover(
    hass: HomeAssistant | None,
    config_entry: MyHOMEConfigEntry | None,
    gateway_mac: str,
    device_id: str,
    entity_id: str | None = None,
) -> bool:
    """Return True when a WHO=2 cover is known not to support timed travel-time calibration."""
    address = Address.from_device_id(device_id)
    if CoverScope.of(address.where, address.interface) is not None:
        return True
    return is_native_point_cover(
        hass, config_entry, gateway_mac, device_id, entity_id=entity_id
    )


def _resolve_cover_device_id(
    hass: HomeAssistant,
    entity_id: str,
    gateway_mac: str,
    config_entry: MyHOMEConfigEntry | None,
) -> str | None:
    """Resolve the WHO=2 device_id for a cover entity_id."""
    reg_entry = er.async_get(hass).async_get(entity_id)
    if reg_entry is None or not reg_entry.unique_id:
        return None
    entry_data = getattr(config_entry, "data", None)
    entry_mac = (
        str(entry_data.get(CONF_MAC, gateway_mac))
        if isinstance(entry_data, Mapping)
        else gateway_mac
    )
    _who, dev_id = parse_unique_id(reg_entry.unique_id, gateway_mac, entry_mac)
    return dev_id or None


def _check_cover_ready_for_calibration(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry | None,
    gateway: MyHOMEGatewayHandler,
    entity_id: str,
    device_id: str | None,
    label: str,
) -> str | None:
    """Return an error message if the target cover is unloaded or unavailable, else None."""
    if not bool(getattr(gateway, "available", True)):
        return f"{label}: gateway is unavailable"
    state = hass.states.get(entity_id)
    if state is not None and state.state == STATE_UNAVAILABLE:
        return f"{label}: cover is unavailable"
    runtime = get_runtime_data(config_entry) if isinstance(config_entry, ConfigEntry) else None
    has_source = False
    live_cover: Any = None
    if runtime is not None and "cover" in runtime.entities:
        has_source = True
        live_cover = runtime.entities["cover"].get(device_id) if device_id else None
    else:
        entity_comp = hass.data.get(DATA_INSTANCES, {}).get("cover")
        if entity_comp is not None and hasattr(entity_comp, "get_entity"):
            has_source = True
            live_cover = entity_comp.get_entity(entity_id)
    if has_source:
        if live_cover is None:
            return f"{label}: cover entity is not loaded"
        if not bool(getattr(live_cover, "available", True)):
            return f"{label}: cover is unavailable"
    return None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> bool:
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
    if runtime.is_follower and runtime.primary_gateway_mac:
        prune_orphaned_companions(hass, config_entry.entry_id, mac, runtime.primary_gateway_mac)

    _buttons: list[ButtonEntity] = []
    _configured_buttons = runtime.platforms[PLATFORM]
    gateway = runtime.gateway

    known_button_actuators: set[str] = set()
    known_calibration_covers: set[str] = set()

    def _calibration_button_for_cover(device_id: str | int, name: str | None) -> list[ButtonEntity]:
        """One 'Calibrate travel time' button per timed cover, on the cover's device."""
        dev_str = str(device_id)
        if not dev_str or dev_str in known_calibration_covers:
            return []
        if is_exposed_on_bus(hass, config_entry, 2, dev_str):
            return []
        if _is_non_calibratable_cover(hass, config_entry, gateway.mac, dev_str):
            reg = er.async_get(hass)
            for prefix_mac in {gateway.mac, mac}:
                if old_entity_id := reg.async_get_entity_id(
                    "button", DOMAIN, f"{prefix_mac}-2-{dev_str}-calibrate"
                ):
                    reg.async_remove(old_entity_id)
            return []
        known_calibration_covers.add(dev_str)
        address = Address.from_device_id(dev_str)
        return [
            CalibrateCoverButtonEntity(
                hass=hass,
                platform=PLATFORM,
                device_id=dev_str,
                where=address.where,
                interface=address.interface,
                name=name or f"Cover {address.suffix}",
                gateway=gateway,
                config_entry=config_entry,
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
        options = getattr(config_entry, "options", None)
        if isinstance(options, Mapping) and not (
            runtime.is_follower and 16 not in runtime.delegated_whos and 22 not in runtime.delegated_whos
        ):
            for slot in range(1, CONF_SOURCE_SLOTS + 1):
                if options.get(CONF_SOURCE_TUNER.format(slot)) is not True:
                    continue
                where = source_address(slot)
                src_name = str(options.get(CONF_SOURCE_NAME.format(slot), "") or "").strip()
                tuner_name = src_name or f"Audio Source {slot}"
                for button_cls in (TunerSeekUpButtonEntity, TunerSeekDownButtonEntity):
                    _buttons.append(
                        button_cls(
                            hass=hass,
                            config_entry=config_entry,
                            where=where,
                            name=tuner_name,
                            gateway=gateway,
                        )
                    )

    def _create_buttons_for_device(dev_id: str | int, cfg: dict[str, Any]) -> list[ButtonEntity]:
        """Lock and unlock buttons for one actuator (a yaml entry or an announcement)."""
        who = str(cfg.get(CONF_WHO, "1"))
        if who not in ("1", "2"):
            return []
        address = Address.from_config(str(dev_id), cfg)
        if not address.where or address.where.startswith("#"):
            return []  # groups / general have no lock
        if is_exposed_on_bus(hass, config_entry, who, address.key):
            return []

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

    def _pruned_in_favour_of_primary(dev_id: str | int, cfg: dict[str, Any]) -> bool:
        """A follower's ``myhome.yaml`` actuator that lives on the primary gateway only.

        ``PlatformDiscovery`` does not create such an actuator again (#453), and
        ``prune_orphaned_companions`` has just removed its buttons: creating them here
        would bring them back at every start, on a device that has nothing else
        (live F455 standby next to an F461, 2026-11-14: 20 lock / unlock buttons on
        10 otherwise empty devices).
        """
        primary_mac = runtime.primary_gateway_mac
        if not runtime.is_follower or not primary_mac:
            return False
        who = str(cfg.get(CONF_WHO, "1"))
        address = Address.from_config(str(dev_id), cfg)
        return actuator_only_on_primary(
            er.async_get(hass), f"{gateway.mac}-{who}-{address.clean_key}", mac, primary_mac
        )

    for _button in list(_configured_buttons.keys()):
        if _pruned_in_favour_of_primary(_button, _configured_buttons[_button]):
            continue
        _buttons.extend(_create_buttons_for_device(_button, _configured_buttons[_button]))

    # Discovered actuators are restored by their platforms from the registry.
    # They no longer emit a new-device signal, so rebuild their buttons here too.
    # Use parent actuators rather than stale button entries: a deleted actuator
    # must not be resurrected just because its old buttons remain registered.
    registry = er.async_get(hass)
    registered_entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    # Registry ids may carry the config entry's MAC spelling as well as the gateway's
    mac_prefixes = (f"{gateway.mac}-", f"{config_entry.data.get(CONF_MAC, mac)}-")

    def _registered_id(registered: er.RegistryEntry) -> str:
        if registered.platform == DOMAIN:
            for prefix in mac_prefixes:
                if registered.unique_id.startswith(prefix):
                    return registered.unique_id[len(prefix):]
        return ""

    # Platform setup order is not guaranteed. Determine sensor/switch ownership
    # before restoring any lights, rather than waiting for light.py's cleanup.
    # Include the interface in every key: equal WHEREs on different buses differ.
    non_light_addresses: set[str] = set()
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

    devices: dr.DeviceRegistry | None = None
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
        if is_exposed_on_bus(hass, config_entry, int(who), device_id):
            continue
        default_suffix = f"{where}I{interface}" if interface else where
        if registered.device_id and devices is None:
            devices = dr.async_get(hass)
        device = devices.async_get(registered.device_id) if registered.device_id and devices is not None else None
        _buttons.extend(_create_buttons_for_device(device_id, {
            CONF_WHO: who,
            CONF_WHERE: where,
            CONF_BUS_INTERFACE: interface or None,
            CONF_NAME: (getattr(device, "name_by_user", None) or getattr(device, "name", None) if device else None) or registered.original_name
            or f"{registered.domain.title()} {default_suffix}",
            CONF_MANUFACTURER: getattr(device, "manufacturer", None) or "BTicino",
            CONF_DEVICE_MODEL: getattr(device, "model", None) or "Actuator",
        }))

    if _buttons:
        async_add_entities(_buttons)

    @callback
    def _async_new_device_listener(dev_info: dict[str, Any]) -> None:
        """Add lock/unlock (and, for covers, calibration) buttons for newly discovered or configured devices."""
        new_btns = _create_buttons_for_device(dev_info.get("device_id", ""), dev_info)
        if str(dev_info.get("who", "")) == "2":
            new_btns.extend(_calibration_button_for_cover(dev_info.get("device_id", ""), dev_info.get("name")))
        if new_btns:
            async_add_entities(new_btns)

    if hasattr(config_entry, "async_on_unload"):
        config_entry.async_on_unload(
            async_dispatcher_connect(
                hass,
                f"myhome_new_device_{dr.format_mac(mac)}",
                _async_new_device_listener,
            )
        )

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
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
        hass: HomeAssistant,
        platform: str,
        name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str | None,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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

        self._attr_extra_state_attributes: dict[str, Any] = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_availability_listener()
        self._register_entity_ref("disable")

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref("disable")

    async def async_press(self) -> None:
        """Press the button; raise when the gateway rejects or never receives the frame."""
        cmd = OWNCommand.parse(f"*14*0*{self._full_where}##")
        if cmd is not None:
            # A live F455 1.0.76 answered this frame with NACK (an F461 2.0.11 acknowledged it;
            # why the F455 refused is unknown): the press must fail visibly, not return success.
            await async_send_confirmed(self._gateway_handler, cmd, name=self._display_name)


class EnableCommandButtonEntity(ButtonEntity, MyHOMEEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        platform: str,
        name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str | None,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
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

        self._attr_extra_state_attributes: dict[str, Any] = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_availability_listener()
        self._register_entity_ref("enable")

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref("enable")

    async def async_press(self) -> None:
        """Press the button; raise when the gateway rejects or never receives the frame."""
        cmd = OWNCommand.parse(f"*14*1*{self._full_where}##")
        if cmd is not None:
            # A live F455 1.0.76 answered this frame with NACK (an F461 2.0.11 acknowledged it;
            # why the F455 refused is unknown): the press must fail visibly, not return success.
            await async_send_confirmed(self._gateway_handler, cmd, name=self._display_name)


class CalibrateCoverButtonEntity(ButtonEntity, MyHOMEEntity):
    """Measure a timed cover's up/down travel times on the bus (myhome.calibrate_cover)."""

    def __init__(
        self,
        hass: HomeAssistant,
        platform: str,
        device_id: str,
        where: str,
        interface: str | None,
        name: str,
        gateway: MyHOMEGatewayHandler,
        config_entry: MyHOMEConfigEntry | None = None,
    ) -> None:
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
        self._config_entry: MyHOMEConfigEntry | None = (
            config_entry if config_entry is not None else getattr(gateway, "config_entry", None)
        )

    @property
    def available(self) -> bool:
        """Unavailable if disconnected or if the cover reports native Dimension 10 position."""
        return bool(super().available) and not _is_non_calibratable_cover(
            self.hass or self._hass,
            self._config_entry,
            self._gateway_handler.mac,
            self._device_id,
        )

    async def async_added_to_hass(self) -> None:
        """Register listeners when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_cover_promoted_{self._gateway_handler.mac}",
                self._handle_cover_promoted,
            )
        )

    @callback
    def _handle_cover_promoted(self, device_id: str) -> None:
        """Refresh availability when this cover auto-promotes to Dimension 10."""
        if device_id == self._device_id:
            self.async_write_ha_state()

    async def async_update(self) -> None:
        """Buttons have no state to request."""

    async def async_press(self) -> None:
        registry = er.async_get(self.hass)
        cfg_entry = self._config_entry
        cover_entity_id = registry.async_get_entity_id(
            "cover", DOMAIN, f"{self._gateway_handler.mac}-2-{self._device_id}"
        )
        if not cover_entity_id and cfg_entry is not None:
            entry_data = getattr(cfg_entry, "data", None)
            entry_mac = entry_data.get(CONF_MAC) if isinstance(entry_data, Mapping) else None
            if entry_mac and entry_mac != self._gateway_handler.mac:
                cover_entity_id = registry.async_get_entity_id(
                    "cover", DOMAIN, f"{entry_mac}-2-{self._device_id}"
                )
        if not cover_entity_id:
            LOGGER.warning("No cover entity found for %s; cannot calibrate.", self._device_id)
            return

        entry_id = (
            cfg_entry.entry_id
            if cfg_entry is not None and isinstance(getattr(cfg_entry, "entry_id", None), str)
            else None
        )
        notification_id = cover_calibration_notification_id(entry_id, self._device_id)

        async def _run_calibration() -> None:
            cover_name = resolve_cover_label(
                self.hass, cover_entity_id, self._device_name
            )
            unready_msg = _check_cover_ready_for_calibration(
                self.hass,
                cfg_entry,
                self._gateway_handler,
                cover_entity_id,
                self._device_id,
                cover_name,
            )
            if unready_msg is not None:
                LOGGER.warning(
                    "%s Cover calibration for %s skipped: %s",
                    self._gateway_handler.log_id,
                    cover_entity_id,
                    unready_msg,
                )
                persistent_notification.async_create(
                    self.hass,
                    unready_msg,
                    title=f"Cover calibration failed: {cover_name}",
                    notification_id=notification_id,
                )
                return
            try:
                await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_CALIBRATE_COVER,
                    {"entity_id": cover_entity_id},
                    blocking=True,
                )
            except CalibrationCutoff as err:
                LOGGER.warning(
                    "%s Cover calibration for %s hit actuator cutoff: %s",
                    self._gateway_handler.log_id,
                    cover_entity_id,
                    err,
                )
                cover_name = resolve_cover_label(
                    self.hass, cover_entity_id, self._device_name
                )
                if entry_id is not None:
                    async_create_cover_cutoff_issue(
                        self.hass,
                        entry_id,
                        self._device_id,
                        cover_entity_id,
                        cover_name,
                        err.limit,
                        err.seconds,
                    )
                persistent_notification.async_create(
                    self.hass,
                    f"{err}\n\n[Open guided timing dialog](/config/repairs)",
                    title=f"Cover calibration needs manual timing: {cover_name}",
                    notification_id=notification_id,
                )
            except HomeAssistantError as err:
                LOGGER.warning(
                    "%s Cover calibration for %s could not complete: %s",
                    self._gateway_handler.log_id,
                    cover_entity_id,
                    err,
                )
                cover_name = resolve_cover_label(
                    self.hass, cover_entity_id, self._device_name
                )
                persistent_notification.async_create(
                    self.hass,
                    str(err),
                    title=f"Cover calibration failed: {cover_name}",
                    notification_id=notification_id,
                )
            else:
                if entry_id is not None:
                    async_delete_cover_cutoff_issue(
                        self.hass, entry_id, self._device_id, cover_entity_id
                    )
                else:
                    persistent_notification.async_dismiss(self.hass, notification_id)

        task_name = f"myhome_calibrate_cover_{self._device_id}"
        if isinstance(cfg_entry, ConfigEntry) and hasattr(
            cfg_entry, "async_create_background_task"
        ):
            cfg_entry.async_create_background_task(
                self.hass, _run_calibration(), name=task_name
            )
        else:
            self.hass.async_create_background_task(_run_calibration(), name=task_name)


class CalibrateAllCoversButtonEntity(ButtonEntity):
    """Run myhome.calibrate_cover for every timed cover of this gateway, one after another."""

    _attr_has_entity_name = True
    _attr_translation_key = "calibrate_all_covers"
    _attr_icon = "mdi:window-shutter-settings"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyHOMEConfigEntry,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._gateway_handler = gateway
        self._attr_unique_id = f"{gateway.mac}-calibrate-all-covers"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, gateway.unique_id)})

    async def async_added_to_hass(self) -> None:
        """Register listeners when entity is added to Home Assistant."""
        await super().async_added_to_hass()
        if hasattr(self._gateway_handler, "availability_signal"):
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass,
                    self._gateway_handler.availability_signal,
                    self._handle_availability_update,
                )
            )
        self.async_on_remove(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                self._handle_registry_update,
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"myhome_cover_promoted_{self._gateway_handler.mac}",
                self._handle_cover_promoted,
            )
        )

    @callback
    def _handle_availability_update(self) -> None:
        """Write state when gateway availability changes."""
        self.async_write_ha_state()

    @callback
    def _handle_cover_promoted(self, _device_id: str) -> None:
        """Refresh availability when any cover on this gateway auto-promotes."""
        self.async_write_ha_state()

    @callback
    def _handle_registry_update(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        """Write state when covers are added, removed, or modified."""
        entity_id = event.data.get("entity_id", "")
        if entity_id.startswith("cover."):
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Unavailable if disconnected or if gateway has no covers to calibrate (#525, #565)."""
        return bool(getattr(self._gateway_handler, "available", True)) and bool(self._cover_entity_ids())

    def _cover_entity_ids(self) -> list[str]:
        registry = er.async_get(self.hass)
        entry_mac = str(self._config_entry.data.get(CONF_MAC, self._gateway_handler.mac))
        result: list[str] = []
        for e in er.async_entries_for_config_entry(registry, self._config_entry.entry_id):
            if e.domain != "cover" or e.disabled:
                continue
            who, device_id = parse_unique_id(
                e.unique_id or "", self._gateway_handler.mac, entry_mac
            )
            if who is not None and who != "2":
                continue
            if device_id and is_exposed_on_bus(self.hass, self._config_entry, 2, device_id):
                continue
            if device_id and _is_non_calibratable_cover(
                self.hass,
                self._config_entry,
                self._gateway_handler.mac,
                device_id,
                entity_id=e.entity_id,
            ):
                continue
            result.append(e.entity_id)
        return sorted(result)

    async def async_press(self) -> None:
        if not self.available:
            LOGGER.warning("%s Cannot calibrate covers: gateway unavailable or no covers present.", self._gateway_handler.log_id)
            return
        notification_id = (
            f"{DOMAIN}_calibrate_all_covers_{re.sub(r'[^a-zA-Z0-9_-]', '_', self._gateway_handler.mac)}"
        )
        hub = get_calibration_hub(self._gateway_handler)
        if hub.is_batch_running:
            LOGGER.warning(
                "%s Batch cover calibration is already running; ignoring duplicate press.",
                self._gateway_handler.log_id,
            )
            persistent_notification.async_create(
                self.hass,
                "A batch cover calibration is already in progress on this gateway.",
                title="Cover calibration already running",
                notification_id=notification_id,
            )
            return
        entity_ids = self._cover_entity_ids()
        LOGGER.info("%s Calibrating %d covers sequentially.", self._gateway_handler.log_id, len(entity_ids))
        gen = hub.begin_batch()
        cfg_entry = self._config_entry
        entry_id = (
            cfg_entry.entry_id
            if cfg_entry is not None and isinstance(getattr(cfg_entry, "entry_id", None), str)
            else None
        )

        async def _run_batch() -> None:
            succeeded: list[str] = []
            failed: list[tuple[str, str]] = []
            stopped_remaining: list[str] = []
            any_cutoff = False
            try:
                for idx, entity_id in enumerate(entity_ids):
                    if hub.is_batch_stopped(gen):
                        stopped_remaining = list(entity_ids[idx:])
                        break
                    label = resolve_cover_label(self.hass, entity_id, entity_id)
                    dev_id = _resolve_cover_device_id(
                        self.hass,
                        entity_id,
                        self._gateway_handler.mac,
                        cfg_entry,
                    )
                    unready_msg = _check_cover_ready_for_calibration(
                        self.hass,
                        cfg_entry,
                        self._gateway_handler,
                        entity_id,
                        dev_id,
                        label,
                    )
                    if unready_msg is not None:
                        LOGGER.warning(
                            "%s Batch calibration for %s skipped: %s",
                            self._gateway_handler.log_id,
                            entity_id,
                            unready_msg,
                        )
                        failed.append((label, unready_msg))
                        continue
                    try:
                        await self.hass.services.async_call(
                            DOMAIN,
                            SERVICE_CALIBRATE_COVER,
                            {"entity_id": entity_id},
                            blocking=True,
                        )
                    except CalibrationCutoff as err:
                        any_cutoff = True
                        LOGGER.warning(
                            "%s Batch calibration for %s hit actuator cutoff: %s",
                            self._gateway_handler.log_id,
                            entity_id,
                            err,
                        )
                        if entry_id is not None and dev_id is not None:
                            async_create_cover_cutoff_issue(
                                self.hass,
                                entry_id,
                                dev_id,
                                entity_id,
                                label,
                                err.limit,
                                err.seconds,
                            )
                        failed.append((label, str(err)))
                    except CalibrationInterrupted as err:
                        LOGGER.warning(
                            "%s Batch calibration for %s interrupted: %s",
                            self._gateway_handler.log_id,
                            entity_id,
                            err,
                        )
                        if hub.is_batch_stopped(gen):
                            stopped_remaining = list(entity_ids[idx:])
                            break
                        failed.append((label, str(err)))
                    except HomeAssistantError as err:
                        LOGGER.warning(
                            "%s Batch calibration for %s failed: %s",
                            self._gateway_handler.log_id,
                            entity_id,
                            err,
                        )
                        failed.append((label, str(err)))
                    else:
                        succeeded.append(label)
                        if entry_id is not None and dev_id is not None:
                            async_delete_cover_cutoff_issue(
                                self.hass, entry_id, dev_id, entity_id
                            )
            finally:
                hub.end_batch(gen)

            lines = [
                f"Succeeded: {len(succeeded)}",
                f"Failed: {len(failed)}",
            ]
            if stopped_remaining:
                lines.append(f"Stopped / remaining: {len(stopped_remaining)}")
            if failed:
                lines.append("")
                for cover_label, reason in failed:
                    lines.append(f"- **{cover_label}**: {reason}")
            if any_cutoff:
                lines.append("")
                lines.append("[Open guided timing dialog](/config/repairs)")
            persistent_notification.async_create(
                self.hass,
                "\n".join(lines),
                title="Cover calibration summary",
                notification_id=notification_id,
            )

        task_name = f"myhome_calibrate_all_covers_{self._gateway_handler.mac}"
        cfg_entry.async_create_background_task(
            self.hass, _run_batch(), name=task_name
        )


class _TunerSeekButtonBase(ButtonEntity, MyHOMEEntity):
    """Companion button for FM radio hardware seek up / down on an Audio Source."""

    _translation_key: str
    _unique_suffix: str

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyHOMEConfigEntry,
        where: str,
        name: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=f"{where}#16",
            who="16",
            where=where,
            manufacturer="BTicino",
            model="Audio Source",
            gateway=gateway,
            translation_key=self._translation_key,
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{gateway.mac}-16-{where}#16-{self._unique_suffix}"
        self._poll_on_add = False

    async def async_update(self) -> None:
        """Buttons have no state to request."""

    @property
    def available(self) -> bool:
        """Return True if this tuner's active WHO subsystem is available."""
        who = "22" if uses_who22_commands(self._gateway_handler) else self._who
        return self._gateway_handler.is_who_available(who)


class TunerSeekUpButtonEntity(_TunerSeekButtonBase):
    """Seek forward to the next receivable FM radio station on an F500 / F500N tuner."""

    _translation_key = "tuner_seek_up"
    _unique_suffix = "seek-up"

    async def async_press(self) -> None:
        """Seek forward on the tuner."""
        await tuner_source(self._config_entry, self._where, self.entity_id).async_seek_up()


class TunerSeekDownButtonEntity(_TunerSeekButtonBase):
    """Seek backward to the previous receivable FM radio station on an F500 / F500N tuner."""

    _translation_key = "tuner_seek_down"
    _unique_suffix = "seek-down"

    async def async_press(self) -> None:
        """Seek backward on the tuner."""
        await tuner_source(self._config_entry, self._where, self.entity_id).async_seek_down()
