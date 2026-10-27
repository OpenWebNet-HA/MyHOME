import asyncio
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
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity
from .poll_health import PollHealth
from .thermo_switch import load_thermo_switch_subentries
from .where_grammar import (
    canonical_thermo_zone,
    is_probe,
    is_pump,
    parse_thermo_actuator_where,
    thermo_event_actuator,
    where_param,
    zone_number,
)

PLATFORM = Platform.CLIMATE
PARALLEL_UPDATES = 0

# A dimension 12 frame and the frame that turns the zone OFF follow within ~0.1 s (#454, #383)
_PROTECTION_FRAME_WINDOW = 2.0
_ZONE_CONTEXT_MODES = {"heating": HVACMode.HEAT, "cooling": HVACMode.COOL, "automatic": HVACMode.AUTO}
_ZONE_STATES_ON = ("setpoint", "comfort", "eco")
_ZONE_STATES_OFF = ("protection", "off")

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
        zone = zone_number(where)
        cfg = _zone_config(configured, ctx.address, ctx.key) or ctx.cfg
        suffix = f"{zone}I{interface}" if interface else zone
        is_central = zone in ("0", "01") or where in ("#0", "#0#1")
        if ctx.source != "bus":
            is_central = cfg.get(CONF_CENTRAL, is_central)
        default_name = f"Central Unit {suffix}" if is_central and ctx.source != "bus" else f"Climate Zone {suffix}"
        registry_name = getattr(ctx.registry_entry, "name", None)
        name = cfg.get(CONF_NAME) or (registry_name if isinstance(registry_name, str) else None) or default_name
        default_model = (
            "Central Unit (3550)" if where == "#0" else "Central Unit (4695)" if where == "#0#1" else "Heating Zone"
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
        if not ctx.address.interface:
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
    where, zone = address.where, zone_number(address.where)
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
    if not interface and len(tail) > 1 and tail[0] == "4":
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

        self._central = True if self._where in ("#0", "#0#1") else central
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
        self._attr_hvac_modes = [HVACMode.OFF]
        self._heating = heating
        self._cooling = cooling
        if heating or cooling:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            self._attr_hvac_modes.append(HVACMode.AUTO)
            if heating:
                self._attr_hvac_modes.append(HVACMode.HEAT)
            if cooling:
                self._attr_hvac_modes.append(HVACMode.COOL)

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
        self._program: int | None = None
        self._holiday_days: int | None = None
        self._scenario: int | None = None
        self._attr_current_humidity: float | None = None
        self._target_temperature: float | None = None
        self._local_offset: float = 0
        self._knob_pos: str = "UNKNOWN"
        self._local_target_temperature: float | None = None
        # True while _local_target_temperature is the zone's own dimension 12 report (the
        # setpoint in force, offset included): dimension 14 and 13 frames then leave it alone.
        self._local_target_reported = False
        self._current_temperature_restored = False
        self._nominal_before_dim12: tuple[float | None, float] | None = None
        self._poll_health = PollHealth()

        self._attr_hvac_mode: HVACMode | None = None
        self._attr_hvac_action: HVACAction | None = None

        if (
            not self._central
            and not self._gateway_supports_zone_status
            and not self._polls_temperature
            and not self._fan
            and not self._humidity
        ):
            self._poll_on_add = False
        elif self._central and self._where not in ("#0", "0"):
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
        """Whether the per-zone temperature poll is withheld because the zone sits behind an F422.

        The firmware drops *#4*Z#4#II*0## before the bus on these gateways (own-firmware-oracle
        PR #60: nack/silent on F450, F454, F459 and MH202 among the SCS-only models; the MH202
        even crashes on *#4*Z#4#II##), so the zone waits for the bus to report instead.
        """
        gateway = getattr(self, "_gateway_handler", None)
        return (
            self._temperature_poll_wanted
            and self._interface is not None
            and getattr(gateway, "interface_zone_request_refused", False) is True
        )

    @property
    def _polls_temperature(self) -> bool:
        """Whether this zone asks for its temperature (*#4*Z*0##) on an SCS-only gateway (#185).

        A pure predicate: ``async_update`` logs (once) when it withholds the poll.
        """
        return self._temperature_poll_wanted and not self._interface_poll_refused

    def _enable_fan_mode(self) -> None:
        """Dynamically enable fan mode support if not already enabled."""
        if self._central:
            return
        if not self._fan:
            self._fan = True
            self._poll_on_add = True
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
            self._poll_on_add = True

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
        if not self._central:
            attrs.update(self._poll_health.attributes())
        return attrs

    async def async_restore_last_state(self, last_state: State | None) -> None:
        """Restore climate state from HA storage."""
        if last_state is not None:
            if not self._central:
                self._poll_health.restore(last_state.attributes)
            else:
                self._clear_unresponsive_issue()
        if last_state is not None and last_state.state is not None:
            try:
                restored_mode = HVACMode(last_state.state)
                if restored_mode in self._attr_hvac_modes:
                    self._attr_hvac_mode = restored_mode
                else:
                    self._attr_hvac_mode = HVACMode.OFF
            except (ValueError, TypeError):
                self._attr_hvac_mode = HVACMode.OFF
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
        if self._central and self._where not in ("#0", "0"):
            # 4-zone central units (#0#1) do not participate in point-to-point status polling;
            # bus captures confirm querying #0#1 times out on plants without a physical 4-zone unit (#629).
            # They receive setpoints via commands (*#4*#0#1*#14*T*M##), broadcast events, or restored state.
            return
        # Humidity is asked only where the gateway forwards the request: the emulated firmware
        # of F450, F453, F453AV, F454, H4684, MH200, MH200N and MH202 did not (own-firmware-oracle
        # thermo-zone-requests finding, emulated firmware). Whether those gateways put the probe's
        # humidity on the event session is not traced; any *#4*<WHERE>*60*<HH>## delivered there
        # still updates current_humidity (handle_event) without a poll.
        wants_humidity = self._humidity and getattr(self._gateway_handler, "humidity_request_refused", False) is not True
        if self._interface_poll_refused and not self._interface_poll_skip_logged:
            # The temperature poll is withheld (the fan / humidity requests below may still go out).
            LOGGER.debug(
                "%s %s is behind interface %s and %s firmware drops `*#4*%s*0##`; waiting for the zone to report",
                self._gateway_handler.log_id,
                self._display_name,
                self._interface,
                self._gateway_handler.model,
                self._full_where,
            )
            self._interface_poll_skip_logged = True
        if (
            not self._central
            and not self._gateway_supports_zone_status
            and not self._polls_temperature
            and not self._fan
            and not wants_humidity
        ):
            # SCS-only gateways (F450, F453, F453AV, F454, F459, H4684, MH200, MH200N, MH202 in the
            # firmware oracle; F455 and MH201 by field reports) expand the dimensionless zone status
            # request *#4*Z## into four bus queries, dimension 0, 12, 14 and 13 ($0499ZZ3000 / 12 /
            # 0E / 0D in trace-corpus.tsv). Against a 99-zone central unit every query is answered
            # (F454 + #0, issue_629 log: *#4*1##..*#4*15## complete in ~1.3 s each with all five
            # frames); a standalone thermostat answers only some of them, and the command session
            # then ends with a NACK after 1.5-2 s (MH202 ACKs). The 10 s socket hang of #674 was an
            # F455, not in the oracle, on actuator-only channels (*#4*Z*19*0*0##) where nothing
            # answered. MyHomeServer1 replies to *#4*Z## from its cache (#649); F460 and F461 expand
            # it into one request ($00499013000) and answer it (#714): those three keep the poll.
            # Where the SCS-only gateway also drops the general request *#4*0##, the zone asks for
            # its temperature below (_polls_temperature); everywhere else it waits for the sweep
            # replies or for the thermostat's own reports.
            return
        if self._poll_health.should_skip(time.time()):
            LOGGER.debug("%s %s did not answer its last polls; not asking again yet", self._gateway_handler.log_id, self._display_name)
            self._raise_unresponsive_issue()
            return
        if self._central or self._gateway_supports_zone_status:
            # A 99-zone central unit (#0) answers the plain status request (*#4*#0##) within ~0.17 s,
            # reporting its mode via *4*202*#0## (conditional OFF) and operational flags (#629). Dimension 14
            # (*#4*#0*14##) is NACKed. Only an F454 has been captured so far, so the poll is informational:
            # an unanswered one never counts towards the "unresponsive zone" repair.
            request = OWNHeatingCommand.status(self._full_where)
            frames_before = self._poll_health.frames
            written = await self._gateway_handler.send_status_request(request)
            if isinstance(written, asyncio.Future) and not self._central:
                written.add_done_callback(lambda future: self._poll_answered(future, frames_before))
        elif self._polls_temperature:
            # One bus query instead of the four that *#4*Z## expands into: *#4*Z*0## is forwarded on
            # all twelve oracle gateways (thermo-ts10.tsv) and a populated zone answers it in ~120 ms
            # (F461 2.0.11, f461_live_2026-10-23 rows 521-525: *#4*2*0## -> *#4*2*0*0239## + ACK in
            # 0.119 s), which keeps a single-worker MH200 / MH201 queue short. *#4*0## is refused on
            # these gateways, so this is the zone's only way to a temperature after a restart. A
            # zone behind an F422 is only polled where the firmware forwards Z#4#II requests
            # (_polls_temperature).
            # No _poll_answered callback on purpose: the oracle's *#4*1*0## NACK on 11 of 12 images
            # is the bus TX timeout of an unpopulated zone (the QEMU images have no thermostat
            # wired; the live F461 NACKs the unpopulated zone 1 the same way after 2.07 s, rows
            # 374-376), not a verdict on a populated one. Until a command-session capture of
            # *#4*Z*0## on an SCS-only gateway (F454 / MH200) exists, the result is left uncounted
            # rather than risk marking a healthy zone unresponsive on a NACK.
            await self._gateway_handler.send_status_request(OWNHeatingCommand.get_temperature(self._full_where))
        if self._fan and self._season_has_fan_coil():
            await self._gateway_handler.send_status_request(
                cast(OWNCommand, OWNHeatingCommand.parse(f"*#4*{self._full_where}*11##"))
            )
        if wants_humidity:
            await self._gateway_handler.send_status_request(
                cast(OWNCommand, OWNHeatingCommand.parse(f"*#4*{self._full_where}*60##"))
            )
        elif self._humidity:
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
            if not getattr(self._gateway_handler, "is_connected", False) or self._poll_health.frames != frames_before:
                return  # the gateway was away, or the zone did answer
            if self._poll_health.failed(time.time()):
                LOGGER.info("%s %s did not answer its status request twice in a row", self._gateway_handler.log_id, self._display_name)
                self._raise_unresponsive_issue()
            self._publish_state()
        elif self._poll_health.answered():
            self._clear_unresponsive_issue()
            self._publish_state()

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

    def _remember_season(self, message: Any) -> None:
        """Keep the season a mode frame carries (``*4*102*#0##`` heating, ``*4*202*#0##`` conditioning).

        OWNd decodes the off state of a central unit per season (OWNd#97); an older
        OWNd gives no season and the last known one stays (#699).
        """
        season = getattr(message, "season", None)
        if isinstance(season, str):
            self._season = season

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
            elif self._standalone_config is not True:
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
        if zone_requests:
            # hvac_action needs the actuators (dimension 20); without asking, it stays unknown
            # until one of them switches. The gateway asks again after a reconnect. A secondary
            # probe (WHERE >= 100) is push-only: *#4*105*...## NACKs in 2.1 s on the F461.
            watch = getattr(self._gateway_handler, "watch_zone_actuators", None)
            if callable(watch):
                self.async_on_remove(watch(self._full_where))

    @callback
    def _handle_central_mode_update(self, master_mode: HVACMode) -> None:
        """Update subordinate zone mode when central unit changes seasonal mode."""
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

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if self._central:
            mode_map = {
                HVACMode.OFF: "off",
                HVACMode.HEAT: "heat",
                HVACMode.COOL: "cool",
                HVACMode.AUTO: "auto",
            }
            cmd_mode = mode_map.get(hvac_mode)
            if cmd_mode:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_central_mode(
                        where=self._where,
                        mode=cmd_mode,
                    )
                )
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
                if self._target_temperature is None:
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
                        temperature=self._target_temperature,
                        mode=SETPOINT_WRITE_MODE,
                        standalone=True,
                    )
                )
                return
            cmd = OWNHeatingCommand.set_mode(
                where=self._where,
                mode=CLIMATE_MODE_AUTO,
                standalone=self._standalone,
            )
            if cmd is not None:
                await self._gateway_handler.send(cmd)
        elif hvac_mode == HVACMode.HEAT:
            if self._target_temperature is not None:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_temperature(
                        where=self._where,
                        temperature=self._target_temperature,
                        mode=CLIMATE_MODE_HEAT,
                        standalone=self._standalone,
                    )
                )
        elif hvac_mode == HVACMode.COOL:
            if self._target_temperature is not None:
                await self._gateway_handler.send(
                    OWNHeatingCommand.set_temperature(
                        where=self._where,
                        temperature=self._target_temperature,
                        mode=CLIMATE_MODE_COOL,
                        standalone=self._standalone,
                    )
                )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
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

    def _apply_zone_state(self, message: OWNHeatingEvent) -> None:
        """Dimension 7: the zone's operating state, and its setpoint in state 'setpoint'.

        Protection and off turn the zone OFF but keep the nominal setpoint, as
        WHAT 102/202 do (#383).  Comfort and eco carry no temperature.
        """
        state = getattr(message, "zone_state", None)
        if state in _ZONE_STATES_OFF:
            self._restore_nominal_before_protection()
            self._attr_hvac_mode = HVACMode.OFF
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
            if self._attr_hvac_action == HVACAction.OFF:
                self._attr_hvac_action = HVACAction.IDLE
        temperature = message.set_temperature if state == "setpoint" else None
        if temperature is not None:
            self._target_temperature = temperature
        if self._attr_hvac_mode != HVACMode.OFF and self._target_temperature is not None and (
            temperature is not None or prev_mode == HVACMode.OFF
        ):
            self._show_nominal()

    @callback
    def handle_event(self, message: OWNHeatingEvent) -> None:
        """Handle an event message."""
        if self._poll_health.frame_seen():
            self._clear_unresponsive_issue()
        if message.message_type == MESSAGE_TYPE_MAIN_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._attr_native_current_temperature = message.main_temperature
            self._current_temperature_restored = False
        elif message.message_type == MESSAGE_TYPE_MAIN_HUMIDITY:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._enable_humidity()
            self._attr_current_humidity = message.main_humidity
        elif message.message_type == MESSAGE_TYPE_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            self._target_temperature = message.set_temperature
            is_protection_or_off = self._dimension_3_is_protection(message)
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
            is_protection_or_off = self._dimension_3_is_protection(message)
            if not is_protection_or_off:
                self._remember_nominal()
                self._target_temperature = (
                    self._local_target_temperature - self._local_offset
                    if self._local_target_temperature is not None
                    else None
                )
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
            previous_season = self._season
            self._season = message.season
            season_mode = HVACMode.HEAT if message.season == SEASON_HEATING else HVACMode.COOL
            prev_mode = self._attr_hvac_mode
            if self._central or self._attr_hvac_mode in (None, HVACMode.OFF):
                if season_mode in self._attr_hvac_modes:
                    LOGGER.debug("%s %s", self._gateway_handler.log_id, message.human_readable_log)
                    self._attr_hvac_mode = season_mode
                    if self._attr_hvac_action == HVACAction.OFF:
                        self._attr_hvac_action = HVACAction.IDLE
            else:
                LOGGER.debug(
                    "%s %s (season only, zone stays %s)",
                    self._gateway_handler.log_id,
                    message.human_readable_log,
                    self._attr_hvac_mode,
                )
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
            prev_mode = self._attr_hvac_mode
            previous_season = self._season
            self._remember_season(message)
            # OWNd releases without MESSAGE_TYPE_SEASON decode the zone operation mode
            # frame *4*1*Z## / *4*0*Z## as a plain heat / cool mode; it still tells the
            # season the thermostat runs in, which the fan coil poll needs.
            raw_what = str(message).split("*")[2] if str(message).startswith("*4*") else ""
            if raw_what in ("0", "1") and not self._central:
                self._season = SEASON_HEATING if raw_what == "1" else "cooling"
                self._follow_season_change(previous_season)
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
                self._show_nominal()
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
                actuator_id = str(
                    getattr(message, "actuator", None)
                    or getattr(message, "_actuator", None)
                    or (message._where_param[0] if getattr(message, "_where_param", None) else "1")
                )
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

        self._publish_state()
