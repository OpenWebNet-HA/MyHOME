"""Support for MyHome sensors (power/energy, temperature, actuator output level, illuminance)."""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from homeassistant.components.climate.const import HVACMode
from homeassistant.components.sensor import DOMAIN as PLATFORM
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.sensor.const import (
    NON_NUMERIC_DEVICE_CLASSES,
    UNIT_CONVERTERS,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ENTITIES,
    CONF_MAC,
    CONF_NAME,
    LIGHT_LUX,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util.enum import try_parse_enum
from OWNd.message import (
    CLIMATE_MODE_OFF,
    MESSAGE_TYPE_ACTIVE_POWER,
    MESSAGE_TYPE_AUTO_UPDATE_INTERVAL,
    MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
    MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
    MESSAGE_TYPE_ENERGY_TOTALIZER,
    MESSAGE_TYPE_ILLUMINANCE,
    MESSAGE_TYPE_MAIN_HUMIDITY,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_MODE,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_SECONDARY_TEMPERATURE,
    MESSAGE_TYPE_ZONE_STATE,
    OWNCommand,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
    who4_temperature,
)
from voluptuous import (
    All,
    Coerce,
    Optional,
    Range,
)

from .climate_discovery import has_humidity_state, has_thermostat_state
from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_CENTRAL,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_HUMIDITY_SUPPORT,
    CONF_MANUFACTURER,
    CONF_STANDALONE,
    CONF_STANDALONE_ZONES,
    CONF_WHERE,
    CONF_WHO,
    CONF_ZONE,
    DOMAIN,
    LOGGER,
    SIGNAL_CLIMATE_HUMIDITY,
    SIGNAL_NEW_CLIMATE_ZONE,
    central_mode_signal,
    normalize_where,
)
from .data import MyHOMEConfigEntry
from .discovery import (
    Address,
    DeviceContext,
    PlatformDiscovery,
    config_for,
    is_broadcast_where,
    parse_unique_id,
    prune_entity,
)
from .expose import is_exposed_on_bus
from .gateway import MyHOMEGatewayHandler
from .ignored import IgnoredAddresses
from .migrate import _carry_entity_customisations
from .myhome_device import MyHOMEEntity
from .thermo_switch import load_thermo_switch_subentries
from .topology import peer_unique_id
from .typing_compat import as_any
from .where_grammar import (
    bus_interface_from_message,
    canonical_thermo_zone,
    is_actuator_where,
    is_central_thermo_where,
    is_central_unit_report,
    is_external_probe,
    is_probe,
    parse_thermo_actuator_where,
    thermo_dim19_status,
    thermo_dim20_actuator_target,
    thermo_dim20_level,
    thermo_dim20_status,
    thermo_event_interface,
    thermo_event_zone,
    thermo_zone_sensor_target,
    validate_standalone_zones,
)

PARALLEL_UPDATES = 0

SCAN_INTERVAL = timedelta(seconds=300)
# How long a reading a probe (WHERE >= 100) sent by itself keeps its fallback read back. A probe
# on a physical plant reported every 10 min (`*#4*303*0*0229##` 08:31:22Z, `*#4*303*0*0230##`
# 08:41:23Z, 2026-11-17), so with the poll interval as the window every second update sent
# `*#4*303*15##`; the F455 1.0.76 on that plant held a command session 9.3 to 9.4 s before it
# NACKed each of them (sent directly to the F461 on the same bus: NACK after 2.1 s). Three
# poll intervals cover that report interval with one update to spare.
# (That read was the dimension 15 one; a slave probe is now asked for dimension 0, see
# `MyHOMETemperatureSensor._fallback_read`.)
PROBE_PUSH_FRESH = 900.0
# A single corrupted frame (e.g. zone 1 bit-7 flip -> 129) must not register a
# permanent phantom probe sensor (#967). Unconfigured bus probes confirm passively
# on a second independent reading within MAX_AGE; identical echoes within
# ECHO_WINDOW (MyHomeServer1 duplicates) are ignored without resetting the timer.
PROBE_DISCOVERY_ECHO_WINDOW = 5.0
PROBE_DISCOVERY_MAX_AGE = 3600.0

SERVICE_SEND_INSTANT_POWER = "start_sending_instant_power"
SERVICE_STOP_SENDING_INSTANT_POWER = "stop_sending_instant_power"

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


def _canonical_temperature_where(where: str) -> str:
    """Return canonical WHERE for a WHO=4 temperature probe (e.g. ``0105`` -> ``105``)."""
    norm = normalize_where(where) or where
    base, sep, rest = norm.partition("#")
    if is_probe(base) and base.isdigit():
        return f"{int(base)}{sep}{rest}"
    return norm


def _sensor_address(who: str | int, where: str | int) -> tuple[str, str]:
    """Match energy replies with and without local-bus suffix, and normalize numeric where."""
    who, where = str(who), str(where)
    if who == "18":
        return who, where.removesuffix("#0")
    if who == "4":
        return who, _canonical_temperature_where(where)
    return who, normalize_where(where)


def _spellings(where: str, who: str | None = None) -> list[str]:
    """``0021`` may also appear as ``21``, a legacy ``1-0021`` as either, and WHO=4 ``0105`` as ``105``."""
    clean = where.split("-")[-1]
    items = [where, normalize_where(where), clean, normalize_where(clean)]
    if who == "4":
        items.extend((_canonical_temperature_where(where), _canonical_temperature_where(clean)))
    return [k for k in items if k]


ENERGY_UNIQUE_ID = re.compile(r"([0-9#]+)-(power|total-energy|daily-energy|monthly-energy)")
ACTUATOR_OUTPUT_UNIQUE_ID = re.compile(
    r"^4-([1-9][0-9]?#[1-9])(?:#4#([0-9]{2}))?-output-level$"
)
CLIMATE_HUMIDITY_UNIQUE_ID = re.compile(
    r"^4-([1-9][0-9]?)(?:#4#([0-9]{2}))?-humidity$"
)
LEGACY_ZONE_TEMP_TAIL = re.compile(
    r"^(?:0([1-9])|#0?([1-9][0-9]?))(?:#4#(0?[0-9]|1[0-5]))?$"
)


