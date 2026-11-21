"""Number entities for MyHOME FM radio tuners and native WHO=2 covers.

Exposes:
- ``number.<source>_frequency`` (87.5–108.0 MHz, step 0.05 MHz, box mode) on
  each declared F500 / F500N tuner's Audio Source device so users can type or
  step to any FM frequency directly from the Home Assistant UI.
- ``number.<cover>_minimum_position_step`` (1–5 %, step 1 %, box mode,
  ``EntityCategory.CONFIG``) on each point-to-point native WHO=2 cover device
  so users can tune ``min_position_delta`` directly from the Home Assistant UI
  without ``myhome.yaml``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
    PERCENTAGE,
    EntityCategory,
    Platform,
    UnitOfFrequency,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_COVER_MIN_POSITION_DELTAS,
    CONF_DEVICE_MODEL,
    CONF_MANUFACTURER,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SOURCE_TUNER,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    MAX_MIN_POSITION_DELTA,
    MIN_MIN_POSITION_DELTA,
    SIGNAL_COVER_MIN_POSITION_DELTA,
)
from .cover import is_native_point_cover, resolve_cover_min_position_delta
from .data import MyHOMEConfigEntry, get_runtime_data
from .discovery import (
    Address,
    actuator_only_on_primary,
    config_for,
    parse_unique_id,
    prune_orphaned_companions,
)
from .expose import is_exposed_on_bus
from .ignored import IgnoredAddresses
from .myhome_device import MyHOMEEntity
from .sound_source import source_address, tuner_source, uses_who22_commands
from .tuner_decode import FM_MAX_KHZ, FM_MIN_KHZ

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

PARALLEL_UPDATES = 0
PLATFORM = Platform.NUMBER


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> bool:
    """Set up FM frequency and native-cover minimum position step number entities."""
    runtime = get_runtime_data(config_entry)
    if runtime is None:
        return True
    mac = runtime.mac
    if runtime.is_follower and runtime.primary_gateway_mac:
        prune_orphaned_companions(
            hass, config_entry.entry_id, mac, runtime.primary_gateway_mac
        )

    entities: list[NumberEntity] = []
    options = getattr(config_entry, "options", None)
    if isinstance(options, Mapping) and not (
        runtime.is_follower
        and 16 not in runtime.delegated_whos
        and 22 not in runtime.delegated_whos
    ):
        for slot in range(1, CONF_SOURCE_SLOTS + 1):
            if options.get(CONF_SOURCE_TUNER.format(slot)) is not True:
                continue
            where = source_address(slot)
            name = str(options.get(CONF_SOURCE_NAME.format(slot), "") or "").strip()
            entities.append(
                MyHOMETunerFrequencyNumber(
                    hass=hass,
                    config_entry=config_entry,
                    where=where,
                    name=name or f"Audio Source {slot}",
                    gateway=runtime.gateway,
                )
            )

    if not (runtime.is_follower and 2 not in runtime.delegated_whos):
        gateway = runtime.gateway
        entry_mac = str(config_entry.data.get(CONF_MAC) or mac)
        ignored_covers = IgnoredAddresses.from_config_entry(config_entry)
        known_cover_delta_numbers: set[str] = set()

        def _registry_cover_meta(
            dev_str: str,
        ) -> tuple[str | None, str | None, str | None]:
            try:
                reg = er.async_get(hass)
                dev_reg = dr.async_get(hass)
            except Exception:  # pragma: no cover - registry unavailable in minimal harnesses
                return None, None, None
            reg_entry = None
            for prefix_mac in {gateway.mac, mac, entry_mac}:
                eid = reg.async_get_entity_id(
                    "cover", DOMAIN, f"{prefix_mac}-2-{dev_str}"
                )
                if eid and (candidate := reg.async_get(eid)) is not None:
                    reg_entry = candidate
                    break
            if reg_entry is None:
                return None, None, None
            device = dev_reg.async_get(reg_entry.device_id) if reg_entry.device_id else None
            name = (
                (device.name_by_user or device.name)
                if device is not None
                else reg_entry.name
            )
            mfr = getattr(device, "manufacturer", None) if device is not None else None
            model = getattr(device, "model", None) if device is not None else None
            return name, mfr, model

        def _cover_delta_number_for(
            device_id: str | int,
            name: str | None,
            *,
            cfg: Mapping[str, Any] | None = None,
            manufacturer: str | None = None,
            model: str | None = None,
        ) -> MyHOMECoverMinPositionDeltaNumber | None:
            raw_str = str(device_id)
            dev_str = raw_str[2:] if raw_str.startswith("2-") else raw_str
            if not dev_str or dev_str in known_cover_delta_numbers:
                return None
            address = Address.from_device_id(dev_str)
            if ignored_covers.is_ignored(2, address.where, address.interface):
                return None
            if is_exposed_on_bus(hass, config_entry, 2, dev_str):
                return None
            if not is_native_point_cover(hass, config_entry, gateway.mac, dev_str):
                reg = er.async_get(hass)
                for prefix_mac in {gateway.mac, mac, entry_mac}:
                    if old_entity_id := reg.async_get_entity_id(
                        "number", DOMAIN, f"{prefix_mac}-2-{dev_str}-min_position_delta"
                    ):
                        reg.async_remove(old_entity_id)
                return None
            if (
                runtime.is_follower
                and runtime.primary_gateway_mac
                and actuator_only_on_primary(
                    er.async_get(hass),
                    f"{gateway.mac}-2-{dev_str}",
                    mac,
                    runtime.primary_gateway_mac,
                )
            ):
                return None
            cover_cfg: Mapping[str, Any]
            if cfg is not None:
                cover_cfg = cfg
            else:
                cover_platforms = runtime.platforms.get("cover", {})
                cover_cfg = config_for(
                    cover_platforms,
                    address,
                    dev_str,
                    f"2-{address.key}",
                    f"2-{address.clean_key}",
                )
            known_cover_delta_numbers.add(dev_str)
            return MyHOMECoverMinPositionDeltaNumber(
                hass=hass,
                config_entry=config_entry,
                device_id=dev_str,
                where=address.where,
                interface=address.interface,
                name=name or str(cover_cfg.get(CONF_NAME) or f"Cover {address.suffix}"),
                manufacturer=manufacturer
                or str(cover_cfg.get(CONF_MANUFACTURER) or "BTicino"),
                model=model
                or str(cover_cfg.get(CONF_DEVICE_MODEL) or "Shutter / Cover"),
                gateway=gateway,
                cfg=cover_cfg,
            )

        # 1. Existing WHO=2 cover entries in the entity registry
        try:
            registry = er.async_get(hass)
            device_registry = dr.async_get(hass)
            for reg_entry in er.async_entries_for_config_entry(
                registry, config_entry.entry_id
            ):
                if reg_entry.domain != "cover" or not reg_entry.unique_id:
                    continue
                who, device_id = parse_unique_id(
                    reg_entry.unique_id, gateway.mac, entry_mac
                )
                if who == "2" and device_id:
                    device = (
                        device_registry.async_get(reg_entry.device_id)
                        if reg_entry.device_id
                        else None
                    )
                    cover_name = (
                        (device.name_by_user or device.name) if device else None
                    )
                    num = _cover_delta_number_for(
                        device_id,
                        cover_name or reg_entry.name,
                        manufacturer=(
                            getattr(device, "manufacturer", None) if device else None
                        ),
                        model=getattr(device, "model", None) if device else None,
                    )
                    if num is not None:
                        entities.append(num)
        except Exception as err:  # pragma: no cover - registry unavailable in some test harnesses
            LOGGER.debug(
                "Could not enumerate covers for min_position_delta numbers: %s", err
            )

        # 2. Configured WHO=2 covers (YAML / migrated snapshot) not yet in the registry
        seen_cfgs: set[int] = set()
        for dev_id, cover_cfg in runtime.platforms.get("cover", {}).items():
            if not isinstance(cover_cfg, Mapping) or id(cover_cfg) in seen_cfgs:
                continue
            seen_cfgs.add(id(cover_cfg))
            if str(cover_cfg.get(CONF_WHO, "2")) != "2":
                continue
            address = Address.from_config(str(dev_id), dict(cover_cfg))
            num = _cover_delta_number_for(
                address.clean_key,
                cover_cfg.get(CONF_NAME),
                cfg=cover_cfg,
                manufacturer=cover_cfg.get(CONF_MANUFACTURER),
                model=cover_cfg.get(CONF_DEVICE_MODEL),
            )
            if num is not None:
                entities.append(num)

        @callback
        def _async_new_device_listener(dev_info: dict[str, Any]) -> None:
            if str(dev_info.get("who", "")) != "2":
                return
            raw_id = str(dev_info.get("device_id", ""))
            dev_str = raw_id[2:] if raw_id.startswith("2-") else raw_id
            name, mfr, model = _registry_cover_meta(dev_str)
            num = _cover_delta_number_for(
                dev_str,
                name or dev_info.get("name"),
                manufacturer=mfr,
                model=model,
            )
            if num is not None:
                async_add_entities([num])

        @callback
        def _async_cover_promoted_listener(device_id: str) -> None:
            raw_id = str(device_id)
            dev_str = raw_id[2:] if raw_id.startswith("2-") else raw_id
            name, mfr, model = _registry_cover_meta(dev_str)
            num = _cover_delta_number_for(
                dev_str,
                name,
                manufacturer=mfr,
                model=model,
            )
            if num is not None:
                async_add_entities([num])

        if hasattr(config_entry, "async_on_unload"):
            config_entry.async_on_unload(
                async_dispatcher_connect(
                    hass,
                    f"myhome_new_device_{mac}",
                    _async_new_device_listener,
                )
            )
            config_entry.async_on_unload(
                async_dispatcher_connect(
                    hass,
                    f"myhome_cover_promoted_{gateway.mac}",
                    _async_cover_promoted_listener,
                )
            )

    if entities:
        async_add_entities(entities)
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    """Unload the MyHOME number platform."""
    return True


class MyHOMECoverMinPositionDeltaNumber(NumberEntity, MyHOMEEntity):
    """Configuration number (1–5 %) for a native WHO=2 cover's minimum position step."""

    _poll_on_add = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = None
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = float(MIN_MIN_POSITION_DELTA)
    _attr_native_max_value = float(MAX_MIN_POSITION_DELTA)
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:arrow-expand-vertical"

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyHOMEConfigEntry,
        device_id: str,
        where: str,
        interface: str | None,
        name: str,
        gateway: MyHOMEGatewayHandler,
        manufacturer: str | None = None,
        model: str | None = None,
        cfg: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize the native cover minimum position step number entity."""
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who="2",
            where=where,
            manufacturer=manufacturer or "BTicino",
            model=model or "Shutter / Cover",
            gateway=gateway,
            translation_key="min_position_delta",
        )
        self._config_entry = config_entry
        self._interface = interface
        self._full_where = (
            f"{where}#4#{interface}" if interface is not None else where
        )
        self._attr_unique_id = f"{gateway.mac}-2-{device_id}-min_position_delta"
        self._attr_native_value: float | None = float(
            resolve_cover_min_position_delta(config_entry, device_id, cfg)
        )

    @property
    def available(self) -> bool:
        """Return True while the gateway WHO=2 subsystem is available and the cover is native."""
        return bool(super().available) and is_native_point_cover(
            self.hass or self._hass,
            self._config_entry,
            self._gateway_handler.mac,
            self._device_id,
        )

    async def async_update(self) -> None:
        """Configuration option is managed locally; no bus polling required."""

    async def async_set_native_value(self, value: float) -> None:
        """Persist the cover's minimum position step (%) and notify the cover entity."""
        delta = max(
            MIN_MIN_POSITION_DELTA, min(MAX_MIN_POSITION_DELTA, int(round(value)))
        )
        target_hass = self.hass or self._hass
        if target_hass is not None and hasattr(target_hass, "config_entries"):
            current_opts = dict(getattr(self._config_entry, "options", None) or {})
            deltas = dict(current_opts.get(CONF_COVER_MIN_POSITION_DELTAS) or {})
            deltas[str(self._device_id)] = delta
            current_opts[CONF_COVER_MIN_POSITION_DELTAS] = deltas
            target_hass.config_entries.async_update_entry(
                self._config_entry, options=current_opts
            )
            async_dispatcher_send(
                target_hass,
                SIGNAL_COVER_MIN_POSITION_DELTA.format(str(self._gateway_handler.mac)),
                str(self._device_id),
                delta,
            )
        self._attr_native_value = float(delta)
        self._publish_state()


