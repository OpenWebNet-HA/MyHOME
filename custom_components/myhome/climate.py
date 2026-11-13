import asyncio
import inspect
import time
from typing import Any, cast

from homeassistant.components.climate import (
    ClimateEntity,
)
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    Platform,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.restore_state import DATA_RESTORE_STATE
from OWNd.message import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL,
    CLIMATE_MODE_HEAT,
    CLIMATE_MODE_OFF,
    LOCAL_CONTROL_NORMAL,
    LOCAL_CONTROL_OFF,
    LOCAL_CONTROL_OFFSET,
    LOCAL_CONTROL_OVERRIDE,
    LOCAL_CONTROL_PROTECTION,
    LOCAL_CONTROL_UNKNOWN,
    MESSAGE_TYPE_ACTION,
    MESSAGE_TYPE_FAN_SPEED,
    MESSAGE_TYPE_LOCAL_OFFSET,
    MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE,
    MESSAGE_TYPE_MAIN_HUMIDITY,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_MODE,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_SEASON,
    MESSAGE_TYPE_TARGET_TEMPERATURE,
    MESSAGE_TYPE_ZONE_STATE,
    SEASON_CONDITIONING,
    SEASON_HEATING,
    OWNCommand,
    OWNHeatingCommand,
    OWNHeatingEvent,
)

from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_CENTRAL,
    CONF_COOLING_SUPPORT,
    CONF_DEVICE_MODEL,
    CONF_FAN_SUPPORT,
    CONF_HEATING_SUPPORT,
    CONF_HUMIDITY_SUPPORT,
    CONF_MANUFACTURER,
    CONF_STANDALONE,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    LOGGER,
)
from .data import get_runtime_data
from .device_health import FaultKind
from .discovery import (
    Address,
    DeviceContext,
    PlatformDiscovery,
    config_for,
    default_known_keys,
    is_broadcast_where,
)
from .expose import is_exposed_on_bus
from .gateway import _ZONE_WHERE, SILENT_ZONE_GRACE, MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity
from .poll_health import PollHealth
from .thermo_switch import load_thermo_switch_subentries
from .where_grammar import (
    canonical_thermo_zone,
    is_actuator_where,
    is_central_thermo_where,
    is_central_unit_report,
    is_probe,
    is_pump,
    parse_thermo_actuator_where,
    thermo_event_actuator,
    where_param,
    zone_number,
)

PLATFORM = Platform.CLIMATE
PARALLEL_UPDATES = 0
# How long one read of the silent-zone poll (#466) waits for its answer before giving up.
PACED_READ_TIMEOUT = 60.0

_FOUR_ZONE_CENTRAL = ("#0#1", "#0#2", "#0#3", "#0#4")
# BTicino 3550 central unit (#0) diagnostic WHAT codes (OpenWebNet Thermoregulation v2.0.0 Table 5):
# 20 = remote control disabled, 21 = remote control enabled, 22 = at least one probe OFF,
# 23 = at least one probe in antifreeze/protection, 24 = at least one probe in manual,
# 30 = failure discovered in a zone, 31 = central unit backup battery KO.
_CENTRAL_DIAGNOSTIC_WHATS = frozenset({20, 21, 22, 23, 24, 30, 31})


def _central_diagnostic_what(message: Any) -> int | None:
    """Return the central unit (#0) diagnostic WHAT code (20..24, 30, 31), or None."""
    if getattr(message, "dimension", None) is not None or getattr(message, "message_type", None) is not None:
        return None
    what = getattr(message, "what", None)
    if isinstance(what, int) and not isinstance(what, bool) and what in _CENTRAL_DIAGNOSTIC_WHATS:
        return what
    return None

# A dimension 12 frame and the frame that turns the zone OFF follow within ~0.1 s (#454, #383)
_PROTECTION_FRAME_WINDOW = 2.0
_ZONE_CONTEXT_MODES = {"heating": HVACMode.HEAT, "cooling": HVACMode.COOL, "automatic": HVACMode.AUTO}
_ZONE_STATES_ON = ("setpoint", "comfort", "eco")
_ZONE_STATES_OFF = ("protection", "off")
# Central unit protection modes (WHO 4 p. 5 and p. 64): the plant keeps its season and
# regulates to the protection setpoint; it is not switched off.
_CENTRAL_PROTECTION = {"102": ("antifreeze", HVACMode.HEAT), "202": ("thermal_protection", HVACMode.COOL)}
# 302 is generic protection (WHO 4 p. 5): the protection of the season the plant is in.
_CENTRAL_GENERIC_PROTECTION = "302"
# An F455 hands Home Assistant's own central command back on the event session 1 ms after
# it was sent; the 3550's answers came 121 ms (*4*203*#0## to *4*303*#0##) or later on the
# F461 and F455 (live, 2026-11-08). Only a frame inside this window can be the echo, so a
# frame equal to the command that arrives later, from the central unit or another client,
# is still taken as a report.
_CENTRAL_ECHO_WINDOW = 0.1
# While the command is still queued or in flight (its ``written`` future not done) the echo has
# no deadline yet; past this time the gateway's command pool has given up on it
# (gateway.PACED_WRITE_TIMEOUT), so the echo is no longer expected.
_CENTRAL_ECHO_PENDING_LIMIT = 60.0
# The 3550's status reply is its mode, then *4*20*#0## / *4*21*#0## (remote control), then
# only the optional flags that are active (*4*22..24*#0##, *4*30*#0##, *4*31*#0##; WHO 4 p. 5,
# p. 24-25). No frame says a flag is clear, so a flag that does not follow a 20 / 21 frame
# within this time is clear. Live F461 and F455, 2026-11-08 / 11-09: 31 came 0-1 ms after 21.
_CENTRAL_FLAGS_SETTLE = 1.0
_CENTRAL_FLAG_WHATS = (22, 23, 24, 30, 31)
# A generic *4*310/311*#Z## frame waits this long for the zone's seasonal status. An F455 put its
# synthetic *4*311*#4## 59-61 ms before *4*210*#4## / *4*203*#4## / *4*202*#4## (18 of 18, live
# 2026-11-08 / 11-09); a generic frame with no seasonal status after it is the zone's state (WHO 4 p. 9).
_GENERIC_ZONE_FRAME_HOLD = 0.5
_CONDITIONING_SEASONS = (SEASON_CONDITIONING, "cooling")

# How long the fan speed poll waits for the one-shot dimension 19 answer (*#4*Z*19##,
# 0.11 s on the live F461) before asking as it did before.
FAN_COIL_SIDES_WAIT = 30.0

# Mode digit of a setpoint write (*#4*W*#14*TTTT*M##). Legrand WHO 4 p. 8 and
# p. 23 define M = 1 heating, 2 conditioning, 3 generic; BTicino's own client
# always writes 3 (libqtdevices probe_device.cpp:122, thermal_device.cpp:134)
# and the mhs1 / F454 firmware forwards 1, 2 and 3 as three different bus
# codes (90/91/92 for a zone, C1 12/22/02 for the central unit). Writing generic
# mode 3 in async_set_temperature keeps the plant's existing season.
SETPOINT_WRITE_MODE = CLIMATE_MODE_AUTO