def _finite_float(value: Any) -> float | None:
    """Return finite float for numeric non-bool values, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fval = float(value)
    return fval if math.isfinite(fval) else None


def _climate_zone_config(
    configured: dict[str, Any], zone: str, interface: str | None
) -> dict[str, Any]:
    """Return the ``myhome.yaml`` ``climate`` entry for ``(zone, interface)``, if any."""
    canon_zone = canonical_thermo_zone(zone) or zone
    address = Address(zone, interface)
    key = address.key
    if interface:
        routing = f"{BUS_ROUTING}{interface}"
        found = config_for(
            configured, address, key, f"4-{zone}{routing}", f"4-{key}", f"#{zone}{routing}"
        )
    else:
        found = config_for(
            configured,
            address,
            key,
            zone,
            f"4-{zone}",
            f"4-{key}",
            f"4-#{zone}",
            f"#{zone}",
            f"zone_{zone}",
        )
    if found:
        found_addr = Address.from_device_id(
            str(found.get(CONF_WHERE, found.get(CONF_ZONE, ""))).removeprefix("4-")
        )
        if (
            not is_actuator_where(found_addr.where)
            and canonical_thermo_zone(found_addr.where) == canon_zone
        ):
            return found
    for cfg in configured.values():
        if not isinstance(cfg, dict):
            continue
        raw_where = cfg.get(CONF_WHERE, cfg.get(CONF_ZONE))
        if raw_where is None:
            continue
        cfg_addr = Address.from_device_id(str(raw_where).removeprefix("4-"))
        raw_if = cfg.get(CONF_BUS_INTERFACE)
        eff_iface = (
            str(raw_if).zfill(2)
            if raw_if is not None and str(raw_if).isdigit()
            else (str(raw_if) if raw_if is not None else cfg_addr.interface)
        )
        if (
            not is_actuator_where(cfg_addr.where)
            and canonical_thermo_zone(cfg_addr.where) == canon_zone
            and eff_iface == interface
        ):
            return dict(cfg)
    return {}

def _probe_frame_without_reading(message: Any) -> bool:
    """A WHO 4 dimension 15 frame whose sensor field and temperature are all zeros.

    An MH201 3.3.6 delivered ``*#4*105*15*0*0000*0000##`` twice, 8 s apart (issue_466, sweep of
    2026-09-25), while probe 105 of that plant reports 29.6 °C on dimension 0
    (``*#4*105*0*0296##``, 7 min 11 s earlier in the trace of the same gateway). OWNd reads the
    frame as sensor 0 at 0.0 °C. The dimension 15 reports in the other captures carry sensor
    ``1``: ``*#4*100*15*1*0253*0001##`` (#308), ``*#4*100*15*1*0200*0001##`` (#264); so does the
    touch screen's test report ``*#4*11*15*1*123*1111##``, and its request is ``15#1``
    (Encyclopedia, WHO 4 dimensions, "Historical external-probe DIMENSION 15"). What a sensor
    field of ``0`` stands for is not documented, so only a frame whose sensor field and
    temperature are both zeros is taken as "no reading"; ``0000`` from sensor 1 is still 0.0 °C.
    """
    if getattr(message, "dimension", None) != 15:
        return False
    values = [str(value) for value in (getattr(message, "dimension_value", None) or [])]
    return len(values) >= 2 and all(value != "" and set(value) == {"0"} for value in values[:2])



async def async_setup_entry(
    hass: HomeAssistant, config_entry: MyHOMEConfigEntry, async_add_entities: AddEntitiesCallback
) -> bool:
    """Set up the sensors of a gateway: energy meters (WHO=18), illuminance (WHO=1),
    temperature probes (WHO=4), and 0-10 V proportional actuator output levels (WHO=4),
    each restored from the registry, created from myhome.yaml, then discovered from the bus.

    A meter is one address with one entity per reported measurement (power,
    total / daily / monthly energy), so its entities are keyed ``<where>-<measurement>``.
    A frame reaches every entity of its address.
    """
    runtime = config_entry.runtime_data
    if PLATFORM not in runtime.platforms:
        return True
    load_thermo_switch_subentries(config_entry, runtime.platforms)
    gateway = runtime.gateway
    entry_mac = str(config_entry.data[CONF_MAC])
    _migrate_temperature_unique_ids(
        hass, config_entry.entry_id, gateway.mac, entry_mac, runtime.platforms[PLATFORM]
    )

    def _safe_registry_entries() -> tuple[er.EntityRegistry | None, list[er.RegistryEntry]]:
        try:
            reg = er.async_get(hass)
            return reg, list(er.async_entries_for_config_entry(reg, config_entry.entry_id))
        except Exception:
            return None, []

    def _apply_legacy_compat_name(
        sensor: MyHOMEEntity, reg: er.EntityRegistry | None
    ) -> None:
        eid = (
            reg.async_get_entity_id(PLATFORM, DOMAIN, str(sensor.unique_id))
            if reg is not None
            else None
        )
        ent = reg.async_get(eid) if reg is not None and eid is not None else None
        if ent is not None and not ent.has_entity_name and ent.name:
            sensor._legacy_compat_name = ent.name

    reg_obj, reg_entries = _safe_registry_entries()

    configured_climate = runtime.platforms.get("climate", {})
    # Zones the owner marked as standalone thermostats in the gateway options (local bus),
    # as the climate platform reads them.
    standalone_zones = frozenset(
        validate_standalone_zones(
            (getattr(config_entry, "options", None) or {}).get(CONF_STANDALONE_ZONES)
        )[0]
    )
    thermo_switch_whole_zones: set[tuple[str, str | None]] = set()
    thermo_switch_actuator_zones: set[tuple[str, str | None]] = set()
    thermo_switch_actuators: set[tuple[str, str, str | None]] = set()
    for sw_cfg in runtime.platforms.get("switch", {}).values():
        if not isinstance(sw_cfg, dict) or str(sw_cfg.get(CONF_WHO)) != "4":
            continue
        try:
            _nw, sw_zone, sw_act, sw_if = parse_thermo_actuator_where(
                sw_cfg.get(CONF_WHERE), sw_cfg.get(CONF_BUS_INTERFACE)
            )
        except ValueError:
            continue
        has_explicit_climate = bool(
            _climate_zone_config(configured_climate, sw_zone, sw_if)
            or (
                len(sw_zone) == 1
                and _climate_zone_config(configured_climate, f"0{sw_zone}", sw_if)
            )
        )
        if not has_explicit_climate:
            if sw_act is None:
                thermo_switch_whole_zones.add((sw_zone, sw_if))
            else:
                thermo_switch_actuator_zones.add((sw_zone, sw_if))
                thermo_switch_actuators.add((sw_zone, sw_act, sw_if))

    # Device class of every configured (WHO, WHERE) under each of its spellings
    configured_class: dict[tuple[str, str], str | None] = {}
    for cfg_id, cfg in runtime.platforms[PLATFORM].items():
        if isinstance(cfg, dict) and CONF_WHERE in cfg:
            who_str = str(cfg.get(CONF_WHO))
            cfg_addr = Address.from_config(str(cfg_id), cfg)
            for spelling in _spellings(cfg_addr.where, who_str):
                key = (
                    Address(spelling, cfg_addr.interface).key
                    if cfg_addr.interface and BUS_ROUTING not in spelling
                    else spelling
                )
                configured_class[(who_str, key)] = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")

    def is_configured(
        who: str, where: str, *classes: str, interface: str | None = None
    ) -> bool:
        return any(
            configured_class.get(
                (
                    who,
                    Address(spelling, interface).key
                    if interface and BUS_ROUTING not in spelling
                    else spelling,
                )
            )
            in classes
            for spelling in _spellings(where, who)
        )

    platform = entity_platform.current_platform.get()
    power_service_registered = False

    @callback
    def register_power_service() -> None:
        nonlocal power_service_registered
        power_service_registered = True
        if platform is not None:
            # Only MyHOMEPowerSensor has these methods: targeting an energy, temperature or
            # illuminance sensor raised AttributeError inside Home Assistant (HTTP 500 on
            # the service call, live F520 plant 2026-11-11 / 2026-11-12). The device-class
            # filter makes Home Assistant answer "does not support this action" instead.
            platform.async_register_entity_service(
                SERVICE_SEND_INSTANT_POWER,
                as_any({Optional(ATTR_DURATION): All(Coerce(int), Range(min=1, max=255))}),
                "start_sending_instant_power",
                entity_device_classes=(SensorDeviceClass.POWER,),
            )
            platform.async_register_entity_service(
                SERVICE_STOP_SENDING_INSTANT_POWER,
                {},
                "async_stop_sending_instant_power",
                entity_device_classes=(SensorDeviceClass.POWER,),
            )

    discovery_for: dict[str, PlatformDiscovery] = {}
    pending_temperature_probes: dict[str, tuple[float, str]] = {}

    def yaml_class(*classes: str) -> Callable[[DeviceContext], bool]:
        def accept(ctx: DeviceContext) -> bool:
            if ctx.source == "yaml":
                return (ctx.cfg.get(CONF_DEVICE_CLASS) or ctx.cfg.get("device_class")) in classes
            if ctx.source == "registry":
                # myhome.yaml wins over the registry: a configured address is not restored,
                # nor is a second registry entry for a restored address (another spelling)
                return not is_configured(
                    ctx.who, ctx.address.where, *classes, interface=ctx.address.interface
                ) and (
                    ctx.who == "18"
                    or _sensor_address(ctx.who, ctx.address.key)[1] not in discovery_for[ctx.who].known
                )
            return True

        return accept

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
        where = str(getattr(message, "where", ""))
        if getattr(message, "is_general", False) is True or is_broadcast_where(where):
            return None
        measurement = ENERGY_MEASUREMENTS.get(cast(str, getattr(message, "message_type", None)))
        if measurement is None:
            return None
        return Address(_sensor_address("18", message.where)[1], key_suffix=f"-{measurement}")

    def duplicate_energy(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        if ctx.cfg:
            return False
        return is_broadcast_where(ctx.address.clean_where)

    def build_energy(ctx: DeviceContext) -> list[MyHOMEEntity] | MyHOMEEntity | None:
        if ctx.source == "yaml":
            cfg = ctx.cfg
            dev_class = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")
            device_id = ctx.config_id or ctx.key
            common: dict[str, Any] = dict(
                hass=hass, device_id=device_id, who=cfg[CONF_WHO], where=cfg[CONF_WHERE], name=cfg[CONF_NAME],
                manufacturer=cfg[CONF_MANUFACTURER], model=cfg[CONF_DEVICE_MODEL], gateway=gateway,
            )
            measurements = list(cfg[CONF_ENTITIES].keys())
            sensors: list[MyHOMEEntity] = []
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
        if is_broadcast_where(where) or is_broadcast_where(ctx.address.clean_where):
            return None
        sensor: MyHOMEEntity
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
            return Address.from_device_id(after_mac.replace(marker, "").split("-")[-1])

        return address_of

    def duplicate_illuminance(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        # Broadcast address or obsolete second registry entry, or an address that myhome.yaml configures
        if is_broadcast_where(ctx.address.clean_where):
            return True
        return _sensor_address("1", ctx.address.key)[1] in discovery_for["1"].known or is_configured(
            "1", ctx.address.where, SensorDeviceClass.ILLUMINANCE, interface=ctx.address.interface
        )

    def illuminance_bus_address(message: Any) -> Address | None:
        if not (
            getattr(message, "message_type", None) == MESSAGE_TYPE_ILLUMINANCE
            or getattr(message, "dimension", None) == 6
            or isinstance(getattr(message, "illuminance", None), (int, float))
        ):
            return None
        if getattr(message, "is_general", False) is True or str(getattr(message, "where", "")) in ("0", "00"):
            return None
        valid_iface, interface = bus_interface_from_message(message)
        if not valid_iface:
            return None
        where = str(message.where)
        return Address(normalize_where(where) or where, interface)

    def build_illuminance(ctx: DeviceContext) -> MyHOMEIlluminanceSensor | None:
        if ctx.source == "yaml":
            cfg = ctx.cfg
            where = str(cfg.get(CONF_WHERE, ""))
            if where in ("0", "00") or normalize_where(where) in ("0", "00"):
                return None
            return MyHOMEIlluminanceSensor(
                hass=hass, device_id=ctx.config_id or ctx.key, who=cfg[CONF_WHO], where=ctx.address.key,
                name=cfg[CONF_NAME], device_class=SensorDeviceClass.ILLUMINANCE, manufacturer=cfg[CONF_MANUFACTURER],
                model=cfg[CONF_DEVICE_MODEL], gateway=gateway,
            )
        where = ctx.address.key
        clean = where.split("-")[-1]
        primary = normalize_where(where) or normalize_where(clean) or where
        if primary in ("0", "00") or ctx.address.clean_where in ("0", "00"):
            return None
        label_addr = Address.from_device_id(normalize_where(clean) or clean)
        sensor = MyHOMEIlluminanceSensor(
            hass=hass, device_id=primary, who="1", where=primary, name=f"Illuminance {label_addr.suffix}",
            device_class=SensorDeviceClass.ILLUMINANCE, manufacturer="BTicino", model="Light Sensor", gateway=gateway,
        )
        if ctx.registry_entry is not None:
            # yaml-era ids are `{mac}-1-{where}-illuminance`; a rebuilt id would orphan
            # the registry entry and create a duplicate.
            sensor._attr_unique_id = ctx.registry_entry.unique_id
        return sensor

    # ── WHO 4: temperature probes ───────────────────────────────────────
    def temperature_bus_address(message: Any) -> Address | None:
        dimension = getattr(message, "dimension", None)
        message_type = getattr(message, "message_type", None)
        where = str(message.where)
        if is_broadcast_where(where):
            return None
        clean = where.split("-")[-1].split("#")[0]
        if not is_probe(clean):
            return None
        if _probe_frame_without_reading(message):
            return None  # no sensor is created for a frame that carries no reading
        is_probe_reading = dimension == 15 or message_type == MESSAGE_TYPE_SECONDARY_TEMPERATURE
        is_probe_main = message_type == MESSAGE_TYPE_MAIN_TEMPERATURE or dimension == 0
        if dimension in (11, 12, 13, 14, 19, 20) or not (is_probe_reading or is_probe_main):
            return None
        addr = Address.from_device_id(_canonical_temperature_where(where))
        return Address(addr.where, thermo_event_interface(message) or addr.interface)

    temperature_yaml_or_registry_accept = yaml_class(SensorDeviceClass.TEMPERATURE)

    def _is_who4_temp_cfg(cfg: Any) -> bool:
        return (
            isinstance(cfg, dict)
            and str(cfg.get(CONF_WHO)) == "4"
            and (cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class"))
            in (None, SensorDeviceClass.TEMPERATURE)
        )

    def accept_temperature(ctx: DeviceContext) -> bool:
        """Confirm unconfigured WHO=4 bus probes passively on a second independent reading (#967),
        while keeping non-probe zone addresses owned by climate companions unless in YAML (#981)."""
        if ctx.source != "yaml" and not is_probe(ctx.address.clean_where):
            return False
        if ctx.source != "bus" or _is_who4_temp_cfg(ctx.cfg):
            return temperature_yaml_or_registry_accept(ctx)
        probe_key = Address(
            _canonical_temperature_where(ctx.address.where),
            ctx.address.interface,
        ).key
        now = time.monotonic()
        raw = str(ctx.message)
        expired = [
            key
            for key, (first_at, _) in pending_temperature_probes.items()
            if (now - first_at) > PROBE_DISCOVERY_MAX_AGE
        ]
        for key in expired:
            del pending_temperature_probes[key]
        if probe_key not in pending_temperature_probes:
            pending_temperature_probes[probe_key] = (now, raw)
            LOGGER.debug(
                "Deferring WHO=4 temperature sensor %s creation until confirmed by a second reading (%s)",
                probe_key,
                raw,
            )
            return False
        first_at, first_raw = pending_temperature_probes[probe_key]
        if raw == first_raw and (now - first_at) < PROBE_DISCOVERY_ECHO_WINDOW:
            return False
        pending_temperature_probes.pop(probe_key, None)
        LOGGER.debug(
            "Confirmed WHO=4 temperature sensor %s on second reading (%s)",
            probe_key,
            raw,
        )
        return True

    def build_temperature(ctx: DeviceContext) -> MyHOMETemperatureSensor | None:
        if ctx.source == "yaml":
            cfg = ctx.cfg
            yaml_addr = Address.from_config(ctx.config_id or ctx.key, cfg)
            canon_key = Address(
                _canonical_temperature_where(yaml_addr.clean_where),
                yaml_addr.interface,
            ).key
            raw_dev_id = ctx.config_id or ctx.key
            dev_id = f"4-{canon_key}" if raw_dev_id.startswith("4-") else raw_dev_id
            sensor = MyHOMETemperatureSensor(
                hass=hass, device_id=dev_id, who=cfg[CONF_WHO], where=canon_key,
                name=cfg[CONF_NAME], device_class=SensorDeviceClass.TEMPERATURE, manufacturer=cfg[CONF_MANUFACTURER],
                model=cfg[CONF_DEVICE_MODEL], gateway=gateway,
            )
            _apply_legacy_compat_name(sensor, reg_obj)
            return sensor
        where = ctx.address.where
        clean = where.split("-")[-1].split("#")[0]
        primary = _canonical_temperature_where(where) or _canonical_temperature_where(clean)
        if is_broadcast_where(primary) or is_broadcast_where(clean):
            return None
        canon_addr = Address(primary, ctx.address.interface)
        label = canon_addr.suffix
        name = f"Probe {label}" if is_probe(clean) else f"Zone {label}"
        # ``4-<where>``, the id validate.py gives a myhome.yaml probe: one unique id either way (#441)
        sensor = MyHOMETemperatureSensor(
            hass=hass, device_id=f"4-{canon_addr.key}", who="4", where=canon_addr.key, name=name,
            device_class=SensorDeviceClass.TEMPERATURE, manufacturer="BTicino", model="Temperature Probe", gateway=gateway,
        )
        return sensor

    def known_keys(ctx: DeviceContext) -> list[str]:
        addr = (
            Address.from_config(ctx.config_id or ctx.key, ctx.cfg)
            if ctx.source == "yaml"
            else ctx.address
        )
        where = addr.where
        iface = addr.interface
        raw_keys = [*_spellings(where, ctx.who), _sensor_address(ctx.who, where)[1]]
        if ctx.source != "yaml":
            clean = where.split("-")[-1].split("#")[0]
            raw_keys.append(_sensor_address(ctx.who, clean)[1] or clean)
        qualified = [
            Address(k, iface).key if iface and BUS_ROUTING not in k else k
            for k in raw_keys
            if k
        ]
        return [k for k in (ctx.key, ctx.config_id or "", *qualified) if k]

    def route_keys(message: Any, address: Address | None) -> list[str]:
        where = str(message.where)
        iface = address.interface if address is not None else thermo_event_interface(message)
        raw_keys = [_sensor_address(message.who, where)[1], where, normalize_where(where)]
        return [
            Address(k, iface).key if iface and BUS_ROUTING not in k else k
            for k in raw_keys
            if k
        ]

    # ── WHO 4: 0-10 V proportional actuator output level sensors (#949) ──
    def _has_climate_zone_entity(zone: str, iface: str | None) -> bool:
        reg = er.async_get(hass)
        for ent in er.async_entries_for_config_entry(reg, config_entry.entry_id):
            if ent.domain != "climate" or not ent.unique_id:
                continue
            c_who, c_dev_id = parse_unique_id(ent.unique_id, gateway.mac, entry_mac)
            if c_who != "4":
                continue
            c_addr = Address.from_device_id(c_dev_id)
            if canonical_thermo_zone(c_addr.where) == zone and c_addr.interface == iface:
                return True
        return False

    def _is_thermo_switch_claimed(zone: str, actuator: str, iface: str | None) -> bool:
        if (zone, iface) in thermo_switch_whole_zones:
            return True
        if (zone, actuator, iface) in thermo_switch_actuators:
            return True
        if (zone, iface) in thermo_switch_actuator_zones:
            return not _has_climate_zone_entity(zone, iface)
        return False

    def _resolve_climate_device_meta(
        zone: str, iface: str | None
    ) -> tuple[str, str, str, str, bool | None]:
        suffix = f"{zone}I{iface}" if iface else zone
        default_dev_id = f"{zone}{BUS_ROUTING}{iface}" if iface else zone
        default_name = f"Climate Zone {suffix}"
        cfg = _climate_zone_config(configured_climate, zone, iface)
        if not cfg and len(zone) == 1:
            cfg = _climate_zone_config(configured_climate, f"0{zone}", iface)
        standalone_cfg: bool | None = None
        if cfg and not bool(cfg.get(CONF_CENTRAL, False)):
            raw_zone = str(cfg.get(CONF_WHERE, cfg.get(CONF_ZONE, zone)))
            parsed_zone = Address.from_device_id(raw_zone.removeprefix("4-"))
            cfg_addr = Address(parsed_zone.where, iface or parsed_zone.interface)
            raw_standalone = cfg.get(CONF_STANDALONE)
            if isinstance(raw_standalone, bool):
                standalone_cfg = raw_standalone
            dev_name = str(cfg.get(CONF_NAME) or default_name)
            manufacturer = str(cfg.get(CONF_MANUFACTURER) or "BTicino")
            model = str(cfg.get(CONF_DEVICE_MODEL) or "Heating Zone")
            return (cfg_addr.key, dev_name, manufacturer, model, standalone_cfg)
        reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        for ent in er.async_entries_for_config_entry(reg, config_entry.entry_id):
            if ent.domain != "climate" or not ent.unique_id:
                continue
            c_who, c_dev_id = parse_unique_id(ent.unique_id, gateway.mac, entry_mac)
            if c_who != "4":
                continue
            c_addr = Address.from_device_id(c_dev_id)
            if canonical_thermo_zone(c_addr.where) == zone and c_addr.interface == iface:
                dev = dev_reg.async_get(ent.device_id) if ent.device_id else None
                raw_name = getattr(dev, "name", None)
                raw_mfg = getattr(dev, "manufacturer", None)
                raw_model = getattr(dev, "model", None)
                dev_name = (
                    raw_name
                    if isinstance(raw_name, str) and raw_name
                    else default_name
                )
                manufacturer = (
                    raw_mfg
                    if isinstance(raw_mfg, str) and raw_mfg
                    else "BTicino"
                )
                model = (
                    raw_model
                    if isinstance(raw_model, str) and raw_model
                    else "Heating Zone"
                )
                return (c_dev_id, dev_name, manufacturer, model, None)
        return (default_dev_id, default_name, "BTicino", "Heating Zone", None)

    def _unpack_actuator_ctx(ctx: DeviceContext) -> tuple[str, str, str, str | None]:
        norm_where, zone, actuator, norm_if = parse_thermo_actuator_where(
            ctx.address.where, ctx.address.interface
        )
        return (norm_where, zone, cast(str, actuator), norm_if)

    def actuator_output_registry_address(entry: er.RegistryEntry) -> Address | None:
        after_mac = entry.unique_id.replace(f"{gateway.mac}-", "", 1)
        if entry_mac and entry_mac != gateway.mac:
            after_mac = after_mac.replace(f"{entry_mac}-", "", 1)
        match = ACTUATOR_OUTPUT_UNIQUE_ID.fullmatch(after_mac)
        if match is None:
            return None
        act_where, iface = match.groups()
        return Address(act_where, iface, key_suffix="-output-level")

    def actuator_output_bus_address(message: Any) -> Address | None:
        if thermo_dim20_level(message) is None:
            return None
        target = thermo_dim20_actuator_target(message)
        if target is None:
            return None
        zone, actuator, iface = target
        return Address(f"{zone}#{actuator}", iface, key_suffix="-output-level")

    def duplicate_actuator_output(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        _nw, zone, actuator, norm_if = _unpack_actuator_ctx(ctx)
        if _is_thermo_switch_claimed(zone, actuator, norm_if):
            LOGGER.info(
                "sensor: pruning actuator output sensor %s claimed by WHO=4 thermo switch (#785)",
                entry.entity_id,
            )
            return True
        return False

    def accept_actuator_output(ctx: DeviceContext) -> bool:
        if ctx.source == "yaml":
            return False
        norm_where, zone, actuator, norm_if = _unpack_actuator_ctx(ctx)
        if _is_thermo_switch_claimed(zone, actuator, norm_if):
            return False
        if ctx.source == "registry":
            full_act = f"{norm_where}{BUS_ROUTING}{norm_if}" if norm_if else norm_where
            return f"{full_act}-output-level" not in discovery_for["4_actuator"].known
        return True

    def build_actuator_output(ctx: DeviceContext) -> MyHOMEActuatorOutputSensor:
        norm_where, zone, actuator, norm_if = _unpack_actuator_ctx(ctx)
        zone_dev_id, dev_name, manufacturer, model, standalone_cfg = (
            _resolve_climate_device_meta(zone, norm_if)
        )
        if not norm_if and canonical_thermo_zone(zone) in standalone_zones:
            standalone_cfg = True  # "Standalone thermostats" in the gateway options
        return MyHOMEActuatorOutputSensor(
            hass=hass,
            name=dev_name,
            device_id=zone_dev_id,
            where=norm_where,
            zone=zone,
            actuator=actuator,
            interface=norm_if,
            standalone=standalone_cfg,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )

    def actuator_output_known_keys(ctx: DeviceContext) -> list[str]:
        norm_where, zone, _act, norm_if = parse_thermo_actuator_where(
            ctx.address.where, ctx.address.interface
        )
        zone_key = f"{zone}{BUS_ROUTING}{norm_if}" if norm_if else zone
        full_act = f"{norm_where}{BUS_ROUTING}{norm_if}" if norm_if else norm_where
        return [
            k
            for k in (
                ctx.key,
                f"{full_act}-output-level",
                f"actuator_output_zone:{zone_key}",
            )
            if k
        ]

    def actuator_output_route_keys(message: Any, address: Address | None) -> list[str]:
        zone = thermo_event_zone(message)
        if zone is None:
            return []
        iface = thermo_event_interface(message)
        zone_key = f"{zone}{BUS_ROUTING}{iface}" if iface else zone
        return [f"actuator_output_zone:{zone_key}"]

    # ── WHO 4: companion temperature & humidity sensors on climate zones (#981) ──
    yaml_zone_temp_pairs: set[tuple[str, str | None]] = set()
    for cfg_id, cfg in runtime.platforms[PLATFORM].items():
        if not isinstance(cfg, dict) or str(cfg.get(CONF_WHO)) != "4" or CONF_WHERE not in cfg:
            continue
        dev_cls = cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")
        if dev_cls != SensorDeviceClass.TEMPERATURE:
            continue
        cfg_addr = Address.from_config(str(cfg_id), cfg)
        if not is_probe(cfg_addr.clean_where) and not is_actuator_where(cfg_addr.clean_where):
            cfg_zone = canonical_thermo_zone(cfg_addr.clean_where)
            if cfg_zone is not None:
                yaml_zone_temp_pairs.add((cfg_zone, cfg_addr.interface))

    ignored_addresses = IgnoredAddresses.from_config_entry(config_entry)
    is_follower = getattr(runtime, "is_follower", False) is True
    primary_mac = getattr(runtime, "primary_gateway_mac", None)
    known_climate_temp: dict[tuple[str, str | None], MyHOMEClimateTemperatureSensor] = {}
    known_climate_humidity: set[tuple[str, str | None]] = set()

    def _zone_key(zone: str, iface: str | None) -> str:
        return f"{zone}{BUS_ROUTING}{iface}" if iface else zone

    def _climate_companion_owned_by_primary(
        reg: er.EntityRegistry | None,
        zone: str,
        iface: str | None,
        zone_dev_id: str | None,
        suffix: str,
    ) -> bool:
        if reg is None or not is_follower or not isinstance(primary_mac, str) or not primary_mac:
            return False
        zkey = _zone_key(zone, iface)
        peer_sensor = peer_unique_id(f"{gateway.mac}-4-{zkey}{suffix}", str(runtime.mac), primary_mac)
        if peer_sensor is not None and reg.async_get_entity_id("sensor", DOMAIN, peer_sensor) is not None:
            return True
        resolved_dev_id = zone_dev_id or _resolve_climate_device_meta(zone, iface)[0]
        for dev_id in dict.fromkeys((zkey, resolved_dev_id)):
            peer_climate = peer_unique_id(f"{gateway.mac}-4-{dev_id}", str(runtime.mac), primary_mac)
            if peer_climate is not None and reg.async_get_entity_id("climate", DOMAIN, peer_climate) is not None:
                return True
        return False

    def _has_central_climate_config(zone: str, iface: str | None) -> bool:
        z_cfg = _climate_zone_config(configured_climate, zone, iface) or (
            _climate_zone_config(configured_climate, f"0{zone}", iface)
            if len(zone) == 1
            else {}
        )
        return bool(z_cfg.get(CONF_CENTRAL, False))

    def _is_climate_companion_excluded(
        zone: str, iface: str | None, *, has_live_climate: bool = False
    ) -> bool:
        zkey = _zone_key(zone, iface)
        if (
            ignored_addresses.is_ignored(4, zone, iface)
            or is_exposed_on_bus(hass, config_entry, 4, zkey)
            or _has_central_climate_config(zone, iface)
        ):
            return True
        pair = (zone, iface)
        if pair in thermo_switch_whole_zones:
            return True
        return pair in thermo_switch_actuator_zones and not has_live_climate

    def _build_climate_temp(
        zone: str,
        iface: str | None,
        *,
        device_id: str | None = None,
        name: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        initial_value: float | None = None,
        has_live_climate: bool = False,
        reg: er.EntityRegistry | None = None,
    ) -> MyHOMEClimateTemperatureSensor | None:
        pair = (zone, iface)
        if pair in known_climate_temp or pair in yaml_zone_temp_pairs:
            return None
        if _is_climate_companion_excluded(zone, iface, has_live_climate=has_live_climate):
            return None
        meta_dev_id, meta_name, meta_mfg, meta_model, _ = _resolve_climate_device_meta(zone, iface)
        zone_dev_id = device_id or meta_dev_id
        if _climate_companion_owned_by_primary(reg, zone, iface, zone_dev_id, "-temperature"):
            return None
        sensor = MyHOMEClimateTemperatureSensor(
            hass=hass,
            name=name or meta_name,
            device_id=zone_dev_id,
            zone=zone,
            interface=iface,
            manufacturer=manufacturer or meta_mfg,
            model=model or meta_model,
            gateway=gateway,
            initial_value=initial_value,
        )
        _apply_legacy_compat_name(sensor, reg)
        known_climate_temp[pair] = sensor
        sensor.async_on_remove(
            runtime.router.subscribe(
                "4", [f"climate_sensor_zone:{_zone_key(zone, iface)}"], sensor.handle_event
            )
        )
        return sensor

    def _build_climate_humidity(
        zone: str,
        iface: str | None,
        *,
        device_id: str | None = None,
        name: str | None = None,
        manufacturer: str | None = None,
        model: str | None = None,
        initial_value: float | None = None,
        has_live_climate: bool = False,
        reg: er.EntityRegistry | None = None,
    ) -> MyHOMEClimateHumiditySensor | None:
        pair = (zone, iface)
        if pair in known_climate_humidity:
            return None
        if _is_climate_companion_excluded(zone, iface, has_live_climate=has_live_climate):
            return None
        meta_dev_id, meta_name, meta_mfg, meta_model, _ = _resolve_climate_device_meta(zone, iface)
        zone_dev_id = device_id or meta_dev_id
        if _climate_companion_owned_by_primary(reg, zone, iface, zone_dev_id, "-humidity"):
            return None
        known_climate_humidity.add(pair)
        sensor = MyHOMEClimateHumiditySensor(
            hass=hass,
            name=name or meta_name,
            device_id=zone_dev_id,
            zone=zone,
            interface=iface,
            manufacturer=manufacturer or meta_mfg,
            model=model or meta_model,
            gateway=gateway,
            initial_value=initial_value,
        )
        sensor.async_on_remove(
            runtime.router.subscribe(
                "4", [f"climate_sensor_zone:{_zone_key(zone, iface)}"], sensor.handle_event
            )
        )
        return sensor

    live_climates = runtime.entities.get("climate", {})

    def _live_climate_seed(
        zone: str, iface: str | None
    ) -> tuple[float | None, float | None, bool]:
        clim_ent = live_climates.get(_zone_key(zone, iface))
        if clim_ent is None:
            return (None, None, False)
        init_temp = (
            _finite_float(getattr(clim_ent, "_attr_native_current_temperature", None))
            if not getattr(clim_ent, "_current_temperature_restored", True)
            else None
        )
        raw_hum = (
            _finite_float(getattr(clim_ent, "_attr_current_humidity", None))
            if getattr(clim_ent, "_humidity_reported", False) is True
            else None
        )
        init_hum = raw_hum if raw_hum is not None and 0.0 <= raw_hum <= 100.0 else None
        return (
            init_temp,
            init_hum,
            init_hum is not None or bool(getattr(clim_ent, "_humidity", False)),
        )

    startup_zones: dict[tuple[str, str | None], tuple[str | None, bool]] = {}
    for ent in reg_entries:
        if ent.domain != "climate" or not ent.unique_id:
            continue
        c_who, c_dev_id = parse_unique_id(ent.unique_id, gateway.mac, entry_mac)
        if c_who != "4":
            continue
        c_addr = Address.from_device_id(c_dev_id)
        if is_probe(c_addr.where) or is_actuator_where(c_addr.where):
            continue
        c_zone = canonical_thermo_zone(c_addr.where)
        if c_zone is None:
            continue
        c_pair = (c_zone, c_addr.interface)
        c_cfg = _climate_zone_config(configured_climate, c_zone, c_addr.interface)
        if not c_cfg and len(c_zone) == 1:
            c_cfg = _climate_zone_config(configured_climate, f"0{c_zone}", c_addr.interface)
        if c_cfg and bool(c_cfg.get(CONF_CENTRAL, False)):
            continue
        if c_pair in thermo_switch_whole_zones:
            continue
        if c_pair in thermo_switch_actuator_zones and not (c_cfg or has_thermostat_state(hass, ent.entity_id)):
            continue
        startup_zones[c_pair] = (
            c_dev_id,
            (bool(c_cfg.get(CONF_HUMIDITY_SUPPORT, False)) if c_cfg else False)
            or has_humidity_state(hass, ent.entity_id),
        )

    seen_climate_cfgs: set[int] = set()
    for cfg_id, cfg in configured_climate.items():
        if not isinstance(cfg, dict) or id(cfg) in seen_climate_cfgs:
            continue
        seen_climate_cfgs.add(id(cfg))
        if bool(cfg.get(CONF_CENTRAL, False)) or cfg.get(CONF_WHERE, cfg.get(CONF_ZONE)) is None:
            continue
        c_addr = Address.from_config(str(cfg_id), cfg)
        if is_probe(c_addr.where) or is_actuator_where(c_addr.where):
            continue
        c_zone = canonical_thermo_zone(c_addr.where)
        if c_zone is None:
            continue
        c_pair = (c_zone, c_addr.interface)
        prev_dev_id, prev_hum = startup_zones.get(c_pair, (None, False))
        startup_zones[c_pair] = (
            prev_dev_id,
            prev_hum or bool(cfg.get(CONF_HUMIDITY_SUPPORT, False)),
        )

    for zkey in live_climates:
        l_addr = Address.from_device_id(str(zkey))
        if (l_zone := canonical_thermo_zone(l_addr.where)) is not None:
            l_pair = (l_zone, l_addr.interface)
            prev_dev_id, prev_hum = startup_zones.get(l_pair, (None, False))
            startup_zones[l_pair] = (prev_dev_id, prev_hum)

    confirmed_climate_zones: set[tuple[str, str | None]] = set(startup_zones)

    restored_humidity_zones: set[tuple[str, str | None]] = set()
    for ent in reg_entries:
        if ent.domain != PLATFORM or not ent.unique_id:
            continue
        after_mac = ent.unique_id.replace(f"{gateway.mac}-", "", 1)
        if entry_mac and entry_mac != gateway.mac:
            after_mac = after_mac.replace(f"{entry_mac}-", "", 1)
        match = CLIMATE_HUMIDITY_UNIQUE_ID.fullmatch(after_mac)
        if match is None:
            continue
        h_zone, h_iface = match.groups()
        h_pair = (h_zone, h_iface)
        h_live = h_pair in confirmed_climate_zones
        if reg_obj is not None and (
            _is_climate_companion_excluded(h_zone, h_iface, has_live_climate=h_live)
            or _climate_companion_owned_by_primary(
                reg_obj, h_zone, h_iface, None, "-humidity"
            )
        ):
            prune_entity(hass, reg_obj, ent, config_entry.entry_id)
            continue
        restored_humidity_zones.add(h_pair)

    companion_sensors: list[Entity] = []
    for (s_zone, s_iface), (s_dev_id, wants_hum) in startup_zones.items():
        seed_temp, seed_hum, live_wants_hum = _live_climate_seed(s_zone, s_iface)
        if (temp := _build_climate_temp(
            s_zone,
            s_iface,
            device_id=s_dev_id,
            initial_value=seed_temp,
            has_live_climate=True,
            reg=reg_obj,
        )) is not None:
            companion_sensors.append(temp)
        if wants_hum or live_wants_hum or (s_zone, s_iface) in restored_humidity_zones:
            if (hum := _build_climate_humidity(
                s_zone,
                s_iface,
                device_id=s_dev_id,
                initial_value=seed_hum,
                has_live_climate=True,
                reg=reg_obj,
            )) is not None:
                companion_sensors.append(hum)

    def duplicate_temperature(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        if (
            ctx.source == "yaml"
            or _is_who4_temp_cfg(ctx.cfg)
            or is_configured(
                "4",
                ctx.address.where,
                SensorDeviceClass.TEMPERATURE,
                interface=ctx.address.interface,
            )
        ):
            return False
        if is_broadcast_where(ctx.address.clean_where):
            if reg_obj is not None and isinstance(entry, er.RegistryEntry):
                prune_entity(hass, reg_obj, entry, config_entry.entry_id)
            return True
        if not is_probe(ctx.address.clean_where):
            c_zone = canonical_thermo_zone(ctx.address.clean_where)
            if c_zone is not None:
                pair = (c_zone, ctx.address.interface)
                if pair not in yaml_zone_temp_pairs and (
                    _is_climate_companion_excluded(
                        c_zone,
                        ctx.address.interface,
                        has_live_climate=pair in confirmed_climate_zones,
                    )
                    or _climate_companion_owned_by_primary(
                        reg_obj,
                        c_zone,
                        ctx.address.interface,
                        None,
                        "-temperature",
                    )
                ):
                    if reg_obj is not None and isinstance(entry, er.RegistryEntry):
                        prune_entity(hass, reg_obj, entry, config_entry.entry_id)
                    return True
        return False

    common_args: dict[str, Any] = dict(
        hass=hass, config_entry=config_entry, async_add_entities=async_add_entities, platform=PLATFORM,
        route_keys=route_keys, one_per_address=False,
    )
    discovery_for["18"] = PlatformDiscovery(
        who="18", event_type=OWNEnergyEvent, build=build_energy, accept=yaml_class(SensorDeviceClass.POWER, SensorDeviceClass.ENERGY),
        registry_address=energy_registry_address, reject_registry_entry=duplicate_energy,
        address=energy_bus_address, known_keys=energy_known_keys, **common_args,
    )
    discovery_for["1"] = PlatformDiscovery(
        who="1", event_type=OWNLightingEvent, build=build_illuminance, accept=yaml_class(SensorDeviceClass.ILLUMINANCE),
        registry_address=class_registry_address("-illuminance", SensorDeviceClass.ILLUMINANCE),
        reject_registry_entry=duplicate_illuminance, address=illuminance_bus_address, known_keys=known_keys, **common_args,
    )
    discovery_for["4"] = PlatformDiscovery(
        who="4", event_type=OWNHeatingEvent, build=build_temperature, accept=accept_temperature,
        registry_address=class_registry_address("-temperature", SensorDeviceClass.TEMPERATURE),
        reject_registry_entry=duplicate_temperature,
        address=temperature_bus_address, known_keys=known_keys, **common_args,
    )
    discovery_for["4_actuator"] = PlatformDiscovery(
        hass=hass,
        config_entry=config_entry,
        async_add_entities=async_add_entities,
        platform=PLATFORM,
        who="4",
        event_type=OWNHeatingEvent,
        build=build_actuator_output,
        accept=accept_actuator_output,
        registry_address=actuator_output_registry_address,
        reject_registry_entry=duplicate_actuator_output,
        address=actuator_output_bus_address,
        known_keys=actuator_output_known_keys,
        route_keys=actuator_output_route_keys,
        one_per_address=False,
    )

    sensors: list[Entity] = list(companion_sensors)
    for discovery in discovery_for.values():
        sensors.extend(discovery.start(listen=False, add=False))
    if sensors:
        async_add_entities(sensors)

    @callback
    def _async_new_climate_zone(info: dict[str, Any]) -> None:
        zone = canonical_thermo_zone(str(info.get("zone", "")))
        if zone is None:
            return
        raw_iface = info.get("interface")
        iface = str(raw_iface) if isinstance(raw_iface, str) and raw_iface else None
        raw_dev_id = info.get("device_id")
        zone_dev_id = str(raw_dev_id) if isinstance(raw_dev_id, str) and raw_dev_id else None
        raw_name = info.get("name")
        dev_name = str(raw_name) if isinstance(raw_name, str) and raw_name else None
        raw_mfg = info.get("manufacturer")
        mfg = str(raw_mfg) if isinstance(raw_mfg, str) and raw_mfg else None
        raw_model = info.get("model")
        model = str(raw_model) if isinstance(raw_model, str) and raw_model else None
        cur_reg, _ = _safe_registry_entries()

        added: list[Entity] = []
        init_temp = _finite_float(info.get("temperature"))
        if (temp := _build_climate_temp(
            zone,
            iface,
            device_id=zone_dev_id,
            name=dev_name,
            manufacturer=mfg,
            model=model,
            initial_value=init_temp,
            has_live_climate=True,
            reg=cur_reg,
        )) is not None:
            added.append(temp)
        elif init_temp is not None:
            existing_temp = known_climate_temp.get((zone, iface))
            if existing_temp is not None and not existing_temp._bus_state_complete:
                existing_temp._bus_state_complete = True
                if existing_temp._attr_native_value != init_temp:
                    existing_temp._attr_native_value = init_temp
                    existing_temp._publish_state()

        init_hum = _finite_float(info.get("current_humidity"))
        if init_hum is not None and not (0.0 <= init_hum <= 100.0):
            init_hum = None
        if bool(info.get("humidity")) or init_hum is not None or (zone, iface) in restored_humidity_zones:
            if (hum := _build_climate_humidity(
                zone,
                iface,
                device_id=zone_dev_id,
                name=dev_name,
                manufacturer=mfg,
                model=model,
                initial_value=init_hum,
                has_live_climate=True,
                reg=cur_reg,
            )) is not None:
                added.append(hum)
        if added:
            async_add_entities(added)

    @callback
    def _async_climate_humidity(info: dict[str, Any]) -> None:
        zone = canonical_thermo_zone(str(info.get("zone", "")))
        if zone is None:
            return
        init_hum = _finite_float(info.get("current_humidity"))
        if init_hum is None or not (0.0 <= init_hum <= 100.0):
            return
        raw_iface = info.get("interface")
        iface = str(raw_iface) if isinstance(raw_iface, str) and raw_iface else None
        raw_dev_id = info.get("device_id")
        zone_dev_id = str(raw_dev_id) if isinstance(raw_dev_id, str) and raw_dev_id else None
        raw_name = info.get("name")
        dev_name = str(raw_name) if isinstance(raw_name, str) and raw_name else None
        raw_mfg = info.get("manufacturer")
        mfg = str(raw_mfg) if isinstance(raw_mfg, str) and raw_mfg else None
        raw_model = info.get("model")
        model = str(raw_model) if isinstance(raw_model, str) and raw_model else None
        cur_reg, _ = _safe_registry_entries()

        if (hum := _build_climate_humidity(
            zone,
            iface,
            device_id=zone_dev_id,
            name=dev_name,
            manufacturer=mfg,
            model=model,
            initial_value=init_hum,
            has_live_climate=True,
            reg=cur_reg,
        )) is not None:
            async_add_entities([hum])

    @callback
    def handle_message(message: Any) -> None:
        if not isinstance(message, (OWNEnergyEvent, OWNHeatingEvent, OWNLightingEvent)):
            return
        mtype = getattr(message, "message_type", None)
        dim = getattr(message, "dimension", None)
        if (
            mtype is None
            and not (isinstance(message, OWNLightingEvent) and dim == 6)
            and not (isinstance(message, OWNHeatingEvent) and dim in (0, 15, 60))
        ):
            return
        for discovery in discovery_for.values():
            discovery.handle_message(message)
        if (
            isinstance(message, OWNHeatingEvent)
            and getattr(message, "is_translation", None) is not True
            and (
                mtype in (MESSAGE_TYPE_MAIN_TEMPERATURE, MESSAGE_TYPE_MAIN_HUMIDITY)
                or (mtype is None and dim in (0, 60))
            )
        ):
            target = thermo_zone_sensor_target(message)
            if target is not None:
                zone, iface = target
                runtime.router.publish("4", [f"climate_sensor_zone:{_zone_key(zone, iface)}"], message)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, f"myhome_message_{gateway.mac}", handle_message)
    )
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_NEW_CLIMATE_ZONE.format(gateway.mac), _async_new_climate_zone
        )
    )
    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass, SIGNAL_CLIMATE_HUMIDITY.format(gateway.mac), _async_climate_humidity
        )
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