class MyHOMETunerFrequencyNumber(NumberEntity, MyHOMEEntity):
    """Direct FM frequency control (87.50–108.00 MHz) for a declared F500 / F500N tuner."""

    _attr_device_class = NumberDeviceClass.FREQUENCY
    _attr_native_unit_of_measurement = UnitOfFrequency.MEGAHERTZ
    _attr_native_min_value = 87.5
    _attr_native_max_value = 108.0
    _attr_native_step = 0.05
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: MyHOMEConfigEntry,
        where: str,
        name: str,
        gateway: MyHOMEGatewayHandler,
    ) -> None:
        """Initialise the tuner FM frequency number entity."""
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
            translation_key="tuner_frequency",
        )
        self._config_entry = config_entry
        self._attr_unique_id = f"{gateway.mac}-16-{where}#16-frequency"
        self._attr_native_value: float | None = None
        self._poll_on_add = False

    @property
    def available(self) -> bool:
        """Return True if this tuner's active WHO subsystem is available."""
        who = "22" if uses_who22_commands(self._gateway_handler) else self._who
        return self._gateway_handler.is_who_available(who)

    async def async_added_to_hass(self) -> None:
        """Restore last frequency and follow the tuner's reported frequency."""
        await super().async_added_to_hass()
        runtime = get_runtime_data(self._config_entry)
        if runtime is None:
            return
        callbacks = runtime.tuner_frequency_callbacks.setdefault(self._where, [])
        callbacks.append(self._apply_khz)

        @callback
        def _unsubscribe_freq() -> None:
            if self._apply_khz in callbacks:
                callbacks.remove(self._apply_khz)

        self.async_on_remove(_unsubscribe_freq)

        source = runtime.sound_sources.get(self._where)
        known_khz = getattr(source, "_frequency_khz", None) if source is not None else None
        if isinstance(known_khz, int) and FM_MIN_KHZ <= known_khz <= FM_MAX_KHZ:
            self._attr_native_value = round(known_khz / 1000.0, 2)

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore the last valid FM frequency across restarts."""
        try:
            mhz = float(last_state.state)
        except (ValueError, TypeError):
            return
        if not math.isfinite(mhz):
            return
        khz = int(round(mhz * 1000))
        if FM_MIN_KHZ <= khz <= FM_MAX_KHZ:
            self._attr_native_value = round(khz / 1000.0, 2)

    async def async_update(self) -> None:
        """Polling is handled by the companion MyHOMESoundSource entity."""

    @callback
    def _apply_khz(self, kilohertz: int | None) -> None:
        if kilohertz is None or not FM_MIN_KHZ <= kilohertz <= FM_MAX_KHZ:
            return
        new_val = round(kilohertz / 1000.0, 2)
        if self._attr_native_value != new_val:
            self._attr_native_value = new_val
            self._publish_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the tuner's FM frequency in MHz."""
        await tuner_source(self._config_entry, self._where, self.entity_id).async_set_frequency(value)
