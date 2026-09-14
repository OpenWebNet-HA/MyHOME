"""Support for MyHome sensors (power/energy, temperature, illuminance)."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import DOMAIN as PLATFORM
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_ENTITIES,
    CONF_MAC,
    CONF_NAME,
    LIGHT_LUX,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from OWNd.message import (
    MESSAGE_TYPE_ACTIVE_POWER,
    MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
    MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
    MESSAGE_TYPE_ENERGY_TOTALIZER,
    MESSAGE_TYPE_ILLUMINANCE,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_SECONDARY_TEMPERATURE,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)
from voluptuous import (
    All,
    Coerce,
    Optional,
    Range,
)

from .const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_MANUFACTURER,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
    normalize_where,
)
from .data import MyHOMEConfigEntry
from .discovery import Address, DeviceContext, PlatformDiscovery
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0

SCAN_INTERVAL = timedelta(seconds=300)

SERVICE_SEND_INSTANT_POWER = "start_sending_instant_power"

ATTR_DURATION = "duration"
ATTR_DATE = "date"
ATTR_MONTH = "month"
ATTR_DAY = "day"

ENERGY_MEASUREMENTS = {
    MESSAGE_TYPE_ACTIVE_POWER: "power",
    MESSAGE_TYPE_ENERGY_TOTALIZER: "total-energy",
    MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION: "daily-energy",
    MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION: "monthly-energy",
}


def _sensor_address(who: str | int, where: str | int) -> tuple[str, str]:
    """Match energy replies with and without local-bus suffix, and normalize numeric where."""
    who, where = str(who), str(where)
    if who == "18":
        return who, where.removesuffix("#0")
    return who, normalize_where(where)


def _spellings(where: str) -> list[str]:
    """``0021`` may also appear as ``21``, and a legacy ``1-0021`` as either."""
    clean = where.split("-")[-1]
    return [k for k in (where, normalize_where(where), clean, normalize_where(clean)) if k]


ENERGY_UNIQUE_ID = re.compile(r"([57]\d+)-(power|total-energy|daily-energy|monthly-energy)")



async def async_setup_entry(
    hass: HomeAssistant, config_entry: MyHOMEConfigEntry, async_add_entities: AddEntitiesCallback
) -> bool:
    """Set up the sensors of a gateway: energy meters (WHO=18), illuminance (WHO=1)
    and temperature probes (WHO=4), each restored from the registry, created from
    myhome.yaml, then discovered from the bus.

    A meter is one address with one entity per reported measurement (power,
    total / daily / monthly energy), so its entities are keyed ``<where>-<measurement>``.
    A frame reaches every entity of its address.
    """
    runtime = config_entry.runtime_data
    if PLATFORM not in runtime.platforms:
        return True
    gateway = runtime.gateway
    entry_mac = str(config_entry.data[CONF_MAC])

    # Device class of every configured (WHO, WHERE) under each of its spellings
    configured_class: dict[tuple[str, str], str | None] = {}
    for cfg in runtime.platforms[PLATFORM].values():
        if isinstance(cfg, dict) and CONF_WHERE in cfg:
            for spelling in _spellings(str(cfg[CONF_WHERE])):
                configured_class[(str(cfg.get(CONF_WHO)), spelling)] = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")

    def is_configured(who: str, where: str, *classes: str) -> bool:
        return any(configured_class.get((who, spelling)) in classes for spelling in _spellings(where))

    platform = entity_platform.current_platform.get()
    power_service_registered = False

    @callback
    def register_power_service() -> None:
        nonlocal power_service_registered
        power_service_registered = True
        if platform is not None:
            platform.async_register_entity_service(
                SERVICE_SEND_INSTANT_POWER,
                {Optional(ATTR_DURATION): All(Coerce(int), Range(min=1, max=255))},
                "start_sending_instant_power",
            )

    discovery_for: dict[str, PlatformDiscovery] = {}

    def yaml_class(*classes: str) -> Callable[[DeviceContext], bool]:
        def accept(ctx: DeviceContext) -> bool:
            if ctx.source == "yaml":
                return (ctx.cfg.get(CONF_DEVICE_CLASS) or ctx.cfg.get("device_class")) in classes
            if ctx.source == "registry":
                # myhome.yaml wins over the registry: a configured address is not restored,
                # nor is a second registry entry for a restored address (another spelling)
                return not is_configured(ctx.who, ctx.address.where, *classes) and (
                    ctx.who == "18" or normalize_where(ctx.address.where) not in discovery_for[ctx.who].known
                )
            return True

        return accept

    def entity_id_of(ctx: DeviceContext) -> str | None:
        return ctx.registry_entry.entity_id if ctx.registry_entry is not None else None

    # ── WHO 18: energy meters ───────────────────────────────────────────
    def energy_registry_address(entry: er.RegistryEntry) -> Address | None:
        prefix = f"{gateway.mac}-18-"
        if not entry.unique_id.startswith(prefix):
            return None
        match = ENERGY_UNIQUE_ID.fullmatch(entry.unique_id[len(prefix):])
        if match is None:
            return None
        where, measurement = match.groups()
        return Address(where, key_suffix=f"-{measurement}")

    def energy_bus_address(message: Any) -> Address | None:
        measurement = ENERGY_MEASUREMENTS.get(getattr(message, "message_type", None))
        if measurement is None:
            return None
        return Address(_sensor_address("18", message.where)[1], key_suffix=f"-{measurement}")

    def build_energy(ctx: DeviceContext) -> list[SensorEntity] | SensorEntity:
        if ctx.source == "yaml":
            cfg = ctx.cfg
            dev_class = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")
            device_id = ctx.config_id or ctx.key
            common: dict[str, Any] = dict(
                hass=hass, device_id=device_id, who=cfg[CONF_WHO], where=cfg[CONF_WHERE], name=cfg[CONF_NAME],
                manufacturer=cfg[CONF_MANUFACTURER], model=cfg[CONF_DEVICE_MODEL], gateway=gateway,
            )
            measurements = list(cfg[CONF_ENTITIES].keys())
            sensors: list[SensorEntity] = []
            if dev_class == SensorDeviceClass.POWER:
                _migrate_power_unique_id(hass, device_id)
                sensors.append(MyHOMEPowerSensor(device_class=dev_class, **common))
                if SensorDeviceClass.POWER in measurements:
                    measurements.remove(SensorDeviceClass.POWER)
                if not power_service_registered:
                    register_power_service()
            sensors.extend(
                MyHOMEEnergySensor(entity_specific_id=m, device_class=SensorDeviceClass.ENERGY, **common)
                for m in measurements
            )
            return sensors
        # Restored or discovered: only the measurements the meter actually reported
        where, measurement = ctx.address.where, ctx.address.key_suffix[1:]
        sensor: SensorEntity
        if measurement == "power":
            sensor = MyHOMEPowerSensor(
                hass=hass, device_id=f"18-{where}", who="18", where=where, name=f"Meter {where}",
                device_class=SensorDeviceClass.POWER, manufacturer=None, model=None, gateway=gateway,
            )
            if not power_service_registered:
                register_power_service()
        else:
            sensor = MyHOMEEnergySensor(
                hass=hass, device_id=f"18-{where}", who="18", where=where, name=f"Meter {where}",
                entity_specific_id=measurement, device_class=SensorDeviceClass.ENERGY,
                manufacturer=None, model=None, gateway=gateway,
            )
        sensor.entity_id = entity_id_of(ctx)  # type: ignore[assignment]
        return sensor

    def energy_known_keys(ctx: DeviceContext) -> list[str]:
        where = ctx.address.where if ctx.source != "yaml" else str(ctx.cfg[CONF_WHERE])
        wheres = [*_spellings(where), _sensor_address("18", where)[1]]
        keys = [ctx.key, ctx.config_id or "", *wheres]
        if ctx.source == "yaml":
            # A configured meter owns every measurement: the bus adds none
            keys.extend(f"{w}-{m}" for w in wheres for m in ENERGY_MEASUREMENTS.values())
        return [k for k in keys if k]

    # ── WHO 1: illuminance ──────────────────────────────────────────────
    def class_registry_address(marker: str, device_class: str) -> Callable[[er.RegistryEntry], Address | None]:
        def address_of(entry: er.RegistryEntry) -> Address | None:
            if marker not in entry.unique_id and entry.original_device_class != device_class:
                return None
            after_mac = entry.unique_id.replace(f"{gateway.mac}-", "", 1).replace(f"{entry_mac}-", "", 1)
            return Address(after_mac.replace(marker, "").split("-")[-1])

        return address_of

    def duplicate_illuminance(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        # Obsolete second registry entry, or an address that myhome.yaml configures
        return normalize_where(ctx.address.where) in discovery_for["1"].known or is_configured(
            "1", ctx.address.where, SensorDeviceClass.ILLUMINANCE
        )

    def illuminance_bus_address(message: Any) -> Address | None:
        if not (
            getattr(message, "message_type", None) == MESSAGE_TYPE_ILLUMINANCE
            or getattr(message, "dimension", None) == 6
            or isinstance(getattr(message, "illuminance", None), (int, float))
        ):
            return None
        where = str(message.where)
        return Address(normalize_where(where) or where)

    def build_illuminance(ctx: DeviceContext) -> MyHOMEIlluminanceSensor:
        if ctx.source == "yaml":
            cfg = ctx.cfg
            return MyHOMEIlluminanceSensor(
                hass=hass, device_id=ctx.config_id or ctx.key, who=cfg[CONF_WHO], where=cfg[CONF_WHERE],
                name=cfg[CONF_NAME], device_class=SensorDeviceClass.ILLUMINANCE, manufacturer=cfg[CONF_MANUFACTURER],
                model=cfg[CONF_DEVICE_MODEL], gateway=gateway,
            )
        where = ctx.address.where
        clean = where.split("-")[-1]
        primary = normalize_where(where) or normalize_where(clean) or where
        sensor = MyHOMEIlluminanceSensor(
            hass=hass, device_id=primary, who="1", where=primary, name=f"Illuminance {normalize_where(clean) or clean}",
            device_class=SensorDeviceClass.ILLUMINANCE, manufacturer="BTicino", model="Light Sensor", gateway=gateway,
        )
        sensor.entity_id = entity_id_of(ctx)  # type: ignore[assignment]
        return sensor

    # ── WHO 4: temperature probes ───────────────────────────────────────
    def temperature_bus_address(message: Any) -> Address | None:
        dimension = getattr(message, "dimension", None)
        message_type = getattr(message, "message_type", None)
        where = str(message.where)
        clean = where.split("-")[-1].split("#")[0]
        is_probe_reading = dimension == 15 or message_type == MESSAGE_TYPE_SECONDARY_TEMPERATURE
        is_probe = (message_type == MESSAGE_TYPE_MAIN_TEMPERATURE or dimension == 0) and clean.isdigit() and int(clean) >= 100
        if dimension in (11, 12, 13, 14, 19, 20) or not (is_probe_reading or is_probe):
            return None
        return Address(normalize_where(where) or where)

    def build_temperature(ctx: DeviceContext) -> MyHOMETemperatureSensor:
        if ctx.source == "yaml":
            cfg = ctx.cfg
            return MyHOMETemperatureSensor(
                hass=hass, device_id=ctx.config_id or ctx.key, who=cfg[CONF_WHO], where=cfg[CONF_WHERE],
                name=cfg[CONF_NAME], device_class=SensorDeviceClass.TEMPERATURE, manufacturer=cfg[CONF_MANUFACTURER],
                model=cfg[CONF_DEVICE_MODEL], gateway=gateway,
            )
        where = ctx.address.where
        clean = where.split("-")[-1].split("#")[0]
        primary = normalize_where(where) or normalize_where(clean) or where
        label = normalize_where(clean) or clean
        name = f"Probe {label}" if clean.isdigit() and int(clean) >= 100 else f"Zone {label}"
        sensor = MyHOMETemperatureSensor(
            hass=hass, device_id=primary, who="4", where=primary, name=name,
            device_class=SensorDeviceClass.TEMPERATURE, manufacturer="BTicino", model="Temperature Probe", gateway=gateway,
        )
        sensor.entity_id = entity_id_of(ctx)  # type: ignore[assignment]
        return sensor

    def known_keys(ctx: DeviceContext) -> list[str]:
        where = ctx.address.where if ctx.source != "yaml" else str(ctx.cfg[CONF_WHERE])
        keys = [ctx.key, ctx.config_id or "", *_spellings(where), _sensor_address(ctx.who, where)[1]]
        if ctx.source != "yaml":
            clean = where.split("-")[-1].split("#")[0]
            keys.append(normalize_where(where) or normalize_where(clean) or where)
        return [k for k in keys if k]

    def route_keys(message: Any, address: Address | None) -> list[str]:
        where = str(message.where)
        return [_sensor_address(message.who, where)[1], where, normalize_where(where)]

    common_args: dict[str, Any] = dict(
        hass=hass, config_entry=config_entry, async_add_entities=async_add_entities, platform=PLATFORM,
        route_keys=route_keys, one_per_address=False,
        general_is_device=True,  # sensor frames are never broadcasts; the address hooks decide
    )
    discovery_for["18"] = PlatformDiscovery(
        who="18", event_type=OWNEnergyEvent, build=build_energy, accept=yaml_class(SensorDeviceClass.POWER, SensorDeviceClass.ENERGY),
        registry_address=energy_registry_address, address=energy_bus_address, known_keys=energy_known_keys, **common_args,
    )
    discovery_for["1"] = PlatformDiscovery(
        who="1", event_type=OWNLightingEvent, build=build_illuminance, accept=yaml_class(SensorDeviceClass.ILLUMINANCE),
        registry_address=class_registry_address("-illuminance", SensorDeviceClass.ILLUMINANCE),
        reject_registry_entry=duplicate_illuminance, address=illuminance_bus_address, known_keys=known_keys, **common_args,
    )
    discovery_for["4"] = PlatformDiscovery(
        who="4", event_type=OWNHeatingEvent, build=build_temperature, accept=yaml_class(SensorDeviceClass.TEMPERATURE),
        registry_address=class_registry_address("-temperature", SensorDeviceClass.TEMPERATURE),
        address=temperature_bus_address, known_keys=known_keys, **common_args,
    )

    sensors: list[Entity] = []
    for discovery in discovery_for.values():
        sensors.extend(discovery.start(listen=False, add=False))
    async_add_entities(sensors)

    @callback
    def handle_message(message: Any) -> None:
        if not isinstance(message, (OWNEnergyEvent, OWNHeatingEvent, OWNLightingEvent)):
            return
        if (
            getattr(message, "message_type", None) is None
            and not (isinstance(message, OWNLightingEvent) and getattr(message, "dimension", None) == 6)
            and not (isinstance(message, OWNHeatingEvent) and getattr(message, "dimension", None) in (0, 15))
        ):
            return
        for discovery in discovery_for.values():
            discovery.handle_message(message)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, f"myhome_message_{gateway.mac}", handle_message)
    )
    return True


def _migrate_power_unique_id(hass: HomeAssistant, device_id: str) -> None:
    """Power sensors once had the bare device id as unique id; move them to ``<id>-power``."""
    try:
        registry = er.async_get(hass)
        existing_entity_id = registry.async_get_entity_id("sensor", DOMAIN, device_id)
        if existing_entity_id is not None:
            LOGGER.warning(
                "Sensor %s: %s will be migrated to %s-%s", device_id, existing_entity_id, device_id, SensorDeviceClass.POWER
            )
            registry.async_update_entity(entity_id=existing_entity_id, new_unique_id=f"{device_id}-{SensorDeviceClass.POWER}")
    except Exception:
        pass


async def async_unload_entry(hass, config_entry):
    runtime = config_entry.runtime_data

    if PLATFORM not in runtime.platforms:
        return True

    _configured_sensors = runtime.platforms[PLATFORM]

    for _sensor in list(_configured_sensors.keys()):
        del runtime.platforms[PLATFORM][
            _sensor
        ]


class MyHOMEPowerSensor(MyHOMEEntity, SensorEntity):
    _name_from_device_class = True

    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: str,
        manufacturer: str | None,
        model: str | None,
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
        )


        self._attr_device_class = device_class
        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        )
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_state_class = SensorStateClass.MEASUREMENT

        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        # await self.start_sending_instant_power(255)

    @callback
    def handle_event(self, message: OWNEnergyEvent):
        """Handle an event message."""
        if message.message_type not in [MESSAGE_TYPE_ACTIVE_POWER]:
            return True

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_native_value = message.active_power
        self._publish_state()

    async def start_sending_instant_power(self, duration):
        """Request automatic instant power."""
        await self._gateway_handler.send(
            OWNEnergyCommand.start_sending_instant_power(self._where, duration)
        )


class MyHOMEEnergySensor(MyHOMEEntity, SensorEntity):
    _name_from_device_class = True

    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        entity_specific_id: str,
        device_class: str,
        manufacturer: str | None,
        model: str | None,
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
        )

        self._entity_specific_id = entity_specific_id
        normalized_id = entity_specific_id.replace("_", "-")
        if normalized_id == "daily-energy":
            self._attr_translation_key = "energy_today"
            self._attr_entity_registry_enabled_default = False
        elif normalized_id == "monthly-energy":
            self._attr_translation_key = "energy_month"
            self._attr_entity_registry_enabled_default = False
        elif normalized_id == "total-energy":
            self._attr_entity_registry_enabled_default = True  # named "Energy" after its device class
        else:
            self._attr_name = entity_specific_id.replace("_", " ").capitalize()
            self._attr_entity_registry_enabled_default = True

        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._entity_specific_id}"
        )
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_should_poll = True
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._entity_specific_id)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._entity_specific_id)

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        normalized_id = self._entity_specific_id.replace("_", "-")
        if normalized_id == "total-energy":
            await self._gateway_handler.send_status_request(
                OWNEnergyCommand.get_total_consumption(self._where)
            )
        elif normalized_id == "monthly-energy":
            await self._gateway_handler.send_status_request(
                OWNEnergyCommand.get_partial_monthly_consumption(self._where)
            )
        elif normalized_id == "daily-energy":
            await self._gateway_handler.send_status_request(
                OWNEnergyCommand.get_partial_daily_consumption(self._where)
            )

    @callback
    def handle_event(self, message: OWNEnergyEvent):
        """Handle an event message."""
        if message.message_type not in [
            MESSAGE_TYPE_ENERGY_TOTALIZER,
            MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
            MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
        ]:
            return True

        norm_id = self._entity_specific_id.replace("_", "-")
        if (
            norm_id == "total-energy"
            and message.message_type == MESSAGE_TYPE_ENERGY_TOTALIZER
        ):
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.total_consumption
        elif (
            norm_id == "monthly-energy"
            and message.message_type == MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION
        ):
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.current_month_partial_consumption
        elif (
            norm_id == "daily-energy"
            and message.message_type == MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION
        ):
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_value = message.current_day_partial_consumption
        self._publish_state()


class MyHOMETemperatureSensor(MyHOMEEntity, SensorEntity):
    _name_from_device_class = True

    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: str,
        manufacturer: str | None,
        model: str | None,
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
        )


        self._attr_device_class = device_class
        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        )
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_should_poll = True
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }
        # Monotonic timestamp of the last temperature received from the bus.
        # Probes (WHERE >= 100, e.g. 3455 via L4577) push readings unsolicited
        # every few seconds and NACK explicit polls, so polling is only a
        # fallback for when the push stream goes quiet (issue #308).
        self._last_push_at: float | None = None

    @property
    def _is_probe(self) -> bool:
        """Return True for slave/external probe addresses (ZPP >= 100)."""
        clean_where = str(self._where).split("-")[-1].split("#")[0]
        return clean_where.isdigit() and int(clean_where) >= 100

    def _push_is_fresh(self) -> bool:
        """Return True when a reading arrived within the last poll interval."""
        return (
            self._last_push_at is not None
            and (time.monotonic() - self._last_push_at) < SCAN_INTERVAL.total_seconds()
        )

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)
        # Probes start receive-only: no initial poll, the push stream fills in
        # and the periodic update only polls if it stays silent (issue #308).
        self._poll_on_add = not self._is_probe
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)

    async def async_update(self):
        """Poll the probe, unless the bus already pushed a fresh reading."""
        if self._push_is_fresh():
            return
        if self._is_probe:
            cmd = (
                getattr(OWNHeatingCommand, "get_probe_temperature", None)
                and OWNHeatingCommand.get_probe_temperature(self._where)
            ) or OWNHeatingCommand.get_temperature(self._where)
        else:
            cmd = OWNHeatingCommand.get_temperature(self._where)
        await self._gateway_handler.send_status_request(cmd)

    @callback
    def handle_event(self, message: OWNHeatingEvent):
        """Handle an event message."""
        val = None
        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            val = message.main_temperature
        elif message.message_type == MESSAGE_TYPE_SECONDARY_TEMPERATURE:
            sec = getattr(message, "secondary_temperature", None)
            if isinstance(sec, (list, tuple)) and len(sec) > 1:
                val = sec[1]
            elif isinstance(sec, (int, float)):
                val = sec
            elif hasattr(message, "probe_temperature") and type(message.probe_temperature).__name__ != "MagicMock":
                val = message.probe_temperature
        elif getattr(message, "dimension", None) == 15:
            dim_val = getattr(message, "dimension_value", None)
            if dim_val:
                raw = dim_val[1] if len(dim_val) >= 2 else dim_val[0]
                try:
                    if len(raw) == 4 and raw.startswith("1"):
                        val = -float(raw[1:]) / 10.0
                    else:
                        val = float(raw) / 10.0
                except (ValueError, TypeError):
                    pass
        elif getattr(message, "dimension", None) == 0:
            dim_val = getattr(message, "dimension_value", None)
            if dim_val:
                raw = dim_val[0]
                try:
                    if len(raw) == 4 and raw.startswith("1"):
                        val = -float(raw[1:]) / 10.0
                    else:
                        val = float(raw) / 10.0
                except (ValueError, TypeError):
                    pass
        else:
            return True

        if val is not None:
            if hasattr(message, "human_readable_log") and message.human_readable_log:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
            self._attr_native_value = val
            self._last_push_at = time.monotonic()
            self._publish_state()


class MyHOMEIlluminanceSensor(MyHOMEEntity, SensorEntity):
    _name_from_device_class = True

    def __init__(
        self,
        hass,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: str,
        manufacturer: str | None,
        model: str | None,
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
        )


        self._attr_device_class = device_class
        self._attr_unique_id = (
            f"{gateway.mac}-{self._device_id}-{self._attr_device_class}"
        )
        self._attr_native_unit_of_measurement = LIGHT_LUX
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }

    async def async_added_to_hass(self):
        """When entity is added to hass."""
        self._register_entity_ref(self._attr_device_class)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self):
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._attr_device_class)

    async def async_update(self):
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(
            OWNLightingCommand.get_illuminance(self._where)
        )

    @callback
    def handle_event(self, message: OWNLightingEvent):
        """Handle an event message."""
        if (
            getattr(message, "message_type", None) != MESSAGE_TYPE_ILLUMINANCE
            and getattr(message, "dimension", None) != 6
            and not isinstance(getattr(message, "illuminance", None), (int, float))
        ):
            return True

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_native_value = message.illuminance
        self._publish_state()