def _temp_entries(
    registry: er.EntityRegistry, entry_id: str, mac: str, entry_mac: str, marker: str
) -> list[tuple[er.RegistryEntry, str]]:
    """Return ``(entry, after_mac_without_marker)`` for temperature sensors of ``entry_id``."""
    return [
        (entry, entry.unique_id[len(prefix) : -len(marker)])
        for entry in er.async_entries_for_config_entry(registry, entry_id)
        if entry.domain == PLATFORM
        and entry.unique_id.endswith(marker)
        and (
            prefix := next(
                (f"{m}-" for m in (mac, entry_mac) if entry.unique_id.startswith(f"{m}-")),
                None,
            )
        )
        is not None
    ]


def _apply_temp_unique_id_migration(
    registry: er.EntityRegistry,
    entry: er.RegistryEntry,
    target: str,
    *,
    carry_customisations: bool = False,
) -> None:
    """Move ``entry`` to ``target`` unique id, or remove it when ``target`` already exists."""
    canonical = registry.async_get_entity_id(PLATFORM, DOMAIN, target)
    try:
        if canonical is not None:
            if carry_customisations:
                _carry_entity_customisations(registry, canonical, entry)
            registry.async_remove(entry.entity_id)
            LOGGER.info(
                "Removed duplicate temperature sensor %s in favor of %s",
                entry.entity_id,
                canonical,
            )
        else:
            registry.async_update_entity(entry.entity_id, new_unique_id=target)
            LOGGER.info("Migrated temperature sensor %s to unique id %s", entry.entity_id, target)
    except ValueError as err:
        LOGGER.warning("Could not migrate temperature sensor %s: %s", entry.entity_id, err)


