import asyncio
import inspect
import time
from collections.abc import Callable
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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
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

from .climate_discovery import ZoneDiscovery, _two_digit_interface, _zone_address, _zone_route_keys
from .const import (
    BUS_ROUTING,
    CONF_CENTRAL,
    CONF_COOLING_SUPPORT,
    CONF_DEVICE_MODEL,
    CONF_FAN_SUPPORT,
    CONF_HEATING_SUPPORT,
    CONF_HUMIDITY_SUPPORT,
    CONF_MANUFACTURER,
    CONF_STANDALONE,
    CONF_STANDALONE_ZONES,
    DOMAIN,
    LOGGER,
)
from .data import get_runtime_data
from .device_health import FaultKind
from .discovery import DeviceContext, PlatformDiscovery
from .gateway import (
    _ZONE_WHERE,
    KNOB_READ_IN_SILENT_ZONE_POLL,
    SILENT_ZONE_GRACE,
    MyHOMEGatewayHandler,
)
from .gateway_events import note_central_unit_report
from .myhome_device import MyHOMEEntity
from .poll_health import PollHealth
from .thermo_switch import load_thermo_switch_subentries
from .where_grammar import (
    canonical_thermo_zone,
    central_report_zone,
    is_actuator_where,
    is_central_thermo_where,
    is_central_unit_report,
    is_probe,
    thermo_dim19_status,
    thermo_dim20_actuator_target,
    thermo_dim20_level,
    thermo_dim20_status,
    thermo_event_actuator,
    thermo_event_interface,
    validate_standalone_zones,
    zone_number,
)

PLATFORM = Platform.CLIMATE
# A zone found on the bus that reports the season the central unit is not in: how long to wait
# before its setpoint in force is read again (``*#4*Z*12##``, answered with the zone's season
# frame) to see whether it still does. A zone of a central unit follows a season change of the
# central unit: 0.53 s and 0.58 s on a 3550 (central_restore_3100_live_2026-11-12, both
# monitors), 2.0 s on a MyHomeServer1 plant, where one status of the zone sent 0.66 s after
# the change still carried the old season (issue_453, ``*4*202*#0##`` 19:39:28.629,
# ``*4*1*4##`` 19:39:29.289, ``*4*202*4##`` 19:39:30.644).
STANDALONE_SEASON_CONFIRM_DELAY = 10.0
# After the central unit changes season its zones report the new state one after the other
# (3550 with two zones, five timed changes on 2026-11-17 / 11-18: zone 3, then zone 4 about
# 0.5 s later, each zone's first frame 0.42 to 1.15 s after the central unit's), and a zone
# asked before it has followed still answers in the old season (issue_453 above, 0.66 s after
# the change). For a large plant the only figure is one sentence of #697, an F454 with a
# 99-zone central unit, two changes 8 s apart and no frame times: "The zones follow the
# central within about 14 seconds." So no zone is compared with a season the central unit
# changed to less than this long ago (four times that figure): read too early, a zone of the
# central unit would be taken as standalone and written directly, which that F454 refuses.
# Not waited when the season is only learned (at start, after a reconnect): no zone is moving.
CENTRAL_SEASON_SETTLE = 60.0
PARALLEL_UPDATES = 0
# How long one read of the silent-zone poll (#466) waits for its answer before giving up.
PACED_READ_TIMEOUT = 60.0
# A read of the silent-zone poll that the gateway acknowledged with no frame on the command
# session: how long a frame of the zone may still arrive on the event session before the read
# counts as unanswered and ends the poll. A margin, not a measured value: on a physical F455
# 1.0.76 the reply was on the command session before the ACK for 26 of 27 reads of the poll
# (two starts, tests/fixtures/traces/issue_932/README.md).
EMPTY_REPLY_SETTLE = 1.0

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

# How long a zone setpoint written in the ``#Z`` form (``*#4*#Z*#14*T*M##``, zone via the
# central unit, WHO 4 p. 6) waits for the plant to confirm it, and the direct form after it.
# Live 3550 behind an F461 2.0.11 and an F455 1.0.76 (2026-11-17, zone 3, a zone of the 3550):
# ``*#4*#3*#14*0250*3##`` was followed on the bus by the central unit's own ``*#4*3*#14*0250*1##``
# 34 ms after the ACK and by ``*4*110*#3##`` / ``*#4*#3*14*0250*1##`` 0.55 s after it. The same
# write for zone 2, a thermostat the 3550 was not running that day, was acknowledged by both
# gateways and nothing answered (2026-11-18 03:01:57 through the F455, 03:03:19 through the F461);
# the direct ``*#4*2*#14*0210*3##`` was answered by the thermostat's ``*#4*2*12*0210*3##`` 45 ms
# after the ACK (tests/fixtures/traces/standalone_zone_2026_11_18).
SETPOINT_CONFIRM_TIMEOUT = 3.0
# How long the write itself may take to be acknowledged: as LOCK_WRITE_TIMEOUT (lock.py),
# reopening an idle command session can take OWNd's whole reconnect loop before the frame goes out.
SETPOINT_DELIVERY_TIMEOUT = 15.0
# After the central unit answered for the zone without the written setpoint: how long its
# setpoint report may still follow before the zone is read (2 ms after the mode frame on an
# F461, 03:34:22.797 / .799 in standalone_zone_2026_11_18; an F455 does not show it).
SETPOINT_CENTRAL_REPORT_GRACE = 0.5
# How long the read ``*#4*Z*14##`` that settles an unconfirmed write may take (a zone answered it
# in 0.09-0.51 s on the F461 and the F455; a dead zone of a MyHomeServer1 was NACKed after ~6.4 s).
SETPOINT_READ_TIMEOUT = 10.0

# Core 2026.11 renames the ClimateEntity temperature API to native_* and logs the
# old names as deprecated (unsupported from 2027.11). Older cores, down to the
# minimum 2026.8, only read the old names; MyHOMEClimate bridges them while they do.
_NATIVE_TEMPERATURE_API = hasattr(ClimateEntity, "native_temperature_unit")