# Core 2026.11 renames the ClimateEntity temperature API to native_* and logs the
# old names as deprecated (unsupported from 2027.11). Older cores, down to the
# minimum 2026.3, only read the old names; MyHOMEClimate bridges them while they do.
_NATIVE_TEMPERATURE_API = hasattr(ClimateEntity, "native_temperature_unit")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the heating zones of a gateway (WHO=4): registry, myhome.yaml, then bus discovery.

    A zone is WHERE 1-99 (the central unit is ``#0`` / ``#0#1``); WHERE >= 100
    is a temperature probe, which belongs to the sensor platform. Heating
    frames often carry the zone they concern in a parameter rather than in
    WHERE (``*4*4001#5*0##``), so the address of a frame is derived here.
    """
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True
    load_thermo_switch_subentries(config_entry, runtime.platforms)
    gateway = runtime.gateway
    configured = runtime.platforms.get(PLATFORM, {})

    thermo_switch_whole_zones: set[tuple[str, str | None]] = set()
    thermo_switch_actuator_zones: set[tuple[str, str | None]] = set()
    for sw_cfg in runtime.platforms.get("switch", {}).values():
        if not isinstance(sw_cfg, dict) or str(sw_cfg.get(CONF_WHO)) != "4":
            continue
        try:
            _nw, sw_zone, sw_act, sw_if = parse_thermo_actuator_where(
                sw_cfg.get(CONF_WHERE), sw_cfg.get(CONF_BUS_INTERFACE)
            )
        except ValueError:
            continue
        zone_addr = Address(sw_zone, sw_if)
        zpad_addr = Address(f"0{sw_zone}", sw_if) if len(sw_zone) == 1 else None
        has_explicit_climate = bool(
            _zone_config(configured, zone_addr, zone_addr.key)
            or (zpad_addr is not None and _zone_config(configured, zpad_addr, zpad_addr.key))
        )
        if not has_explicit_climate:
            if sw_act is None:
                thermo_switch_whole_zones.add((sw_zone, sw_if))
            else:
                thermo_switch_actuator_zones.add((sw_zone, sw_if))

    real_hvac_states = {
        HVACMode.HEAT.value,
        HVACMode.COOL.value,
        HVACMode.HEAT_COOL.value,
        HVACMode.AUTO.value,
        HVACMode.DRY.value,
        HVACMode.FAN_ONLY.value,
    }

    def has_real_thermostat_state(entity_id: str) -> bool:
        # MyHOMEClimate.async_restore_last_state coerces STATE_UNKNOWN /
        # STATE_UNAVAILABLE into HVACMode.OFF ("off"), which HA then persists in
        # .storage/core.restore_state across restarts (#263). Bare state == "off"
        # without thermostat telemetry is therefore not evidence of a physical
        # thermostat (#785).
        states_obj = getattr(hass, "states", None)
        live = states_obj.get(entity_id) if states_obj is not None else None
        hass_data = getattr(hass, "data", None)
        restore_obj = hass_data.get(DATA_RESTORE_STATE) if isinstance(hass_data, dict) else None
        # HA 2026.11+ (core#183906) indexes stored states by entity registry ID and
        # exposes async_get_stored_state(entity_id); HA <= 2026.10 stores a dict on
        # RestoreStateData.last_states keyed by entity_id.
        get_stored = getattr(restore_obj, "async_get_stored_state", None)
        if callable(get_stored):
            stored = get_stored(entity_id)
        else:
            last_states = getattr(restore_obj, "last_states", None)
            stored = last_states.get(entity_id) if isinstance(last_states, dict) else None
        restored = getattr(stored, "state", None)
        for st in (live, restored):
            if st is None:
                continue
            attrs = getattr(st, "attributes", None) or {}
            if (
                attrs.get("current_temperature") is not None
                or attrs.get("temperature") is not None
                or attrs.get("current_humidity") is not None
                or attrs.get("season") is not None
                or attrs.get("program") is not None
                or attrs.get("holiday_days") is not None
                or attrs.get("scenario") is not None
                or attrs.get("knob_pos") not in (None, "UNKNOWN")
                or attrs.get("fan_coil_cooling") is True
                or attrs.get("fan_coil_heating") is True
                or getattr(st, "state", None) in real_hvac_states
            ):
                return True
        return False

    def thermo_zone_pair(address: Address) -> tuple[str, str | None] | None:
        cz = canonical_thermo_zone(address.where)
        if cz is None:
            return None
        return (cz, _two_digit_interface(address.interface))

    def is_thermo_switch_whole_zone(address: Address) -> bool:
        pair = thermo_zone_pair(address)
        return pair is not None and pair in thermo_switch_whole_zones

    def is_thermo_switch_actuator_zone(address: Address) -> bool:
        pair = thermo_zone_pair(address)
        return pair is not None and pair in thermo_switch_actuator_zones

    def is_thermo_switch_ghost(entry: er.RegistryEntry, address: Address) -> bool:
        pair = thermo_zone_pair(address)
        if pair is None:
            return False
        if pair in thermo_switch_whole_zones:
            return True
        if pair in thermo_switch_actuator_zones:
            return not has_real_thermostat_state(entry.entity_id)
        return False

    pending_actions: dict[tuple[str, str | None], dict[str, OWNHeatingEvent]] = {}

    def build(ctx: DeviceContext) -> MyHOMEClimate:
        where, interface = ctx.address.where, ctx.address.interface
        clean_where = where.removeprefix("4-")
        zone = zone_number(where)
        cfg = _zone_config(configured, ctx.address, ctx.key) or ctx.cfg
        suffix = f"{zone}I{interface}" if interface else zone
        is_central = clean_where in ("0", "#0", *_FOUR_ZONE_CENTRAL) or (
            ctx.source != "bus" and bool(cfg.get(CONF_CENTRAL, False))
        )
        default_name = f"Central Unit {suffix}" if is_central and ctx.source != "bus" else f"Climate Zone {suffix}"
        registry_name = getattr(ctx.registry_entry, "name", None)
        name = cfg.get(CONF_NAME) or (registry_name if isinstance(registry_name, str) else None) or default_name
        default_model = (
            "Central Unit (3550)"
            if clean_where == "#0"
            else "Central Unit (4695)"
            if clean_where in _FOUR_ZONE_CENTRAL
            else "Heating Zone"
        )
        entity = MyHOMEClimate(
            hass=hass,
            device_id=ctx.key,
            who=ctx.who,
            where=where,
            interface=interface,
            name=name,
            heating=cfg.get(CONF_HEATING_SUPPORT, True),
            cooling=cfg.get(CONF_COOLING_SUPPORT, True),
            fan=cfg.get(CONF_FAN_SUPPORT, False),
            humidity=cfg.get(CONF_HUMIDITY_SUPPORT, False),
            standalone=cfg.get(CONF_STANDALONE),
            central=is_central,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, default_model),
            gateway=gateway,
        )
        zone_pair = thermo_zone_pair(ctx.address)
        if zone_pair is not None:
            for deferred in pending_actions.pop(zone_pair, {}).values():
                entity.handle_event(deferred)
        return entity

    def accept(ctx: DeviceContext) -> bool:
        if is_probe(ctx.address.where):
            LOGGER.debug("Skipping non-zone address %s for climate platform", ctx.address.where)
            return False
        if is_thermo_switch_whole_zone(ctx.address):
            LOGGER.debug(
                "Skipping climate zone %s: configured as WHO=4 thermo switch (#785)",
                ctx.address.key,
            )
            return False
        if (
            not ctx.cfg
            and ctx.source == "bus"
            and getattr(ctx.message, "dimension", None) == 0
        ):
            full_where = (
                f"{ctx.address.where}#4#{ctx.address.interface}"
                if ctx.address.interface
                else ctx.address.where
            )
            if is_exposed_on_bus(hass, config_entry, 4, full_where):
                zone_pair = thermo_zone_pair(ctx.address)
                if zone_pair is not None:
                    pending_actions.setdefault(zone_pair, {})["dim0"] = ctx.message
                LOGGER.debug(
                    "Deferring climate zone %s creation on exposed WHO=4 virtual probe Dimension 0 frame",
                    ctx.address.key,
                )
                return False
        if (
            ctx.source == "bus"
            and is_thermo_switch_actuator_zone(ctx.address)
            and getattr(ctx.message, "message_type", None) is None
        ):
            LOGGER.debug(
                "Deferring climate zone %s creation on typeless WHO=4 frame (%s) for thermo switch zone (#785)",
                ctx.address.key,
                ctx.message,
            )
            return False
        if (
            ctx.source == "bus"
            and is_thermo_switch_actuator_zone(ctx.address)
            and getattr(ctx.message, "message_type", None) == MESSAGE_TYPE_ACTION
            and getattr(ctx.message, "dimension", None) == 19
            and not isinstance(getattr(ctx.message, "cooling_fan_on", None), bool)
            and not isinstance(getattr(ctx.message, "fan_on", None), bool)
        ):
            zone_pair = thermo_zone_pair(ctx.address)
            if zone_pair is not None:
                events = pending_actions.setdefault(zone_pair, {})
                dim19_active = bool(
                    ctx.message.is_active()
                    or ctx.message.is_heating()
                    or ctx.message.is_cooling()
                )
                if dim19_active:
                    events.pop("dim19", None)
                    events["dim19"] = ctx.message
                else:
                    events.pop("dim19", None)
                    events.pop("dim19_off", None)
                    events["dim19_off"] = ctx.message
            LOGGER.debug(
                "Deferring climate zone %s creation on Dimension 19 valve-only frame (#785)",
                ctx.address.key,
            )
            return False
        if (
            ctx.source == "bus"
            and getattr(ctx.message, "message_type", None) == MESSAGE_TYPE_ACTION
            and getattr(ctx.message, "dimension", None) == 20
            and not isinstance(getattr(ctx.message, "fan_speed", None), int)
            and not isinstance(getattr(ctx.message, "fan_on", None), bool)
        ):
            zone_pair = thermo_zone_pair(ctx.address)
            if zone_pair is not None:
                slot = f"act:{thermo_event_actuator(ctx.message)}"
                events = pending_actions.setdefault(zone_pair, {})
                events.pop(slot, None)
                events[slot] = ctx.message
            LOGGER.debug(
                "Deferring climate zone %s creation on Dimension 20 actuator-only frame (#785)",
                ctx.address.key,
            )
            return False
        return True

    def duplicate_climate(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        if is_thermo_switch_ghost(entry, ctx.address):
            LOGGER.info(
                "climate: pruning probe-less zone %s claimed by WHO=4 thermo switch (#785)",
                entry.entity_id,
            )
            return True
        if ctx.cfg:
            return False
        return is_broadcast_where(ctx.address.clean_where)

    def known_keys(ctx: DeviceContext) -> list[str]:
        keys = [*default_known_keys(ctx), ctx.address.where, ctx.address.clean_where, ctx.config_id or ""]
        if not ctx.address.interface and "#" not in ctx.address.clean_where.removeprefix("#"):
            keys.append(zone_number(ctx.address.where))
        return [k for k in keys if k]

    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="4", event_type=OWNHeatingEvent, build=build,
        accept=accept, reject_registry_entry=duplicate_climate, known_keys=known_keys,
        address=_zone_address, route_keys=_zone_route_keys,
        general_is_device=True,  # WHERE=0 frames name their zone in a parameter; _zone_address decides
    ).start()
    return True


def _zone_config(configured: dict[str, Any], address: Address, key: str) -> dict[str, Any]:
    """The ``myhome.yaml`` entry of a zone under every spelling older versions accepted.

    A routed zone only matches interface-qualified spellings: zone 1 exists
    on every bus, so the bare ones name the local bus's zone (#408).
    """
    where, clean = address.where, address.clean_where
    zone = zone_number(where) if "#" not in clean.removeprefix("#") else clean
    if address.interface:
        routing = f"{BUS_ROUTING}{address.interface}"
        return config_for(configured, address, key, f"4-{zone}{routing}", f"4-{key}", f"#{zone}{routing}")
    return config_for(
        configured, address, key, zone,
        f"4-{zone}", f"4-{where}", f"4-{key}", f"4-#{zone}", f"4-#{where}",
        f"#{zone}", f"#{where}", f"zone_{zone}", f"zone_{where}",
    )


def _bus_zone(message: Any) -> int | None:
    """OWNd's zone of a frame, or ``None`` where the frame names no heating zone.

    Two different cases end in ``None``.

    Not a zone's frame, though OWNd decodes it correctly: a bare WHERE >= 100 is
    ``PZZ``, probe ``P`` (1-8) of zone ``ZZ`` (probe 105 -> sensor 1, zone 5).
    Read as the zone's it was delivered to that zone and, where the zone is not a
    heating zone (an external probe 105 beside zones 1-4), it discovered a
    phantom one (#549). Probes belong to the sensor platform.

    Misread by OWNd <= 2.0.0b8: on an unhashed WHERE ``0#<p>`` OWNd reports ``p`` as the zone, but ``p`` is
    never one: ``0#<n>`` is actuator ``n`` of zone 0, the pump the zones call
    with ``*4*4001#<zone>*0#<n>##`` (zones 1, 2, 3, 5 and 6 of one plant all
    call ``0#3``: #303, #333, #404), and ``0#4#<if>`` is WHERE=0 behind an
    F422 interface. Taken as zones, pump 2 switched zone 2 (#431) and the F422
    form became zone 4. ``#0#<n>`` (4-zone central unit) keeps OWNd's zone.
    """
    if is_pump(message):
        return None
    if is_probe(str(getattr(message, "where", None))):
        return None  # probe P of zone ZZ (105 = probe 1 of zone 5), not the zone's own frame
    zone: int | None = getattr(message, "zone", None)
    return zone


def _two_digit_interface(interface: str | None) -> str | None:
    """The F422 interface as the bus and the configuration validator spell it (``01``)."""
    if interface is None:
        return None
    interface = str(interface)
    return interface.zfill(2) if interface.isdigit() else interface


def _calling_zones(message: Any) -> tuple[list[str], str | None]:
    """Zones a heating frame concerns, and the F422 interface it came through."""
    raw_where = getattr(message, "where", None)
    zone = _bus_zone(message)
    interface = getattr(message, "interface", None)
    # OWNHeatingEvent keeps WHAT only as ``_what``; it has no ``what`` property.
    what = getattr(message, "what", None) or getattr(message, "_what", None)
    what_param = getattr(message, "what_param", None) or getattr(message, "_what_param", None) or []
    tail = where_param(message)
    if not interface and len(tail) == 2 and tail[0] == "4":
        interface = str(tail[1])
    elif not interface and len(tail) > 2 and tail[1] == "4":
        interface = str(tail[2])
    interface = _two_digit_interface(interface)

    zones: list[str] = []
    # WHERE ``<zone>#<n>`` names actuator <n> of the zone (``*#4*2#1*20*1##``
    # = zone 2, actuator 1 is on), never zone <n>: routing it there made zone 1
    # "heat" whenever any zone's actuator 1 opened (#333), and pump 2 (``0#2``)
    # switch zone 2 (#431).
    if zone is not None and zone > 0:
        zones.append(str(zone))
    if what in ("4001", "4002", 4001, 4002) and what_param:
        try:
            zones.append(str(int(what_param[0])))
        except (ValueError, TypeError):
            pass
    clean_raw = str(raw_where).split("-")[-1] if raw_where is not None else ""
    if not zones and raw_where and not is_broadcast_where(clean_raw) and clean_raw != "" and not is_probe(str(raw_where)):
        zones.append(str(raw_where))
    return zones, interface


def _zone_address(message: Any) -> Address | None:
    """The zone a frame discovers; ``None`` for broadcasts and probes."""
    zones, interface = _calling_zones(message)
    if not zones:
        return None
    return Address(zones[0], interface)


def _zone_route_keys(message: Any, address: Address | None) -> list[str]:
    """Every key a heating frame is delivered under: the zone, WHERE, and the calling zones."""
    zones, interface = _calling_zones(message)
    zone = _bus_zone(message)
    keys = [] if zone is None else [f"#{zone}" if zone == 0 else str(zone)]
    # WHERE "0" with a parameter is pump ``0#N``, not the general "0" (#431).
    if getattr(message, "where", None) and not is_probe(str(message.where)) and not (
        is_pump(message)
    ):
        keys.append(str(message.where))
    for z in zones:
        keys.append(z)
        if interface:
            keys.append(f"{z}{BUS_ROUTING}{interface}")
    return keys


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    _configured_climate_devices = runtime.platforms[PLATFORM]

    for _climate_device in list(_configured_climate_devices.keys()):
        del runtime.platforms[PLATFORM][_climate_device]
    return True


class MyHOMEClimate(MyHOMEEntity, ClimateEntity):
    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        who: str,
        where: str,
        heating: bool,
        cooling: bool,
        fan: bool,
        standalone: bool | None,
        central: bool,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        interface: str | None = None,
        humidity: bool = False,
    ) -> None:
        if interface is None and BUS_ROUTING in where:
            base_where, _, inline_interface = where.rpartition(BUS_ROUTING)
            if base_where and inline_interface.isdigit() and 1 <= len(inline_interface) <= 2:
                where = base_where
                interface = inline_interface
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
        if hass is not None:
            self.hass = hass

        self._interface = _two_digit_interface(interface)
        self._full_where = (
            f"{self._where}{BUS_ROUTING}{self._interface}" if self._interface is not None else self._where
        )
        self._interface_poll_skip_logged = False

        self._central = True if self._where.removeprefix("4-") in ("0", "#0", *_FOUR_ZONE_CENTRAL) else central
        # None: not configured. A zone then follows the plant, see the standalone property.
        self._standalone_config = False if self._central else standalone
        if self._central:
            gateway.has_central_unit = True

        self._attr_native_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_precision = 0.1
        self._attr_target_temperature_step = 0.5
        self._attr_min_temp = 5
        self._attr_max_temp = 40

        # HVACMode.OFF is always available, so climate.turn_off / turn_on must be
        # advertised explicitly (mandatory since core 2025.1).
        self._attr_supported_features = ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        self._heating = heating
        self._cooling = cooling
        if heating or cooling:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
        self._attr_hvac_modes = self._advertised_hvac_modes()

        # Fan mode support (fancoil 3-speed + auto)
        self._fan: bool = False
        self._fan_configured: bool = bool(fan and not self._central)
        self._attr_fan_mode: str | None = None
        self._attr_fan_modes: list[str] | None = None
        self._running_fan_speed: str | None = None
        # Per-actuator state from dimension 20 (*#4*Z#N*20*V##), exposed as ``actuators``.
        self._actuator_states: dict[str, bool] = {}
        # Zone-level fallback from dimension 19 (*#4*Z*19*C*H##): whether any valve or fan
        # coil of the zone is active; None until seen. Whichever of the two arrived last
        # drives hvac_action, so a stale one cannot keep the zone "heating".
        self._zone_active: bool | None = None
        self._active_source: str | None = None  # "actuators" (dimension 20) or "zone" (19)
        # Which side of the zone drives a fan coil, from dimension 19
        # (*#4*Z*19*COOLING*HEATING##: a value of 5..8 on a side is a fan coil, 0..4 a valve).
        # Empty until a dimension 19 frame has been seen.
        self._fan_coil_sides: dict[str, bool] = {}
        # When the one-shot *#4*Z*19## was asked (a fan coil showed up before dimension 19
        # did): while its answer is pending the fan speed is not polled blindly.
        self._fan_coil_sides_asked_at: float | None = None
        # The dimension 19 request was queued while a sweep held the zone requests back.
        self._fan_coil_sides_held = False
        if fan and not self._central:
            self._enable_fan_mode()

        # Humidity support (dimension 60, Living Now / humidity-capable probes)
        self._humidity: bool = False
        if humidity and not self._central:
            self._enable_humidity()

        self._attr_native_current_temperature: float | None = None
        self._season: str | None = None
        # Whether the season came from a frame since start-up, not from the restored state.
        self._season_reported = False
        # Central unit: the protection mode last reported (102 antifreeze, 202 thermal protection).
        self._central_operation: str | None = None
        # Central unit: Home Assistant's last command and when its local echo stops being expected,
        # and the echo frame once seen (the same frame object can be delivered more than once).
        self._central_echo: tuple[str, float | None, float] | None = None
        self._central_echo_seen: object | None = None
        self._program: int | None = None
        self._holiday_days: int | None = None
        self._scenario: int | None = None
        self._attr_current_humidity: float | None = None
        self._target_temperature: float | None = None
        self._local_offset: float = 0
        self._local_offset_reported = False
        self._knob_pos: str = "UNKNOWN"
        self._local_target_temperature: float | None = None
        # True while _local_target_temperature is the zone's own dimension 12 report (the
        # setpoint in force, offset included): dimension 14 and 13 frames then leave it alone.
        self._local_target_reported = False
        self._dim12_at: float | None = None
        self._dim14_at: float | None = None
        self._written_target_temperature: tuple[float, float] | None = None
        self._current_temperature_restored = False
        # True while the setpoint / the mode are what Home Assistant saved, not what the zone
        # said since the restart: the silent-zone poll (#466) does not take them for a report.
        self._target_temperature_restored = False
        self._hvac_mode_restored = False
        self._humidity_reported = False
        self._fan_mode_reported = False
        self._nominal_before_dim12: tuple[float | None, float] | None = None
        self._poll_health = PollHealth()
        # BTicino 3550 central unit (#0) diagnostic status flags (WHAT 20..24, 30, 31)
        self._remote_control: str | None = None
        self._at_least_one_probe_off: bool = False
        self._at_least_one_probe_antifreeze: bool = False
        self._at_least_one_probe_manual: bool = False
        self._failure_detected: bool = False
        self._battery_fault: bool = False
        # The optional flags seen since the last 20 / 21 frame, and the timer that clears the others.
        self._central_flags_seen: set[int] = set()
        self._central_flags_unsub: Any = None
        # A generic *4*310/311*#Z## frame held back until the zone's seasonal status shows whether
        # it was only a prelude, the timer that applies it otherwise, and the frame being applied.
        self._generic_zone_frame_unsub: Any = None
        self._generic_zone_frame_release: object | None = None

        self._attr_hvac_mode: HVACMode | None = None
        self._attr_hvac_action: HVACAction | None = None

        if (
            self._interface_poll_refused
            or is_actuator_where(self._where)
            or (
                not self._central
                and not self._gateway_supports_zone_status
                and not self._polls_temperature
                and not self._fan
                and not self._humidity
            )
        ):
            self._poll_on_add = False
        elif self._central and self._where.removeprefix("4-") not in ("#0", "0"):
            self._poll_on_add = False

    @property
    def _gateway_supports_zone_status(self) -> bool:
        """Whether the gateway supports point-to-point zone status queries (*#4*Z##)."""
        gateway = getattr(self, "_gateway_handler", None)
        return bool(getattr(gateway, "supports_zone_status", True))

    @property
    def _temperature_poll_wanted(self) -> bool:
        """Whether this zone's gateway needs each zone to ask for its temperature (``*#4*Z*0##``)."""
        gateway = getattr(self, "_gateway_handler", None)
        return not self._central and getattr(gateway, "zone_temperature_poll_needed", False) is True

    @property
    def _interface_poll_refused(self) -> bool:
        """Whether WHO 4 polls are withheld because the zone sits behind an ``#4#II`` interface.

        Zero of the twelve emulated gateways in own-firmware-oracle (``findings/thermo-zone-requests.md``)
        emit ``$06EC`` / ``$06E4`` interface-routed SCS telegrams for WHO 4: gateways either NACK/drop
        them, truncate ``Z#4#II`` at ``#`` and cross-poll local zone ``Z`` on the main bus, or on
        MH202 ``010024`` misparse trailing ``#4#01`` as local zone 4 actuator 1 and crash ``coso``
        with ``SIGSEGV`` on ``*#4*Z#4#01##`` (including while the profile is still ``Generic``).
        """
        gateway = getattr(self, "_gateway_handler", None)
        return (
            (self._interface is not None or BUS_ROUTING in self._full_where)
            and getattr(gateway, "interface_zone_request_refused", False) is True
        )

    @property
    def _polls_temperature(self) -> bool:
        """Whether this zone asks for its temperature (*#4*Z*0##) on an SCS-only gateway (#185).

        A pure predicate: ``async_update`` logs (once) when it withholds the poll.
        """
        return (
            self._temperature_poll_wanted
            and not self._interface_poll_refused
            and not is_actuator_where(self._where)
        )

    def _enable_fan_mode(self) -> None:
        """Dynamically enable fan mode support if not already enabled."""
        if self._central:
            return
        if not self._fan:
            self._fan = True
            self._poll_on_add = not self._interface_poll_refused and not is_actuator_where(self._where)
            self._attr_supported_features |= ClimateEntityFeature.FAN_MODE
            self._attr_fan_modes = ["auto", "low", "medium", "high"]
            if self._attr_fan_mode is None:
                self._attr_fan_mode = "auto"

    def _has_no_fan_coil_sides(self) -> bool:
        """Whether dimension 19 reported that neither side of this zone is a fan coil."""
        return bool(self._fan_coil_sides) and not any(self._fan_coil_sides.values())

    def _disable_fan_mode(self) -> None:
        """Retract dynamically enabled fan mode once dimension 19 proves a pure-valve zone."""
        self._fan = False
        self._attr_supported_features &= ~ClimateEntityFeature.FAN_MODE
        self._attr_fan_modes = None
        self._attr_fan_mode = None
        self._running_fan_speed = None

    def _enable_humidity(self) -> None:
        """Dynamically enable humidity support if not already enabled."""
        if self._central:
            return
        if not self._humidity:
            self._humidity = True
            self._poll_on_add = not self._interface_poll_refused and not is_actuator_where(self._where)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device specific attributes."""
        attrs: dict[str, Any] = {
            "local_offset": self._local_offset,
            "local_target_temperature": self._local_target_temperature,
            "knob_pos": self._knob_pos,
        }
        if self._season is not None:
            attrs["season"] = self._season
        if self._program is not None:
            attrs["program"] = self._program
        if self._holiday_days is not None:
            attrs["holiday_days"] = self._holiday_days
        if self._scenario is not None:
            attrs["scenario"] = self._scenario
        if self._fan:
            attrs["fan_mode"] = self._attr_fan_mode
            if self._running_fan_speed is not None:
                attrs["running_fan_speed"] = self._running_fan_speed
        if self._actuator_states:
            attrs["actuators"] = {
                actuator: "on" if active else "off" for actuator, active in sorted(self._actuator_states.items())
            }
        if self._current_temperature_restored:
            attrs["current_temperature_restored"] = True
        if self._fan_coil_sides:
            attrs["fan_coil_heating"] = self._fan_coil_sides.get("heating", False)
            attrs["fan_coil_cooling"] = self._fan_coil_sides.get("cooling", False)
        if self._interface is not None:
            attrs["Int"] = self._interface
        if self._central:
            if self._central_operation is not None:
                attrs["central_operation"] = self._central_operation
            if self._remote_control is not None:
                attrs["remote_control"] = self._remote_control
            attrs["at_least_one_probe_off"] = self._at_least_one_probe_off
            attrs["at_least_one_probe_antifreeze"] = self._at_least_one_probe_antifreeze
            attrs["at_least_one_probe_manual"] = self._at_least_one_probe_manual
            attrs["failure_detected"] = self._failure_detected
            attrs["battery_fault"] = self._battery_fault
        else:
            attrs.update(self._poll_health.attributes())
        return attrs

    async def async_restore_last_state(self, last_state: State | None) -> None:
        """Restore climate state from HA storage."""
        if last_state is not None:
            if not self._central:
                self._poll_health.restore(last_state.attributes)
            else:
                self._clear_unresponsive_issue()
                restored_rc = last_state.attributes.get("remote_control")
                if restored_rc in ("enabled", "disabled"):
                    self._remote_control = str(restored_rc)
                restored_operation = last_state.attributes.get("central_operation")
                if restored_operation in ("antifreeze", "thermal_protection"):
                    self._central_operation = str(restored_operation)
                self._at_least_one_probe_off = bool(
                    last_state.attributes.get("at_least_one_probe_off", False)
                )
                self._at_least_one_probe_antifreeze = bool(
                    last_state.attributes.get("at_least_one_probe_antifreeze", False)
                )
                self._at_least_one_probe_manual = bool(
                    last_state.attributes.get("at_least_one_probe_manual", False)
                )
                self._failure_detected = bool(last_state.attributes.get("failure_detected", False))
                self._battery_fault = bool(last_state.attributes.get("battery_fault", False))
        if last_state is not None and last_state.state is not None:
            try:
                restored_mode = HVACMode(last_state.state)
                # Any configured mode is restored (the plant's next frames replace it); the
                # advertised list is recomputed around it.
                if restored_mode in self._configured_hvac_modes():
                    self._attr_hvac_mode = restored_mode
                else:
                    self._attr_hvac_mode = HVACMode.OFF
                self._refresh_hvac_modes()
            except (ValueError, TypeError):
                self._attr_hvac_mode = HVACMode.OFF
            self._hvac_mode_restored = True
            target_temp = last_state.attributes.get("temperature")
            if target_temp is not None:
                try:
                    shown = float(target_temp)
                except (ValueError, TypeError):
                    shown = None
                if shown is not None:
                    # The saved value is what the slider showed, knob offset included (the
                    # setpoint in force, or the commanded one plus the offset), so the nominal
                    # is that less the saved offset. As on the live path, the first dimension
                    # 13 / 14 report after the restart then puts the offset on the nominal,
                    # not a second time on a value that already carries it (#791).
                    try:
                        self._local_offset = int(float(last_state.attributes.get("local_offset") or 0))
                    except (ValueError, TypeError):
                        pass
                    self._target_temperature = shown - self._local_offset
                    self._local_target_temperature = shown
                    self._target_temperature_restored = True
            if "program" in last_state.attributes:
                try:
                    self._program = int(last_state.attributes["program"])
                except (ValueError, TypeError):
                    pass
            if "holiday_days" in last_state.attributes:
                try:
                    self._holiday_days = int(last_state.attributes["holiday_days"])
                except (ValueError, TypeError):
                    pass
            if "scenario" in last_state.attributes:
                try:
                    self._scenario = int(last_state.attributes["scenario"])
                except (ValueError, TypeError):
                    pass
            restored_current = last_state.attributes.get("current_temperature")
            if restored_current is not None and self._attr_native_current_temperature is None:
                # Shown until the zone reports again, flagged so it is not taken for a reading:
                # SCS-only gateways may not hear from a standalone thermostat for a long time (#185).
                try:
                    self._attr_native_current_temperature = float(restored_current)
                    self._current_temperature_restored = True
                except (ValueError, TypeError):
                    pass
            restored_season = last_state.attributes.get("season")
            if restored_season in (SEASON_HEATING, "cooling", "conditioning"):
                self._season = str(restored_season)
            restored_knob = last_state.attributes.get("knob_pos")
            if isinstance(restored_knob, str) and restored_knob and restored_knob != "UNKNOWN":
                self._knob_pos = restored_knob
            if (
                "fan_coil_heating" in last_state.attributes
                or "fan_coil_cooling" in last_state.attributes
            ):
                self._fan_coil_sides = {
                    "heating": bool(last_state.attributes.get("fan_coil_heating", False)),
                    "cooling": bool(last_state.attributes.get("fan_coil_cooling", False)),
                }
            if (
                "fan_mode" in last_state.attributes
                or bool(last_state.attributes.get("supported_features", 0) & ClimateEntityFeature.FAN_MODE)
            ) and not (self._has_no_fan_coil_sides() and not self._fan_configured):
                self._enable_fan_mode()
                restored_fan_mode = last_state.attributes.get("fan_mode")
                if restored_fan_mode in ("auto", "low", "medium", "high"):
                    self._attr_fan_mode = restored_fan_mode
            if (
                "current_humidity" in last_state.attributes
                and last_state.attributes["current_humidity"] is not None
            ):
                self._enable_humidity()
                try:
                    self._attr_current_humidity = float(last_state.attributes["current_humidity"])
                except (ValueError, TypeError):
                    pass
            if self._fan and not self._central and not is_probe(self._where):
                # A restored fan mode without the fan coil's side: ask for it now, before
                # ``async_added_to_hass`` polls the zone, or the poll asks ``*#4*Z*11##`` blindly.
                self._ask_fan_coil_sides()

    async def async_update(self) -> None:
        """Request status update from gateway, unless the zone has stopped answering."""
        if self._central and self._where.removeprefix("4-") not in ("#0", "0"):
            # 4-zone central units (#0#1..#0#4) do not participate in point-to-point status polling;
            # bus captures confirm querying #0#1 times out on plants without a physical 4-zone unit (#629).
            # They receive setpoints via commands (*#4*#0#1*#14*T*M##), broadcast events, or restored state.
            return
        # Humidity is asked only where the gateway forwards the request: the emulated firmware
        # of F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202 did not (own-firmware-oracle
        # thermo-zone-requests finding, emulated firmware). Whether those gateways put the probe's
        # humidity on the event session is not traced; any *#4*<WHERE>*60*<HH>## delivered there
        # still updates current_humidity (handle_event) without a poll.
        if is_actuator_where(self._where):
            # Per-actuator and split addresses (Z#N, Z#0, 0#N, 0#0, 3#Z#N) are not zone probes:
            # *#4*Z#N## crashes MH202 coso with SIGSEGV (NACK on 9 others, local-zone cross-poll
            # on H4684/MH200), and *0## / *11## / *60## either NACK or truncate onto zone Z.
            return
        wants_humidity = self._humidity and getattr(self._gateway_handler, "humidity_request_refused", False) is not True
        if self._interface_poll_refused:
            if (
                self._central
                or self._gateway_supports_zone_status
                or self._temperature_poll_wanted
                or self._fan
                or wants_humidity
            ) and not self._interface_poll_skip_logged:
                iface = self._interface or self._full_where.rpartition(BUS_ROUTING)[2]
                LOGGER.debug(
                    "%s %s is behind interface %s and %s does not route WHO 4 requests across `#4#%s`; waiting for the zone to report",
                    self._gateway_handler.log_id,
                    self._display_name,
                    iface,
                    self._gateway_handler.model,
                    iface,
                )
                self._interface_poll_skip_logged = True
            return
        if (
            not self._central
            and not self._gateway_supports_zone_status
            and not self._polls_temperature
            and not self._fan
            and not wants_humidity
        ):
            # SCS-only gateways without multi-session *#4*Z## verification (F450, F453, F453AV, F459,
            # H4684, MH200, MH200N in the firmware oracle; F455 and MH201 by field reports) expand
            # the dimensionless zone status request *#4*Z## into four bus queries, dimension 0, 12, 14 and
            # 13 ($0499ZZ3000 / 12 / 0E / 0D in trace-corpus.tsv). The 10 s socket hang of #674 was an F455,
            # not in the oracle, on actuator-only channels (*#4*Z*19*0*0##) where nothing answered. F454
            # (up to 4 command sessions) answers *#4*Z## in ~1.3 s and MH202 in ~160-250 ms with all five
            # frames on both central units (#629) and standalone thermostats (#866); F460, F461 and
            # MyHomeServer1 expand it onto the SCS bus ($0499013000) and answer all five frames in
            # ~1.3-1.9 s (#649, #714, #886): those five keep the poll. Where an SCS-only gateway also drops
            # the general request *#4*0##, the zone asks for its temperature below (_polls_temperature);
            # everywhere else it waits for the sweep replies or for the thermostat's own reports.
            return
        await self._async_send_zone_polls()

    async def _send_zone_poll_frame(
        self,
        request: OWNCommand,
        *,
        pace: bool = False,
        track_health: bool = False,
    ) -> None:
        """Send one WHO 4 status frame, register poll health if needed, and optionally pace."""
        frames_before = self._poll_health.frames
        written = await self._gateway_handler.send_status_request(request)
        if track_health and isinstance(written, asyncio.Future):
            written.add_done_callback(lambda future: self._poll_answered(future, frames_before))
        if pace:
            pace_write = getattr(self._gateway_handler, "pace_startup_status_write", None)
            if callable(pace_write):
                await pace_write(written, request)

    async def _async_send_zone_polls(
        self,
        *,
        skip_reported: bool = False,
        humidity_coalesced: bool = False,
        pace: bool = False,
    ) -> None:
        """Dispatch point-to-point WHO 4 status queries needed by this entity."""
        wants_humidity = (
            self._humidity
            and getattr(self._gateway_handler, "humidity_request_refused", False) is not True
        )
        ask_status = self._central or (
            self._gateway_supports_zone_status
            and (not skip_reported or not self._core_zone_state_reported())
        )
        ask_temp = (
            not self._central
            and not self._gateway_supports_zone_status
            and self._polls_temperature
            and (not skip_reported or not self._temperature_reported())
        )
        ask_fan = (
            self._fan
            and self._season_has_fan_coil()
            and (not skip_reported or not self._fan_mode_reported)
        )
        ask_humidity = (
            wants_humidity
            and not humidity_coalesced
            and (not skip_reported or not self._humidity_reported)
        )
        if skip_reported and not (ask_status or ask_temp or ask_fan or ask_humidity):
            self._bus_state_complete = True
            LOGGER.debug(
                "%s: state already reported on the bus, skipping post-discovery poll",
                self.entity_id,
            )
            return
        if self._poll_health.should_skip(time.time()):
            LOGGER.debug(
                "%s %s did not answer its last polls; not asking again yet",
                self._gateway_handler.log_id,
                self._display_name,
            )
            self._raise_unresponsive_issue()
            return
        if ask_status:
            # A 99-zone central unit (#0) answers the plain status request (*#4*#0##) within ~0.17 s,
            # reporting its mode via *4*202*#0## (conditional OFF) and operational flags (#629). Dimension 14
            # (*#4*#0*14##) is NACKed. Only an F454 has been captured so far, so the poll is informational:
            # an unanswered one never counts towards the "unresponsive zone" repair.
            await self._send_zone_poll_frame(
                OWNHeatingCommand.status(self._full_where),
                pace=pace,
                track_health=not self._central,
            )
        elif ask_temp:
            # One bus query instead of the four that *#4*Z## expands into: *#4*Z*0## is forwarded on
            # all twelve oracle gateways (thermo-ts10.tsv) and a populated zone answers it in ~120 ms
            # (F461 2.0.11, f461_live_2026-10-23 rows 521-525: *#4*2*0## -> *#4*2*0*0239## + ACK in
            # 0.119 s), which keeps a single-worker MH200 / MH201 queue short. *#4*0## is refused on
            # these gateways, so this is the zone's only way to a temperature after a restart. A
            # zone behind an #4#II interface is withheld above unless the OWNd profile explicitly
            # sets supports_interface_zone_requests = True (_polls_temperature).
            # No _poll_answered callback on purpose: the oracle's *#4*1*0## NACK on 11 of 12 images
            # is the bus TX timeout of an unpopulated zone (the QEMU images have no thermostat
            # wired; the live F461 NACKs the unpopulated zone 1 the same way after 2.07 s, rows
            # 374-376), not a verdict on a populated one. Until a command-session capture of
            # *#4*Z*0## on a single-session SCS-only gateway (MH200 / H4684) exists, the result is
            # left uncounted rather than risk marking a healthy zone unresponsive on a NACK.
            await self._send_zone_poll_frame(
                OWNHeatingCommand.get_temperature(self._full_where),
                pace=pace,
            )
        if ask_fan:
            await self._send_zone_poll_frame(
                cast(OWNCommand, OWNHeatingCommand.parse(f"*#4*{self._full_where}*11##")),
                pace=pace,
            )
        if ask_humidity:
            await self._send_zone_poll_frame(
                cast(OWNCommand, OWNHeatingCommand.parse(f"*#4*{self._full_where}*60##")),
                pace=pace,
            )
        elif self._humidity and not wants_humidity:
            LOGGER.debug(
                "%s Zone %s: this gateway refuses humidity requests; not asked (a *#4*%s*60*HH## event still updates it)",
                self._gateway_handler.log_id,
                self._full_where,
                self._full_where,
            )

    @callback
    def _poll_answered(self, written: asyncio.Future[float], frames_before: int) -> None:
        """Count a status request the gateway refused or never answered (see ``poll_health``)."""
        if written.cancelled():
            if (
                getattr(written, "_gateway_desync", False)
                or not getattr(self._gateway_handler, "is_connected", False)
                or self._poll_health.frames != frames_before
            ):
                return  # gateway FIFO desync (#886), gateway was away, or the zone did answer
            if self._poll_health.failed(time.time()):
                LOGGER.info("%s %s did not answer its status request twice in a row", self._gateway_handler.log_id, self._display_name)
                self._raise_unresponsive_issue()
            self._publish_state()
        elif self._poll_health.answered():
            self._clear_unresponsive_issue()
            self._publish_state()

    def _temperature_reported(self) -> bool:
        """Whether this zone's ambient temperature arrived from the bus since startup."""
        return (
            self._attr_native_current_temperature is not None
            and not self._current_temperature_restored
        )

    def _core_zone_state_reported(self) -> bool:
        """Whether live temperature, operating mode, and setpoint (when running) arrived on the bus."""
        if not self._temperature_reported():
            return False
        if self._attr_hvac_mode is None or self._hvac_mode_restored:
            return False
        if self._attr_hvac_mode == HVACMode.OFF:
            return True
        return self.native_target_temperature is not None and not self._target_temperature_restored

    def _refresh_bus_state_complete(self) -> None:
        """Mark ``_bus_state_complete`` once every field the startup poll asks has arrived live."""
        if self._central or is_actuator_where(self._where):
            return
        if self._gateway_supports_zone_status:
            core_ok = self._core_zone_state_reported()
        elif self._polls_temperature:
            core_ok = self._temperature_reported()
        else:
            core_ok = True
        fan_ok = not (self._fan and self._season_has_fan_coil()) or self._fan_mode_reported
        wants_humidity = (
            self._humidity
            and getattr(self._gateway_handler, "humidity_request_refused", False) is not True
        )
        hum_ok = not wants_humidity or self._humidity_reported
        self._bus_state_complete = bool(core_ok and fan_ok and hum_ok)

    async def _async_poll_after_discovery(self) -> None:
        """Poll WHO 4 zone state after discovery, coalescing humidity and pacing per-zone reads (#886)."""
        handler = self._gateway_handler
        startup_lock = getattr(handler, "_who4_startup_poll_lock", None)
        if (
            self._central
            or is_actuator_where(self._where)
            or self._interface_poll_refused
            or not isinstance(startup_lock, asyncio.Lock)
        ):
            await super()._async_poll_after_discovery()
            return

        await handler.wait_for_initial_discovery()

        wants_humidity = (
            self._humidity
            and getattr(handler, "humidity_request_refused", False) is not True
        )
        humidity_coalesced = False
        if wants_humidity and not self._humidity_reported and _ZONE_WHERE.match(self._full_where):
            humidity_coalesced = self._request_zone_dimension(60)

        wait_zone_reqs = getattr(handler, "wait_for_zone_requests", None)
        if callable(wait_zone_reqs):
            await wait_zone_reqs()

        async with startup_lock:
            self._refresh_bus_state_complete()
            await self._async_send_zone_polls(
                skip_reported=True,
                humidity_coalesced=humidity_coalesced,
                pace=True,
            )

    def _season_has_fan_coil(self) -> bool:
        """Whether the fan speed request makes sense right now.

        A zone with a valve on one side and a fan coil on the other (F461 plant: heating valve,
        cooling fan coil) answers *#4*Z*11## only in the fan coil's season and NACKs it in the
        other one. Until dimension 19 has told which side is which, or while the season is
        unknown, ask anyway.
        """
        if not self._fan_coil_sides:
            # Dimension 19 is on its way (asked once a fan coil showed up in dimension 20):
            # on the live F461 it answers in 0.11 s, while *#4*Z*11## in the valve's season
            # is NACKed after 2.1 s. Do not ask blindly while the answer is pending.
            return not self._fan_coil_sides_pending()
        if self._season is None:
            return True
        side = "heating" if self._season == SEASON_HEATING else "cooling"
        return self._fan_coil_sides.get(side, False)

    @staticmethod
    def _season_side(season: str | None) -> str | None:
        """The side of the zone a season runs on (``heating`` / ``cooling``), None when unknown."""
        if season is None:
            return None
        return "heating" if season == SEASON_HEATING else "cooling"

    def _follow_season_change(self, previous: str | None) -> None:
        """Ask the fan speed once the season moves to the fan coil's side at runtime.

        A mixed zone (F461 plant: heating valve, cooling fan coil) that booted in the valve's
        season skipped ``*#4*Z*11##`` (NACKed after 2.1 s there). The entity is not polled
        afterwards (``should_poll`` is False), so when the season frame (``*4*0*Z##`` /
        ``*4*1*Z##``) switches the zone to the fan coil's season the one-shot request is
        queued here, as the dimension 19 / 20 paths do; ``fan_mode`` would otherwise keep
        the synthetic ``auto`` until a command or a reconnect. The frame repeats with every
        zone poll and OWNd spells the cooling season ``conditioning`` where a restored
        state says ``cooling``, so only a change of side counts. The first season after a
        start (``None`` -> a season) is the poll on add, which asked the speed itself when
        the sides were known, and dimension 19 asks it once they become known.
        """
        side = self._season_side(self._season)
        if side is None or self._season_side(previous) in (None, side):
            return
        if self._fan and self._fan_coil_sides.get(side, False):
            self._request_zone_dimension(11)

    def _ask_fan_coil_sides(self) -> None:
        """Ask once which side of the zone is the fan coil (``*#4*Z*19##``).

        A fan coil revealed by an actuator report (dimension 20, value 5..8) does not tell its
        side; without dimension 19 the fan speed poll (``*#4*Z*11##``) would be sent in the
        valve's season too and NACKed after 2.1 s on the F461. Every gateway firmware of the
        oracle forwards ``*#4*Z*19##`` (own-firmware-oracle thermo-zone-requests).
        """
        if self._fan_coil_sides or self._fan_coil_sides_pending():
            return
        if self._request_zone_dimension(19):
            self._fan_coil_sides_asked_at = time.monotonic()
            self._fan_coil_sides_held = self._zone_requests_held()

    def _request_zone_dimension(self, dimension: int) -> bool:
        """Queue ``*#4*Z*DIM##`` through the gateway's paced sender; False on an older handler."""
        if self._interface_poll_refused or is_actuator_where(self._where):
            return False
        request = getattr(self._gateway_handler, "request_zone_dimension", None)
        if not callable(request):
            return False
        request(self._full_where, dimension)
        return True

    def _fan_coil_sides_pending(self) -> bool:
        """Whether the one-shot dimension 19 request is still waiting for its answer.

        An answer that never comes (request refused, session lost before it was sent) must
        not disable the fan speed poll for good: after ``FAN_COIL_SIDES_WAIT`` the zone is
        back to asking as before, and a later fan coil report may ask dimension 19 again.
        The wait starts once the request can leave the gateway: while the startup sweep (or
        another paced sweep) holds the zone requests back, nothing has been asked yet, and a
        request queued during the hold starts its wait at the first check after it.
        """
        asked_at = self._fan_coil_sides_asked_at
        if asked_at is None:
            return False
        if self._fan_coil_sides_held:
            if self._zone_requests_held():
                return True
            self._fan_coil_sides_held = False
            self._fan_coil_sides_asked_at = time.monotonic()
            return True
        return time.monotonic() - asked_at < FAN_COIL_SIDES_WAIT

    def _zone_requests_held(self) -> bool:
        """Whether the gateway is still holding the zone requests back (startup or paced sweep)."""
        handler = self._gateway_handler
        return (
            getattr(handler, "initial_discovery_pending", False) is True
            or getattr(handler, "sweep_active", False) is True
        )

    def _any_actuator_active(self) -> bool:
        """Whether the most recent actuator report (dimension 20 or 19) shows activity."""
        if self._active_source == "zone":
            return bool(self._zone_active)
        return any(self._actuator_states.values())

    def _mark_actuators_off(self) -> None:
        """The zone was switched OFF: every known actuator is off, the attribute stays."""
        self._actuator_states = dict.fromkeys(self._actuator_states, False)
        if self._zone_active is not None:
            self._zone_active = False

    @property
    def _standalone(self) -> bool:
        """Whether setpoints and modes are addressed as ``Z`` (thermostat alone) or ``#Z``.

        A configured value wins. Otherwise a zone is standalone unless its plant has a
        central unit: behind a 99-zone central the gateway rejects ``*#4*Z*#14*...`` and
        wants ``*#4*#Z*#14*...`` (#697).
        """
        if self._standalone_config is not None:
            return self._standalone_config
        return getattr(self._gateway_handler, "has_central_unit", False) is not True

    def _configured_hvac_modes(self) -> list[HVACMode]:
        """Every mode the zone's configuration allows, before the plant's topology is applied."""
        modes = [HVACMode.OFF]
        if self._heating or self._cooling:
            modes.append(HVACMode.AUTO)
            if self._heating:
                modes.append(HVACMode.HEAT)
            if self._cooling:
                modes.append(HVACMode.COOL)
        return modes

    def _advertised_hvac_modes(self) -> list[HVACMode]:
        """The modes ``async_set_hvac_mode`` can honour for this zone's place in the plant.

        ``auto`` is the central unit's program: a standalone thermostat on a plant with a
        central unit has none (``*4*311*#2##`` is ACKed by the live F461 and nothing answers;
        ``auto`` on zone 2 answered HTTP 400 on both gateway entries, 2026-11-12 14:37 UTC),
        so it is not offered once the central unit is known. A zone of the central unit runs
        in the central's season: the off-season mode (``heat`` in conditioning, 2026-11-12
        15:13 UTC on zone 4, HTTP 400) is not offered while that season is reported. The
        central unit itself and a standalone thermostat without a central unit keep every
        mode. The mode shown is never dropped (Home Assistant lists it).
        """
        modes = [HVACMode.OFF]
        if self._heating or self._cooling:
            has_central = getattr(self._gateway_handler, "has_central_unit", False) is True
            if self._central or not (self._standalone and has_central):
                modes.append(HVACMode.AUTO)
            # Called from the constructor before the season fields exist.
            season = getattr(self, "_season", None)
            under_central = (
                not self._central
                and not self._standalone
                and has_central
                and getattr(self, "_season_reported", False)
                and season is not None
            )
            if self._heating and not (under_central and season in _CONDITIONING_SEASONS):
                modes.append(HVACMode.HEAT)
            if self._cooling and not (under_central and season == SEASON_HEATING):
                modes.append(HVACMode.COOL)
        current = getattr(self, "_attr_hvac_mode", None)
        if current is not None and current not in modes:
            modes.append(current)
        return modes

    def _refresh_hvac_modes(self) -> bool:
        """Recompute the advertised modes; True when the list changed."""
        modes = self._advertised_hvac_modes()
        if modes == self._attr_hvac_modes:
            return False
        self._attr_hvac_modes = modes
        return True

    def _remember_season(self, message: Any) -> None:
        """Keep the season a mode frame carries (``*4*102*#0##`` heating, ``*4*202*#0##`` conditioning).

        OWNd decodes the off state of a central unit per season (OWNd#97); an older
        OWNd gives no season and the last known one stays (#699).
        """
        season = getattr(message, "season", None)
        if isinstance(season, str):
            self._season = season
            self._season_reported = True

    def _raise_unresponsive_issue(self) -> None:
        self._report_fault(FaultKind.UNRESPONSIVE)

    def _clear_unresponsive_issue(self) -> None:
        self._clear_fault(FaultKind.UNRESPONSIVE)

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        target_hass = self.hass or self._hass
        if target_hass is not None:
            if self._central:
                self._clear_unresponsive_issue()
                self.async_on_remove(self._cancel_central_flag_cycle)
            else:
                self.async_on_remove(self._cancel_generic_zone_frame)
            if not self._central and self._standalone_config is not True:
                self.async_on_remove(
                    async_dispatcher_connect(
                        target_hass,
                        f"myhome_central_mode_{self._gateway_handler.mac}",
                        self._handle_central_mode_update,
                    )
                )
        zone_requests = not self._central and not is_probe(self._where)
        if zone_requests and self._fan:
            # A configured fan coil without its side: ask for it before the poll on add
            # (inside ``super().async_added_to_hass()``), which must not ask ``*#4*Z*11##``
            # blindly. A restored fan mode asks at the end of ``async_restore_last_state``.
            self._ask_fan_coil_sides()
        await super().async_added_to_hass()
        if zone_requests and _ZONE_WHERE.match(self._full_where):
            # hvac_action needs the actuators (dimension 20); without asking, it stays unknown
            # until one of them switches. The gateway asks again after a reconnect. A secondary
            # probe (WHERE >= 100) or 3-token split address (3#Z#N) is not a Dimension 20 target.
            watch = getattr(self._gateway_handler, "watch_zone_actuators", None)
            if callable(watch):
                self.async_on_remove(watch(self._full_where))
            # Whether the gateway wants the silent-zone poll (#466) is decided inside the task,
            # once the startup sweep and the grace period are over: the in-band identity (WHO 13 /
            # WHO 1013) can still correct the model after the entities were added, as the
            # actuator request already allows for (``watch_zone_actuators``). So the task is
            # scheduled when the gateway wants the poll now, or while the sweep is still to run
            # and the model may still change.
            if (
                not self._interface_poll_refused
                and not is_actuator_where(self._where)
                and target_hass is not None
                and (
                    getattr(self._gateway_handler, "silent_zone_poll_needed", False) is True
                    or getattr(self._gateway_handler, "initial_discovery_pending", False) is True
                )
            ):
                task = target_hass.async_create_background_task(
                    self._poll_if_silent(), name=f"myhome_{self.entity_id}_silent_zone_poll"
                )

                def _cancel_silent_zone_poll() -> None:
                    task.cancel()

                self.async_on_remove(_cancel_silent_zone_poll)

    def _silent_zone_reads(self) -> list[str]:
        """The reads of the silent-zone poll (#466) this zone still needs, in the order they go out.

        What the zone said since the restart is not asked again; a value Home Assistant restored
        is. The temperature (``*0``) comes first because a zone without a probe refuses it, and
        that refusal ends the poll. The setpoint (``*14``), then the setpoint in force (``*12``,
        the F455 follows its reply with the zone's season frame, which gives the mode), then
        actuator 1 (``#1*20``) last: ``hvac_action`` is derived when an actuator report finds the
        mode known. The zone is named by its number: a ``#Z`` entry of ``myhome.yaml`` would
        otherwise ask through the central unit, and an actuator behind an interface is
        ``Z#N#4#II``, not ``Z#4#II#N``.
        """
        zone = zone_number(self._where)
        routing = f"{BUS_ROUTING}{self._interface}" if self._interface is not None else ""
        temperature_known = self._attr_native_current_temperature is not None and not self._current_temperature_restored
        setpoint_known = self.native_target_temperature is not None and not self._target_temperature_restored
        mode_known = self._attr_hvac_mode is not None and not self._hvac_mode_restored
        reads: list[str] = []
        if not temperature_known:
            reads.append(f"*#4*{zone}{routing}*0##")
        if not setpoint_known:
            reads.append(f"*#4*{zone}{routing}*14##")
        if not setpoint_known or not mode_known:
            reads.append(f"*#4*{zone}{routing}*12##")
        if not self._actuator_states and self._zone_active is None:
            reads.append(f"*#4*{zone}#1{routing}*20##")
        return reads

    async def _poll_if_silent(self, grace: float = SILENT_ZONE_GRACE) -> None:
        """Ask a zone that has not reported by itself since the startup sweep, read by read (#466).

        Only on a gateway in ``SILENT_ZONE_POLL`` (the one that serves WHO 4, read once the
        in-band identity has had the sweep and the grace period to settle). Nothing goes out for
        what the zone reported itself (``_silent_zone_reads``). Each read waits for the previous
        one to be answered, so a refused or unanswered read (a zone without a probe) ends it
        after one request.
        """
        wait = getattr(self._gateway_handler, "wait_for_initial_discovery", None)
        if callable(wait):
            waiting = wait()
            if inspect.isawaitable(waiting):
                await waiting
        await asyncio.sleep(grace)
        if getattr(self._gateway_handler, "silent_zone_poll_needed", False) is not True:
            return  # not an F455 after all, or its zones are another gateway's to ask
        reads = self._silent_zone_reads()
        if not reads:
            return  # the zone reported its temperature, setpoint, mode and an actuator on its own
        LOGGER.debug(
            "%s %s has not reported since the startup sweep; asking %s", self._gateway_handler.log_id, self._display_name, reads
        )
        for frame in reads:
            try:
                written: object = await self._gateway_handler.send_status_request(
                    cast(OWNCommand, OWNHeatingCommand.parse(frame))
                )
            except Exception:  # a transport error must not end the task silently (#593)
                LOGGER.exception("%s %s: `%s` could not be sent", self._gateway_handler.log_id, self._display_name, frame)
                return
            if isinstance(written, asyncio.Future):
                await asyncio.wait([written], timeout=PACED_READ_TIMEOUT)
                if not written.done() or written.cancelled():
                    return

    def _refuse_interface_write(self) -> bool:
        """Refuse WHO 4 writes that would strip ``#4#II`` and hit local main-bus ``_where``."""
        if not self._interface_poll_refused:
            return False
        iface = self._interface or self._full_where.rpartition(BUS_ROUTING)[2]
        LOGGER.warning(
            "%s %s is behind interface %s and %s does not route WHO 4 across `#4#%s`; not writing to local `%s`",
            self._gateway_handler.log_id,
            self._display_name,
            iface,
            self._gateway_handler.model,
            iface,
            self._where,
        )
        return True

    @callback
    def _handle_central_mode_update(self, master_mode: HVACMode) -> None:
        """Update subordinate zone mode when central unit changes seasonal mode."""
        prev_mode = self._attr_hvac_mode
        if master_mode == HVACMode.OFF:
            self._attr_hvac_mode = HVACMode.OFF
            self._attr_hvac_action = HVACAction.OFF
            self._mark_actuators_off()
        elif master_mode in (HVACMode.HEAT, HVACMode.COOL):
            if self._attr_hvac_mode != HVACMode.OFF:
                self._attr_hvac_mode = master_mode
        elif master_mode == HVACMode.AUTO:
            if self._attr_hvac_mode != HVACMode.OFF and HVACMode.AUTO in self._attr_hvac_modes:
                self._attr_hvac_mode = HVACMode.AUTO
        self._hydrate_nominal_from_local(prev_mode)
        self._refresh_hvac_modes()
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set new target fan mode."""
        fan_mode_map = {
            "auto": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }
        speed_code = fan_mode_map.get(str(fan_mode).lower())
        if speed_code is not None:
            if self._refuse_interface_write():
                return
            # OWNd raises for a central unit or general zone: the firmware forwards
            # *#4*Z*#11*S## only for a plain zone 1..99 (OWNd#77, A2).
            try:
                command = OWNHeatingCommand.set_fan_speed(
                    where=self._where,
                    speed=speed_code,
                    standalone=self._standalone,
                )
            except ValueError as err:
                raise ServiceValidationError(
                    f"{self._display_name} has no fan speed of its own: only a zone (1-99) takes one",
                    translation_domain=DOMAIN,
                    translation_key="fan_speed_zone_only",
                    translation_placeholders={"name": self._display_name},
                ) from err
            self._attr_fan_mode = fan_mode
            await self._gateway_handler.send(command)
            if self.hass is not None:
                self.async_write_ha_state()

    @property
    def native_target_temperature(self) -> float | None:
        if self._local_target_temperature is not None:
            return self._local_target_temperature
        else:
            return self._target_temperature

    if not _NATIVE_TEMPERATURE_API:
        # Core < 2026.11 has no native_* temperature API: serve the names it reads.
        @property  # type: ignore[misc, unused-ignore]
        def temperature_unit(self) -> str:
            return self._attr_native_temperature_unit

        @property  # type: ignore[misc, unused-ignore]
        def current_temperature(self) -> float | None:
            return self._attr_native_current_temperature

        @property  # type: ignore[misc, unused-ignore]
        def target_temperature(self) -> float | None:
            return self.native_target_temperature

    def _effective_nominal_temperature(self) -> float | None:
        """Return the nominal setpoint to write in mode changes.

        On plants where the gateway or probe reports Dimension 12 (setpoint in force)
        before the zone's operating mode is known, ``_target_temperature`` may still be
        ``None`` until Dimension 14 arrives; fall back to ``_local_target_temperature - _local_offset``
        when the zone is not OFF (#819).
        """
        if self._target_temperature is not None:
            return self._target_temperature
        if self._local_target_temperature is not None and self._attr_hvac_mode != HVACMode.OFF:
            return self._local_target_temperature - self._local_offset
        return None

    def _derive_local_offset(self, local_target: float | None, nominal_target: float | None) -> bool:
        """Derive the knob offset from a paired Dimension 12 and Dimension 14 when Dimension 13 is absent (#466)."""
        if (
            self._local_offset_reported
            or self._attr_hvac_mode in (None, HVACMode.OFF)
            or local_target is None
            or nominal_target is None
        ):
            return False
        diff = round(local_target - nominal_target, 1)
        if not (-3.0 <= diff <= 3.0 and float(diff).is_integer()):
            return False
        offset = int(diff)
        if offset == 0 and self._local_offset == 0:
            return False
        self._local_offset = offset
        self._knob_pos = "0" if offset == 0 else f"{offset:+d}"
        return True

    def _reconcile_local_target(self) -> None:
        """Update nominal setpoint and derived knob offset from a Dimension 12 report (#466)."""
        now = time.monotonic()
        written = self._written_target_temperature
        self._written_target_temperature = None
        if written is not None and now - written[1] <= _PROTECTION_FRAME_WINDOW:
            self._derive_local_offset(self._local_target_temperature, written[0])
            self._dim12_at = None
            self._dim14_at = None
        elif self._dim14_at is not None and now - self._dim14_at <= _PROTECTION_FRAME_WINDOW:
            self._derive_local_offset(self._local_target_temperature, self._target_temperature)
            self._dim12_at = None
            self._dim14_at = None
        self._target_temperature = (
            self._local_target_temperature - self._local_offset
            if self._local_target_temperature is not None
            else None
        )

    def _hydrate_nominal_from_local(self, prev_mode: HVACMode | None) -> None:
        """Promote an initial Dimension 12/14 reading once the zone is known to be active (#819)."""
        if self._central or prev_mode is not None or self._attr_hvac_mode in (None, HVACMode.OFF):
            return
        if self._local_target_temperature is not None and self._local_target_reported:
            if (
                self._written_target_temperature is None
                and self._target_temperature is not None
                and self._dim14_at is not None
                and time.monotonic() - self._dim14_at <= _PROTECTION_FRAME_WINDOW
            ):
                self._derive_local_offset(self._local_target_temperature, self._target_temperature)
                self._dim12_at = None
                self._dim14_at = None
            else:
                self._reconcile_local_target()
        elif (
            self._local_target_temperature is None
            and self._target_temperature is not None
            and not self._local_target_reported
        ):
            self._local_target_temperature = self._target_temperature + self._local_offset

    def _begin_central_flag_cycle(self) -> None:
        """Start collecting the 3550's optional flags after its remote-control frame (#819).

        On ``*#4*#0##`` (and unsolicited ``#0`` bursts) the 3550 sends its operating mode,
        ``WHAT 20``/``21``, then whichever optional flags (``WHAT 22, 23, 24, 30, 31``) are
        active (WHO 4 p. 24-25). A flag has no all-clear code; it is omitted once resolved.
        The flags are not cleared here: clearing them at the start of the burst wrote
        ``battery_fault`` False and, 2 ms later, True on every reply of a 3550 without a
        battery (live F461 + F455, 2026-11-09). A flag still missing when the burst has
        settled is cleared by ``_settle_central_flags``. A second copy of the same frame
        (climate and sensor platform routing) does not restart an open cycle.
        """
        if self._central_flags_unsub is not None:
            return
        self._central_flags_seen = set()
        if self.hass is None or self.platform is None or not self.entity_id:
            # Not live yet (discovery seed, restore): nothing is written in between, so there is
            # no flip to avoid; the flags of this burst follow directly.
            self._settle_central_flags(None)
            return
        self._central_flags_unsub = async_call_later(self.hass, _CENTRAL_FLAGS_SETTLE, self._settle_central_flags)

    @callback
    def _settle_central_flags(self, _now: Any) -> None:
        """Clear the optional central flags the last burst did not report."""
        self._central_flags_unsub = None
        seen = self._central_flags_seen
        before = self._central_flag_values()
        self._at_least_one_probe_off = 22 in seen
        self._at_least_one_probe_antifreeze = 23 in seen
        self._at_least_one_probe_manual = 24 in seen
        self._failure_detected = 30 in seen
        self._battery_fault = 31 in seen
        if not self._battery_fault:
            self._clear_fault(FaultKind.CENTRAL_BATTERY_FAULT)
        if self._central_flag_values() != before:
            self._publish_state()

    def _central_flag_values(self) -> tuple[bool, ...]:
        return (
            self._at_least_one_probe_off,
            self._at_least_one_probe_antifreeze,
            self._at_least_one_probe_manual,
            self._failure_detected,
            self._battery_fault,
        )

    @callback
    def _cancel_central_flag_cycle(self) -> None:
        if self._central_flags_unsub is not None:
            self._central_flags_unsub()
            self._central_flags_unsub = None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if self._refuse_interface_write():
            return
        if self._central:
            mode_map = {
                HVACMode.OFF: "off",
                HVACMode.HEAT: "heat",
                HVACMode.COOL: "cool",
                HVACMode.AUTO: "auto",
            }
            cmd_mode = mode_map.get(hvac_mode)
            if cmd_mode:
                command = OWNHeatingCommand.set_central_mode(
                    where=self._where,
                    mode=cmd_mode,
                )
                # Armed before sending, without a deadline: the echo can arrive while the frame
                # waits in the command pool or while send() is still awaited. The window starts
                # when the frame is written (the ``written`` future's result), not now, so a busy
                # CommandWorkerPool does not push the echo out of it.
                frame = str(command)
                armed_at = time.monotonic()
                self._central_echo = (frame, None, armed_at)
                try:
                    written: Any = await self._gateway_handler.send(command)
                except BaseException:
                    self._central_echo = None
                    raise
                if isinstance(written, asyncio.Future):

                    def _on_written(future: "asyncio.Future[float]", armed_at: float = armed_at) -> None:
                        self._anchor_central_echo(armed_at, future)

                    written.add_done_callback(_on_written)
                else:
                    self._anchor_central_echo(armed_at, None)
                if hvac_mode in (HVACMode.HEAT, HVACMode.COOL) and self._where.removeprefix("4-") in ("#0", "0"):
                    # *4*1*#0## / *4*0*#0## only change the season (WHO 4 p. 5). The 3550
                    # answers with the operating mode it is in, e.g. *4*102*#0## antifreeze,
                    # or nothing when the season does not change (live F461 and F455,
                    # 2026-11-08), so the mode shown comes from that answer. A 4-zone
                    # central (#0#1..#0#4) is not polled and its reports are routed to the
                    # zone, so it keeps the mode it was set to.
                    return
                self._central_operation = None
                if hvac_mode == HVACMode.OFF:
                    self._attr_hvac_action = HVACAction.OFF
                self._attr_hvac_mode = hvac_mode
                if self.hass is not None:
                    self.async_write_ha_state()
                    async_dispatcher_send(
                        self.hass,
                        f"myhome_central_mode_{self._gateway_handler.mac}",
                        hvac_mode,
                    )
            return

        if hvac_mode == HVACMode.OFF:
            cmd = OWNHeatingCommand.set_mode(
                where=self._where,
                mode=CLIMATE_MODE_OFF,
                standalone=self._standalone,
            )
            if cmd is not None:
                await self._gateway_handler.send(cmd)
        elif hvac_mode == HVACMode.AUTO:
            if self._standalone and getattr(self._gateway_handler, "has_central_unit", False) is not True:
                # Automatic is the central unit's program. On a plant without one the
                # gateway ACKs *4*311*#Z## and nobody acts on it, and *4*311*Z## is
                # NACKed (F461 2.0.11, tests/fixtures/traces/f461_live_2026-10-15). The
                # only frame that takes a standalone thermostat out of OFF or protection
                # is a setpoint write; mode 3 keeps the thermostat's own season.
                nominal = self._effective_nominal_temperature()
                if nominal is None:
                    LOGGER.warning(
                        "%s %s has no central unit and no known setpoint, cannot switch it on",
                        self._gateway_handler.log_id,
                        self._display_name,
                    )
                    return
                LOGGER.debug(
                    "%s %s is a standalone zone without a central unit: AUTO is written as a setpoint",
                    self._gateway_handler.log_id,
                    self._display_name,
                )
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_temperature(
                        where=self._where,
                        temperature=nominal,
                        mode=SETPOINT_WRITE_MODE,
                        standalone=True,
                    )
                )
                return
            if self._standalone:
                # *4*311*#Z## asks the central unit to put zone Z on its program (WHO 4
                # p. 9). A standalone thermostat is not one of its zones: the live F461
                # (3550 on the bus, zone 2 standalone) ACKs the frame and nothing answers.
                raise ServiceValidationError(
                    f"{self._display_name} is a standalone thermostat: the central unit has no program for it",
                    translation_domain=DOMAIN,
                    translation_key="auto_standalone_zone",
                    translation_placeholders={"name": self._display_name},
                )
            cmd = OWNHeatingCommand.set_mode(
                where=self._where,
                mode=CLIMATE_MODE_AUTO,
                standalone=self._standalone,
            )
            if cmd is not None:
                await self._gateway_handler.send(cmd)
        elif hvac_mode in (HVACMode.HEAT, HVACMode.COOL):
            heat = hvac_mode == HVACMode.HEAT
            under_central = not self._standalone and getattr(self._gateway_handler, "has_central_unit", False) is True
            if under_central and self._season_reported and self._season is not None and (
                self._season in _CONDITIONING_SEASONS if heat else self._season == SEASON_HEATING
            ):
                # A zone of a central unit runs in the central's season. The live 3550
                # (2026-11-08, plant in conditioning) answers nothing to a heating setpoint
                # for its zone 4; the season is changed on the central unit.
                raise ServiceValidationError(
                    f"{self._display_name} follows the central unit, which is in the {self._season} season",
                    translation_domain=DOMAIN,
                    translation_key="zone_heat_in_conditioning_season" if heat else "zone_cool_in_heating_season",
                    translation_placeholders={"name": self._display_name},
                )
            nominal = self._effective_nominal_temperature()
            if nominal is not None:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_temperature(
                        where=self._where,
                        temperature=nominal,
                        mode=CLIMATE_MODE_HEAT if heat else CLIMATE_MODE_COOL,
                        standalone=self._standalone,
                    )
                )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if self._refuse_interface_write():
            return
        target_temperature = float(
            kwargs.get("temperature", self._local_target_temperature)  # type: ignore[arg-type]
        ) - self._local_offset
        if self._central:
            mode = "heat" if self._attr_hvac_mode != HVACMode.COOL else "cool"
            await self._gateway_handler.send(
                OWNHeatingCommand.set_central_temperature(
                    where=self._where,
                    temperature=target_temperature,
                    mode=mode,
                )
            )
            self._target_temperature = target_temperature
            if self.hass is not None:
                self.async_write_ha_state()
            return
        await self._gateway_handler.send(
            OWNHeatingCommand.set_temperature(
                where=self._where,
                temperature=target_temperature,
                mode=SETPOINT_WRITE_MODE,
                standalone=self._standalone,
            )
        )
        # The write's echo decides what is shown next: dimension 12 where the gateway reports
        # the setpoint in force (F461, MyHomeServer1), else the commanded dimension 14.
        self._local_target_reported = False
        self._written_target_temperature = (target_temperature, time.monotonic())

    def _dimension_3_is_protection(self, message: OWNHeatingEvent) -> bool:
        """Whether a dimension 12/14 frame may be a protection setpoint rather than the nominal one.

        The trailing ``3`` cannot tell them apart (a manual write on a MyHomeServer1 plant
        ends in it too, #454), so the zone's mode decides: OFF, or not known yet, means
        protection is possible; a zone known to be running heats or cools to what it reports.
        """
        if self._attr_hvac_mode == HVACMode.OFF:
            return True
        value: list[str] = getattr(message, "dimension_value", None) or []
        return bool(len(value) > 1 and value[1] == "3" and self._attr_hvac_mode is None)

    def _remember_nominal(self) -> None:
        """Keep the nominal setpoint a dimension 12 frame is about to replace."""
        self._nominal_before_dim12 = (self._target_temperature, time.monotonic())

    def _restore_nominal_before_protection(self) -> None:
        """A protection setpoint arrives just before the frame that turns the zone OFF.

        The dimension 12 frame of a running zone was taken as the new nominal setpoint;
        if the OFF frame follows at once, it was the protection setpoint, so put the
        previous nominal back (#383).
        """
        remembered, self._nominal_before_dim12 = self._nominal_before_dim12, None
        self._dim12_at = None
        self._dim14_at = None
        if remembered is not None and time.monotonic() - remembered[1] <= _PROTECTION_FRAME_WINDOW:
            self._target_temperature = remembered[0]

    def _show_nominal(self) -> None:
        """Show the nominal setpoint plus the knob offset until the zone reports dimension 12 again.

        Used when the zone leaves OFF (the dimension 12 value seen while OFF was the protection
        setpoint) or a frame carries a new setpoint of its own.
        """
        if self._target_temperature is None:
            return
        self._local_target_temperature = self._target_temperature + self._local_offset
        self._local_target_reported = False
        self._dim12_at = None
        self._dim14_at = None

    def _apply_zone_state(self, message: OWNHeatingEvent) -> None:
        """Dimension 7: the zone's operating state, and its setpoint in state 'setpoint'.

        Protection and off turn the zone OFF but keep the nominal setpoint, as
        WHAT 102/202 do (#383).  Comfort and eco carry no temperature.
        """
        state = getattr(message, "zone_state", None)
        if state in _ZONE_STATES_OFF:
            self._restore_nominal_before_protection()
            self._attr_hvac_mode = HVACMode.OFF
            self._hvac_mode_restored = False
            self._attr_hvac_action = HVACAction.OFF
            self._mark_actuators_off()
            self._holiday_days = None
            self._program = None
            self._scenario = None
            return
        if state not in _ZONE_STATES_ON:
            return
        prev_mode = self._attr_hvac_mode
        mode = _ZONE_CONTEXT_MODES.get(getattr(message, "zone_context", None) or "")
        if mode is not None and mode in self._attr_hvac_modes:
            self._attr_hvac_mode = mode
            self._hvac_mode_restored = False  # the zone reported the mode it runs in
            if self._attr_hvac_action == HVACAction.OFF:
                self._attr_hvac_action = HVACAction.IDLE
        temperature = message.set_temperature if state == "setpoint" else None
        if temperature is not None:
            self._target_temperature = temperature
            self._target_temperature_restored = False
        else:
            self._hydrate_nominal_from_local(prev_mode)
        if self._attr_hvac_mode != HVACMode.OFF and self._target_temperature is not None and (
            temperature is not None or prev_mode == HVACMode.OFF
        ):
            self._show_nominal()

    @callback
    def _anchor_central_echo(self, armed_at: float, written: "asyncio.Future[float] | None") -> None:
        """Start the echo window at the time the command was written (MyHOMEGatewayHandler.send).

        The future resolves to the monotonic time taken just before the frame went on the
        socket; it is cancelled when the frame is never written, and then no echo comes.
        Without a future (a gateway double, a delegated send) the window starts now. Only the
        command armed at ``armed_at`` is anchored: a later command, even the same frame, keeps
        waiting for its own write.
        """
        echo = self._central_echo
        if echo is None or echo[2] != armed_at:
            return
        frame = echo[0]
        if written is None:
            self._central_echo = (frame, time.monotonic() + _CENTRAL_ECHO_WINDOW, echo[2])
            return
        if written.cancelled() or written.exception() is not None:
            self._central_echo = None
            return
        self._central_echo = (frame, float(written.result()) + _CENTRAL_ECHO_WINDOW, echo[2])

    @callback
    def _is_own_central_echo(self, message: OWNHeatingEvent) -> bool:
        """Whether ``message`` is the gateway handing back Home Assistant's own central command.

        An F455 returns ``*4*0*#0##`` on the event session 1 ms after Home Assistant sent it,
        whether or not the 3550 answers (live, 2026-11-08); an F461 does not. The echo is not
        the central unit's state: taken as a season frame it cleared the battery flag the 3550
        had just reported and turned the action from OFF to IDLE.

        The climate and sensor platforms both route WHO 4 frames to the zone, so the one
        echo frame can reach this entity twice, as the same object (live) or as another copy:
        every frame equal to the command inside the window is the echo, and the same object
        stays recognised after it.
        """
        if message is self._central_echo_seen:
            return True
        echo = self._central_echo
        if echo is None:
            return False
        frame, deadline, armed_at = echo
        now = time.monotonic()
        if (deadline is None and now - armed_at > _CENTRAL_ECHO_PENDING_LIMIT) or (
            deadline is not None and now > deadline
        ):
            self._central_echo = None
            return False
        if str(message) != frame:
            return False
        self._central_echo_seen = message
        return True

    def _is_central_addressed_mode_frame(self, message: OWNHeatingEvent) -> bool:
        """Whether a mode / season frame for a standalone zone is in the ``#Z`` (via central unit) form.

        ``*4*what*#Z##`` is a zone of the central unit (WHO 4 p. 6, p. 9, p. 65). A standalone
        thermostat answers in the plain ``Z`` form. An F455 emits ``*4*311*#2##`` after every
        status report of the standalone zone 2 (live, 2026-11-08: 8 of 8, none on the F461
        monitoring the same bus), which OWNd decodes as zone 2 in automatic.
        """
        if self._central or not self._standalone:
            return False
        if message.message_type not in (MESSAGE_TYPE_MODE, MESSAGE_TYPE_MODE_TARGET, MESSAGE_TYPE_SEASON):
            return False
        return is_central_thermo_where(message)

    def _is_generic_central_zone_frame(self, message: OWNHeatingEvent) -> bool:
        """Whether a ``*4*310*#Z##`` / ``*4*311*#Z##`` frame reaches a zone of the central unit.

        WHO 4 p. 9 lists 310 / 311 (manual / automatic generic) both as the command that sets a
        zone of the central and as a monitor frame. The 3550 reports its zones' state with the
        season in the code: ``*4*111*#1##`` for a zone in automatic heating, also as its answer to
        ``*4*311*#1##`` (MH200N, #466), ``*4*110*#3##`` (F454, #697), ``*4*210*#4##`` (F461, 0 generic
        frames in 525, 2026-11-08 / 11-09). An F455 and an MH201 put a generic ``*4*311*#Z##`` on
        their event session right before the zone's real status (``*4*210*#4##`` 59 ms later on the
        F455, ``*4*110*#4##`` on the MH201): live F455, 2026-11-09 07:45:02, at every quarter-hour
        push of the 3550, where the F461 shows ``*#4*#4*14*0240*2##`` instead. Taken as the zone's
        state it turned the zone AUTO and back every 15 minutes. So the generic frame is held
        (``_GENERIC_ZONE_FRAME_HOLD``): a seasonal status of the zone that follows replaces it, and
        only a generic frame with none after it is taken as the zone's state.
        """
        if self._central or message.message_type != MESSAGE_TYPE_MODE:
            return False
        if not is_central_thermo_where(message):
            return False
        raw = str(message)  # a MESSAGE_TYPE_MODE frame is *4*WHAT*WHERE##
        where = raw.split("*")[3] if raw.count("*") >= 3 else ""
        if where.removeprefix("#").split("#", 1)[0] == "0":
            return False
        return raw.split("*")[2] in ("310", "311")

    def _hold_generic_zone_frame(self, message: OWNHeatingEvent) -> bool:
        """Hold a generic ``#Z`` frame until the zone's seasonal status shows it was a prelude.

        Returns False when the entity is not live yet (nothing to hold it on); the frame is
        then handled at once, as before.
        """
        if self.hass is None or self.platform is None or not self.entity_id:
            return False
        self._cancel_generic_zone_frame()
        LOGGER.debug(
            "%s %s: generic mode frame for %s via the central unit; waiting for its seasonal status",
            self._gateway_handler.log_id,
            message,
            self._display_name,
        )

        @callback
        def _apply(_now: Any) -> None:
            self._generic_zone_frame_unsub = None
            self._generic_zone_frame_release = message
            self.handle_event(message)

        self._generic_zone_frame_unsub = async_call_later(self.hass, _GENERIC_ZONE_FRAME_HOLD, _apply)
        return True

    @callback
    def _cancel_generic_zone_frame(self) -> None:
        """Drop a held generic frame: the zone's own status came, or the entity goes away."""
        if self._generic_zone_frame_unsub is not None:
            self._generic_zone_frame_unsub()
            self._generic_zone_frame_unsub = None

    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Handle an event message."""
        if self._central and self._is_own_central_echo(message):
            LOGGER.debug("%s %s: own command echoed by the gateway, not a central unit report", self._gateway_handler.log_id, message)
            return
        # A central unit's own report first: on a zero-config plant its *4*111*#1## makes zone 1
        # a zone of the central before the standalone check below looks at it.
        if is_central_unit_report(message):
            self._gateway_handler.has_central_unit = True
        if self._poll_health.frame_seen():
            self._clear_unresponsive_issue()
        if self._is_central_addressed_mode_frame(message):
            LOGGER.debug(
                "%s %s: %s is a standalone thermostat, a frame for it via the central unit is not its state",
                self._gateway_handler.log_id,
                message,
                self._display_name,
            )
            return
        if message is self._generic_zone_frame_release:
            self._generic_zone_frame_release = None
        elif self._is_generic_central_zone_frame(message):
            if self._hold_generic_zone_frame(message):
                return
        elif message.message_type in (MESSAGE_TYPE_MODE, MESSAGE_TYPE_MODE_TARGET, MESSAGE_TYPE_SEASON):
            self._cancel_generic_zone_frame()
        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_current_temperature = message.main_temperature
            self._current_temperature_restored = False
            self._dim12_at = None
            self._dim14_at = None
        elif message.message_type == MESSAGE_TYPE_MAIN_HUMIDITY:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._enable_humidity()
            self._attr_current_humidity = message.main_humidity
            self._humidity_reported = True
        elif message.message_type == MESSAGE_TYPE_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._target_temperature = message.set_temperature
            self._target_temperature_restored = False
            self._written_target_temperature = None
            now = time.monotonic()
            is_protection_or_off = self._dimension_3_is_protection(message)
            if (
                not is_protection_or_off
                and self._dim12_at is not None
                and now - self._dim12_at <= _PROTECTION_FRAME_WINDOW
            ):
                self._derive_local_offset(self._local_target_temperature, self._target_temperature)
                self._dim12_at = None
                self._dim14_at = None
            else:
                self._dim14_at = (
                    now
                    if self._target_temperature is not None and self._attr_hvac_mode != HVACMode.OFF
                    else None
                )
            # Dimension 14 is the commanded setpoint. The thermostat can regulate to another one
            # (F461: 14 = 21.0 while 12 = 23.5), so a dimension 12 report stays what is shown.
            if not is_protection_or_off and not self._local_target_reported:
                self._local_target_temperature = (
                    self._target_temperature + self._local_offset
                    if self._target_temperature is not None
                    else None
                )
        elif message.message_type == MESSAGE_TYPE_LOCAL_OFFSET:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._local_offset = message.local_offset if message.local_offset is not None else 0
            self._local_offset_reported = True
            self._dim12_at = None
            self._dim14_at = None
            if message.local_control_state in (LOCAL_CONTROL_UNKNOWN, None):
                self._knob_pos = "UNKNOWN"
            elif message.local_control_state == LOCAL_CONTROL_OFFSET:
                self._knob_pos = f"{self._local_offset:+d}"
            elif message.local_control_state == LOCAL_CONTROL_NORMAL:
                self._knob_pos = "0"
            elif message.local_control_state == LOCAL_CONTROL_OFF:
                self._knob_pos = "OFF"
            elif message.local_control_state == LOCAL_CONTROL_PROTECTION:
                self._knob_pos = "*"
            elif message.local_control_state == LOCAL_CONTROL_OVERRIDE:
                self._knob_pos = "?"
            else:
                self._knob_pos = "UNKNOWN"
            if (
                self._target_temperature is not None
                and self._attr_hvac_mode != HVACMode.OFF
                and not self._local_target_reported  # dimension 12 already includes the offset
            ):
                self._local_target_temperature = self._target_temperature + self._local_offset
        elif message.message_type == MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._local_target_temperature = message.local_set_temperature
            self._local_target_reported = self._local_target_temperature is not None
            self._dim12_at = (
                time.monotonic()
                if self._local_target_reported and self._attr_hvac_mode != HVACMode.OFF
                else None
            )
            self._target_temperature_restored = False
            is_protection_or_off = self._dimension_3_is_protection(message)
            if not is_protection_or_off:
                self._remember_nominal()
                self._reconcile_local_target()
        elif message.message_type == MESSAGE_TYPE_SEASON:
            # *4*1*Z## / *4*0*Z## is the zone operation mode frame: the zone
            # (or the central unit) runs in the heating / conditioning season
            # (Legrand WHO 4 v2.0.0 p. 5, 13, 16, 19, 63). OWNd decodes it as
            # ``season``; it is not an operating-mode change. A zone that is
            # AUTO, HEAT or COOL keeps its mode (BTicino's client keeps a zone
            # in automatic on it, libqtdevices probe_device.cpp:240-253); a
            # zone that was OFF or not yet known is running again, so it takes
            # the season's mode. For a central unit the season is the plant
            # mode Home Assistant shows.
            # A standalone thermostat has no program: in the plain Z form it reports its mode
            # only as this frame, protection (102 / 202) or off (303) (WHO 4 p. 63), never as
            # automatic or manual. So for it this frame is the mode it runs in, whoever changed
            # it: Home Assistant (*#4*2*#14*0265*2## -> *4*0*2##), another gateway on the bus,
            # a touch screen or the thermostat itself. Two gateways on one bus then show the
            # same mode (live F455 + F461, 2026-11-08), and a stale restored AUTO is replaced.
            # An AUTO the zone itself reported (a MyHomeServer1 schedule, Dimension 7 context
            # automatic) is not cancelled: the season frame that follows it is its season.
            standalone_mode = self._standalone and not (
                self._attr_hvac_mode == HVACMode.AUTO and not self._hvac_mode_restored
            )
            previous_season = self._season
            self._season = message.season
            self._season_reported = True
            self._refresh_hvac_modes()
            season_mode = HVACMode.HEAT if message.season == SEASON_HEATING else HVACMode.COOL
            prev_mode = self._attr_hvac_mode
            if self._central or standalone_mode or self._attr_hvac_mode in (None, HVACMode.OFF):
                if season_mode in self._attr_hvac_modes:
                    LOGGER.debug("%s %s", self._gateway_handler.log_id, message.human_readable_log)
                    self._attr_hvac_mode = season_mode
                    self._hvac_mode_restored = False
                    if self._attr_hvac_action == HVACAction.OFF:
                        self._attr_hvac_action = HVACAction.IDLE
            else:
                LOGGER.debug(
                    "%s %s (season only, zone stays %s)",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                    self._attr_hvac_mode,
                )
            self._hydrate_nominal_from_local(prev_mode)
            if (
                prev_mode == HVACMode.OFF
                and self._attr_hvac_mode != HVACMode.OFF
                and self._target_temperature is not None
            ):
                self._show_nominal()
            if self._central and self.hass is not None and self._attr_hvac_mode is not None:
                async_dispatcher_send(
                    self.hass,
                    f"myhome_central_mode_{self._gateway_handler.mac}",
                    self._attr_hvac_mode,
                )
            self._follow_season_change(previous_season)
        elif message.message_type == MESSAGE_TYPE_MODE:
            self._hvac_mode_restored = False  # the zone itself says what it runs in
            prev_mode = self._attr_hvac_mode
            previous_season = self._season
            self._remember_season(message)
            # OWNd releases without MESSAGE_TYPE_SEASON decode the zone operation mode
            # frame *4*1*Z## / *4*0*Z## as a plain heat / cool mode; it still tells the
            # season the thermostat runs in, which the fan coil poll needs.
            raw_what = str(message).split("*")[2] if str(message).startswith("*4*") else ""
            if raw_what in ("0", "1") and not self._central:
                self._season = SEASON_HEATING if raw_what == "1" else "cooling"
                self._season_reported = True
                self._refresh_hvac_modes()
                self._follow_season_change(previous_season)
            self._program = getattr(message, "program", None)
            self._holiday_days = getattr(message, "holiday_days", None)
            self._scenario = getattr(message, "scenario", None)
            protection = _CENTRAL_PROTECTION.get(raw_what) if self._central else None
            if self._central and raw_what == _CENTRAL_GENERIC_PROTECTION and self._season is not None:
                protection = _CENTRAL_PROTECTION["102" if self._season == SEASON_HEATING else "202"]
            if self._central:
                self._central_operation = protection[0] if protection is not None else None
            if protection is not None:
                # *4*102*#0## / *4*202*#0## is the central unit in antifreeze / thermal
                # protection (WHO 4 p. 5, p. 64): the plant keeps its season and holds the
                # protection setpoint (live 3550: zone 4 at 7.0 / 35.0 °C), it is not off.
                # BTicino's client shows it as a status of the season (libqtdevices
                # thermal_device.cpp WIN_PROTECTION / SUM_PROTECTION).
                LOGGER.debug("%s %s (%s)", self._gateway_handler.log_id, message.human_readable_log, protection[0])
                if protection[1] in self._attr_hvac_modes:
                    self._attr_hvac_mode = protection[1]
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_AUTO and HVACMode.AUTO in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.AUTO
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_COOL and HVACMode.COOL in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.COOL
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_HEAT and HVACMode.HEAT in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.HEAT
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_OFF:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._restore_nominal_before_protection()
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.OFF
                self._mark_actuators_off()
            self._hydrate_nominal_from_local(prev_mode)
            if (
                prev_mode == HVACMode.OFF
                and self._attr_hvac_mode != HVACMode.OFF
                and self._target_temperature is not None
            ):
                self._show_nominal()
            if self._central and self.hass is not None and self._attr_hvac_mode is not None:
                async_dispatcher_send(
                    self.hass,
                    f"myhome_central_mode_{self._gateway_handler.mac}",
                    self._attr_hvac_mode,
                )
        elif message.message_type == MESSAGE_TYPE_MODE_TARGET:
            if self._central:
                self._central_operation = None
            self._hvac_mode_restored = False
            prev_mode = self._attr_hvac_mode
            self._remember_season(message)
            self._program = getattr(message, "program", None)
            self._holiday_days = getattr(message, "holiday_days", None)
            self._scenario = getattr(message, "scenario", None)
            if message.mode == CLIMATE_MODE_AUTO and HVACMode.AUTO in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.AUTO
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_COOL and HVACMode.COOL in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.COOL
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_HEAT and HVACMode.HEAT in self._attr_hvac_modes:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._attr_hvac_mode = HVACMode.HEAT
                if self._attr_hvac_action == HVACAction.OFF:
                    self._attr_hvac_action = HVACAction.IDLE
            elif message.mode == CLIMATE_MODE_OFF:
                LOGGER.debug(
                    "%s %s",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                )
                self._restore_nominal_before_protection()
                self._attr_hvac_mode = HVACMode.OFF
                self._attr_hvac_action = HVACAction.OFF
                self._mark_actuators_off()
            if message.set_temperature is not None and message.set_temperature >= 0:
                self._target_temperature = message.set_temperature
                self._target_temperature_restored = False
                self._show_nominal()
            else:
                self._hydrate_nominal_from_local(prev_mode)
            if self._central and self.hass is not None and self._attr_hvac_mode is not None:
                async_dispatcher_send(
                    self.hass,
                    f"myhome_central_mode_{self._gateway_handler.mac}",
                    self._attr_hvac_mode,
                )
        elif message.message_type == MESSAGE_TYPE_ZONE_STATE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._apply_zone_state(message)
        elif message.message_type == MESSAGE_TYPE_ACTION:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            # Valves status (dimension 19): OWNd sets cooling_fan_on / fan_on only for a
            # fan-coil value (5..8) on that side, so their presence tells which side is a fan coil.
            sides_resolved = False
            if getattr(message, "dimension", None) == 19:
                sides_resolved = not self._fan_coil_sides
                self._fan_coil_sides = {
                    "cooling": isinstance(getattr(message, "cooling_fan_on", None), bool),
                    "heating": isinstance(getattr(message, "fan_on", None), bool),
                }
                self._fan_coil_sides_asked_at = None
                self._fan_coil_sides_held = False
                if self._has_no_fan_coil_sides() and not self._fan_configured:
                    # Pure-valve actuator channels can report value 5 on dimension 20 (issue #466 H/L4684
                    # zones 11..88) while dimension 19 carries 0..4 on both sides once _ask_fan_coil_sides()
                    # (*#4*Z*19##) is answered (myhomeserver1_live_2026-10-25 zones 35..70, issue_674 F455):
                    # retract the tentative fan_mode once dimension 19 proves neither side is a fan coil.
                    self._disable_fan_mode()
            # Actuator status (Dimension 20) with values >= 5 reports fancoil fan status in OWNd,
            # unless dimension 19 already proved neither side of an unconfigured zone is a fan coil.
            is_fan = (
                isinstance(getattr(message, "fan_speed", None), int)
                or isinstance(getattr(message, "fan_on", None), bool)
            ) and not (self._has_no_fan_coil_sides() and not self._fan_configured)
            fan_enabled_now = is_fan and not self._fan
            if is_fan:
                self._enable_fan_mode()
                if getattr(message, "dimension", None) == 20:
                    # The actuator report revealed a fan coil; ask which side it is on.
                    self._ask_fan_coil_sides()
                speed = getattr(message, "fan_speed", None)
                if speed == 1:
                    self._running_fan_speed = "low"
                elif speed == 2:
                    self._running_fan_speed = "medium"
                elif speed == 3:
                    self._running_fan_speed = "high"
                elif getattr(message, "fan_on", None) is False or speed == 4:
                    self._running_fan_speed = "off"
                else:
                    self._running_fan_speed = None
            if (
                (sides_resolved or (fan_enabled_now and self._fan_coil_sides))
                and self._fan
                and self._season_has_fan_coil()
            ):
                # The entity is not polled after add (``should_poll`` is False) and the poll on
                # add skipped ``*#4*Z*11##`` while the side was unknown or pending, or the fan
                # coil showed up (dimension 20) once the side was already known: ask for the
                # configured speed now, or ``fan_mode`` keeps the synthetic ``auto`` (dimension 20
                # value 5, fan off, carries no speed).
                self._request_zone_dimension(11)

            is_active = bool(message.is_active())
            dimension = getattr(message, "dimension", None)
            if dimension == 20:
                actuator_id = thermo_event_actuator(message)
                self._actuator_states[actuator_id] = is_active
                self._active_source = "actuators"
            else:
                # Dimension 19 (valves status) is the zone as a whole. OWNd <= 2.0.0b11 clears
                # _is_active when a fan-coil side is off (value 5), even if the other side's
                # valve is open (*#4*2*19*5*1##, OWNd#116).
                if dimension == 19:
                    is_active = bool(
                        is_active
                        or message.is_heating()
                        or message.is_cooling()
                        or getattr(message, "cooling_fan_on", None)
                        or getattr(message, "fan_on", None)
                    )
                self._zone_active = is_active
                self._active_source = "zone"
                if not self._zone_active:
                    # Neither side is active: every actuator of the zone is off.
                    self._actuator_states = dict.fromkeys(self._actuator_states, False)
            any_active = self._any_actuator_active()

            if any_active:
                if self._heating and self._cooling:
                    if message.is_heating():
                        self._attr_hvac_action = HVACAction.HEATING
                    elif message.is_cooling():
                        self._attr_hvac_action = HVACAction.COOLING
                    elif self._season == SEASON_HEATING:
                        self._attr_hvac_action = HVACAction.HEATING
                    elif self._season in (SEASON_CONDITIONING, "cooling"):
                        self._attr_hvac_action = HVACAction.COOLING
                    elif self._attr_hvac_mode == HVACMode.COOL:
                        self._attr_hvac_action = HVACAction.COOLING
                    elif self._attr_hvac_mode == HVACMode.HEAT:
                        self._attr_hvac_action = HVACAction.HEATING
                    elif self._attr_hvac_mode == HVACMode.AUTO:
                        if (
                            self._target_temperature is not None
                            and self._attr_native_current_temperature is not None
                        ):
                            if self._attr_native_current_temperature < self._target_temperature:
                                self._attr_hvac_action = HVACAction.HEATING
                            else:
                                self._attr_hvac_action = HVACAction.COOLING
                elif self._heating:
                    self._attr_hvac_action = HVACAction.HEATING
                elif self._cooling:
                    self._attr_hvac_action = HVACAction.COOLING
            elif self._attr_hvac_mode == HVACMode.OFF:
                self._attr_hvac_action = HVACAction.OFF
            else:
                self._attr_hvac_action = HVACAction.IDLE
        elif message.message_type == MESSAGE_TYPE_FAN_SPEED or (
            hasattr(message, "fan_speed") and message.fan_speed is not None
        ):
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._enable_fan_mode()
            self._fan_mode_reported = True
            speed = getattr(message, "fan_speed", None)
            if speed == 0:
                self._attr_fan_mode = "auto"
            elif speed == 1:
                self._attr_fan_mode = "low"
            elif speed == 2:
                self._attr_fan_mode = "medium"
            elif speed == 3:
                self._attr_fan_mode = "high"
            elif getattr(message, "fan_on", None) is True:
                self._attr_fan_mode = "auto"
        elif self._central and (diag_what := _central_diagnostic_what(message)) is not None:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                getattr(message, "human_readable_log", str(message)),
            )
            if diag_what in (20, 21):
                self._begin_central_flag_cycle()
            elif diag_what in _CENTRAL_FLAG_WHATS:
                self._central_flags_seen.add(diag_what)
            if diag_what == 20:
                self._remote_control = "disabled"
                self._report_fault(FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED, code="20")
            elif diag_what == 21:
                self._remote_control = "enabled"
                self._clear_fault(FaultKind.CENTRAL_REMOTE_CONTROL_DISABLED)
            elif diag_what == 22:
                self._at_least_one_probe_off = True
            elif diag_what == 23:
                self._at_least_one_probe_antifreeze = True
            elif diag_what == 24:
                self._at_least_one_probe_manual = True
            elif diag_what == 30:
                self._failure_detected = True
            elif diag_what == 31:
                self._battery_fault = True
                self._report_fault(FaultKind.CENTRAL_BATTERY_FAULT, code="31")

        self._refresh_bus_state_complete()
        self._publish_state()