def _migrate_temperature_unique_ids(
    hass: HomeAssistant,
    entry_id: str,
    mac: str,
    entry_mac: str,
    configured_sensors: dict[str, Any] | None = None,
) -> None:
    """Restored and discovered probes were ``<mac>-<where>-temperature``, a myhome.yaml
    probe ``<mac>-4-<where>-temperature`` (#441). Move the first form to the second; when
    both exist, the WHO-less one is the duplicate (``sensor.<name>_2``) and goes.

    Then canonicalize unconfigured legacy WHO=4 zone ids (``4-01-temperature``,
    ``4-#1-temperature``, with optional ``#4#II``) to ``4-<zone>[#4#<iface>]-temperature``
    so removing a zero-padded ``myhome.yaml`` zone sensor preserves entity_id and LTS (#981).
    """
    marker = f"-{SensorDeviceClass.TEMPERATURE}"
    try:
        registry = er.async_get(hass)
        pass1 = _temp_entries(registry, entry_id, mac, entry_mac, marker)
    except Exception:  # registry not loaded in some harnesses
        return
    for entry, where in pass1:
        raw_where = where.removeprefix("4-")
        canon_where = _canonical_temperature_where(raw_where)
        if not raw_where or "-" in raw_where or (where.startswith("4-") and canon_where == raw_where):
            continue
        _apply_temp_unique_id_migration(registry, entry, f"{mac}-4-{canon_where}{marker}")

    active_yaml_tails: set[str] = {
        Address.from_config(str(cfg_id), cfg).clean_key
        for cfg_id, cfg in (configured_sensors or {}).items()
        if isinstance(cfg, dict)
        and str(cfg.get(CONF_WHO)) == "4"
        and CONF_WHERE in cfg
        and (cfg.get(CONF_DEVICE_CLASS) or cfg.get("device_class")) == SensorDeviceClass.TEMPERATURE
    }
    for entry, raw in _temp_entries(registry, entry_id, mac, entry_mac, marker):
        tail = raw.removeprefix("4-")
        if (
            tail == raw
            or tail in active_yaml_tails
            or (match := LEGACY_ZONE_TEMP_TAIL.fullmatch(tail)) is None
        ):
            continue
        canon_zone = cast(str, match.group(1) or match.group(2))
        routing = f"{BUS_ROUTING}{int(match.group(3)):02d}" if match.group(3) is not None else ""
        _apply_temp_unique_id_migration(
            registry,
            entry,
            f"{mac}-4-{canon_zone}{routing}{marker}",
            carry_customisations=True,
        )


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    runtime = config_entry.runtime_data

    if PLATFORM not in runtime.platforms:
        return True

    _configured_sensors = runtime.platforms[PLATFORM]

    for _sensor in list(_configured_sensors.keys()):
        del runtime.platforms[PLATFORM][
            _sensor
        ]
    return True