def _delivered(written: object) -> bool:
    """Whether a frame's delivery future says the gateway acknowledged it (no future: assumed so)."""
    if not isinstance(written, asyncio.Future):
        return True
    return written.done() and not written.cancelled() and written.exception() is None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the heating zones of a gateway (WHO=4).

    Which zones exist, and when a zone entity is created, is ``climate_discovery``: the
    registry, ``myhome.yaml``, the bus, and the reads at start.
    """
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True
    load_thermo_switch_subentries(config_entry, runtime.platforms)
    gateway = runtime.gateway
    # Zones the owner marked as standalone thermostats in the gateway options (local bus).
    standalone_zones = frozenset(
        validate_standalone_zones((getattr(config_entry, "options", None) or {}).get(CONF_STANDALONE_ZONES))[0]
    )

    def build(ctx: DeviceContext, cfg: dict[str, Any]) -> MyHOMEClimate:
        """The entity of one zone or central unit, from its ``myhome.yaml`` entry where it has one."""
        where, interface = ctx.address.where, ctx.address.interface
        clean_where = where.removeprefix("4-")
        zone = zone_number(where)
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
            # ``myhome.yaml`` says whether a zone cools (``cool:``, False unless stated). A zone
            # found on the bus has no such entry: it learns it from its valve status (None here).
            # A central unit has no valve status and runs both seasons, as before.
            cooling=cfg.get(CONF_COOLING_SUPPORT, True) if is_central else cfg.get(CONF_COOLING_SUPPORT),
            fan=cfg.get(CONF_FAN_SUPPORT, False),
            humidity=cfg.get(CONF_HUMIDITY_SUPPORT, False),
            standalone=(
                True
                if not interface
                and not is_central
                and not is_actuator_where(where)
                and canonical_thermo_zone(where) in standalone_zones
                else cfg.get(CONF_STANDALONE)
            ),
            central=is_central,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, default_model),
            gateway=gateway,
        )
        return entity

    zones = ZoneDiscovery(hass, config_entry, runtime, build)
    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="4", event_type=OWNHeatingEvent, build=zones.build,
        accept=zones.accept, reject_registry_entry=zones.duplicate, known_keys=zones.known_keys,
        address=_zone_address, route_keys=_zone_route_keys,
        general_is_device=True,  # WHERE=0 frames name their zone in a parameter; _zone_address decides
    ).start()
    zones.start_reads()
    return True



async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    _configured_climate_devices = runtime.platforms[PLATFORM]

    for _climate_device in list(_configured_climate_devices.keys()):
        del runtime.platforms[PLATFORM][_climate_device]
    return True


class MyHOMEClimate(MyHOMEEntity, ClimateEntity):
    _unrecorded_attributes = frozenset({"actuator_levels"})

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        device_id: str,
        who: str,
        where: str,
        heating: bool,
        cooling: bool | None,
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
        # A zone nobody configured, on a plant with a central unit: how it is written comes from
        # the bus. ``_standalone_detected`` once a setpoint the zone did not take through the
        # central unit was taken in the direct form (``_confirm_setpoint_write``), until the
        # central unit reports the zone in the ``#Z`` form again (``_note_central_zone_report``,
        # which sets ``_central_zone_seen``).
        self._central_zone_seen = False
        self._standalone_detected = False
        # Whether the zone's own (plain ``Z``) frames last said it is off or in protection; None
        # until one came. Not the mode shown: an F455 turns that to AUTO for a moment with its
        # made-up ``*4*311*#Z##`` (``_asleep``).
        self._own_off: bool | None = None
        # The season the zone last reported in its own (plain ``Z``) form, how many such frames
        # came, and the running check of that season against the central unit's
        # (``_check_season_against_central``).
        self._own_season: str | None = None
        self._own_season_reported: asyncio.Event | None = None
        self._season_check_task: asyncio.Task[None] | None = None
        # The setpoint write waiting for its confirmation: the nominal written, the value
        # dimension 12 would have shown for it when it was written (the check itself follows
        # the knob offset as it is known when a report comes), and the event a matching
        # report sets.
        self._setpoint_check: tuple[float, float, asyncio.Event] | None = None
        # The last dimension 14 the zone reported while a write was being checked, and whether
        # the central unit answered for the zone meanwhile.
        self._setpoint_check_reported: float | None = None
        # What the zone reported, other than the value written, before the direct write.
        self._setpoint_check_before: float | None = None
        # The central unit's off marked this zone's actuators off (``_handle_central_mode_update``).
        self._actuators_off_by_central = False
        # Actuators that had reported activity when the zone's off marked them off, and have
        # not reported since ("zone": activity known from the valve status only, dimension
        # 19). Asked again once the zone runs (``_ask_actuators_assumed_off``).
        self._actuators_assumed_off: set[str] = set()
        self._setpoint_check_by_central = False
        self._setpoint_check_central_reports = 0
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
        # ``None``: not configured. The zone then cools once the bus has shown an actuator on
        # its conditioning side (``_note_conditioning_side``), as ``cool: true`` says in
        # ``myhome.yaml``; until then it is a heating zone, as ``myhome.yaml`` takes it by default.
        self._cooling_configured = cooling is not None
        self._cooling = bool(cooling)
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
        # Per-actuator proportional 0-10 V output level (0..100 %) from dimension 20 codes
        # 100..200 (*#4*Z#N*20*1xx##), exposed as ``actuator_levels`` (#949).
        self._actuator_levels: dict[str, int] = {}
        # The actuator numbers the zone reported before the restart (the keys of the saved
        # ``actuators`` attribute; their states are not restored). The silent-zone poll asks
        # for each of them instead of actuator 1 alone.
        self._remembered_actuators: tuple[str, ...] = ()
        # Per-actuator fan speed from dimension 20 for actuators known to be fan coils.
        self._actuator_fan_speeds: dict[str, str | None] = {}
        # Zone-level fallback from dimension 19 (*#4*Z*19*C*H##): whether any valve or fan
        # coil of the zone is active; None until seen. Whichever of the two arrived last
        # drives hvac_action, so a stale one cannot keep the zone "heating".
        self._zone_active: bool | None = None
        self._active_source: str | None = None  # "actuators" (dimension 20) or "zone" (19)
        # The sides (heating, cooling) the last report of an active actuator named; None while
        # none is active or none has reported (``_settle_action_of_active_actuators``).
        self._active_sides: tuple[bool, bool] | None = None
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
        self._local_offset_read_sent = False
        self._zone_status_request_sent = False  # *#4*Z## went out: it asks for the knob too
        self._knob_pos: str = "UNKNOWN"
        self._local_target_temperature: float | None = None
        # True while _local_target_temperature is the zone's own dimension 12 report (the
        # setpoint in force, offset included): dimension 14 and status-burst dimension 13
        # frames leave it alone, while standalone live knob movements adjust it by the delta.
        self._local_target_reported = False
        self._local_target_base: float | None = None
        self._nominal_from_dim12 = False
        self._dim12_at: float | None = None
        self._dim13_at: float | None = None
        self._unpaired_dim12_at: float | None = None
        self._paired_burst: tuple[str, float] | None = None
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
                and not self._asks_local_offset
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

    @property
    def _local_offset_read_pending(self) -> bool:
        """Whether a point-to-point local knob offset (Dimension 13) read is still owed (#466).

        The gateway answers ``*#4*Z*13##`` with ``*#4*Z*13*OO##`` (``OO``: ``00`` centre,
        ``01``..``03`` +1..+3, ``11``..``13`` -1..-3, ``4`` local OFF, ``5`` local protection; WHO
        4 spec pages 16-17, Encyclopedia ``who-4-temperature-control/dimensions.md``). Knowing the
        knob lets a setpoint write compensate it on the first write after a restart, instead of
        waiting for the Dimension 12/14 echo of a write to derive it (#890). Not for the central
        unit, a zone whose knob already reported, an actuator or probe address, or a zone behind
        an interface the gateway does not route. The ``F455`` (``KNOB_READ_IN_SILENT_ZONE_POLL``)
        is excluded: a single-dimension read on a probe-less channel holds its command session
        ~9 s before NACKing (#674), so there the knob read is folded into the serial silent-zone
        poll (``_silent_zone_reads``) instead of a point-to-point request. Elsewhere the read is
        not held back; the silent-zone poll repeats it once, as its last read, for a zone that
        is still silent and whose knob read brought nothing.

        This deliberately ignores the one-shot send guard (``_local_offset_read_sent``): a zone
        that has sent the read but not yet heard the answer still owes its knob, so
        ``message_has_state`` must keep treating its state as incomplete until the knob arrives.
        """
        gateway = getattr(self, "_gateway_handler", None)
        in_poll = (
            getattr(gateway, "knob_read_in_silent_zone_poll", False) is True
            or getattr(gateway, "model", None) in KNOB_READ_IN_SILENT_ZONE_POLL
        )
        return (
            not self._central
            and not self._local_offset_reported
            and not is_actuator_where(self._where)
            and not is_probe(self._where)
            and not self._interface_poll_refused
            and not in_poll
        )

    @property
    def _asks_local_offset(self) -> bool:
        """Whether this zone sends ``*#4*Z*13##`` point-to-point now.

        Every emulated gateway forwards the per-zone Dimension 13 request to the bus and none
        crashes (own-firmware-oracle ``6341ba3``, 13 images; central/general forms are dropped on
        10 of 13 and are not used). This is the send-path gate: a pending read (see
        ``_local_offset_read_pending``) that has not already gone out. The model can be corrected
        in band after ``__init__``, so this is re-evaluated at send time.
        """
        return self._local_offset_read_pending and not self._local_offset_read_sent

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
        self._actuator_fan_speeds.clear()

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
        if self._actuator_levels:
            attrs["actuator_levels"] = {
                actuator: level for actuator, level in sorted(self._actuator_levels.items())
            }
        if self._current_temperature_restored:
            attrs["current_temperature_restored"] = True
        if self._fan_coil_sides:
            attrs["fan_coil_heating"] = self._fan_coil_sides.get("heating", False)
            attrs["fan_coil_cooling"] = self._fan_coil_sides.get("cooling", False)
        if self._interface is not None:
            attrs["Int"] = self._interface
        if self._standalone_detected:
            # Found from the bus, not configured: kept across restarts (``async_restore_last_state``).
            attrs["standalone_detected"] = True
        if self._cooling and not self._cooling_configured:
            # Learned from the bus, not configured: kept across restarts as well.
            attrs["cooling_seen"] = True
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
            self._restore_cooling_seen(last_state)  # first: `cool` is then a mode the zone can be restored in
            try:
                restored_mode = HVACMode(last_state.state)
                # Any configured mode is restored (the plant's next frames replace it); the
                # advertised list is recomputed around it.
                if restored_mode in self._configured_hvac_modes():
                    self._attr_hvac_mode = restored_mode
                else:
                    self._attr_hvac_mode = HVACMode.OFF
            except (ValueError, TypeError):
                self._attr_hvac_mode = HVACMode.OFF
            # Also for a state that is no mode (``unavailable``, ``unknown``): the cooling the
            # saved attributes showed is offered either way.
            self._refresh_hvac_modes()
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
                    self._target_temperature = round(shown - self._local_offset, 1)
                    self._local_target_base = None
                    self._nominal_from_dim12 = False
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
            saved_actuators = last_state.attributes.get("actuators")
            if isinstance(saved_actuators, dict):
                self._remembered_actuators = tuple(
                    sorted(
                        {
                            str(int(str(actuator)))
                            for actuator in saved_actuators
                            if str(actuator).isascii() and str(actuator).isdigit() and 1 <= int(str(actuator)) <= 9
                        },
                        key=int,
                    )
                )
            restored_season = last_state.attributes.get("season")
            if restored_season in (SEASON_HEATING, "cooling", "conditioning"):
                self._season = str(restored_season)
            if (
                last_state.attributes.get("standalone_detected") is True
                and not self._central
                and self._standalone_config is None
            ):
                # What a setpoint write found before the restart; a report of the central unit
                # for this zone still overrides it (``_note_central_zone_report``).
                self._set_standalone_detected(True)
                self._refresh_hvac_modes()
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
            and not self._asks_local_offset
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
            # everywhere else it waits for the sweep replies or for the thermostat's own reports. What a
            # zone of any gateway has still not reported SILENT_ZONE_GRACE seconds after the sweep, it
            # asks for once, read by read (_poll_if_silent, #466 / #932).
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
        # The sweep has just asked the central unit's status, and this entity was created from
        # the answer: the same request is not sent again (``central_status_just_asked``).
        central_asked = self._central and getattr(self._gateway_handler, "central_status_just_asked", False) is True
        ask_status = (
            not central_asked
            if self._central
            else self._gateway_supports_zone_status and (not skip_reported or not self._core_zone_state_reported())
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
        ask_offset = self._asks_local_offset and not ask_status
        if skip_reported and not (ask_status or ask_temp or ask_fan or ask_humidity or ask_offset):
            self._bus_state_complete = True
            LOGGER.debug(
                "%s: state already reported on the bus, skipping post-discovery poll",
                self.entity_id,
            )
            return
        if (ask_status or ask_temp or ask_fan or ask_humidity) and self._poll_health.should_skip(time.time()):
            LOGGER.debug(
                "%s %s did not answer its last polls; not asking again yet",
                self._gateway_handler.log_id,
                self._display_name,
            )
            self._raise_unresponsive_issue()
            return
        if ask_status:
            self._zone_status_request_sent = not self._central
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
        if ask_offset:
            # *#4*Z*13## carries the knob directly, so the first setpoint write after a
            # restart is compensated without waiting for a write echo (#890 stays the fallback).
            # Built from the zone number so a `#Z` / `4-Z` myhome.yaml entry does not send the
            # central-unit form *#4*#Z*13## (dropped on 10 of 13 oracle images). Sent last,
            # paced, not counted for poll health: an unpopulated zone NACKs it like *#4*Z*0##.
            # Once on add per entity lifetime; a zone still silent after the grace repeats it once
            # at the end of its silent-zone poll (``_silent_zone_reads``).
            self._local_offset_read_sent = True
            zone = zone_number(self._where)
            routing = f"{BUS_ROUTING}{self._interface}" if self._interface is not None else ""
            await self._send_zone_poll_frame(
                cast(OWNCommand, OWNHeatingCommand.parse(f"*#4*{zone}{routing}*13##")),
                pace=pace,
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

    def _poll_on_add_waits(self) -> bool:
        """Also while the zones known only from their actuators are being read.

        A zone those reads create is added after the start-up sweep, when the poll on add no
        longer waits for anything: it ran next to the following zone's read, outside the
        start-up poll lock that read holds (``climate_discovery._ask_actuator_only_zones``),
        and an F455 hands a reply to the wrong one of two crossing reads. Left to
        ``_async_poll_after_discovery`` it takes its turn under that lock.
        """
        return super()._poll_on_add_waits() or (
            hasattr(self._gateway_handler, "wait_for_initial_discovery")
            and getattr(self._gateway_handler, "actuator_only_reads_running", False) is True
        )

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
        if self._central or self._fan_coil_sides or self._fan_coil_sides_pending():
            return  # not asked for a central unit, as on add and on restore
        if self._request_zone_dimension(19):
            self._fan_coil_sides_asked_at = time.monotonic()
            self._fan_coil_sides_held = self._zone_requests_held()

    def _request_zone_dimension(self, dimension: int, *, again: bool = False) -> bool:
        """Queue ``*#4*Z*DIM##`` through the gateway's paced sender; False on an older handler."""
        if self._interface_poll_refused or is_actuator_where(self._where):
            return False
        request = getattr(self._gateway_handler, "request_zone_dimension", None)
        if not callable(request):
            return False
        if again:
            request(self._full_where, dimension, again=True)
        else:
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

    def _action_by_season_or_mode(self) -> HVACAction | None:
        """What an active actuator is doing when its own frame does not say which side it is on.

        By the zone's season, else by its mode; ``None`` while neither is known.
        """
        if self._season == SEASON_HEATING:
            return HVACAction.HEATING
        if self._season in (SEASON_CONDITIONING, "cooling"):
            return HVACAction.COOLING
        if self._attr_hvac_mode == HVACMode.COOL:
            return HVACAction.COOLING
        if self._attr_hvac_mode == HVACMode.HEAT:
            return HVACAction.HEATING
        if (
            self._attr_hvac_mode == HVACMode.AUTO
            and self._target_temperature is not None
            and self._attr_native_current_temperature is not None
        ):
            if self._attr_native_current_temperature < self._target_temperature:
                return HVACAction.HEATING
            if self._cooling:
                return HVACAction.COOLING
            # Above its setpoint and not known to cool: nothing says what the actuator does.
        return None

    def _action_of_active_actuators(self, heating_active: bool, cooling_active: bool) -> HVACAction | None:
        """What a zone with an active actuator is doing; ``None`` while nothing says it.

        ``heating_active`` / ``cooling_active``: the side the report itself named (the valve
        status, dimension 19). An actuator status (dimension 20) names none.
        """
        # A zone found on the bus that has not shown a conditioning side yet may still
        # have one: its active actuator is not called heating before the frame says so.
        if self._heating and (self._cooling or not self._cooling_configured):
            if heating_active:
                return HVACAction.HEATING
            if cooling_active:
                return HVACAction.COOLING
            return self._action_by_season_or_mode()
        if self._heating:
            return HVACAction.HEATING
        return HVACAction.COOLING if self._cooling else None

    def _settle_action_of_active_actuators(self) -> None:
        """Decide again what an active actuator is doing, with what the zone has said since.

        A plain valve reports "on" with no side (``*#4*40#1*20*1##``) and does not report again
        until it changes. What that means goes by the zone's season, else its mode
        (``_action_of_active_actuators``), and those may come or change after the valve's
        frame: a zone found on the bus whose valve was open before its status was read
        (live 2026-11-19, MyHomeServer1 and 3488, zones 40 and 53: ``heat`` with no action,
        ``heating`` with the same zones in ``myhome.yaml``); a zone that was off, or whose
        other valve had reported "off", when the open valve reported; a zone restored in one
        season that turns out to run in the other. So the same rule is applied before every
        state is published, with the sides the last active report named. Nothing is done
        while no actuator is active: off, idle and protection are the mode frames' business.
        """
        if self._active_sides is None or not self._any_actuator_active():
            return
        action = self._action_of_active_actuators(*self._active_sides)
        if action is not None:
            self._attr_hvac_action = action

    def _any_actuator_active(self) -> bool:
        """Whether the most recent actuator report (dimension 20 or 19) shows activity."""
        if self._active_source == "zone":
            return bool(self._zone_active)
        return any(self._actuator_states.values())

    @staticmethod
    def _decode_running_fan_speed(
        msg_fan_on: bool | None, msg_fan_speed: int | None
    ) -> str | None:
        """Map decoded fan flags to ``running_fan_speed`` (``low``/``medium``/``high``/``off``/``None``)."""
        if msg_fan_speed == 1:
            return "low"
        if msg_fan_speed == 2:
            return "medium"
        if msg_fan_speed == 3:
            return "high"
        if msg_fan_on is False or msg_fan_speed == 4:
            return "off"
        return None

    def _sync_actuator_running_fan_speed(self) -> None:
        """Recompute ``_running_fan_speed`` from tracked fan-coil actuators."""
        for speed in reversed(self._actuator_fan_speeds.values()):
            if speed in ("low", "medium", "high"):
                self._running_fan_speed = speed
                return
        if any(speed is None for speed in self._actuator_fan_speeds.values()):
            self._running_fan_speed = None
        else:
            self._running_fan_speed = "off"

    def _mark_actuators_off(self) -> None:
        """The zone was switched OFF: every known actuator is off, the attribute stays.

        An assumption until the actuator says so itself: the ones that had reported activity
        are remembered (``_ask_actuators_assumed_off``).
        """
        running = {actuator for actuator, active in self._actuator_states.items() if active}
        if not running and self._zone_active:
            running = {"zone"}  # known from the valve status only: no actuator to name
        self._actuators_assumed_off |= running
        self._actuator_states = dict.fromkeys(self._actuator_states, False)
        self._actuator_levels = dict.fromkeys(self._actuator_levels, 0)
        self._actuator_fan_speeds = dict.fromkeys(self._actuator_fan_speeds, "off")
        if self._zone_active is not None:
            self._zone_active = False
        if self._actuator_fan_speeds or self._running_fan_speed in ("low", "medium", "high"):
            self._running_fan_speed = "off"

    def _ask_actuators_assumed_off(self) -> None:
        """Ask the zone's actuators again once it runs, where its off had only assumed them off.

        An actuator reports when it changes. A zone that is switched off and on again before
        its thermostat has stopped the actuator gets no report at all
        (tests/fixtures/traces/zone_short_off_2026_11_19, zone 2, a fan coil thermostat behind
        an F461 and an F455): off for 1.2 s, no ``*#4*2#1*20*...##`` followed ``*4*303*2##`` or
        ``*4*0*2##``, the zone was shown idle, and another client's request 98.7 s later was
        answered ``*#4*2#1*20*8##``, fan speed 3, as before the off. Off for 8.3 s, the same
        actuator reported ``*#4*2#1*20*5##`` 3.1 s after the off and ``*#4*2#1*20*8##`` 4.9 s
        after the zone ran again. So the actuators that were marked off without having said so
        are asked once the zone shows a running mode, with the request the gateway takes
        (``request_zone_dimension``). Nothing is asked for a zone whose actuators reported
        their off, or had none active.
        """
        if not self._actuators_assumed_off:
            return
        if self._attr_hvac_mode in (None, HVACMode.OFF) or self._own_off is True:
            return
        self._actuators_assumed_off.clear()
        self._request_zone_dimension(20, again=True)

    @property
    def _standalone(self) -> bool:
        """Whether setpoints and modes are addressed as ``Z`` (thermostat alone) or ``#Z``.

        A configured value wins (``standalone:`` in ``myhome.yaml``, or the zone listed under
        "Standalone thermostats" in the gateway options). Otherwise a zone is standalone unless
        its plant has a central unit: behind a 99-zone central the gateway rejects
        ``*#4*Z*#14*...`` and wants ``*#4*#Z*#14*...`` (#697).

        A plant with a central unit can still have a thermostat the central unit is not running.
        Live plant, a 3550 with zones 2, 3 and 4: on 2026-11-12 (conditioning, weekly program)
        the 3550 wrote all three zones and reported ``*4*211*#2##``; on 2026-11-18 (the 3550 in
        the heating season, zone 2 in conditioning) it wrote and reported zones 3 and 4 only,
        ``*#4*#2*#14*0210*3##`` was acknowledged and ignored, and the direct
        ``*#4*2*#14*0210*3##`` set the thermostat. Nothing on the bus lists the zones of a
        central unit, so the form goes by the result: a zone that did not take a setpoint
        through the central unit and took it directly is written directly
        (``_confirm_setpoint_write``) until the central unit reports it again.
        """
        if self._standalone_config is not None:
            return self._standalone_config
        if getattr(self._gateway_handler, "has_central_unit", False) is not True:
            return True
        # Read from the constructor (``_advertised_hvac_modes``) before the field exists.
        return bool(getattr(self, "_standalone_detected", False))

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
            changed = season != self._season or not self._season_reported
            self._season = season
            self._season_reported = True
            if changed:
                # What a zone of the central unit offers goes by the season (``heat`` is not
                # offered in conditioning), and the frame's own mode is checked against the
                # list right after this: ``*4*210*#4##`` on a zone last seen in heating was
                # dropped because ``cool`` was not in a list made for the heating season.
                self._refresh_hvac_modes()

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
                self.async_on_remove(self._cancel_season_check)
            if not self._central and self._standalone_config is not True:
                self.async_on_remove(
                    async_dispatcher_connect(
                        target_hass,
                        f"myhome_central_mode_{self._gateway_handler.mac}",
                        self._handle_central_mode_update,
                    )
                )
            if not self._central:
                self.async_on_remove(
                    async_dispatcher_connect(
                        target_hass,
                        f"myhome_central_season_{self._gateway_handler.mac}",
                        self._handle_central_season_update,
                    )
                )
                self.async_on_remove(self._forget_standalone_detected)
        zone_requests = not self._central and not is_probe(self._where)
        if zone_requests and self._fan:
            # A configured fan coil without its side: ask for it before the poll on add
            # (inside ``super().async_added_to_hass()``), which must not ask ``*#4*Z*11##``
            # blindly. A restored fan mode asks at the end of ``async_restore_last_state``.
            self._ask_fan_coil_sides()
        await super().async_added_to_hass()
        # A zone created from its own season frame handled that frame before it was live.
        self._check_season_against_central()
        if zone_requests and _ZONE_WHERE.match(self._full_where):
            # hvac_action needs the actuators (dimension 20); without asking, it stays unknown
            # until one of them switches. The gateway asks again after a reconnect. A secondary
            # probe (WHERE >= 100) or 3-token split address (3#Z#N) is not a Dimension 20 target.
            watch = getattr(self._gateway_handler, "watch_zone_actuators", None)
            if callable(watch):
                self.async_on_remove(watch(self._full_where))
            # The silent-zone poll (#466, #932) runs on every gateway that serves its zones.
            # Whether this one does is decided inside the task, once the startup sweep and the
            # grace period are over: the roles on a shared bus can still settle after the
            # entities were added. So the task is scheduled when the gateway wants the poll
            # now, or while the sweep is still to run.
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
        """The reads of the silent-zone poll (#466, #932) this zone still needs, in the order they go out.

        What the zone said since the restart is not asked again; a value Home Assistant restored
        is. The temperature (``*0``) comes first because a zone without a probe refuses it, and
        that refusal ends the poll. The knob (``*13``) second only if the zone has not asked for
        it already when it was added (``_local_offset_read_pending``), and not when the knob
        is all that a zone left out of its answer to ``*#4*Z##``; a knob read that was sent on
        add and brought nothing is repeated as the very last read. The setpoint (``*14``), then
        the setpoint in force (``*12``, the F455 follows its reply with the zone's season frame,
        which gives the mode), then actuator 1 (``#1*20``) last: ``hvac_action`` is derived when
        an actuator report finds the mode known. Not on a gateway that drops the actuator request
        (``silent_zone_actuator_read_refused``); and a zone that misses nothing else was already
        asked for its actuators when it was added (``watch_zone_actuators``), so it asks for
        actuator 1 only where that request is not sent (``zone_actuator_request_refused``, the
        F455). The zone is named by its number: a ``#Z`` entry of ``myhome.yaml`` would otherwise
        ask through the central unit, and an actuator behind an interface is ``Z#N#4#II``, not
        ``Z#4#II#N``.
        """
        zone = zone_number(self._where)
        routing = f"{BUS_ROUTING}{self._interface}" if self._interface is not None else ""
        temperature_known = self._attr_native_current_temperature is not None and not self._current_temperature_restored
        setpoint_known = self.native_target_temperature is not None and not self._target_temperature_restored
        mode_known = self._attr_hvac_mode is not None and not self._hvac_mode_restored
        knob_missing = not self._local_offset_reported and not is_probe(self._where)
        silent = not temperature_known or not setpoint_known or not mode_known
        reads: list[str] = []
        if not temperature_known:
            reads.append(f"*#4*{zone}{routing}*0##")
        if (
            knob_missing
            and not self._local_offset_read_sent
            and (silent or not self._zone_status_request_sent)
        ):
            # The knob read (#466) goes after *0## (a probe-less zone refuses *0## and the
            # serial poll ends there) and before *14## / *12## so a reported knob keeps the
            # #890 derivation from running on the setpoint echo. A zone that reported its
            # temperature itself skips *0## and asks only *13## here: it has a probe (the
            # temperature proves it), so this is safe. F455 1.0.76 answered *#4*2*13## ->
            # *#4*2*13*00## in 0.108 s (F455_direct_requests_2026-11-06.jsonl line 70). A zone
            # that sent the read when it was added (every gateway but the F455,
            # KNOB_READ_IN_SILENT_ZONE_POLL) does not send it here (see the end of this
            # method), and a zone that answered *#4*Z## with everything but a knob is not asked
            # for one.
            reads.append(f"*#4*{zone}{routing}*13##")
        if not setpoint_known:
            reads.append(f"*#4*{zone}{routing}*14##")
        if not setpoint_known or not mode_known:
            reads.append(f"*#4*{zone}{routing}*12##")
        zone_form_refused = getattr(self._gateway_handler, "zone_actuator_request_refused", False) is True
        if (
            self._zone_active is None
            and getattr(self._gateway_handler, "silent_zone_actuator_read_refused", False) is not True
            and (reads or zone_form_refused)
        ):
            if self._remembered_actuators:
                # The actuators the zone had before the restart, each one that has not reported
                # since: a zone with a valve on actuator 1 and a fan coil on actuator 2 is then
                # asked for both, and a zone with one actuator is never asked for a second one
                # (a physical F455 1.0.76 NACKed the read of an absent actuator after 8.9 s).
                reads.extend(
                    f"*#4*{zone}#{actuator}{routing}*20##"
                    for actuator in self._remembered_actuators
                    if actuator not in self._actuator_states
                )
            elif not self._actuator_states:
                reads.append(f"*#4*{zone}#1{routing}*20##")
        if zone_form_refused and self._zone_active is None:
            # Where the zone form of the actuator request is refused (the F455) nothing tells
            # which actuators a zone has. Dimension 19 gives the state of both sides of the zone
            # in one read, whatever their number: the action, the fan coil side and the running
            # fan speed. After the actuator reads on purpose: the most recent of the two kinds
            # of report drives ``hvac_action``, and an "off" of actuator 1 says nothing about a
            # fan coil on actuator 2. The temperature read before it has shown that the zone
            # has a probe; without one the F455 NACKs this read only after 9 s
            # (issue_466/F455_direct_requests_zone3_2026-11-06.jsonl).
            reads.append(f"*#4*{zone}{routing}*19##")
        if knob_missing and self._local_offset_read_sent and silent:
            # The knob read went out when the zone was added and brought nothing. A zone that is
            # still silent asks for it once more, as the last read: a refusal of it must not
            # keep the setpoint reads from going out, and the #890 derivation from the
            # *12## / *14## pair above stays the fallback when it is refused again.
            reads.append(f"*#4*{zone}{routing}*13##")
        return reads

    def _silent_read_answered(self, frame: str) -> bool:
        """Whether what a read of the silent-zone poll asks for has arrived from the bus by now."""
        if frame.endswith("*0##"):
            return self._attr_native_current_temperature is not None and not self._current_temperature_restored
        if frame.endswith("*13##"):
            return self._local_offset_reported
        setpoint_known = self.native_target_temperature is not None and not self._target_temperature_restored
        if frame.endswith("*14##"):
            return setpoint_known
        if frame.endswith("*12##"):
            return setpoint_known and self._attr_hvac_mode is not None and not self._hvac_mode_restored
        if frame.endswith("*19##"):
            # An answer an actuator's report has overtaken since is not the one the poll's last
            # read is after (``_ask_what_is_missing``).
            return self._active_source == "zone"
        # Dimension 19 gives the zone's state, not this actuator's own entry (its state, its fan
        # speed, its number for the next restart): only the actuator's own report answers.
        where = frame.split("*")[2].split("#")  # Z#N*20## or Z#N#4#II*20##
        return len(where) > 1 and where[1] in self._actuator_states

    async def _poll_if_silent(self, grace: float = SILENT_ZONE_GRACE) -> None:
        """Ask a zone that has not reported by itself since the startup sweep, read by read (#466, #932).

        On every gateway that serves its zones (``silent_zone_poll_needed``), ``grace`` seconds
        after the startup sweep and not before this zone's own poll on add has finished (the
        polls on add go out one after another, #886, and on a large plant they are still running
        when the grace is over). Nothing goes out for what the zone reported itself
        (``_silent_zone_reads``), nor for a zone that answered neither of its last two status
        requests (``poll_health``).

        One zone at a time (the gateway's startup poll lock is held for the zone's reads), and
        each read waits for the previous one to be answered and for the bus to be quiet after it
        (``pace_startup_status_write``): the frame a device sends twice then falls into that
        quiet time and not into the next read. A request a zone made on its own in the meantime
        (``request_zone_dimension``) is sent before the next read. A refused or unanswered read
        (a zone without a probe) ends the poll after one request: a NACK, no reply at all within
        ``PACED_READ_TIMEOUT``, or an ACK that came without a frame while the zone sent nothing
        either (``EMPTY_REPLY_SETTLE``). A read dropped because it collected another zone's
        frame is not an answer of this zone: it is sent once more, unless the zone's own answer
        arrived on the event session in the meantime.
        """
        handler = self._gateway_handler
        wait = getattr(handler, "wait_for_initial_discovery", None)
        if callable(wait):
            waiting = wait()
            if inspect.isawaitable(waiting):
                await waiting
        await asyncio.sleep(grace)
        own_poll = self._poll_on_add_task
        if isinstance(own_poll, asyncio.Task) and not own_poll.done():
            await asyncio.wait([own_poll])  # its answers are part of what the zone "reported"
        if getattr(handler, "silent_zone_poll_needed", False) is not True:
            return  # a passive follower: its zones are another gateway's to ask
        startup_lock = getattr(handler, "_who4_startup_poll_lock", None)
        if isinstance(startup_lock, asyncio.Lock):
            # Fair: every startup poll and every other zone's silent poll queued before this
            # point has finished when the lock is granted.
            async with startup_lock:
                await self._ask_what_is_missing()
        else:
            await self._ask_what_is_missing()

    async def _ask_what_is_missing(self) -> None:
        """The reads of ``_poll_if_silent``, one after another."""
        handler = self._gateway_handler
        if self._poll_health.should_skip(time.time()):
            return  # no frame and no answer for two restarts: not asked again yet
        reads = self._silent_zone_reads()
        if not reads:
            return  # the zone reported its temperature, setpoint, mode and an actuator on its own
        LOGGER.debug("%s %s has not reported since the startup sweep; asking %s", handler.log_id, self._display_name, reads)
        pace = getattr(handler, "pace_startup_status_write", None)
        own_requests = getattr(handler, "wait_for_zone_requests", None)
        for frame in reads:
            if frame.endswith("*19##") and (self._active_source == "zone" or self._fan_coil_sides_pending()):
                # Asked meanwhile because an actuator report showed a fan coil
                # (``_ask_fan_coil_sides``), or answered and still in the lead. That request
                # can leave before the read of a later actuator: when that actuator's first
                # report then took the lead, dimension 19 is read once more, last as planned.
                continue
            frames_before = self._poll_health.frames
            request = cast(OWNCommand, OWNHeatingCommand.parse(frame))
            written: object = None
            for _attempt in range(2):
                if callable(own_requests):
                    # A request a zone makes on its own while the poll runs (the fan coil's
                    # side once an actuator reported a fan coil, the fan speed after that)
                    # goes through the gateway's paced sender, which waits for the same quiet
                    # bus as this poll. It is let through first, with its own quiet time,
                    # instead of being written back to back with the next read.
                    await own_requests()
                try:
                    written = await handler.send_status_request(request)
                except Exception:  # a transport error must not end the task silently (#593)
                    LOGGER.exception("%s %s: `%s` could not be sent", handler.log_id, self._display_name, frame)
                    return
                if isinstance(written, asyncio.Future):
                    await asyncio.wait([written], timeout=PACED_READ_TIMEOUT)
                    if not written.done():
                        return  # no reply at all within PACED_READ_TIMEOUT
                if callable(pace):
                    # Bus quiet before the next read of any zone opens. On a physical F455
                    # 1.0.76 plant the state of an actuator came twice, 0.09-0.27 s apart, after
                    # one read (tests/fixtures/traces/issue_932/README.md).
                    await pace(written, request)
                if not (
                    isinstance(written, asyncio.Future)
                    and written.cancelled()
                    and getattr(written, "_gateway_desync", False) is True
                ):
                    break
                # Another zone's frame was taken for the reply of this read and the read was
                # dropped (the #886 guard). That says nothing about this zone. Its own answer
                # went out on the bus all the same and usually arrives on the event session a
                # moment later: then nothing is sent again. Otherwise the read is repeated once.
                if self._silent_read_answered(frame):
                    written = None
                    break
            if isinstance(written, asyncio.Future):
                if written.cancelled():
                    return  # refused (NACK), timed out, or dropped twice by the desync guard
                if getattr(written, "_empty_reply", False) is True:
                    # Acknowledged with no frame on the command session. The zone's answer can
                    # still come on the event session: it gets EMPTY_REPLY_SETTLE seconds.
                    if self._poll_health.frames == frames_before:
                        await asyncio.sleep(EMPTY_REPLY_SETTLE)
                    if self._poll_health.frames == frames_before:
                        LOGGER.debug(
                            "%s %s: `%s` was acknowledged and nothing answered; not asking further",
                            handler.log_id,
                            self._display_name,
                            frame,
                        )
                        return

    def _note_central_zone_report(self, message: OWNHeatingEvent) -> None:
        """The central unit reported this zone in the ``#Z`` form: it is running it.

        Called for a frame ``is_central_unit_report`` accepts: a ``#Z`` dimension
        (``*#4*#3*14*0250*1##``) or a ``#Z`` zone status with its season (``*4*110*#3##``), never
        the generic ``*4*310*#Z##`` / ``*4*311*#Z##`` an F455 makes up for any thermostat. Live
        3550 (F461 and F455, 2026-11-17 / 11-18): both frames follow every ``*#4*3##`` and every
        setpoint written as ``*#4*#3*#14*T*M##``, and the mode frame comes with the central
        unit's quarter-hour push, for the zones it is running. A zone found standalone by a
        setpoint write goes back to the central form; a configured zone keeps its form.
        """
        if self._central:
            return
        zone = central_report_zone(message)
        if zone is None or zone != canonical_thermo_zone(self._where):
            return  # #0 / #0#N (the central unit itself), or another zone's frame
        if _two_digit_interface(thermo_event_interface(message)) != self._interface:
            return  # the same zone number on another bus
        self._central_zone_seen = True
        if self._standalone_detected:
            self._set_standalone_detected(False)
            LOGGER.info(
                "%s %s: the central unit reports this zone (%s); it is written through the central unit again",
                self._gateway_handler.log_id,
                self._display_name,
                message,
            )
            self._refresh_hvac_modes()
        check = self._setpoint_check
        if check is not None:
            # The central unit answered for the zone the setpoint went to: it is one of its
            # zones. Only its report of the written setpoint confirms the write. A mode frame
            # (all an F455 shows, ``*4*110*#3##``) or the setpoint it had before leaves the
            # read-back to decide.
            self._setpoint_check_by_central = True
            self._setpoint_check_central_reports += 1
            reported = getattr(message, "set_temperature", None)
            if (
                message.message_type in (MESSAGE_TYPE_TARGET_TEMPERATURE, MESSAGE_TYPE_MODE_TARGET)
                and isinstance(reported, (int, float))
                and abs(float(reported) - check[0]) < 0.05
            ):
                check[2].set()

    def _note_setpoint_report(self, message: OWNHeatingEvent) -> None:
        """While a setpoint write is being checked: does this frame show the zone took it?

        Dimension 14 is the commanded setpoint, dimension 12 the one in force (the commanded one
        plus the knob offset). The value has to match: another client's status request brings
        the old setpoint during the wait (live, 2026-11-18 03:01:58: ``*#4*2*14*0200*3##`` after
        the unanswered ``*#4*#2*#14*0210*3##``).
        """
        check = self._setpoint_check
        if check is None:
            return
        if _two_digit_interface(thermo_event_interface(message)) != self._interface:
            return  # the same zone number on another bus
        moved = False
        if message.message_type == MESSAGE_TYPE_TARGET_TEMPERATURE:
            reported = getattr(message, "set_temperature", None)
            expected = check[0]
            if isinstance(reported, (int, float)) and not is_central_thermo_where(message):
                self._setpoint_check_reported = float(reported)
            # The zone reported another setpoint before the direct write: the requested one
            # now is that write arriving, whatever the zone's mode (``_setpoint_check_before``).
            moved = self._setpoint_check_before is not None
        elif message.message_type == MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE:
            if self._is_off() or self._dimension_3_is_protection(message):
                # What a zone that is off keeps to, not the setpoint that was written; also
                # while the mode is not known yet (the protection frame follows its setpoint).
                return
            reported = getattr(message, "local_set_temperature", None)
            expected = round(check[0] + self._local_offset, 1)  # with the knob as it is known now
        else:
            return
        if self._asleep() and not moved:
            return  # its own report shows what it holds, not that the write arrived
        if isinstance(reported, (int, float)) and abs(float(reported) - expected) < 0.05:
            check[2].set()

    def _note_own_state(self, message: OWNHeatingEvent) -> None:
        """Keep whether the zone's own frame says it is off (``*4*303*2##``) or running (``*4*0*2##``)."""
        if self._central or is_central_thermo_where(message):
            return
        if _two_digit_interface(thermo_event_interface(message)) != self._interface:
            return  # the same zone number on another bus
        if message.message_type == MESSAGE_TYPE_SEASON:
            self._own_off = False
        elif message.message_type in (MESSAGE_TYPE_MODE, MESSAGE_TYPE_MODE_TARGET):
            self._own_off = getattr(message, "mode", None) == CLIMATE_MODE_OFF
        elif message.message_type == MESSAGE_TYPE_ZONE_STATE:
            state = getattr(message, "zone_state", None)  # dimension 7, a MyHomeServer1 zone
            if state in _ZONE_STATES_OFF:
                self._own_off = True
            elif state in _ZONE_STATES_ON:
                self._own_off = False

    def _is_off(self) -> bool:
        """Off by the zone's own last frame. While none has come: by the mode shown, and a mode
        Home Assistant restored says nothing about a thermostat that may have been switched off."""
        if self._own_off is None:
            return self._attr_hvac_mode == HVACMode.OFF or self._hvac_mode_restored
        return self._own_off

    def _asleep(self) -> bool:
        """Whether this zone, found on the bus, is off and the central unit has not answered for it.

        A thermostat that is off still holds its setpoint: its report of the requested value
        does not show that a write arrived. Live 2026-11-18, zone 2 off at 20.0, 20.0 written as
        ``*#4*#2*#14*0200*3##``: ``*#4*2*14*0200*3##`` came (its own read-back on an F461,
        another client's status request on an F455) and the zone stayed ``*4*303*2##``. On the
        F455 the entity showed AUTO for 0.1 s in between (the gateway's ``*4*311*#2##``), so
        the zone's own frames decide, not the mode shown.
        """
        return self._is_off() and self._standalone_config is None and not self._setpoint_check_by_central

    async def _wait_for_setpoint(self, written: object, check: tuple[float, float, asyncio.Event]) -> bool:
        """Whether the plant confirmed the write behind ``written`` within ``SETPOINT_CONFIRM_TIMEOUT``.

        The time runs from the gateway's ACK (the delivery future), not from the queueing. A
        frame the gateway refused or that was never written (the future is cancelled: an F454
        NACKs the direct form for a zone of its central unit, #697) is not confirmed.
        """
        if isinstance(written, asyncio.Future):
            await asyncio.wait([written], timeout=SETPOINT_DELIVERY_TIMEOUT)
            if not written.done():
                written.cancel()  # withdrawn: it must not go out after the check has given up
            if not _delivered(written):
                return False
        deadline = time.monotonic() + SETPOINT_CONFIRM_TIMEOUT
        # Reports of the central unit that came while the frame was still queued (its
        # quarter-hour report, another client's poll) are not answers to it.
        reports_before = self._setpoint_check_central_reports
        central_answered = False
        while not check[2].is_set() and time.monotonic() < deadline:
            if self._setpoint_check_central_reports > reports_before and not central_answered:
                # The central unit answered without the written setpoint. Where it reports the
                # setpoint, that frame follows its mode frame at once (2 ms on an F461), so the
                # read-back does not have to wait for the whole timeout.
                central_answered = True
                deadline = min(deadline, time.monotonic() + SETPOINT_CENTRAL_REPORT_GRACE)
            await asyncio.sleep(0.02)
        return check[2].is_set()

    async def _read_back_setpoint(self, check: tuple[float, float, asyncio.Event]) -> float | None:
        """Ask the zone for its commanded setpoint (``*#4*Z*14##``) while a write is checked.

        Returns what the zone reported, or ``None`` when nothing answered. The caller reads
        ``check[2]`` for "the zone has the written setpoint".
        """
        handler = self._gateway_handler
        self._setpoint_check_reported = None
        frame = f"*#4*{canonical_thermo_zone(self._where) or zone_number(self._where)}*14##"
        try:
            read = await handler.send_status_request(cast(OWNCommand, OWNHeatingCommand.parse(frame)))
        except Exception:  # a transport error is not the zone's answer
            LOGGER.exception("%s %s: `%s` could not be sent", handler.log_id, self._display_name, frame)
            return None
        if isinstance(read, asyncio.Future):
            await asyncio.wait([read], timeout=SETPOINT_READ_TIMEOUT)
            if not read.done():
                read.cancel()  # withdrawn, as the write above
        if self._setpoint_check_reported is None and not check[2].is_set():
            # The reply is handed to the entity right after the read's future resolves, or
            # comes on the event session a moment later.
            deadline = time.monotonic() + EMPTY_REPLY_SETTLE
            while self._setpoint_check_reported is None and not check[2].is_set() and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
        return self._setpoint_check_reported

    async def _confirm_setpoint_write(
        self, written: object, frame: str, nominal: float, *, allow_direct: bool = True
    ) -> None:
        """Make sure a setpoint written in the ``#Z`` form (through the central unit) reached the zone.

        A central unit acknowledges that form for a thermostat it is not running and nothing
        happens (``_standalone``). Only on a gateway that tells what became of the frame (the
        delivery future). Every conclusion rests on a report of the zone, never on silence:

        1. wait for the central unit's answer for this zone, or for the zone's own report of
           the new setpoint: confirmed, nothing else is sent;
        2. else read ``*#4*Z*14##``. The new setpoint: confirmed. No answer: nothing is known,
           a warning is logged (the unresponsive-zone repair covers a dead zone). The old
           setpoint: the zone did not take the write;
        3. then, for a zone found on the bus (``standalone`` not configured), write the direct
           form ``*#4*Z*#14*T*M##`` and check it the same way. A zone that takes it is written
           directly from now on (``_standalone_detected``), until the central unit reports it;
        4. a zone that still reports the old setpoint, or a zone configured as one of the
           central unit's, gets an error instead of a dial that silently jumps back. When the
           gateway refuses the direct form of step 3 (NACK), the error is the one of the
           write through the central unit, the frame that was asked for.

        ``allow_direct`` is False when the gateway has just refused the direct form for this
        zone (``_drop_refused_standalone_finding``): step 3 is left out, so the refused frame
        is not sent a second time, and a write the zone did not take is reported as not
        taken through the central unit.
        """
        handler = self._gateway_handler
        check = (nominal, round(nominal + self._local_offset, 1), asyncio.Event())
        self._setpoint_check = check
        self._setpoint_check_reported = None
        self._setpoint_check_before = None
        self._setpoint_check_by_central = False
        try:
            if await self._wait_for_setpoint(written, check) or self._setpoint_check is not check:
                return  # confirmed, or a newer write took over and its own check decides
            if not _delivered(written):
                # Not known to have reached the plant: nothing says the central unit ignored it,
                # so no read-back and no direct write follow.
                self._setpoint_not_delivered(written, frame)
                return
            reported = await self._read_back_setpoint(check)
            if self._setpoint_check is not check:
                return
            if check[2].is_set():
                return
            holds_it = reported is not None and abs(reported - nominal) < 0.05
            if holds_it and not (allow_direct and self._asleep()):
                # A zone that is running, answered for by the central unit or configured; or
                # one whose direct form the gateway has just refused: it holds the setpoint
                # and there is no other form to try.
                return
            if reported is None:
                LOGGER.warning(
                    "%s %s did not confirm `%s` and did not answer a read of its setpoint",
                    handler.log_id,
                    self._display_name,
                    frame,
                )
                return
            if self._standalone_config is not None or self._setpoint_check_by_central or not allow_direct:
                # Configured as a zone of the central unit, or the central unit answered for it:
                # the direct form would leave it on a setpoint the central unit does not know.
                # Or the gateway has just refused that form for this zone.
                raise self._not_taken_via_central(frame, nominal, reported)
            direct_command = OWNHeatingCommand.set_temperature(
                where=self._where, temperature=nominal, mode=SETPOINT_WRITE_MODE, standalone=True
            )
            LOGGER.debug(
                "%s %s still reports %.1f °C after `%s`; writing the thermostat directly (`%s`)",
                handler.log_id,
                self._display_name,
                reported,
                frame,
                direct_command,
            )
            check[2].clear()  # only a report that follows the direct write counts
            # A zone that is off holds its setpoint, so its report of the requested value proves
            # nothing while that is what it held (``_asleep``). After a read that gave another
            # value it does: only the write that follows can have changed it, also on a zone
            # whose mode is not known yet or that sends no mode frame with its setpoint.
            self._setpoint_check_before = None if holds_it else reported
            self._arm_setpoint_echo(nominal)
            direct = await handler.send(direct_command)
            taken = await self._wait_for_setpoint(direct, check)
            if self._setpoint_check is not check:
                return
            if not _delivered(direct):
                if holds_it:
                    # The zone holds the requested setpoint after the write through the central
                    # unit, and the direct form did not get through (an F454 refuses it for a
                    # zone of its central unit, #697). Nothing is left undone, and nothing
                    # shows a standalone thermostat: no error for a write that arrived.
                    LOGGER.debug(
                        "%s %s holds %.1f °C after `%s`; `%s` was not taken by the gateway and is not needed",
                        handler.log_id,
                        self._display_name,
                        nominal,
                        frame,
                        direct_command,
                    )
                    return
                if getattr(direct, "_gateway_nack", False) is True:
                    # The zone still reports another setpoint after the write through the
                    # central unit, and the gateway refuses the direct form for it: the write
                    # that failed is the one that was asked for, as when the direct form was
                    # refused first (``allow_direct``). Not "the gateway refused" a frame
                    # nobody asked for.
                    LOGGER.debug(
                        "%s %s: the gateway refused `%s` after `%s` was not taken",
                        handler.log_id,
                        self._display_name,
                        direct_command,
                        frame,
                    )
                    raise self._not_taken_via_central(frame, nominal, reported)
                self._setpoint_not_delivered(direct, str(direct_command))
                return
            if not taken:
                reported = await self._read_back_setpoint(check)
                if self._setpoint_check is not check:
                    return
                taken = check[2].is_set()
            if taken:
                if not self._setpoint_check_by_central:  # the thermostat's own report, not the central unit's
                    self._mark_standalone_detected()
                return
            if reported is None:
                LOGGER.warning(
                    "%s %s did not confirm `%s` and did not answer a read of its setpoint",
                    handler.log_id,
                    self._display_name,
                    direct_command,
                )
                return
            if abs(reported - nominal) < 0.05:
                # Off, and holding the requested setpoint before and after both writes: there
                # is nothing to report as refused, and nothing that tells which form it takes.
                LOGGER.debug(
                    "%s %s is off and holds %.1f °C; neither `%s` nor `%s` changed what it reports",
                    handler.log_id,
                    self._display_name,
                    reported,
                    frame,
                    direct_command,
                )
                return
            raise HomeAssistantError(
                f"{self._display_name} did not take the setpoint {nominal:.1f} °C ({frame}, {direct_command}): "
                f"it still reports {reported:.1f} °C",
                translation_domain=DOMAIN,
                translation_key="setpoint_not_taken",
                translation_placeholders={
                    "name": self._display_name,
                    "temperature": f"{nominal:.1f}",
                    "reported": f"{reported:.1f}",
                },
            )
        finally:
            if self._setpoint_check is check:
                self._setpoint_check = None
                self._setpoint_check_before = None

    def _arm_setpoint_echo(self, nominal: float) -> None:
        """Forget what the previous write and its read-back left, before the direct write.

        The read-back's dimension 14 (the old setpoint) would otherwise be paired with the
        dimension 12 the direct write brings, and read as a knob offset: with a knob that had
        not reported yet, 21.0 after 20.0 gave an offset of +1 and the next 20.0 would have
        been written as 19.0.
        """
        self._local_target_reported = False
        self._local_target_base = None
        self._dim13_at = None
        self._dim14_at = None
        self._unpaired_dim12_at = None
        self._paired_burst = None
        self._written_target_temperature = (nominal, time.monotonic())

    def _not_taken_via_central(self, frame: str, nominal: float, reported: float) -> HomeAssistantError:
        """The error for a zone that still reports ``reported`` after ``frame`` (the ``#Z`` form)."""
        return HomeAssistantError(
            f"{self._display_name} did not take the setpoint {nominal:.1f} °C sent through the "
            f"central unit ({frame}): it still reports {reported:.1f} °C",
            translation_domain=DOMAIN,
            translation_key="setpoint_not_taken_via_central",
            translation_placeholders={
                "name": self._display_name,
                "temperature": f"{nominal:.1f}",
                "reported": f"{reported:.1f}",
                "frame": frame,
            },
        )

    def _setpoint_not_delivered(self, written: object, frame: str) -> None:
        """A setpoint write that is not known to have reached the plant.

        Refused by the gateway (a NACK): an error. Anything else (no reply in time, a reply
        out of step, a transport error): a warning, since the frame may still have arrived.
        """
        if getattr(written, "_gateway_nack", False) is True:
            raise HomeAssistantError(
                f"{self._display_name}: the gateway refused the setpoint write ({frame})",
                translation_domain=DOMAIN,
                translation_key="setpoint_refused",
                translation_placeholders={"name": self._display_name, "frame": frame},
            )
        LOGGER.warning(
            "%s %s: `%s` was not acknowledged by the gateway; the setpoint is not known to have arrived",
            self._gateway_handler.log_id,
            self._display_name,
            frame,
        )

    def _drop_refused_standalone_finding(self, written: object, frame: str) -> bool:
        """Whether the gateway refused the direct form for a zone that was only found standalone.

        The finding is dropped then, and the caller writes through the central unit again. An
        F454 NACKs ``*#4*Z*#14*T*M##`` for a zone of its central unit and takes
        ``*#4*#Z*#14*T*M##`` (#697: ``*#4*3*#14*0160*1##`` "Could not send message",
        ``*#4*#3*#14*0145*1##`` answered with ``*#4*3*12*0145*3##``), so a zone held standalone
        there by mistake would have every setpoint refused, with nothing to end it but a report
        of the central unit. A zone that really is standalone is found again by the check of
        the next setpoint write (``_confirm_setpoint_write``). A configured zone keeps its form,
        and so does any zone after a frame that was merely not acknowledged in time.
        """
        if not self._standalone_detected or self._standalone_config is not None:
            return False
        if getattr(written, "_gateway_nack", False) is not True:
            return False
        self._set_standalone_detected(False)
        LOGGER.info(
            "%s %s: the gateway refused `%s`; the zone is written through the central unit again",
            self._gateway_handler.log_id,
            self._display_name,
            frame,
        )
        self._refresh_hvac_modes()
        self._publish_state()
        return True

    def _mark_standalone_detected(self, *, by_season: bool = False) -> None:
        """The central unit is not running this zone.

        Found by a setpoint only the direct form changed (``_confirm_setpoint_write``), or
        ``by_season``: the zone keeps reporting the season the central unit is not in
        (``_confirm_season_against_central``).
        """
        self._set_standalone_detected(True)
        self._central_zone_seen = False
        if by_season:
            LOGGER.info(
                "%s %s runs in %s while the central unit is in %s and has not reported it: it is "
                "handled as a standalone thermostat (list it under \"Standalone thermostats\" in "
                "the gateway options to make that permanent)",
                self._gateway_handler.log_id,
                self._display_name,
                self._own_season,
                getattr(self._gateway_handler, "central_season", None),
            )
        else:
            LOGGER.info(
                "%s %s took its setpoint as `*#4*%s*#14*...` and not through the central unit: it is "
                "handled as a standalone thermostat (list it under \"Standalone thermostats\" in the "
                "gateway options to make that permanent)",
                self._gateway_handler.log_id,
                self._display_name,
                zone_number(self._where),
            )
        self._cancel_generic_zone_frame()
        self._refresh_hvac_modes()
        if (
            self._season_reported
            and self._season is not None
            and (
                not self._own_off
                if self._own_off is not None
                else self._attr_hvac_mode != HVACMode.OFF or self._hvac_mode_restored
            )
        ):
            # A standalone thermostat's season frame is its mode (#880): the mode shown so far
            # was a restored one, the AUTO of an F455's made-up *4*311*#Z##, or the mode of the
            # central unit the zone was taken to follow (live 2026-11-18: ``heat`` next to the
            # zone's ``*4*0*2##``). A zone whose own frame says off or protection
            # (``*4*303*2##``, ``*4*102*2##``) keeps that: its season is not what it runs in.
            season_mode = HVACMode.HEAT if self._season == SEASON_HEATING else HVACMode.COOL
            if season_mode in self._configured_hvac_modes():
                self._attr_hvac_mode = season_mode
                self._hvac_mode_restored = False
                if self._attr_hvac_action == HVACAction.OFF:
                    # Left by the central unit's off (``_handle_central_mode_update``): a zone
                    # that runs is idle until its actuators report, as on every other way out of off.
                    self._attr_hvac_action = HVACAction.IDLE
                self._refresh_hvac_modes()
        if self._actuators_off_by_central:
            # The off of a central unit that does not run this zone marked its actuators off
            # here and set the zone's 0-10 V output sensor to 0 %. Its actuators are asked
            # again, so both show what the plant does and not what that off implied.
            self._actuators_off_by_central = False
            self._actuators_assumed_off.clear()
            self._request_zone_dimension(20, again=True)
        self._publish_state()  # the entry may have been unloaded during the wait

    def _note_own_season(self, message: OWNHeatingEvent) -> None:
        """Keep the season the zone reports in its own, plain ``Z`` form (``*4*0*2##``, ``*4*202*4##``)."""
        season = getattr(message, "season", None)
        if self._central or not isinstance(season, str) or is_central_thermo_where(message):
            return
        if _two_digit_interface(thermo_event_interface(message)) != self._interface:
            return  # the same zone number on another bus
        self._own_season = season
        if self._own_season_reported is not None:
            self._own_season_reported.set()
        self._check_season_against_central()

    def _season_against_central(self) -> bool:
        """Whether this zone, found on the bus, reports the season the central unit is not in.

        Only for a zone nobody configured, not yet found standalone, on a plant with a central
        unit that has not reported it in the ``#Z`` form (``_central_zone_seen``). Only on the
        gateway's own bus: the central unit whose season is kept is that bus's, and a zone
        behind an interface may have another.
        """
        if (
            self._central
            or self._interface is not None
            or self._standalone_config is not None
            or self._standalone_detected
            or self._central_zone_seen
            or getattr(self._gateway_handler, "has_central_unit", False) is not True
        ):
            return False
        central_season = getattr(self._gateway_handler, "central_season", None)
        if not isinstance(central_season, str) or self._own_season is None:
            return False
        return self._season_side(self._own_season) != self._season_side(central_season)

    def _central_season_settling(self) -> float:
        """Seconds left before a zone is compared with a season the central unit has just changed to.

        0 when the season has only been learned, or changed ``CENTRAL_SEASON_SETTLE`` ago.
        """
        changed_at = getattr(self._gateway_handler, "central_season_changed_at", None)
        if not isinstance(changed_at, (int, float)):
            return 0.0
        return max(0.0, changed_at + CENTRAL_SEASON_SETTLE - time.monotonic())

    def _check_season_against_central(self) -> None:
        """Start the check of a zone that reports the season the central unit is not in.

        A central unit runs its zones in its own season. In the 22 captures of tests/fixtures
        with a central unit's season, the only zone a central unit reports (``*4*203*#4##``)
        that is seen in the other season is zone 4 of issue_453, once, in the 2.0 s after a
        season change (``STANDALONE_SEASON_CONFIRM_DELAY``); the other zones seen in the other
        season are this plant's zone 2 on days the 3550 did not report it. Live plant,
        2026-11-18: the 3550 in ``*4*102*#0##`` (heating), its zones 3 and 4 in ``*4*1*3##`` /
        ``*4*1*4##``, and zone 2, which it is not running, in ``*4*0*2##``. Taken as a zone of
        the central unit, zone 2 showed ``heat`` while it was cooling and offered no ``cool``,
        until its first setpoint write found it standalone (``_confirm_setpoint_write``).
        One frame proves nothing, so the zone is read again (``_confirm_season_against_central``).
        """
        if self._season_check_task is not None or not self._season_against_central():
            return
        if self.hass is None or self.platform is None or not self.entity_id:
            return
        self._season_check_task = self.hass.async_create_background_task(
            self._confirm_season_against_central(),
            name=f"myhome_{self._gateway_handler.mac}_{self._full_where}_season_check",
        )

    async def _confirm_season_against_central(self) -> None:
        """Read the zone again; a second report of the other season makes it standalone.

        After the startup requests and ``STANDALONE_SEASON_CONFIRM_DELAY`` seconds, and not
        before the zones have had the time to follow a season change of the central unit
        (``CENTRAL_SEASON_SETTLE``), the zone is
        asked its setpoint in force, ``*#4*Z*12##``: the thermostat answers with dimension 12
        and its season frame (live 2026-11-18, ``*#4*2*12##`` -> ``*#4*2*12*0200*3##``,
        ``*4*0*2##`` on F461 2.0.11 and F455 1.0.76; ``*#4*70*12##`` -> ``*#4*70*12*0230*3##``,
        ``*4*1*70##`` on MyHomeServer1 3.87.13; every emulated firmware forwards the request,
        own-firmware-oracle trace-corpus.tsv:57). Nothing is concluded from silence: no answer,
        a zone that has moved to the central unit's season, or a report of the central unit
        about the zone meanwhile, and the zone stays one of the central unit's. A zone found
        standalone goes back under the central unit when it reports it
        (``_note_central_zone_report``).
        """
        try:
            wait = getattr(self._gateway_handler, "wait_for_initial_discovery", None)
            if callable(wait):
                await wait()
            await asyncio.sleep(STANDALONE_SEASON_CONFIRM_DELAY)
            while (settling := self._central_season_settling()) > 0:
                # The central unit changed season a moment ago and its zones are still
                # following it; a second change meanwhile starts the wait again.
                await asyncio.sleep(settling)
            if not self._season_against_central():
                return
            reported = self._own_season_reported = asyncio.Event()
            if not self._request_zone_dimension(12):
                return
            try:
                await asyncio.wait_for(reported.wait(), timeout=PACED_READ_TIMEOUT)
            except TimeoutError:
                LOGGER.debug(
                    "%s %s did not answer `*#4*%s*12##` with its season; it stays a zone of the central unit",
                    self._gateway_handler.log_id,
                    self._display_name,
                    self._full_where,
                )
                return
            # A season change of the central unit while the read was out needs no second look:
            # the zone was in the season the central unit was not in, so after the change the
            # two agree and nothing is concluded below.
            if self._season_against_central():
                self._mark_standalone_detected(by_season=True)
        finally:
            self._season_check_task = None
            self._own_season_reported = None

    def _cancel_season_check(self) -> None:
        """Stop the running season check: the entity goes away."""
        task, self._season_check_task = self._season_check_task, None
        if task is not None:
            task.cancel()

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
    def _handle_central_season_update(self) -> None:
        """The plant's central unit, or its season, has just become known.

        Sent for any report of the central unit, also one about another zone: on an F455 the
        central unit's status is not asked at start, and its quarter-hour push names zones only
        (``*4*110*#3##``, ``*4*110*#4##``, no ``#0`` frame), so ``_handle_central_mode_update``
        never runs there. A zone that reported its own season before (``*4*0*2##`` while no
        central unit was known) is compared now, and the modes offered follow the plant.
        """
        changed = self._refresh_hvac_modes()
        self._check_season_against_central()
        if changed:
            self._publish_state()

    def _restore_cooling_seen(self, last_state: State) -> None:
        """What the saved state of a zone found on the bus says about its cooling.

        Looked at before the saved mode: a zone saved while it was cooling came back as ``off``,
        because ``cool`` was not yet one of its modes when the saved mode was checked. Any of:

        * ``cooling_seen``: what the bus had shown, saved since this rule exists;
        * the saved mode ``cool``: the zone was in conditioning. A state saved by 2.0.0 has
          no ``cooling_seen``, and a zone found on the bus could be in ``cool`` there;
        * ``fan_coil_cooling``: a fan coil state on the conditioning side of its valve status.
          With the fan coil sides restored that status is not asked again, so a state saved by
          2.0.0 would leave a zone with a cooling fan coil without ``cool`` until it next reports.

        Nothing else is taken from an older state: a zone saved in ``heat`` with none of these
        learns it from the bus again (``_note_conditioning_side``, ``_note_conditioning_report``).
        """
        if self._cooling_configured or self._cooling:
            return
        attributes = last_state.attributes
        if (
            attributes.get("cooling_seen") is True
            or attributes.get("fan_coil_cooling") is True
            or last_state.state == HVACMode.COOL.value
        ):
            self._cooling = True

    def _note_conditioning_report(self, message: OWNHeatingEvent) -> None:
        """A zone found on the bus cools once the bus reports it in conditioning.

        Its own season or mode frame (``*4*0*2##``) or the central unit's report about it
        (``*4*210*#5##``: zones 5 and 12 of the F454 plant of #629 are put in manual
        conditioning that way and must show ``cool``). Not the central unit's own frames:
        its season says nothing about one zone's actuators.
        """
        if self._cooling_configured or self._cooling or self._central:
            return
        if getattr(message, "season", None) not in _CONDITIONING_SEASONS:
            return
        if _two_digit_interface(thermo_event_interface(message)) != self._interface:
            return  # the same zone number on another bus
        if is_central_thermo_where(message) and central_report_zone(message) != canonical_thermo_zone(self._where):
            return  # #0 / #0#N, or the central unit about another zone
        self._cooling = True
        self._refresh_hvac_modes()

    def _note_conditioning_side(self, message: OWNHeatingEvent) -> None:
        """A zone found on the bus cools once its valve status shows a conditioning actuator.

        ``*#4*Z*19*CV*HV##``: ``CV`` is the conditioning side (WHO 4 document, 1.5.7). A zone
        with no actuator on that side reports 0 there and nothing else: live 2026-11-18, the
        heating-only zone 3 behind an F461 and an F455 ``*#4*3*19*0*1##``, the 29 heating-only
        zones behind a 3488 and a MyHomeServer1 ``*#4*49*19*0*0##``, while the zones with a
        cooling fan coil report its state, ``*#4*4*19*5*1##``, ``*#4*2*19*8*5##``,
        ``*#4*47*19*5*0##``. In tests/fixtures every zone with a dimension 19 frame has 0 or
        a fan coil state (5, 7, 8) on that side, none a plain valve state (counted by trace
        folder and zone when this was written: 86 zones, 24 with a fan coil state, 62 with 0).
        A cooling valve that is off reads 0 as well, so such a zone cools from the
        first time its valve is seen in any other state; ``cool: true`` in ``myhome.yaml``
        says it at once. Never unlearned: 0 is also a valve that is off.
        """
        if self._cooling_configured or self._cooling:
            return
        if _two_digit_interface(thermo_event_interface(message)) != self._interface:
            return  # the same zone number on another bus
        values = getattr(message, "dimension_value", None)
        side = str(values[0]) if isinstance(values, (list, tuple)) and values else ""
        if side.isdigit() and int(side) != 0:
            self._cooling = True
            self._refresh_hvac_modes()

    def _set_standalone_detected(self, detected: bool) -> None:
        """Keep the finding, and share it with what else hangs on this zone's device.

        The 0-10 V output sensor of the zone's actuator (``sensor.MyHOMEActuatorOutputSensor``)
        goes to 0 % when the central unit switches its zones off; not for a zone it does not run.
        """
        self._standalone_detected = detected
        shared = getattr(self._gateway_handler, "standalone_detected_zones", None)
        zone = canonical_thermo_zone(self._where)
        if isinstance(shared, set) and zone is not None:
            if detected:
                shared.add((zone, self._interface))
            else:
                shared.discard((zone, self._interface))

    def _forget_standalone_detected(self) -> None:
        """The entity goes away: its finding no longer speaks for the zone."""
        shared = getattr(self._gateway_handler, "standalone_detected_zones", None)
        zone = canonical_thermo_zone(self._where)
        if isinstance(shared, set) and zone is not None:
            shared.discard((zone, self._interface))

    @callback
    def _handle_central_mode_update(self, master_mode: HVACMode) -> None:
        """Update subordinate zone mode when central unit changes seasonal mode."""
        if self._standalone_detected:
            return  # found not to be run by the central unit: its own frames say what it does
        self._check_season_against_central()
        prev_mode = self._attr_hvac_mode
        if master_mode == HVACMode.OFF:
            self._attr_hvac_mode = HVACMode.OFF
            self._attr_hvac_action = HVACAction.OFF
            self._mark_actuators_off()
            self._actuators_off_by_central = True
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
            return round(self._local_target_temperature - self._local_offset, 1)
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
        self._local_target_base = round(local_target - offset, 1)
        if offset == 0 and self._local_offset == 0:
            return False
        self._local_offset = offset
        self._knob_pos = "0" if offset == 0 else f"{offset:+d}"
        return True

    def _reconcile_local_target(self) -> bool:
        """Update nominal setpoint and derived knob offset from a Dimension 12 report (#466).

        Returns True when this Dimension 12 reconciled Home Assistant's own manual write echo.
        """
        now = time.monotonic()
        written = self._written_target_temperature
        self._written_target_temperature = None
        from_write_echo = written is not None and now - written[1] <= _PROTECTION_FRAME_WINDOW
        if from_write_echo and written is not None:
            self._derive_local_offset(self._local_target_temperature, written[0])
            self._dim12_at = None
            self._unpaired_dim12_at = None
            self._dim14_at = None
        elif self._dim14_at is not None and now - self._dim14_at <= _PROTECTION_FRAME_WINDOW:
            self._derive_local_offset(self._local_target_temperature, self._target_temperature)
            self._dim12_at = None
            self._dim14_at = None
        base = (
            round(self._local_target_temperature - self._local_offset, 1)
            if self._local_target_temperature is not None
            else None
        )
        self._target_temperature = base
        self._nominal_from_dim12 = base is not None
        if self._local_offset_reported or self._local_target_base is not None:
            self._local_target_base = base
        return from_write_echo

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
                if self._local_offset_reported:
                    self._local_target_base = round(
                        self._local_target_temperature - self._local_offset, 1
                    )
                self._dim12_at = None
                self._dim14_at = None
            else:
                self._reconcile_local_target()
        elif (
            self._local_target_temperature is None
            and self._target_temperature is not None
            and not self._local_target_reported
        ):
            self._local_target_temperature = round(
                self._target_temperature + self._local_offset, 1
            )

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
            await self._send_in_zone_form(
                lambda direct: OWNHeatingCommand.set_mode(where=self._where, mode=CLIMATE_MODE_OFF, standalone=direct)
            )
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
                season_mode = CLIMATE_MODE_HEAT if heat else CLIMATE_MODE_COOL
                await self._send_in_zone_form(
                    lambda direct: OWNHeatingCommand.set_temperature(
                        where=self._where, temperature=nominal, mode=season_mode, standalone=direct
                    )
                )

    async def _send_in_zone_form(self, build: Callable[[bool], Any]) -> None:
        """Send a mode command in the form this zone is written in: ``Z``, or ``#Z`` through the central unit.

        ``build(direct)`` makes the frame. For a zone that was only found standalone, a
        refusal of the direct form by the gateway drops the finding and the same command
        goes through the central unit, as for a setpoint (``_drop_refused_standalone_finding``):
        the F454 of #697 refused ``*4*303*3##`` ("Could not send message") and took
        ``*4*303*#3##``, answered by ``*4*303*3##`` and ``*4*103*#3##``. Every other zone is
        written once and not waited for, as before.
        """
        direct = self._standalone
        command = build(direct)
        if command is None:
            return
        written = await self._gateway_handler.send(command)
        if not (
            direct
            and self._standalone_detected
            and self._standalone_config is None
            and isinstance(written, asyncio.Future)
        ):
            return
        await asyncio.wait([written], timeout=SETPOINT_DELIVERY_TIMEOUT)
        if not self._drop_refused_standalone_finding(written, str(command)):
            return
        command = build(False)
        if command is not None:
            await self._gateway_handler.send(command)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if self._refuse_interface_write():
            return
        target_temperature = round(
            float(
                kwargs.get("temperature", self._local_target_temperature)  # type: ignore[arg-type]
            )
            - self._local_offset,
            1,
        )
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
            self._local_target_base = None
            self._nominal_from_dim12 = False
            if self.hass is not None:
                self.async_write_ha_state()
            return
        direct = self._standalone
        command = OWNHeatingCommand.set_temperature(
            where=self._where,
            temperature=target_temperature,
            mode=SETPOINT_WRITE_MODE,
            standalone=direct,
        )
        written_before = self._written_target_temperature
        written = await self._gateway_handler.send(command)
        # The write's echo decides what is shown next: dimension 12 where the gateway reports
        # the setpoint in force (F461, MyHomeServer1), else the commanded dimension 14.
        self._local_target_reported = False
        self._local_target_base = None
        self._dim13_at = None
        self._unpaired_dim12_at = None
        self._paired_burst = None
        on_the_wire = min(40.0, max(5.0, round(target_temperature * 2) / 2))  # as OWNd writes it
        checked = isinstance(written, asyncio.Future) and not is_actuator_where(self._where) and self._interface is None
        if direct or not checked:
            self._written_target_temperature = (on_the_wire, time.monotonic())
        # else: written through the central unit and about to be checked. Until the plant
        # confirms it, a setpoint in force that comes back is not this write's echo: another
        # client's status answer 0.72 s after an ignored ``*#4*#2*#14*0210*3##`` was read as a
        # knob at -1, and the next 20.0 went out as 21.0.
        if not isinstance(written, asyncio.Future) or is_actuator_where(self._where) or self._interface is not None:
            # No delivery information; or an address whose setpoint is not read back as
            # ``*#4*Z*14##``: one actuator or split (``Z#N``), a zone behind an interface.
            return
        if direct:
            # A gateway that refuses the direct form says so with a NACK (an F454 for a zone of
            # its central unit, #697; the emulated F453, F453AV, H4684, MH200 and MH200N for any
            # zone, own-firmware-oracle trace-corpus.tsv:55, not yet seen on a real one).
            await asyncio.wait([written], timeout=SETPOINT_DELIVERY_TIMEOUT)
            if not written.done():
                written.cancel()  # withdrawn: it must not go out after the call has returned
            if _delivered(written):
                return
            if not self._drop_refused_standalone_finding(written, str(command)):
                self._setpoint_not_delivered(written, str(command))
                return
            # Found standalone, and the gateway refuses the direct form for it: the same
            # setpoint goes through the central unit, checked like any write in that form.
            command = OWNHeatingCommand.set_temperature(
                where=self._where,
                temperature=target_temperature,
                mode=SETPOINT_WRITE_MODE,
                standalone=False,
            )
            self._written_target_temperature = written_before  # as before a write in that form
            written = await self._gateway_handler.send(command)
        # Written through the central unit: a thermostat it is not running ignores that form
        # and both gateways still acknowledge it. Returns as soon as the plant confirms. The
        # value checked is the one on the wire (0.5 steps, 5.0 to 40.0).
        await self._confirm_setpoint_write(written, str(command), on_the_wire, allow_direct=not direct)

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
        self._local_target_base = None
        self._dim12_at = None
        self._dim13_at = None
        self._unpaired_dim12_at = None
        self._paired_burst = None
        self._dim14_at = None
        if remembered is not None and time.monotonic() - remembered[1] <= _PROTECTION_FRAME_WINDOW:
            self._target_temperature = remembered[0]
            self._nominal_from_dim12 = False

    def _show_nominal(self) -> None:
        """Show the nominal setpoint plus the knob offset until the zone reports dimension 12 again.

        Used when the zone leaves OFF (the dimension 12 value seen while OFF was the protection
        setpoint) or a frame carries a new setpoint of its own.
        """
        if self._target_temperature is None:
            return
        self._local_target_temperature = round(
            self._target_temperature + self._local_offset, 1
        )
        self._local_target_reported = False
        self._local_target_base = None
        self._dim12_at = None
        self._dim13_at = None
        self._unpaired_dim12_at = None
        self._paired_burst = None
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
            self._nominal_from_dim12 = False
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
        """Handle an event message, then see whether it confirms a setpoint write being checked.

        After, not before: the frame that shows the written setpoint can be the one that
        teaches the knob offset it is shown with (a knob at +1 that had not reported: 21.0
        written, ``*#4*2*12*0220*3##`` back).
        """
        self._note_conditioning_report(message)  # first: the frame is then read as a cooling zone's
        try:
            self._handle_event(message)
        finally:
            self._note_setpoint_report(message)

    def _handle_event(self, message: OWNHeatingEvent) -> None:
        """Take what the frame says about this zone or central unit."""
        if self._central and self._is_own_central_echo(message):
            LOGGER.debug("%s %s: own command echoed by the gateway, not a central unit report", self._gateway_handler.log_id, message)
            return
        # A central unit's own report first: on a zero-config plant its *4*111*#1## makes zone 1
        # a zone of the central before the standalone check below looks at it.
        if is_central_unit_report(message):
            # Also here: a reply collected on the command session reaches the entity without
            # passing the event session's bookkeeping (``gateway_events``), which tells the
            # other zones when this is news.
            central_news, central_found_by_zone = note_central_unit_report(self._gateway_handler, message)
            self._note_central_zone_report(message)
            if central_news and self.hass is not None:
                async_dispatcher_send(self.hass, f"myhome_central_season_{self._gateway_handler.mac}")
            if central_found_by_zone:
                ask = getattr(self._gateway_handler, "request_central_status", None)
                if callable(ask):
                    ask()  # the central unit has no entity until a frame of its own comes
        self._note_own_state(message)
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
            self._nominal_from_dim12 = False
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
                if self._unpaired_dim12_at is not None:
                    self._unpaired_dim12_at = now
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
                    round(self._target_temperature + self._local_offset, 1)
                    if self._target_temperature is not None
                    else None
                )
        elif message.message_type == MESSAGE_TYPE_LOCAL_OFFSET:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            now = time.monotonic()
            raw_msg = str(message)
            prev_base = self._local_target_base
            paired_with_dim12 = (
                self._unpaired_dim12_at is not None
                and now - self._unpaired_dim12_at <= _PROTECTION_FRAME_WINDOW
            ) or (
                self._paired_burst is not None
                and self._paired_burst[0] == raw_msg
                and now - self._paired_burst[1] <= _PROTECTION_FRAME_WINDOW
            )
            self._unpaired_dim12_at = None
            self._local_offset = message.local_offset if message.local_offset is not None else 0
            self._local_offset_reported = True
            self._dim12_at = None
            self._dim14_at = None
            is_numeric_offset = message.local_control_state in (
                LOCAL_CONTROL_OFFSET,
                LOCAL_CONTROL_NORMAL,
            )
            if is_numeric_offset:
                self._nominal_before_dim12 = None
            if paired_with_dim12:
                self._paired_burst = (raw_msg, now)
                self._dim13_at = None
            else:
                self._paired_burst = None
                self._dim13_at = now if is_numeric_offset else None
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
            if self._attr_hvac_mode != HVACMode.OFF:
                if not self._local_target_reported:
                    if self._target_temperature is not None:
                        self._local_target_temperature = round(
                            self._target_temperature + self._local_offset, 1
                        )
                elif is_numeric_offset and self._local_target_temperature is not None:
                    if prev_base is None or paired_with_dim12:
                        # Initial Dimension 13 report or trailing frame of a Dimension 12
                        # status burst: _local_target_temperature already includes this offset.
                        self._local_target_base = round(
                            self._local_target_temperature - self._local_offset, 1
                        )
                        if self._nominal_from_dim12:
                            self._target_temperature = self._local_target_base
                    else:
                        # Standalone live knob movement (e.g. BTicino N4692 / H/L4684 under
                        # 3550 emits *#4*Z*13*OO## without a follow-up Dimension 12 frame).
                        self._local_target_temperature = round(
                            prev_base + self._local_offset, 1
                        )
        elif message.message_type == MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE:
            LOGGER.debug(
                "%s %s",
                self._gateway_handler.log_id,
                message.human_readable_log,
            )
            now = time.monotonic()
            raw_msg = str(message)
            self._local_target_temperature = message.local_set_temperature
            self._local_target_reported = self._local_target_temperature is not None
            self._dim12_at = (
                now
                if self._local_target_reported and self._attr_hvac_mode != HVACMode.OFF
                else None
            )
            self._target_temperature_restored = False
            is_protection_or_off = self._dimension_3_is_protection(message)
            paired_with_dim13 = (
                self._dim13_at is not None
                and now - self._dim13_at <= _PROTECTION_FRAME_WINDOW
            ) or (
                self._paired_burst is not None
                and self._paired_burst[0] == raw_msg
                and now - self._paired_burst[1] <= _PROTECTION_FRAME_WINDOW
            )
            if paired_with_dim13:
                self._dim13_at = None
                self._unpaired_dim12_at = None
                self._paired_burst = (raw_msg, now)
            else:
                self._dim13_at = None
                self._paired_burst = None
                self._unpaired_dim12_at = self._dim12_at
            if not is_protection_or_off:
                self._remember_nominal()
                if self._reconcile_local_target():
                    self._paired_burst = (raw_msg, now)
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
            self._note_own_season(message)
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
            if not self._central and is_central_thermo_where(message):
                self._unpaired_dim12_at = None
            self._hvac_mode_restored = False  # the zone itself says what it runs in
            prev_mode = self._attr_hvac_mode
            previous_season = self._season
            self._remember_season(message)
            self._note_own_season(message)
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
            if not self._central and is_central_thermo_where(message):
                self._unpaired_dim12_at = None
            if self._central:
                self._central_operation = None
            self._hvac_mode_restored = False
            prev_mode = self._attr_hvac_mode
            self._remember_season(message)
            self._note_own_season(message)
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
                self._nominal_from_dim12 = False
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
            dimension = getattr(message, "dimension", None)
            sides_resolved = False
            cooling_active = False
            heating_active = False
            dim20_level: int | None = None
            if dimension == 19:
                dim19 = thermo_dim19_status(message)
                if dim19 is None:
                    return
                self._note_conditioning_side(message)
                (
                    cooling_active,
                    heating_active,
                    is_active,
                    cooling_fan_on,
                    heating_fan_on,
                    msg_fan_speed,
                ) = dim19
                if heating_fan_on is True or cooling_fan_on is True:
                    msg_fan_on: bool | None = True
                elif heating_fan_on is False or (
                    cooling_fan_on is False and self._fan
                ):
                    msg_fan_on = False
                else:
                    msg_fan_on = None
                sides_resolved = not self._fan_coil_sides
                self._fan_coil_sides = {
                    "cooling": isinstance(cooling_fan_on, bool),
                    "heating": isinstance(heating_fan_on, bool),
                }
                self._fan_coil_sides_asked_at = None
                self._fan_coil_sides_held = False
                if self._has_no_fan_coil_sides() and not self._fan_configured:
                    # Pure-valve actuator channels can report value 5 on dimension 20 (issue #466 H/L4684
                    # zones 11..88) while dimension 19 carries 0..4 on both sides once _ask_fan_coil_sides()
                    # (*#4*Z*19##) is answered (myhomeserver1_live_2026-10-25 zones 35..70, issue_674 F455):
                    # retract the tentative fan_mode once dimension 19 proves neither side is a fan coil.
                    self._disable_fan_mode()
            elif dimension == 20:
                dim20 = thermo_dim20_status(message)
                if dim20 is None:
                    return
                is_active, msg_fan_on, msg_fan_speed = dim20
                dim20_level = thermo_dim20_level(message)
            else:
                cooling_active = bool(message.is_cooling())
                heating_active = bool(message.is_heating())
                is_active = bool(message.is_active())
                msg_fan_on = getattr(message, "fan_on", None)
                msg_fan_speed = getattr(message, "fan_speed", None)

            # Actuator status (Dimension 20) with fan-coil codes (5..9, 14..16) reports fan status
            # unless dimension 19 already proved neither side of an unconfigured zone is a fan coil.
            is_fan = (
                isinstance(msg_fan_speed, int) or isinstance(msg_fan_on, bool)
            ) and not (self._has_no_fan_coil_sides() and not self._fan_configured)
            fan_enabled_now = is_fan and not self._fan
            if is_fan:
                self._enable_fan_mode()
                if dimension == 20:
                    # The actuator report revealed a fan coil; ask which side it is on.
                    self._ask_fan_coil_sides()
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

            # An "off" of an actuator that was already known to be off tells nothing new. While
            # dimension 19 leads with an active side, the actuator that is running is one that
            # has not reported: the repeated "off" must not make the zone idle. Physical F455
            # plant, 2026-11-17, zone 4 with its fan coil on actuator 2: another client read
            # actuator 1, 23 s after `*#4*4*19*8*0##`, and the answer `*#4*4#1*20*0##` set the
            # zone to idle for 3 s while the fan coil ran (`tests/fixtures/traces/issue_932`).
            # Anything else an actuator says takes the lead as before.
            dim20_target = (
                thermo_dim20_actuator_target(message) if dimension == 20 else None
            )
            repeated_off = (
                dimension == 20
                and not is_active
                and self._active_source == "zone"
                and self._zone_active is True
                and self._actuator_states.get(thermo_event_actuator(message)) is False
            )
            if repeated_off:
                if dim20_target is not None:
                    target_actuator = dim20_target[1]
                    if dim20_level is not None:
                        self._actuator_levels[target_actuator] = dim20_level
                    elif target_actuator in self._actuator_levels:
                        self._actuator_levels[target_actuator] = 0
            elif dimension == 20:
                actuator_id = thermo_event_actuator(message)
                self._actuator_states[actuator_id] = is_active
                self._active_source = "actuators"
                self._actuators_assumed_off -= {actuator_id, "zone"}  # it has said so itself
                if dim20_target is not None:
                    target_actuator = dim20_target[1]
                    if dim20_level is not None:
                        self._actuator_levels[target_actuator] = dim20_level
                    elif target_actuator in self._actuator_levels:
                        if is_active:
                            self._actuator_levels.pop(target_actuator, None)
                        else:
                            self._actuator_levels[target_actuator] = 0
                if is_fan:
                    self._actuator_fan_speeds.pop(actuator_id, None)
                    self._actuator_fan_speeds[actuator_id] = (
                        self._decode_running_fan_speed(msg_fan_on, msg_fan_speed)
                    )
                    self._sync_actuator_running_fan_speed()
                elif actuator_id in self._actuator_fan_speeds:
                    self._actuator_fan_speeds[actuator_id] = (
                        None if is_active else "off"
                    )
                    self._sync_actuator_running_fan_speed()
                elif not self._any_actuator_active() and self._running_fan_speed in (
                    "low",
                    "medium",
                    "high",
                ):
                    self._running_fan_speed = "off"
            else:
                self._zone_active = is_active
                self._active_source = "zone"
                self._actuators_assumed_off.clear()  # the zone's valve status speaks for both sides
                if not self._zone_active:
                    # Neither side is active: every actuator of the zone is off.
                    self._actuator_states = dict.fromkeys(self._actuator_states, False)
                    self._actuator_levels = dict.fromkeys(self._actuator_levels, 0)
                    self._actuator_fan_speeds = dict.fromkeys(
                        self._actuator_fan_speeds, "off"
                    )
                if is_fan:
                    self._running_fan_speed = self._decode_running_fan_speed(
                        msg_fan_on, msg_fan_speed
                    )
                    if self._running_fan_speed == "off":
                        self._actuator_fan_speeds = dict.fromkeys(
                            self._actuator_fan_speeds, "off"
                        )
                elif self._fan:
                    dim_vals = (
                        getattr(message, "dimension_value", None)
                        if dimension == 19
                        else None
                    )
                    modulating = isinstance(dim_vals, (list, tuple)) and any(
                        str(v).isascii() and str(v).isdigit() and 101 <= int(str(v)) <= 200
                        for v in dim_vals[:2]
                    )
                    if modulating:
                        self._actuator_fan_speeds = dict.fromkeys(
                            self._actuator_fan_speeds, None
                        )
                        self._running_fan_speed = None
                    elif (dimension == 19 or not self._zone_active) and (
                        self._actuator_fan_speeds
                        or self._running_fan_speed in ("low", "medium", "high")
                    ):
                        self._actuator_fan_speeds = dict.fromkeys(
                            self._actuator_fan_speeds, "off"
                        )
                        self._running_fan_speed = "off"
            any_active = self._any_actuator_active()

            if not any_active:
                self._active_sides = None
            elif is_active:
                # The sides this report named, for the frames that follow it
                # (``_settle_action_of_active_actuators``). An "off" of one actuator while
                # another is on leaves what the active one said.
                self._active_sides = (heating_active, cooling_active)
            if repeated_off:
                pass  # hvac_action stays what dimension 19 made it
            elif any_active:
                action = self._action_of_active_actuators(heating_active, cooling_active)
                if action is not None:
                    self._attr_hvac_action = action
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

        self._settle_action_of_active_actuators()
        self._ask_actuators_assumed_off()
        # The list goes by the season, the plant and the mode shown: whatever the frame changed.
        self._refresh_hvac_modes()
        self._refresh_bus_state_complete()
        self._publish_state()