class MyHOMESensorEntity(MyHOMEEntity, RestoreSensor):
    """Base class for MyHOME sensors restoring native values via RestoreSensor."""

    _integral_native_value: bool = False

    def _is_numeric_sensor(self) -> bool:
        """Return True when Home Assistant expects a finite numeric native_value."""
        device_class = try_parse_enum(SensorDeviceClass, self.device_class)
        if device_class in NON_NUMERIC_DEVICE_CLASSES:
            return False
        if (
            self.state_class is not None
            or self.native_unit_of_measurement is not None
            or self.suggested_display_precision is not None
        ):
            return True
        return device_class is not None

    @staticmethod
    def _finite_int(value: int) -> int | None:
        """Return ``int(value)`` when representable as a finite float, else ``None``."""
        try:
            math.isfinite(value)
        except OverflowError:
            return None
        return int(value)

    def _coerce_native_value(
        self, value: Any
    ) -> int | float | str | date | datetime | None:
        """Validate and normalize a restored value already in native units."""
        if isinstance(value, bool):
            return None
        device_class = try_parse_enum(SensorDeviceClass, self.device_class)
        if device_class in (SensorDeviceClass.TIMESTAMP, SensorDeviceClass.UPTIME):
            dt_val = (
                dt_util.parse_datetime(value)
                if isinstance(value, str)
                else value if isinstance(value, datetime) else None
            )
            return dt_val if dt_val is not None and dt_val.tzinfo is not None else None
        if device_class is SensorDeviceClass.DATE:
            if isinstance(value, str):
                return dt_util.parse_date(value)
            return (
                value
                if isinstance(value, date) and not isinstance(value, datetime)
                else None
            )
        if self.options is not None or device_class is SensorDeviceClass.ENUM:
            if device_class is not SensorDeviceClass.ENUM or not isinstance(value, str):
                return None
            if self.options and value not in self.options:
                return None
            return value
        if isinstance(value, int):
            return self._finite_int(value)
        if isinstance(value, Decimal):
            if not value.is_finite():
                return None
            numeric = float(value)
            if not math.isfinite(numeric):
                return None
            exp = value.as_tuple().exponent
            return int(value) if isinstance(exp, int) and exp >= 0 else numeric
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, str):
            if self._is_numeric_sensor():
                stripped = value.strip()
                try:
                    if re.fullmatch(r"[+-]?[0-9]+", stripped):
                        return self._finite_int(int(stripped))
                    numeric = float(stripped)
                except ValueError:
                    return None
                return numeric if math.isfinite(numeric) else None
            return value
        return None

    def _convert_to_native_unit(
        self, value: Any, from_unit: str, to_unit: str
    ) -> int | float | None:
        """Convert a finite numeric value from ``from_unit`` to ``to_unit``."""
        if isinstance(value, bool) or not isinstance(
            value, (int, float, Decimal, str)
        ):
            return None
        try:
            numeric = float(value)
        except (ValueError, TypeError, OverflowError):
            return None
        if not math.isfinite(numeric):
            return None
        device_class = try_parse_enum(SensorDeviceClass, self.device_class)
        converter = (
            UNIT_CONVERTERS.get(device_class) if device_class is not None else None
        )
        if (
            converter is None
            or from_unit not in converter.VALID_UNITS
            or to_unit not in converter.VALID_UNITS
        ):
            return None
        converted = converter.convert(numeric, from_unit, to_unit)
        if not math.isfinite(converted):
            return None
        if self._integral_native_value:
            rounded = round(converted)
            if math.isclose(converted, rounded, rel_tol=0.0, abs_tol=1e-6):
                return int(rounded)
        return converted

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore native sensor value without conflating display and native units."""
        if self._attr_native_value is not None:
            return
        try:
            sensor_data = await self.async_get_last_sensor_data()
        except Exception:
            sensor_data = None

        native_unit = self.native_unit_of_measurement
        if sensor_data is not None:
            raw_val = sensor_data.native_value
            if raw_val is None:
                return
            stored_unit = sensor_data.native_unit_of_measurement
            if stored_unit == native_unit:
                coerced = self._coerce_native_value(raw_val)
                if coerced is not None:
                    self._attr_native_value = coerced
                return
            if stored_unit is None or native_unit is None:
                return
            converted = self._convert_to_native_unit(
                raw_val, stored_unit, native_unit
            )
            if converted is not None:
                self._attr_native_value = converted
            return

        # Migration fallback when extra_data is absent (e.g. first restart after
        # upgrading from RestoreEntity): last_state.state is serialized in display units.
        if last_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        raw_state_unit = last_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        state_unit = str(raw_state_unit) if raw_state_unit is not None else None
        if state_unit == native_unit or (
            state_unit is None
            and self.unit_of_measurement in (None, native_unit)
        ):
            coerced = self._coerce_native_value(last_state.state)
            if coerced is not None:
                self._attr_native_value = coerced
            return
        if state_unit is None or native_unit is None:
            return

        converted = self._convert_to_native_unit(
            last_state.state, state_unit, native_unit
        )
        if converted is not None:
            self._attr_native_value = converted


class MyHOMEPowerSensor(MyHOMESensorEntity):
    _name_from_device_class = True
    _integral_native_value = True

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: SensorDeviceClass,
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
        self._attr_should_poll = True
        self._streaming_until: float = 0.0

        self._attr_native_value = None
        self._attr_extra_state_attributes = {
            "Sensor": f"({self._where[0]}){self._where[1:]}"
        }

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_entity_ref(str(self._attr_device_class))
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref(str(self._attr_device_class))

    def _is_streaming_active(self) -> bool:
        """Return True if automatic instant power streaming is active."""
        return time.monotonic() < self._streaming_until

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service or periodic polling.
        """
        if self._is_streaming_active():
            return
        where = (
            f"{self._where}#0"
            if str(self._where).startswith("7") and not str(self._where).endswith("#0")
            else str(self._where)
        )
        builder = getattr(OWNEnergyCommand, "get_instant_power", None) or getattr(
            OWNEnergyCommand, "request_active_power", None
        )
        if builder is not None:
            cmd = builder(where)
        else:
            cmd = OWNCommand.parse(f"*#18*{where}*113##")
        if cmd is not None:
            await self._gateway_handler.send_status_request(cmd)

    @callback
    def handle_event(self, message: OWNEnergyEvent) -> None:
        """Handle an event message."""
        msg_type = getattr(message, "message_type", None)
        dim = getattr(message, "dimension", None)

        if msg_type in (MESSAGE_TYPE_AUTO_UPDATE_INTERVAL, "auto_update_interval") or dim == 1200:
            interval = getattr(message, "update_interval", None)
            if interval is None:
                dim_vals = getattr(
                    message,
                    "dimension_values",
                    getattr(message, "dimension_value", []),
                )
                try:
                    interval = int(dim_vals[0]) if dim_vals else 0
                except (ValueError, IndexError):
                    interval = 0

            LOGGER.debug(
                "%s %s (interval=%s)",
                self._gateway_handler.log_id,
                message.human_readable_log,
                interval,
            )
            if interval > 0:
                self._streaming_until = time.monotonic() + (interval * 60)
            else:
                self._streaming_until = 0.0
            return None

        if msg_type not in [MESSAGE_TYPE_ACTIVE_POWER]:
            return True  # type: ignore

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_native_value = message.active_power
        self._publish_state()
        return None

    async def start_sending_instant_power(self, duration: int = 65) -> None:
        """Request automatic instant power."""
        if duration > 0:
            self._streaming_until = time.monotonic() + (duration * 60)
        else:
            self._streaming_until = 0.0
        builder = getattr(OWNEnergyCommand, "start_sending_instant_power", None)
        if builder is not None:
            cmd = builder(self._where, duration)
        else:
            where = (
                f"{self._where}#0"
                if str(self._where).startswith("7") and not str(self._where).endswith("#0")
                else str(self._where)
            )
            cmd = OWNCommand.parse(f"*#18*{where}*#1200#1*{duration}##")
        if cmd is not None:
            await self._gateway_handler.send(cmd)

    async def async_stop_sending_instant_power(self) -> None:
        """Stop automatic instant power streaming."""
        builder = getattr(OWNEnergyCommand, "stop_sending_instant_power", None)
        if builder is not None:
            self._streaming_until = 0.0
            cmd = builder(self._where)
            if cmd is not None:
                await self._gateway_handler.send(cmd)
                return
        await self.start_sending_instant_power(0)

    async def stop_sending_instant_power(self) -> None:
        """Alias for async_stop_sending_instant_power."""
        await self.async_stop_sending_instant_power()


class MyHOMEEnergySensor(MyHOMESensorEntity):
    _name_from_device_class = True
    _integral_native_value = True

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        who: str,
        where: str,
        entity_specific_id: str,
        device_class: SensorDeviceClass,
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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_entity_ref(self._entity_specific_id)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref(self._entity_specific_id)

    async def async_update(self) -> None:
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
    def handle_event(self, message: OWNEnergyEvent) -> None:
        """Handle an event message."""
        if message.message_type not in [
            MESSAGE_TYPE_ENERGY_TOTALIZER,
            MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
            MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
        ]:
            return True  # type: ignore

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
        return None


class MyHOMETemperatureSensor(MyHOMESensorEntity):
    _name_from_device_class = True

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: SensorDeviceClass,
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
        # Readings received from the bus, to tell whether one arrived while a fallback read was out.
        self._pushes = 0
        # Set when the gateway refused the fallback read of a probe: it is not sent again (until
        # the entry is reloaded, or the gateway comes back from an outage). The readings the
        # probe sends by itself are all it has then.
        self._fallback_read_refused = False
        # The gateway's event-session count when the refusal came: a reconnect asks once more.
        self._fallback_refused_on_connection: int | None = None
        # The gateway that refused it (the one that served the read): another gateway is asked once.
        self._fallback_refused_by: Any = None

    @property
    def _is_probe(self) -> bool:
        """Return True for slave/external probe addresses (ZPP >= 100)."""
        return is_probe(str(self._where).split("#")[0])

    def _read_gateway(self) -> Any:
        """The gateway that serves this sensor's read: a delegated secondary or the warm standby, else its own."""
        who4_target = getattr(self._gateway_handler, "_who4_target", None)
        return who4_target() if callable(who4_target) else self._gateway_handler

    @property
    def _interface_poll_refused(self) -> bool:
        """Return True when ``_where`` carries ``#4#II`` and the serving gateway refuses routed WHO=4 polls."""
        return (
            BUS_ROUTING in str(self._where)
            and getattr(self._read_gateway(), "interface_zone_request_refused", False) is True
        )

    def _fallback_refusal_stands(self) -> bool:
        """Whether the gateway that would serve the read now is the one, on the connection, that refused it.

        It also stands while no gateway can serve the read: an outage is no takeover.
        """
        target = self._read_gateway()
        if getattr(target, "is_connected", False) is not True:
            return True
        if target is not self._fallback_refused_by:
            return False
        connections = getattr(target, "event_connections", None)
        return not isinstance(connections, int) or connections == self._fallback_refused_on_connection

    def _fallback_read(self) -> OWNHeatingCommand:
        """The read of this sensor: dimension 0, except for an external probe (``N00``).

        A slave probe ``PZZ`` is read like a zone: "Slave probe temperature request: where =
        [1-8]+[01-99]", ``*#4*where*0##``, example ``*#4*301*0##`` (WHO 4 PDF, 1.5.1). Probe 303
        of a physical plant, 2026-11-18, asked directly on a command session: ``*#4*303*0##``
        was answered with ``*#4*303*0*0233##`` and an ACK after 0.09 s by an F455 1.0.76 and
        after 0.14 s by an F461 2.0.11; ``*#4*303*15##`` was NACKed with no frame after 8.9 s
        (F455) and 2.1 s (F461). Probe 105 behind an MH200N 1.1.8 and an MH201 3.3.6 reports on
        dimension 0 too (``*#4*105*0*0296##``, issue_466); ``*#4*105*15##`` sent through that
        MH200N was followed by no frame of the probe.

        An external probe ``N00`` keeps the dimension 15 read of #308: its reports are dimension
        15 frames (``*#4*100*15*1*0253*0001##``, a 3455 behind an L4577 on an F454), no capture
        shows it answering a dimension 0 read, and that read is rationed by the latch below.
        """
        if is_external_probe(str(self._where).split("#")[0]):
            build = getattr(OWNHeatingCommand, "get_probe_temperature", None)  # absent in old OWNd
            if build is not None:
                return cast(OWNHeatingCommand, build(self._where))
        return OWNHeatingCommand.get_temperature(self._where)

    def _push_is_fresh(self) -> bool:
        """Return True when a reading arrived within the last poll interval (a probe: ``PROBE_PUSH_FRESH``)."""
        window = PROBE_PUSH_FRESH if self._is_probe else SCAN_INTERVAL.total_seconds()
        return self._last_push_at is not None and (time.monotonic() - self._last_push_at) < window

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_entity_ref(str(self._attr_device_class))
        # Probes start receive-only: no initial poll, the push stream fills in
        # and the periodic update only polls if it stays silent (issue #308).
        self._poll_on_add = (
            self._poll_on_add and not self._is_probe and not self._interface_poll_refused
        )
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref(str(self._attr_device_class))

    @callback
    def _handle_availability_update(self) -> None:
        """Ask a probe once more after the gateway came back from an outage.

        The refusal was the answer of the gateway before the outage (a restart of the gateway, a
        power cut on the plant, another gateway taking over). One refused read per outage is the
        cost; without it one command timeout latched the probe until the entry was reloaded.
        """
        target = self._read_gateway()
        if (
            self._fallback_read_refused
            and getattr(target, "is_connected", False) is True
            # A gateway that comes back does so on a new event session, and a gateway that takes
            # over is another gateway. This signal is also sent for a secondary of another WHO and
            # for the standby: it ends nothing while the gateway that refused serves the read on
            # the session it refused it on.
            and not self._fallback_refusal_stands()
        ):
            self._fallback_read_refused = False
            LOGGER.debug(
                "%s %s: the gateway is available again; probe %s is asked once more when it stays silent",
                self._gateway_handler.log_id,
                self.entity_id,
                self._where,
            )
        super()._handle_availability_update()

    async def async_update(self) -> None:
        """Poll the probe, unless the bus already pushed a fresh reading."""
        if self._push_is_fresh():
            return
        if self._is_probe and self._fallback_read_refused:
            # Asked once more by another gateway than the one that refused (the standby taking over, the
            # primary coming back), and after a reconnect of the event session of the one that refused
            # (also one too short to make the gateway unavailable).
            if self._fallback_refusal_stands():
                return
            self._fallback_read_refused = False
        if self._interface_poll_refused:
            # Behind an interface where the serving gateway does not route WHO 4, a probe has only its
            # own reports, and the read of a zone would be answered for the local zone of that number.
            return
        cmd = self._fallback_read()
        pushes_before = self._pushes
        served_by = self._read_gateway()
        asked_on = getattr(served_by, "event_connections", None)
        written = await self._gateway_handler.send_status_request(cmd)
        if self._is_probe and isinstance(written, asyncio.Future):
            # Not awaited here: a refusal can take 9 s (above), and the update must not wait for it.
            written.add_done_callback(
                lambda future: self._fallback_read_done(future, pushes_before, served_by, asked_on)
            )

    def _fallback_read_done(
        self, written: asyncio.Future[Any], pushes_before: int, served_by: Any = None, asked_on: Any = None
    ) -> None:
        """Stop the fallback read of a probe once the gateway refused it.

        Refused: the gateway answered the written read with a NACK (``_gateway_nack``), or with
        nothing before the command timeout (``_command_timeout``), while the gateway that served it was connected. The
        sender sets these marks. Not a refusal: every other cancelled read (the command session
        could not be opened or lost its socket, the read was dropped because it collected another
        zone's frame (#886), the queue was shut down), a read cancelled with the connection, and
        a read during which the probe reported (the gateway then ACKs it).
        """
        if not written.cancelled():
            return
        if not (
            getattr(written, "_gateway_nack", False) is True
            or getattr(written, "_command_timeout", False) is True
        ):
            return
        gateway = served_by if served_by is not None else self._gateway_handler
        if getattr(gateway, "is_connected", False) is not True:
            return
        connections = getattr(gateway, "event_connections", None)
        # The session this gateway's sender wrote the read under. A read that carries no such mark
        # (a double) is judged by the session at the time it was queued.
        written_by, written_on = getattr(written, "_written_under", None) or (None, None)
        if written_by is gateway and isinstance(written_on, int):
            asked_on = written_on
        if isinstance(asked_on, int) and connections != asked_on:
            # The connection that was asked is gone: this says nothing about the new one.
            return
        if gateway is not self._read_gateway():
            # Another gateway serves the read by now: a late answer of this one does not stop it.
            return
        if self._pushes != pushes_before:
            return
        self._fallback_read_refused = True
        self._fallback_refused_by = gateway
        self._fallback_refused_on_connection = connections
        LOGGER.debug(
            "%s %s: the gateway refused the read of probe %s; it is not asked again, its own reports are used",
            self._gateway_handler.log_id,
            self.entity_id,
            self._where,
        )

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Handle an event message."""
        if _probe_frame_without_reading(message):
            # Not a reading and not a report: it changes neither the value nor the fallback window.
            return None
        val = None
        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            val = message.main_temperature
        elif message.message_type == MESSAGE_TYPE_SECONDARY_TEMPERATURE:
            sec = getattr(message, "secondary_temperature", None)
            probe_temp = getattr(message, "probe_temperature", None)
            if isinstance(sec, (list, tuple)) and len(sec) > 1:
                val = sec[1]
            elif isinstance(sec, (int, float)) and not isinstance(sec, bool):
                val = sec
            elif isinstance(probe_temp, (int, float)) and not isinstance(probe_temp, bool):
                val = probe_temp
        elif getattr(message, "dimension", None) == 15:
            dim_val = getattr(message, "dimension_value", None)
            if dim_val:
                raw = dim_val[1] if len(dim_val) >= 2 else dim_val[0]
                val = who4_temperature(str(raw))
        elif getattr(message, "dimension", None) == 0:
            dim_val = getattr(message, "dimension_value", None)
            if dim_val:
                raw = dim_val[0]
                val = who4_temperature(str(raw))
        else:
            return True  # type: ignore

        if val is not None:
            if hasattr(message, "human_readable_log") and message.human_readable_log:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
            self._attr_native_value = val
            self._last_push_at = time.monotonic()
            self._pushes += 1
            self._publish_state()
        return None


class MyHOMEIlluminanceSensor(MyHOMESensorEntity):
    _name_from_device_class = True

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        who: str,
        where: str,
        device_class: SensorDeviceClass,
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

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        self._register_entity_ref(str(self._attr_device_class))
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """When entity is removed from hass."""
        self._unregister_entity_ref(str(self._attr_device_class))

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        await self._gateway_handler.send_status_request(
            OWNLightingCommand.get_illuminance(self._where)
        )

    @callback
    def handle_event(self, message: OWNLightingEvent) -> None:
        """Handle an event message."""
        if (
            getattr(message, "message_type", None) != MESSAGE_TYPE_ILLUMINANCE
            and getattr(message, "dimension", None) != 6
            and not isinstance(getattr(message, "illuminance", None), (int, float))
        ):
            return True  # type: ignore

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        self._attr_native_value = message.illuminance
        self._publish_state()
        return None


class MyHOMEActuatorOutputSensor(MyHOMEEntity, SensorEntity):
    """Percentage output level (0-100 %) for a WHO=4 0-10 V proportional actuator (#949)."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        where: str,
        zone: str,
        actuator: str,
        interface: str | None,
        standalone: bool | None,
        manufacturer: str | None,
        model: str | None,
        gateway: MyHOMEGatewayHandler,
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
            translation_key="actuator_output_level",
        )
        self._zone = zone
        self._actuator = actuator
        self._interface = interface
        self._standalone_config = standalone
        self._full_where = (
            f"{self._where}{BUS_ROUTING}{self._interface}"
            if self._interface is not None
            else self._where
        )
        self._attr_device_class = None
        self._attr_unique_id = f"{gateway.mac}-4-{self._full_where}-output-level"
        self._attr_translation_placeholders = {"actuator": actuator}
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_suggested_display_precision = 0
        self._attr_should_poll = False
        self._attr_native_value = None
        attrs: dict[str, Any] = {
            "zone": int(self._zone),
            "actuator": int(self._actuator),
        }
        if self._interface is not None:
            attrs["Int"] = self._interface
        self._attr_extra_state_attributes = attrs
        who4_target = getattr(gateway, "_who4_target", lambda: gateway)()
        interface_refused = (
            self._interface is not None
            and getattr(who4_target, "interface_zone_request_refused", False) is True
        )
        self._poll_on_add = not interface_refused and not callable(
            getattr(gateway, "watch_zone_actuators", None)
        )

    @property
    def _standalone(self) -> bool:
        """Whether zone mode follows standalone ``Z`` addressing rather than a central unit."""
        if self._standalone_config is not None:
            return self._standalone_config
        if getattr(self._gateway_handler, "has_central_unit", False) is not True:
            return True
        # A zone the climate entity found not to be run by the central unit (by a setpoint
        # only the direct form changed, or by its season): the central unit's off is not its.
        detected = getattr(self._gateway_handler, "standalone_detected_zones", None)
        return isinstance(detected, set) and (self._zone, self._interface) in detected

    @callback
    def _handle_central_mode_update(self, master_mode: HVACMode) -> None:
        """Reset output level to 0 % when the central unit turns subordinate zones OFF."""
        if master_mode != HVACMode.OFF or self._standalone:
            return
        if self._attr_native_value != 0:
            self._attr_native_value = 0
            self._publish_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to central mode OFF updates and Dimension 20 actuator watches."""
        target_hass = self.hass or self._hass
        if target_hass is not None and self._standalone_config is not True:
            self.async_on_remove(
                async_dispatcher_connect(
                    target_hass,
                    central_mode_signal(self._gateway_handler.mac, self._interface),
                    self._handle_central_mode_update,
                )
            )
        await super().async_added_to_hass()
        who4_target = getattr(
            self._gateway_handler, "_who4_target", lambda: self._gateway_handler
        )()
        interface_refused = (
            self._interface is not None
            and getattr(who4_target, "interface_zone_request_refused", False) is True
        )
        watch = getattr(self._gateway_handler, "watch_zone_actuators", None)
        if callable(watch) and not interface_refused:
            self.async_on_remove(watch(self._full_where))

    async def async_update(self) -> None:
        """Request Dimension 20 status for this actuator on gateways that forward it."""
        target = getattr(
            self._gateway_handler, "_who4_target", lambda: self._gateway_handler
        )()
        # Decided by the form of the read: this actuator (``Z#N``), not the zone (as the thermo
        # switch does); a gateway double without the method keeps the zone-level check.
        read_refused = getattr(self._gateway_handler, "actuator_read_refused", None)
        refused = read_refused(self._where) if callable(read_refused) else None
        if not isinstance(refused, bool):
            refused = getattr(target, "actuator_request_refused", False) is True
        if refused:
            return
        if (
            self._interface is not None
            and getattr(target, "interface_zone_request_refused", False) is True
        ):
            return
        await self._gateway_handler.send_status_request(
            OWNHeatingCommand.actuators_status(self._full_where)
        )

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Update output percentage from WHO=4 Dimension 20 events and zone resets."""
        if is_central_unit_report(message):
            self._gateway_handler.has_central_unit = True
        if thermo_event_interface(message) != self._interface:
            return
        if thermo_event_zone(message) != self._zone:
            return

        new_val: int | None = None
        dimension = getattr(message, "dimension", None)
        if dimension == 20:
            target = thermo_dim20_actuator_target(message)
            if target == (self._zone, self._actuator, self._interface):
                lvl = thermo_dim20_level(message)
                if lvl is not None:
                    new_val = lvl
                else:
                    status = thermo_dim20_status(message)
                    if status is not None and not status[0]:
                        new_val = 0
        elif dimension == 19:
            dim19 = thermo_dim19_status(message)
            if dim19 is not None and not dim19[2]:
                new_val = 0
        elif message.message_type in (MESSAGE_TYPE_MODE, MESSAGE_TYPE_MODE_TARGET):
            if self._standalone and is_central_thermo_where(message):
                return
            if getattr(message, "mode", None) == CLIMATE_MODE_OFF:
                new_val = 0
        elif message.message_type == MESSAGE_TYPE_ZONE_STATE:
            if getattr(message, "zone_state", None) in ("off", "protection"):
                new_val = 0

        if new_val is None:
            return

        if hasattr(message, "human_readable_log") and message.human_readable_log:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
        self._bus_state_complete = True
        if self._attr_native_value != new_val:
            self._attr_native_value = new_val
            self._publish_state()


class _MyHOMEClimateZoneSensor(MyHOMESensorEntity):
    """Base class for passive companion sensors attached to a WHO=4 climate zone (#981)."""

    _name_from_device_class = True
    _unique_id_suffix: str

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        zone: str,
        interface: str | None,
        manufacturer: str | None,
        model: str | None,
        gateway: MyHOMEGatewayHandler,
        device_class: SensorDeviceClass,
        native_unit: str,
        initial_value: float | None = None,
    ) -> None:
        super().__init__(
            hass=hass,
            name=name,
            platform=PLATFORM,
            device_id=device_id,
            who="4",
            where=zone,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
        )
        self._zone = zone
        self._interface = interface
        self._zone_key = (
            f"{self._zone}{BUS_ROUTING}{self._interface}"
            if self._interface is not None
            else self._zone
        )
        self._full_where = self._zone_key
        self._attr_device_class = device_class
        self._attr_unique_id = f"{gateway.mac}-4-{self._zone_key}{self._unique_id_suffix}"
        self._attr_native_unit_of_measurement = native_unit
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_entity_registry_enabled_default = True
        self._attr_should_poll = False
        self._poll_on_add = False
        self._attr_native_value = _finite_float(initial_value)
        attrs: dict[str, Any] = {"zone": int(self._zone)}
        if self._interface is not None:
            attrs["Int"] = self._interface
        self._attr_extra_state_attributes = attrs

    async def async_update(self) -> None:
        """Strictly passive: readings arrive via climate zone bus frames."""
        return

    def _matches_zone(self, message: OWNHeatingEvent) -> bool:
        """Return True when ``message`` targets this sensor's zone and F422 interface."""
        return thermo_zone_sensor_target(message) == (self._zone, self._interface)

    def _apply_reading(self, message: OWNHeatingEvent, value: float | None) -> None:
        """Publish a finite reading when it changes."""
        if value is None or not math.isfinite(value):
            return
        if hasattr(message, "human_readable_log") and message.human_readable_log:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
        self._bus_state_complete = True
        if self._attr_native_value != value:
            self._attr_native_value = value
            self._publish_state()


class MyHOMEClimateTemperatureSensor(_MyHOMEClimateZoneSensor):
    """Passive temperature sensor attached to a confirmed WHO=4 climate zone (#981)."""

    _unique_id_suffix = "-temperature"

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        zone: str,
        interface: str | None,
        manufacturer: str | None,
        model: str | None,
        gateway: MyHOMEGatewayHandler,
        initial_value: float | None = None,
    ) -> None:
        super().__init__(
            hass=hass,
            name=name,
            device_id=device_id,
            zone=zone,
            interface=interface,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit=UnitOfTemperature.CELSIUS,
            initial_value=initial_value,
        )

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Update temperature from WHO=4 Dimension 0 readings for this zone."""
        if not self._matches_zone(message):
            return
        val: float | None = None
        msg_type = getattr(message, "message_type", None)
        if msg_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            val = _finite_float(getattr(message, "main_temperature", None))
        elif msg_type is None and getattr(message, "dimension", None) == 0:
            dim_val = getattr(message, "dimension_value", None)
            if dim_val:
                val = _finite_float(who4_temperature(str(dim_val[0])))
        self._apply_reading(message, val)


class MyHOMEClimateHumiditySensor(_MyHOMEClimateZoneSensor):
    """Passive humidity sensor attached to a humidity-capable WHO=4 climate zone (#981)."""

    _unique_id_suffix = "-humidity"

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        zone: str,
        interface: str | None,
        manufacturer: str | None,
        model: str | None,
        gateway: MyHOMEGatewayHandler,
        initial_value: float | None = None,
    ) -> None:
        super().__init__(
            hass=hass,
            name=name,
            device_id=device_id,
            zone=zone,
            interface=interface,
            manufacturer=manufacturer,
            model=model,
            gateway=gateway,
            device_class=SensorDeviceClass.HUMIDITY,
            native_unit=PERCENTAGE,
            initial_value=initial_value,
        )

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Update humidity from WHO=4 Dimension 60 readings for this zone."""
        if not self._matches_zone(message):
            return
        val: float | None = None
        msg_type = getattr(message, "message_type", None)
        if msg_type == MESSAGE_TYPE_MAIN_HUMIDITY:
            val = _finite_float(getattr(message, "main_humidity", None))
        elif msg_type is None and getattr(message, "dimension", None) == 60:
            dim_val = getattr(message, "dimension_value", None)
            if dim_val:
                try:
                    val = _finite_float(float(str(dim_val[0])))
                except (ValueError, TypeError):
                    val = None
        if val is not None and not (0.0 <= val <= 100.0):
            return
        self._apply_reading(message, val)
