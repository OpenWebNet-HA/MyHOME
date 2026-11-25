"""Support for MyHome lights."""

import asyncio
import inspect
import time
from typing import Any, cast

from homeassistant.components.light import (  # type: ignore[attr-defined, unused-ignore]
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_FLASH,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    FLASH_LONG,
    FLASH_SHORT,
    LightEntity,
)
from homeassistant.components.light.const import (
    DOMAIN as PLATFORM,
)
from homeassistant.components.light.const import (
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData
from homeassistant.util.color import (
    color_hs_to_RGB,
    color_RGB_to_hs,
    color_temperature_kelvin_to_mired,
    color_temperature_mired_to_kelvin,
)
from OWNd.message import (
    OWNLightingCommand,
    OWNLightingEvent,
)

from .actuator_lock import (
    AREA_WHERES,
    actuator_locks_of,
    entity_key,
    is_valid_point_where,
    normalize_iface,
    parse_hash_group,
    point_area,
)
from .const import (
    BUS_ROUTING,
    COARSE_PRESET_TO_PERCENT,
    CONF_COLOR_TEMP,
    CONF_DEVICE_MODEL,
    CONF_DIMMABLE,
    CONF_ENTITY_NAME,
    CONF_HS,
    CONF_ICON,
    CONF_ICON_ON,
    CONF_LOCK_FEATURES,
    CONF_MANUFACTURER,
    CONF_MEMBERS,
    CONF_RGB,
    CONF_TRANSITION_MODE,
    CONF_WHO,
    CONF_WORKER_COUNT,
    DEFAULT_TRANSITION_MODE,
    LOGGER,
    TRANSITION_MODE_AUTO,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
    build_timed_turn_on_command,
    eight_bits_to_min_percent,
    eight_bits_to_percent,
    normalize_where,
    percent_to_eight_bits,
)
from .data import MyHOMEConfigEntry
from .discovery import (
    Address,
    DeviceContext,
    KnownDevices,
    PlatformDiscovery,
    is_motion_end,
    parse_unique_id,
)
from .gateway import MyHOMEGatewayHandler
from .gateway_confirm import ECHO_CONFIRM_DELAY_S
from .light_dali import DaliFeatureLock
from .light_fade import DEFAULT_FIRST_ECHO_TIMEOUT, SoftwareFadeEngine
from .light_group import MyHOMELightGroup, _color_modes_from_flags
from .myhome_device import MyHOMEEntity
from .services import async_register_turn_on_timed_service
from .where_grammar import (
    bus_interface_from_message,
    canonical_point_spelling,
    expose_message_where,
    is_valid_hs_tuple,
    is_valid_rgb_tuple,
)

PARALLEL_UPDATES = 0

# OWNd's DIMMER_SPEED_COARSE_ONLY: the speed slot a pre-09W29 coarse-only dimmer
# reports on Dimension 1 (#767); such a dimmer stays on the preset table (#851).
_COARSE_ONLY_SPEED = 240
# One Dimension 1 read per burst of WHAT 2..10 echoes on a fine dimmer (#851).
_BRIGHTNESS_REFRESH_WINDOW_S = 1.0
# Refreshes in a row for the same preset that brought no new level.
_BRIGHTNESS_REFRESH_REPEATS = 2
# No preset frame of the light for this long ends the hold.
_BRIGHTNESS_REFRESH_QUIET_S = 30.0
# How long a Dimension 1 brightness refresh waits for its request to close.
_BRIGHTNESS_REFRESH_TIMEOUT_S = 20.0
# A level 0 is shown on while its status check is out only when the light's "on" status is at
# most this old: the captured level followed its status by 0.14 to 0.63 s, an MH200 off came
# 2.6 s after "on" (tests/fixtures/traces/dimmer_on_at_level_0_2026_11_20).
_LEVEL_ZERO_STATUS_FRESH_S = 2.0
# A dimmer confirmed on at level 0 is verified again at most this often; also the window of
# the burst limit below.
_LEVEL_ZERO_STATUS_CHECK_INTERVAL_S = 30.0
# Status checks of one light in one interval.
_LEVEL_ZERO_CHECK_BURST = 3
# How long a status check waits for its request to be closed.
_LEVEL_ZERO_CHECK_TIMEOUT_S = 20.0
# Wait after the request is closed: the gateway handler resolves the delivery future before it
# hands the collected replies to the entities, and a reply may come on the event session only.
_LEVEL_ZERO_CHECK_GRACE_S = 1.0
# The command pool drops a reply identical to a frame received 0.1 s before the request was taken
# (gateway_sessions.py, ``has_frame_since``) or 0.2 s before the reply (bus_monitor.py dedup
# window): an "on" status this close before the write may be the hidden answer of the check.
_LEVEL_ZERO_HIDDEN_ANSWER_S = 0.2
# What a dimmer that is on at level 0 is shown at: the lowest level a light that is on can have.
_LEVEL_ZERO_ON_PCT = 1

# Legacy mired attribute of stored states (core dropped ATTR_COLOR_TEMP in 2025).
ATTR_COLOR_TEMP = "color_temp"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyHOMEConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the lights of a gateway (WHO=1): registry, myhome.yaml, then bus discovery.

    WHO=1 is shared with switches (configured relays) and with motion /
    illuminance sensors, so the light platform owns the WHO=1 discovery and
    routes frames for those addresses to their platforms instead of creating
    a light for them.
    """
    runtime = config_entry.runtime_data
    mac = config_entry.data[CONF_MAC]
    gateway = runtime.gateway

    foreign = _ForeignAddresses(hass, config_entry, gateway.mac, mac)

    def build(ctx: DeviceContext) -> MyHOMEEntity | None:
        cfg = ctx.cfg
        where = ctx.address.where

        if where.startswith("#"):
            group = int(where[1:])
            members = cfg.get(CONF_MEMBERS, [])
            return MyHOMELightGroup(
                hass,
                cfg.get(CONF_NAME, f"Lighting Group {group}"),
                ctx.key,
                group,
                gateway,
                members,
                dimmable=cfg.get(CONF_DIMMABLE, False),
                color_temp=cfg.get(CONF_COLOR_TEMP, False),
                rgb=cfg.get(CONF_RGB, False) or cfg.get(CONF_HS, False),
                hs=cfg.get(CONF_HS, False),
                icon=cfg.get(CONF_ICON),
                icon_on=cfg.get(CONF_ICON_ON),
            )

        if where in ("0", "00", "1", "2", "3", "4", "5", "6", "7", "8", "9", "100"):
            # Matches validate.py's General()/Area() validators exactly: a yaml
            # `where` this loose is a broadcast address, not a light - never an
            # auto-discovered entity for a group, area or general address (#368).
            # Note: Area 10 is '100' on the bus; '10' is Point-to-Point (A=1, PL=0, #402).
            # Not is_apl_address(): plenty of real point-to-point WHEREs (F422
            # sub-bus addresses like "02") are not full APL-feasible and must
            # still build a light (see #256/#257, #288).
            LOGGER.warning(
                "Refusing to create a light entity for broadcast WHERE %s (must be group or point-to-point)",
                where,
            )
            return None

        # With lock_features the light is exactly what myhome.yaml declares and
        # never learns another mode from the bus (#288 / #307).
        lock_features = cfg.get(CONF_LOCK_FEATURES, False)
        dimmable = cfg.get(CONF_DIMMABLE, False)
        probe_dimmer_on_add = False
        if ctx.source == "bus" and not dimmable and not lock_features:
            # Auto-detect a dimmer from the first frame that carries a level or no-load status
            dimmable = (
                ctx.message.brightness is not None
                or ctx.message.brightness_preset is not None
                or getattr(ctx.message, "unknown_state", None) == 19
            )
            if not dimmable and getattr(ctx.message, "is_on", None) is True:
                probe_dimmer_on_add = (
                    getattr(gateway, "sweep_active", False) is True
                    or getattr(gateway, "initial_discovery_pending", False) is True
                )
        kwargs: dict[str, Any] = {
            "lock_features": lock_features,
            "probe_dimmer_on_add": probe_dimmer_on_add,
        }
        if ctx.source != "bus":
            kwargs |= {
                "color_temp": cfg.get(CONF_COLOR_TEMP, False),
                "rgb": cfg.get(CONF_RGB, False) or cfg.get(CONF_HS, False),
            }
        return MyHOMELight(
            hass=hass,
            name=cfg.get(CONF_NAME, f"Light {ctx.suffix}"),
            entity_name=cfg.get(CONF_ENTITY_NAME),
            icon=cfg.get(CONF_ICON),
            icon_on=cfg.get(CONF_ICON_ON),
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            interface=ctx.address.interface,
            dimmable=dimmable,
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Lighting Device"),
            gateway=gateway,
            **kwargs,
        )

    def ghost(entry: er.RegistryEntry, ctx: DeviceContext) -> bool:
        # A light created in an earlier session for an address that is really a switch or
        # sensor. A light declared in myhome.yaml is never a ghost: a WHO 1 motion sensor
        # can share its address with the actuator it drives (#777).
        if not foreign.blocks_light(ctx.address, ctx.key):
            return False
        if foreign.is_sensor_only(ctx.address, ctx.key):
            LOGGER.warning(
                "Removing %s: WHO 1 address %s also reports motion or illuminance, so its light was"
                " taken for sensor telemetry. If a light actuator shares this address (a detector"
                " driving its own load), declare it under light: in myhome.yaml",
                entry.entity_id,
                ctx.address.key,
            )
        return True

    def accept(ctx: DeviceContext) -> bool:
        return not foreign.blocks_light(ctx.address, ctx.key)

    hinted: set[str] = set()

    @callback
    def route_foreign(message: Any, address: Address, known: KnownDevices) -> bool:
        """Frames of switch / sensor addresses are never lights.

        Motion and illuminance frames are delivered by the binary_sensor and
        sensor platforms themselves; switches do not listen to the bus, so
        their frames are published from here.
        """
        valid_iface, iface = bus_interface_from_message(message)
        if foreign.is_sensor_frame(message):
            if valid_iface:
                sensor_addr = Address(address.where, iface)
                foreign.mark_sensor(sensor_addr)
                if not foreign.is_configured_light(sensor_addr):
                    known.discard(sensor_addr.key)
                    known.discard(address.key)
            return True
        if not valid_iface:
            return True
        effective_addr = Address(address.where, iface) if iface != address.interface else address
        if (
            foreign.is_sensor_only(effective_addr, effective_addr.key)
            and effective_addr.key not in hinted
            and getattr(message, "is_on", None) is not None
        ):
            # A sensor that switches no load of its own (Legrand 048834 in scenario mode,
            # #244) and a detector driving the actuator at its own address (067226 + F411,
            # #777) look the same here; only the user knows which it is.
            hinted.add(effective_addr.key)
            LOGGER.info(
                "WHO 1 address %s reports motion or illuminance, so its on/off frames do not create a"
                " light. If a light actuator shares this address, declare it under light: in myhome.yaml",
                effective_addr.key,
            )
        if foreign.blocks_light(effective_addr, effective_addr.key):
            keys: tuple[str, ...] = (
                tuple(
                    dict.fromkeys(
                        k
                        for k in (
                            effective_addr.key,
                            effective_addr.clean_key,
                            address.key,
                            address.clean_key,
                        )
                        if k
                    )
                )
                if effective_addr.interface and not foreign.is_switch(effective_addr, effective_addr.key)
                else (effective_addr.key, effective_addr.where, normalize_where(effective_addr.where))
            )
            runtime.router.publish("1", keys, message)
            return True
        return False

    PlatformDiscovery(
        hass,
        config_entry,
        async_add_entities,
        platform=PLATFORM,
        who="1",
        event_type=OWNLightingEvent,
        build=build,
        announce=True,
        reject_registry_entry=ghost,
        accept=accept,
        pre_message=route_foreign,
    ).start()

    async_register_turn_on_timed_service(hass)


class _ForeignAddresses:
    """WHO=1 addresses that belong to the switch, cover, lock, or sensor platforms, not to a light."""

    SENSOR_MESSAGE_TYPES = (
        "motion_detected",
        "illuminance_value",
        "pir_sensitivity",
        "motion_timeout",
    )

    def __init__(
        self, hass: HomeAssistant, config_entry: MyHOMEConfigEntry, gateway_mac: str, entry_mac: str
    ) -> None:
        runtime = config_entry.runtime_data
        self.switches: set[str] = set()
        self.covers: set[str] = set()
        self.sensors: set[str] = set()
        #: Lights declared in myhome.yaml: they keep their address even when a
        #: WHO 1 sensor reports on it (#777).
        self.lights: set[str] = set()

        for dev_id, cfg in runtime.platforms.get("light", {}).items():
            address = Address.from_config(dev_id, cfg)
            if address.where.startswith("#"):
                continue
            self.lights.update(self._spellings(address))

        for dev_id, cfg in runtime.platforms.get("switch", {}).items():
            if str(cfg.get(CONF_WHO, "1")) != "1":
                continue
            address = Address.from_config(dev_id, cfg)
            self.switches.update(
                {str(dev_id), address.where, address.clean_key, address.clean_where}
            )
        for platform, default_who in (("cover", "2"), ("lock", "6")):
            for dev_id, cfg in runtime.platforms.get(platform, {}).items():
                if str(cfg.get(CONF_WHO, default_who)) != "1":
                    continue
                address = Address.from_config(dev_id, cfg)
                self._add_cover(str(dev_id), address)
        for platform, default_who in (("binary_sensor", "25"), ("sensor", "1")):
            for dev_id, cfg in runtime.platforms.get(platform, {}).items():
                if str(cfg.get(CONF_WHO, default_who)) != "1":
                    continue
                address = Address.from_config(dev_id, cfg)
                self._add_sensor(address, str(dev_id))

        try:
            registry = er.async_get(hass)
            entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
        except Exception:
            entries = []
        for entry in entries:
            uid = entry.unique_id or ""
            who, device_id = parse_unique_id(uid, gateway_mac, entry_mac)
            if entry.domain == "switch" and who in (None, "1"):
                self.switches.update({device_id, device_id.split("#4#")[0].split("-")[-1]})
            elif entry.domain == "lock" and who == "1":
                # WHO=1 impulse covers are YAML-only (already added from runtime.platforms["cover"]
                # above); any WHO=1 cover registry row absent from YAML is stale and pruned
                # concurrently by cover.py, so it must never block a configured or discovered light.
                self._add_cover(device_id, Address.from_device_id(device_id))
            elif entry.domain in ("binary_sensor", "sensor"):
                after_mac = uid.replace(f"{gateway_mac}-", "", 1).replace(
                    f"{entry_mac}-", "", 1
                )
                if who == "1":
                    dev = device_id.removesuffix("-motion").removesuffix("-illuminance")
                elif (
                    ("-motion" in uid or "-illuminance" in uid)
                    and who not in ("4", "9", "18", "25")
                ):
                    dev = after_mac.removesuffix("-motion").removesuffix("-illuminance")
                else:
                    continue
                self._add_sensor(Address.from_device_id(dev), dev)

    def _add_cover(self, dev_id: str, address: Address) -> None:
        if address.interface:
            self.covers.update({dev_id, address.key, address.clean_key})
        else:
            self.covers.update(
                {dev_id, address.key, address.clean_key, address.where, address.clean_where}
            )

    def _add_sensor(self, address: Address, dev_id: str | None = None) -> None:
        self.sensors.update(self._spellings(address))
        if dev_id and (not address.interface or BUS_ROUTING in dev_id):
            self.sensors.add(dev_id)

    def mark_sensor(self, address: Address) -> None:
        self._add_sensor(address)

    @staticmethod
    def _spellings(address: Address) -> set[str]:
        """Every spelling of the address, bus interface included: a light at 52 behind
        interface 01 does not vouch for a sensor at 52 on another bus."""
        iface = (
            address.interface.zfill(2)
            if address.interface
            and address.interface.isascii()
            and address.interface.isdigit()
            and 1 <= len(address.interface) <= 2
            and int(address.interface) <= 15
            else address.interface
        )
        routings = (
            {f"{BUS_ROUTING}{iface}", f"{BUS_ROUTING}{address.interface}"}
            if address.interface
            else {""}
        )
        wheres = {
            address.where,
            address.clean_where,
            normalize_where(address.where),
            normalize_where(address.clean_where),
            canonical_point_spelling(address.where),
            canonical_point_spelling(address.clean_where),
        }
        return {f"{where}{routing}" for where in wheres if where for routing in routings}

    def is_switch(self, address: Address, key: str) -> bool:
        return bool({key, address.where, address.clean_where} & self.switches)

    def is_cover(self, address: Address, key: str) -> bool:
        cover_candidates = (
            {key, address.key, address.clean_key}
            if address.interface
            else {key, address.key, address.clean_key, address.where, address.clean_where}
        )
        return bool(cover_candidates & self.covers)

    def is_sensor(self, address: Address, key: str) -> bool:
        candidates = set(self._spellings(address))
        if not address.interface or BUS_ROUTING in key:
            candidates.add(key)
        return bool(candidates & self.sensors)

    def is_configured_light(self, address: Address) -> bool:
        """Whether myhome.yaml declares a light at this address (and bus interface)."""
        return bool(self._spellings(address) & self.lights)

    def blocks_light(self, address: Address, key: str) -> bool:
        """Whether no light may exist at this address.

        Switch and cover addresses always block. A sensor address blocks only
        lights that nobody declared: a motion detector such as the Legrand 067226
        drives an F411 channel at its own A/PL and reports WHAT 34 / 39 and
        dimensions 5-7 there, so a configured light and the sensor coexist (#777).
        """
        if self.is_switch(address, key) or self.is_cover(address, key):
            return True
        return self.is_sensor(address, key) and not self.is_configured_light(address)

    def is_sensor_only(self, address: Address, key: str) -> bool:
        """A sensor address with no configured light: the one case a user can fix in myhome.yaml."""
        return (
            not self.is_switch(address, key)
            and not self.is_cover(address, key)
            and self.is_sensor(address, key)
            and not self.is_configured_light(address)
        )

    @classmethod
    def is_sensor_frame(cls, message: Any) -> bool:
        """Motion / illuminance / PIR frames are never lights, whatever the address."""
        return (
            getattr(message, "is_sensor", False) is True
            or getattr(message, "motion", False) is True
            or isinstance(getattr(message, "illuminance", None), int)
            or getattr(message, "message_type", None) in cls.SENSOR_MESSAGE_TYPES
            or getattr(message, "dimension", None) in (5, 6, 7)
            or getattr(message, "_state", None) == 34
            or is_motion_end(message)
        )


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    """Unload light platform."""
    return True


_PENDING_CMD_QUEUE_MAX_S = 30.0
_PENDING_UNSENT = object()

type _PendingCmdToken = tuple[int, float, list[Any]]


def _pending_cmd_written(pending: _PendingCmdToken | None) -> bool:
    """Return True once a pending command token has already left the send queue."""
    if pending is None:
        return True
    handle = pending[2][0]
    if handle is _PENDING_UNSENT:
        return False
    return not isinstance(handle, asyncio.Future) or handle.done()


class MyHOMELight(MyHOMEEntity, LightEntity):
    _pending_cmd_brightness_pct: _PendingCmdToken | None = None
    _pending_cmd_color_temp: _PendingCmdToken | None = None
    _combined_ct_fade_task: asyncio.Task[None] | None = None
    _brightness_restored: bool = False

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
        dimmable: bool,
        manufacturer: str | None,
        model: str | None,
        gateway: MyHOMEGatewayHandler,
        color_temp: bool = False,
        rgb: bool = False,
        lock_features: bool = False,
        probe_dimmer_on_add: bool = False,
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
        self._full_where = (
            f"{self._where}#4#{self._interface}" if self._interface is not None else self._where
        )
        self._probe_dimmer_on_add = probe_dimmer_on_add

        # With lock_features the configuration is authoritative: the light
        # supports exactly the modes declared (rgb / color_temp / dimmable) and
        # never learns another one from the bus or from a restored state. This
        # is the answer to DALI gateways that keep replaying an HSV or tunable
        # white value that was once written to a fixture that cannot use it
        # (issue #288).
        self._feature_lock = DaliFeatureLock(
            lock_features=lock_features,
            dimmable=dimmable,
            color_temp=color_temp,
            rgb=rgb,
        )
        self._fade_engine = SoftwareFadeEngine(
            where=self._where,
            create_task_cb=lambda coro: self.hass.async_create_task(coro),
            send_instant_cb=lambda pct: self._set_brightness_instant(pct),
            apply_state_cb=lambda pct, is_on: self._apply_brightness_state(pct, is_on),
            update_ha_state_cb=lambda: self.async_schedule_update_ha_state(),
            get_worker_count_cb=lambda: self._get_worker_count_config(),
            get_transition_mode_cb=lambda: self._get_transition_mode_config(),
            is_on_cb=lambda: bool(self._attr_is_on),
        )
        self._lock_features = self._feature_lock.lock_features
        self._allowed_color_modes: set[ColorMode] = self._feature_lock.allowed_color_modes

        self._attr_supported_features = LightEntityFeature(0)
        self._attr_supported_color_modes: set[ColorMode] = set()

        modes, color_mode = _color_modes_from_flags(dimmable, color_temp, rgb, False)
        self._attr_supported_color_modes = modes
        self._attr_color_mode = color_mode

        if modes == {ColorMode.ONOFF}:
            # Plain on/off light: flash is the only extra it can do.
            self._attr_supported_features |= LightEntityFeature.FLASH
        else:
            self._attr_supported_features |= LightEntityFeature.TRANSITION

        self._attr_min_color_temp_kelvin = 2000
        self._attr_max_color_temp_kelvin = 6535
        self._attr_color_temp_kelvin: int | None = None
        self._attr_color_temp: int | None = None  # mireds, what the bus speaks (dimension 14)
        self._attr_hs_color: tuple[float, float] | None = None
        self._attr_rgb_color: tuple[int, int, int] | None = None

        self._attr_extra_state_attributes: dict[str, Any] = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface
        if self._actuator_locked is not None:
            self._attr_extra_state_attributes["actuator_locked"] = self._actuator_locked

        self._on_icon = icon_on
        self._off_icon = icon

        if self._off_icon is not None:
            self._attr_icon = self._off_icon

        self._attr_is_on = None
        # True while is_on is only what Home Assistant restored at startup: not
        # worth keeping against a fault report (e.g. an actuator reporting an unmapped WHAT).
        self._is_on_restored = False
        self._attr_brightness: int | None = None
        self._attr_brightness_pct: int | None = None

        self._last_brightness_pct: int = 100
        self._pending_cmd_brightness_pct: _PendingCmdToken | None = None
        self._pending_cmd_color_temp: _PendingCmdToken | None = None
        self._brightness_restored: bool = False
        self._brightness_from_coarse_preset: bool = False
        # A fine dimmer has reported an exact level on Dimension 1 / 4 with a normal
        # speed; a coarse-only one (speed 240, #767) is still a coarse dimmer even if
        # it reports levels (#851).
        self._exact_level_seen = False
        self._coarse_speed_seen = False
        self._brightness_refresh_after: float = 0.0
        self._brightness_refresh_out = False
        self._brightness_refresh_again = False
        self._brightness_refresh_task: asyncio.Task[bool] | None = None
        self._brightness_refresh_preset: int | None = None
        self._brightness_refresh_next_preset: int | None = None
        self._brightness_refresh_level: int | None = None
        self._brightness_refresh_repeats = 0
        self._exact_level_pct: int | None = None
        self._exact_level_since_refresh = False
        self._last_preset_echo_at: float | None = None
        # What the light's last on / off status frame (``*1*<what>*<where>##``, not a level
        # report) said, and when: it decides what an exact level 0 means (``_level_zero_while_on``).
        self._status_on: bool | None = None
        self._status_on_at: float = 0.0
        self._status_check_after: float = 0.0
        # Confirmed: the light answered a status check with "on"; pending: a check is out, unsettled.
        self._level_zero_on = False
        self._level_zero_pending = False
        self._level_zero_shown_on = False
        self._level_zero_fine = False
        self._level_zero_check_task: asyncio.Task[None] | None = None
        self._level_zero_check_times: list[float] = []
        # The delivery futures of checks given up at their timeout with the request still out: once
        # one is closed, its replies may still arrive and answer nothing (only the check's own).
        self._level_zero_abandoned: list[asyncio.Future[Any]] = []
        # Whether the last "on" status is one that confirms (plain on or lowest preset) and can be
        # the hidden answer of a check (no off status just before it).
        self._status_confirms = False
        # Active software fade task started by a combined CT + brightness call, if any.
        self._combined_ct_fade_task: asyncio.Task[None] | None = None

    @property
    def color_temp(self) -> int | None:
        """Colour temperature in mireds, as carried on the bus (dimension 14).

        Current cores no longer expose LightEntity.color_temp; keep the
        accessor so the mired value stays inspectable alongside the Kelvin one.
        """
        return self._attr_color_temp

    @property
    def _fade_task(self) -> asyncio.Task[None] | None:
        """Return active fade task from engine (compatibility shim)."""
        return self._fade_engine.fade_task

    @_fade_task.setter
    def _fade_task(self, task: asyncio.Task[None] | None) -> None:
        """Set active fade task on engine (compatibility shim)."""
        self._fade_engine.fade_task = task

    @property
    def _fade_id(self) -> int:
        """Return current fade ID from engine (compatibility shim)."""
        return self._fade_engine.fade_id

    @_fade_id.setter
    def _fade_id(self, val: int) -> None:
        """Set current fade ID on engine (compatibility shim)."""
        self._fade_engine.fade_id = val

    @property
    def _cmd_lock(self) -> asyncio.Lock:
        """Return command lock from engine (compatibility shim)."""
        return self._fade_engine.cmd_lock

    @property
    def _warned_multi_worker(self) -> bool:
        """Return whether multi-worker warning was logged (compatibility shim)."""
        return self._fade_engine.warned_multi_worker

    @_warned_multi_worker.setter
    def _warned_multi_worker(self, val: bool) -> None:
        """Set whether multi-worker warning was logged (compatibility shim)."""
        self._fade_engine.warned_multi_worker = val

    def _is_mode_forbidden(self, mode: ColorMode) -> bool:
        """Return whether lock_features keeps this light from adopting ``mode``."""
        return self._feature_lock.is_mode_forbidden(mode)

    def _log_locked_out(self, message: OWNLightingEvent, dimension: str) -> None:
        """Log that an incoming frame is ignored because the mode is locked out."""
        self._feature_lock.log_locked_out(
            self._gateway_handler.log_id,
            self._full_where,
            str(message),
            dimension,
        )

    def _promote_color_mode(self, mode: ColorMode) -> None:
        """Add a color capability learned from the bus without dropping others."""
        new_modes, new_mode, add_feat, rm_feat = self._feature_lock.promote_color_mode(
            self._attr_supported_color_modes, mode
        )
        if new_mode is not None:
            self._attr_supported_color_modes = new_modes
            self._attr_color_mode = new_mode
            self._attr_supported_features |= add_feat
            self._attr_supported_features &= ~rm_feat

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        """Persist ``actuator_locked`` across restarts saved while ``unavailable``."""
        if self._actuator_locked is not None:
            return RestoredExtraData({"actuator_locked": self._actuator_locked})
        return None

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore previous state attributes and color modes."""
        await self._async_restore_actuator_lock(last_state)
        # 1. Restore color modes and features (all of them, not just the "best")
        last_modes = last_state.attributes.get("supported_color_modes") or []
        if (
            ColorMode.HS in last_modes
            or "hs" in last_modes
            or ColorMode.RGB in last_modes
            or "rgb" in last_modes
        ) and not self._is_mode_forbidden(ColorMode.HS):
            self._promote_color_mode(ColorMode.HS)
        if (
            ColorMode.COLOR_TEMP in last_modes or "color_temp" in last_modes
        ) and not self._is_mode_forbidden(ColorMode.COLOR_TEMP):
            self._promote_color_mode(ColorMode.COLOR_TEMP)
        if (
            ColorMode.BRIGHTNESS in last_modes or "brightness" in last_modes
        ) and not self._is_mode_forbidden(ColorMode.BRIGHTNESS):
            if not self._attr_supported_color_modes & {ColorMode.HS, ColorMode.COLOR_TEMP}:
                self._promote_color_mode(ColorMode.BRIGHTNESS)
        last_mode = last_state.attributes.get("color_mode")
        if last_mode in self._attr_supported_color_modes:
            self._attr_color_mode = ColorMode(last_mode)

        # 2. Restore brightness
        last_brightness = last_state.attributes.get(ATTR_BRIGHTNESS)
        if isinstance(last_brightness, (int, float)):
            self._attr_brightness = int(last_brightness)
            self._attr_brightness_pct = eight_bits_to_percent(self._attr_brightness)
            self._brightness_restored = True
            if self._attr_brightness_pct > 0:
                self._last_brightness_pct = self._attr_brightness_pct

        # 3. Restore color temperature (Kelvin / mireds)
        last_kelvin = last_state.attributes.get(ATTR_COLOR_TEMP_KELVIN)
        last_mired = last_state.attributes.get(ATTR_COLOR_TEMP)
        if isinstance(last_kelvin, (int, float)):
            self._attr_color_temp_kelvin = int(last_kelvin)
            self._attr_color_temp = color_temperature_kelvin_to_mired(self._attr_color_temp_kelvin)
        elif isinstance(last_mired, (int, float)):
            self._attr_color_temp = int(last_mired)
            self._attr_color_temp_kelvin = color_temperature_mired_to_kelvin(self._attr_color_temp)

        # 4. Restore HS / RGB color
        last_hs = last_state.attributes.get(ATTR_HS_COLOR)
        if isinstance(last_hs, (list, tuple)) and len(last_hs) == 2:
            self._attr_hs_color = (float(last_hs[0]), float(last_hs[1]))
            r, g, b = color_hs_to_RGB(self._attr_hs_color[0], self._attr_hs_color[1])
            self._attr_rgb_color = (r, g, b)
        else:
            last_rgb = last_state.attributes.get(ATTR_RGB_COLOR)
            if isinstance(last_rgb, (list, tuple)) and len(last_rgb) == 3:
                self._attr_rgb_color = (int(last_rgb[0]), int(last_rgb[1]), int(last_rgb[2]))
                self._attr_hs_color = color_RGB_to_hs(*self._attr_rgb_color)

        # 5. Restore power state
        if last_state.state == "on":
            self._attr_is_on = True
            self._is_on_restored = True
        elif last_state.state == "off":
            self._attr_is_on = False
            self._is_on_restored = True

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        if (
            ColorMode.HS in self._attr_supported_color_modes
            or ColorMode.RGB in self._attr_supported_color_modes
        ):
            await self._gateway_handler.send_status_request(
                OWNLightingCommand.get_brightness(self._full_where)
            )
            if hasattr(OWNLightingCommand, "get_hsv_color"):
                await self._gateway_handler.send_status_request(
                    OWNLightingCommand.get_hsv_color(self._full_where)
                )
            elif hasattr(OWNLightingCommand, "get_rgb_color"):  # pragma: no cover
                await self._gateway_handler.send_status_request(
                    OWNLightingCommand.get_rgb_color(self._full_where)
                )
            if ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
                await self._gateway_handler.send_status_request(
                    OWNLightingCommand.get_color_temperature(self._full_where)
                )
        elif ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            await self._gateway_handler.send_status_request(
                OWNLightingCommand.get_brightness(self._full_where)
            )
            await self._gateway_handler.send_status_request(
                OWNLightingCommand.get_color_temperature(self._full_where)
            )
        elif ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            await self._gateway_handler.send_status_request(
                OWNLightingCommand.get_brightness(self._full_where)
            )
        else:
            await self._gateway_handler.send_status_request(
                OWNLightingCommand.status(self._full_where)
            )

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        if self._probe_dimmer_on_add:
            self._probe_dimmer_on_add = False
            target_hass = self.hass or self._hass
            if target_hass is not None:
                probe_task = target_hass.async_create_background_task(
                    self.async_probe_dimmer_capability(),
                    name=f"myhome_{self.entity_id}_probe_dimmer",
                )

                def _cancel_probe() -> None:
                    probe_task.cancel()

                self.async_on_remove(_cancel_probe)

    async def async_probe_dimmer_capability(self) -> None:
        """Query Dimension 1 (*#1*WHERE*1##) after any active sweep to detect ON dimmers (#733)."""
        if self._lock_features or (
            set(self._attr_supported_color_modes or ()) - {ColorMode.ONOFF, ColorMode.UNKNOWN}
        ):
            return
        if (
            hasattr(self._gateway_handler, "wait_for_initial_discovery")
            and (
                getattr(self._gateway_handler, "sweep_active", False) is True
                or getattr(self._gateway_handler, "initial_discovery_pending", False) is True
            )
        ):
            await self._gateway_handler.wait_for_initial_discovery()
            if self._lock_features or (
                set(self._attr_supported_color_modes or ()) - {ColorMode.ONOFF, ColorMode.UNKNOWN}
            ):
                return
        await self._gateway_handler.send_status_request(
            OWNLightingCommand.get_brightness(self._full_where)
        )

    # ── Transition helpers (software stepped dimming) ────────────────────────

    def _get_transition_mode_config(self) -> str:
        if not self._gateway_handler or not self._gateway_handler.config_entry:
            return DEFAULT_TRANSITION_MODE
        raw = str(
            self._gateway_handler.config_entry.options.get(
                CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE
            )
        ).lower()
        if raw == TRANSITION_MODE_AUTO:
            return TRANSITION_MODE_SOFTWARE
        if raw not in (TRANSITION_MODE_SOFTWARE, TRANSITION_MODE_NATIVE):
            return DEFAULT_TRANSITION_MODE
        return raw

    def _get_worker_count_config(self) -> int:
        if not self._gateway_handler or not self._gateway_handler.config_entry:
            return 1
        return int(self._gateway_handler.config_entry.options.get(CONF_WORKER_COUNT, 1))

    def _get_transition_mode(self) -> str:
        return self._fade_engine.get_transition_mode()

    def _should_use_software_stepped(self, transition: float | None) -> bool:
        return self._fade_engine.should_use_software_stepped(transition)

    async def _set_brightness_instant(self, pct: int) -> Any:
        """Send set_brightness with transition=0, or switch_off when pct <= 0."""
        if pct <= 0:
            return await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))
        cmd = OWNLightingCommand.set_brightness(self._full_where, pct, 0)
        send_fade = getattr(self._gateway_handler, "send_fade_step", None)
        if self._fade_engine.is_fading and inspect.iscoroutinefunction(send_fade):
            written = await send_fade(cmd)
            self._supersede_level_zero_check(written)
            return written
        return await self._send_switch_on(cmd)

    async def _maybe_instant_brightness(
        self, start_pct: int, target_pct: int, is_on: bool | None = None
    ) -> bool:
        return await self._fade_engine.maybe_instant_brightness(start_pct, target_pct, is_on=is_on)

    def _apply_brightness_state(self, pct: int, is_on: bool | None = None) -> None:
        pct = max(0, min(100, int(pct)))
        self._attr_brightness_pct = pct
        self._attr_brightness = percent_to_eight_bits(pct)
        if is_on is not None:
            self._attr_is_on = is_on
        else:
            self._attr_is_on = pct > 0
        self._is_on_restored = False
        self._brightness_restored = False
        self._brightness_from_coarse_preset = False
        if pct > 0:
            self._last_brightness_pct = pct

    def _next_fade_id(self) -> int:
        return self._fade_engine.next_fade_id()

    def _cancel_fade_if_active(self) -> None:
        self._fade_engine.cancel_fade_if_active()

    async def _cancel_fade_robustly(self) -> None:
        await self._fade_engine.cancel_fade_robustly()

    def _is_own_point_event(self, message: Any) -> bool:
        """Return True if ``message`` is a point-to-point WHO=1 event for this entity's address."""
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

    def _bus_gateways_for_who1(self) -> tuple[Any, ...]:
        """Return candidate gateways on this bus that may hold WHO=1 sweeps or in-flight polls."""
        gw = self._gateway_handler
        pri_fn = getattr(gw, "_get_primary_gateway", None)
        pri_gw = (
            pri_fn()
            if callable(pri_fn) and getattr(gw, "is_follower", False) is True
            else None
        )
        root = pri_gw if pri_gw is not None else gw
        sec_fn = getattr(root, "_get_secondary_for_who", None)
        stb_fn = getattr(root, "_get_standby_gateway", None)
        return tuple(
            h
            for h in (
                gw,
                pri_gw,
                sec_fn(1) if callable(sec_fn) else None,
                stb_fn() if callable(stb_fn) else None,
            )
            if h is not None
        )

    def _poll_targets_self(self, sent: Any) -> bool:
        """Return True if a WHO=1 status/dimension poll ``sent`` covers this light (point or scope)."""
        if self._is_own_point_event(sent):
            return True
        raw_where = expose_message_where(sent)
        if not raw_where:
            return False
        # Unrouted General '0' addresses the complete Lighting system (main bus + #4#II local buses;
        # WHO_1.pdf §2.3 & F461 DALI capture tests/fixtures/plants/issue_288_f461_dali_dt8).
        if raw_where == "0":
            return True
        base, sep, sent_iface_raw = raw_where.rpartition("#4#")
        own_iface = normalize_iface(self._interface)
        if sep:
            sent_iface = normalize_iface(sent_iface_raw)
            if sent_iface is None or self._interface is None or sent_iface != own_iface:
                return False
            scope_where = base.strip()
        else:
            if self._interface is not None:
                return False
            sent_iface = None
            scope_where = raw_where
        if scope_where == "0":
            return True
        if scope_where in AREA_WHERES:
            own_canon = canonical_point_spelling(normalize_where(self._where) or self._where)
            return is_valid_point_where(own_canon) and point_area(own_canon) == scope_where
        grp = parse_hash_group(scope_where)
        if grp is not None:
            mgr = actuator_locks_of(self._gateway_handler)
            if mgr is not None and entity_key(self) in mgr._resolve_group_keys(grp, sent_iface):
                return True
            grp_key = f"{grp}#4#{sent_iface}" if sent_iface is not None else grp
            own_canon = canonical_point_spelling(normalize_where(self._where) or self._where)
            own_ptp = f"{own_canon}#4#{own_iface}" if own_iface is not None else own_canon
            return any(
                own_ptp
                in (getattr(getattr(h, "_resync_manager", None), "group_members", None) or {}).get(
                    grp_key, ()
                )
                for h in self._bus_gateways_for_who1()
            )
        return False

    def _is_from_poll(self, message: Any, dimension: int) -> bool:
        """Return True when ``message`` is a reply to a status/dimension poll or sweep."""
        if getattr(message, "_from_status_request", False) is True:
            return True
        allowed_dims = (None, 1) if dimension == 1 else (dimension,)
        for h in self._bus_gateways_for_who1():
            if getattr(h, "sweep_active", False) is True:
                return True
            in_flight = getattr(getattr(h, "_command_pool", None), "_in_flight_messages", ())
            for sent in tuple(in_flight):
                if (
                    getattr(sent, "who", None) == 1
                    and (
                        getattr(sent, "is_dimension_request", False) is True
                        or getattr(sent, "is_status_request", False) is True
                    )
                    and getattr(sent, "dimension", None) in allowed_dims
                    and self._poll_targets_self(sent)
                ):
                    return True
        return False

    @callback
    def handle_actuator_lock(self, locked: bool) -> None:
        """Apply WHO=14 lock state, cancelling active fades on lock and polling only if incomplete."""
        lock_state = bool(locked)
        prev_locked = self._actuator_locked
        if lock_state:
            self._fade_engine.cancel_fade_if_active()
            if (
                self._pending_cmd_brightness_pct is None
                and self._combined_ct_fade_task is not None
            ):
                self._restore_combined_ct_brightness_on_abort()
        super().handle_actuator_lock(lock_state)
        if not lock_state and prev_locked is True and not self._bus_state_complete:
            target_hass = getattr(self, "hass", None)
            if target_hass is not None:
                target_hass.async_create_task(
                    self.async_update(),
                    name=f"myhome_light_{self._where}_unlock_sync",
                )

    async def async_will_remove_from_hass(self) -> None:
        await self._cancel_fade_robustly()
        self._brightness_refresh_again = False
        self._brightness_refresh_next_preset = None
        if self._brightness_refresh_task is not None:
            self._brightness_refresh_task.cancel()
        if self._level_zero_check_task is not None:
            self._level_zero_check_task.cancel()
        await super().async_will_remove_from_hass()

    async def _async_fade_to(
        self, start_pct: int, target_pct: int, duration: float, fade_id: int
    ) -> None:
        await self._fade_engine.async_fade_to(start_pct, target_pct, duration, fade_id)

    async def async_turn_on_timed(
        self,
        duration: float | None = None,
        hours: int = 0,
        minutes: int = 0,
        seconds: float = 0,
        brightness: int | None = None,
        brightness_pct: int | None = None,
    ) -> None:
        """Turn on light with a hardware-offloaded bus timer."""
        await self._cancel_fade_robustly()

        target_pct: int | None = None
        if brightness_pct is not None:
            target_pct = brightness_pct
        elif brightness is not None:
            target_pct = eight_bits_to_min_percent(brightness)

        if (
            target_pct is not None
            and target_pct > 0
            and (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.RGB in self._attr_supported_color_modes
            )
        ):
            clamped_pct = max(1, min(100, int(target_pct)))
            await self._send_with_pending_brightness(
                OWNLightingCommand.set_brightness(self._full_where, clamped_pct),
                clamped_pct,
            )

        cmd = build_timed_turn_on_command(
            self._full_where,
            duration=duration,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )
        await self._send_switch_on(cmd)

    async def _send_switch_on(self, message: Any) -> Any:
        """Send a frame that switches the light on; return its delivery future."""
        self._reset_brightness_refresh_guard()
        written = await self._gateway_handler.send(message)
        self._supersede_level_zero_check(written)
        return written

    def _supersede_level_zero_check(self, written: Any) -> None:
        """A switch-on from Home Assistant: once written, the check that is out must not show the light off.

        A frame that is never written (its delivery future cancelled or failed), or one that has
        not even been queued, supersedes nothing: that check still decides.
        """
        task = self._level_zero_check_task
        if task is None:
            return

        def supersede(_: Any = None) -> None:
            if self._level_zero_check_task is not task:
                return
            if isinstance(written, asyncio.Future) and (
                written.cancelled() or written.exception() is not None
            ):
                return
            self._level_zero_pending = False

        if isinstance(written, asyncio.Future) and not written.done():
            written.add_done_callback(supersede)
        else:
            supersede()

    def _restore_combined_ct_brightness_on_abort(
        self, task: asyncio.Task[None] | None = None
    ) -> None:
        """Restore ``_last_brightness_pct`` if a combined CT write turned the light ON without Dim 1."""
        if task is not None and self._combined_ct_fade_task is not task:
            return
        self._combined_ct_fade_task = None
        has_dimmer_mode = bool(
            set(self._attr_supported_color_modes or ())
            - {ColorMode.ONOFF, ColorMode.UNKNOWN}
        )
        if (
            has_dimmer_mode
            and self._attr_is_on is True
            and not self._attr_brightness_pct
            and self._last_brightness_pct
        ):
            self._apply_brightness_state(self._last_brightness_pct, is_on=True)
            self._publish_state()

    async def _send_with_pending_brightness(
        self,
        cmd: Any,
        pct: int,
        *,
        prior_pending: _PendingCmdToken | None = None,
        use_prior_pending: bool = False,
        fallback_last_on_cancel: bool = False,
    ) -> Any:
        """Send ``cmd`` with a bounded pending brightness token, rolling back on send failure."""
        saved = prior_pending if use_prior_pending else self._pending_cmd_brightness_pct
        tok: list[Any] = [_PENDING_UNSENT]
        self._pending_cmd_brightness_pct = (
            pct,
            time.monotonic() + _PENDING_CMD_QUEUE_MAX_S,
            tok,
        )
        try:
            written = await self._send_switch_on(cmd)
        except (Exception, asyncio.CancelledError):
            if (
                self._pending_cmd_brightness_pct is not None
                and self._pending_cmd_brightness_pct[2] is tok
            ):
                self._pending_cmd_brightness_pct = saved
            if fallback_last_on_cancel:
                self._restore_combined_ct_brightness_on_abort()
            raise
        self._anchor_pending_cmd(
            "_pending_cmd_brightness_pct",
            pct,
            tok,
            written,
            fallback_last_on_cancel=fallback_last_on_cancel,
        )
        return written

    def _anchor_pending_cmd(
        self,
        attr_name: str,
        value: int,
        token: list[Any],
        written: Any,
        *,
        echo_window: float = ECHO_CONFIRM_DELAY_S,
        fallback_last_on_cancel: bool = False,
    ) -> None:
        """Anchor a pending parameterized write on socket delivery and clear it on NACK/cancel."""
        token[0] = written

        def _on_written(_: Any = None) -> None:
            failed = isinstance(written, asyncio.Future) and (
                written.cancelled() or written.exception() is not None
            )
            cur: _PendingCmdToken | None = getattr(self, attr_name)
            if cur is None or cur[2] is not token:
                return
            if failed:
                setattr(self, attr_name, None)
                if fallback_last_on_cancel:
                    self._restore_combined_ct_brightness_on_abort()
                return
            write_ts = written.result() if isinstance(written, asyncio.Future) else None
            anchor = (
                float(write_ts)
                if isinstance(write_ts, (int, float)) and not isinstance(write_ts, bool)
                else time.monotonic()
            )
            setattr(self, attr_name, (value, anchor + echo_window, token))

        if isinstance(written, asyncio.Future) and not written.done():
            written.add_done_callback(_on_written)
        else:
            _on_written()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        await self._async_turn_on(**kwargs)

    async def _async_turn_on(self, **kwargs: Any) -> None:
        """Send the switch-on Home Assistant asked for."""
        self._reset_brightness_refresh_guard()
        if ATTR_FLASH in kwargs and self._attr_supported_features & LightEntityFeature.FLASH:
            if kwargs[ATTR_FLASH] == FLASH_SHORT:
                await self._send_switch_on(OWNLightingCommand.flash(self._full_where, 0.5))
                return
            elif kwargs[ATTR_FLASH] == FLASH_LONG:
                await self._send_switch_on(OWNLightingCommand.flash(self._full_where, 1.5))
                return

        # HS / HSV color control (DALI F429)
        if (ATTR_HS_COLOR in kwargs or ATTR_RGB_COLOR in kwargs) and (
            ColorMode.HS in self._attr_supported_color_modes
            or ColorMode.RGB in self._attr_supported_color_modes
        ):
            if ATTR_HS_COLOR in kwargs:
                h, s = kwargs[ATTR_HS_COLOR]
                r, g, b = color_hs_to_RGB(h, s)
            else:
                r, g, b = kwargs[ATTR_RGB_COLOR]
                h, s = color_RGB_to_hs(r, g, b)

            # Determine Value (brightness 0-100%)
            pending = self._pending_cmd_brightness_pct
            if ATTR_BRIGHTNESS in kwargs:
                v = eight_bits_to_min_percent(kwargs[ATTR_BRIGHTNESS])
            elif ATTR_BRIGHTNESS_PCT in kwargs:
                v = kwargs[ATTR_BRIGHTNESS_PCT]
            elif pending is not None and time.monotonic() < pending[1]:
                v = pending[0]
            elif self._attr_brightness_pct is not None and self._attr_brightness_pct > 0:
                v = self._attr_brightness_pct
            elif self._last_brightness_pct:
                v = self._last_brightness_pct
            else:
                v = 100

            h_int = max(0, min(359, int(round(h))))
            s_int = max(0, min(100, int(round(s))))
            v_int = max(0, min(100, int(round(v))))

            if hasattr(OWNLightingCommand, "set_hsv_color"):
                cmd = OWNLightingCommand.set_hsv_color(self._full_where, h_int, s_int, v_int)
            else:  # pragma: no cover
                cmd = OWNLightingCommand.set_rgb_color(self._full_where, int(r), int(g), int(b))
            if v_int > 0:
                await self._send_with_pending_brightness(cmd, v_int)
            else:
                await self._send_switch_on(cmd)
            return

        has_brightness_kwarg = ATTR_BRIGHTNESS in kwargs or ATTR_BRIGHTNESS_PCT in kwargs
        combined_ct_brightness = False
        prev_pending_br = self._pending_cmd_brightness_pct
        tok_ct_br: list[Any] | None = None

        def _rollback_ct_br() -> None:
            if (
                tok_ct_br is not None
                and self._pending_cmd_brightness_pct is not None
                and self._pending_cmd_brightness_pct[2] is tok_ct_br
            ):
                self._pending_cmd_brightness_pct = prev_pending_br

        # Color temperature control (DALI Tunable White)
        if (
            ATTR_COLOR_TEMP_KELVIN in kwargs or ATTR_COLOR_TEMP in kwargs
        ) and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                target_kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
                target_mireds = color_temperature_kelvin_to_mired(target_kelvin)
            else:
                target_mireds = int(kwargs[ATTR_COLOR_TEMP])

            prev_pending_ct = self._pending_cmd_color_temp
            token: list[Any] = [_PENDING_UNSENT]
            if target_mireds > 1:
                self._pending_cmd_color_temp = (
                    target_mireds,
                    time.monotonic() + _PENDING_CMD_QUEUE_MAX_S,
                    token,
                )
            if has_brightness_kwarg:
                raw_br = (
                    int(kwargs[ATTR_BRIGHTNESS_PCT])
                    if ATTR_BRIGHTNESS_PCT in kwargs
                    else eight_bits_to_min_percent(int(kwargs[ATTR_BRIGHTNESS]))
                )
                if raw_br > 0:
                    combined_ct_brightness = True
                    tok_ct_br = [_PENDING_UNSENT]
                    self._pending_cmd_brightness_pct = (
                        max(1, min(100, raw_br)),
                        time.monotonic() + _PENDING_CMD_QUEUE_MAX_S,
                        tok_ct_br,
                    )
            try:
                written = await self._send_switch_on(
                    OWNLightingCommand.set_color_temperature(self._full_where, target_mireds)
                )
            except (Exception, asyncio.CancelledError):
                if (
                    self._pending_cmd_color_temp is not None
                    and self._pending_cmd_color_temp[2] is token
                ):
                    self._pending_cmd_color_temp = prev_pending_ct
                _rollback_ct_br()
                raise
            if target_mireds > 1:
                self._anchor_pending_cmd(
                    "_pending_cmd_color_temp",
                    target_mireds,
                    token,
                    written,
                    echo_window=DEFAULT_FIRST_ECHO_TIMEOUT,
                )
            if not has_brightness_kwarg:
                return

        # Original combined condition preserved for compatibility
        if (
            has_brightness_kwarg
            and (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.RGB in self._attr_supported_color_modes
            )
        ) or (
            ATTR_TRANSITION in kwargs
            and self._attr_supported_features & LightEntityFeature.TRANSITION
        ):
            transition = float(kwargs.get(ATTR_TRANSITION, 0.0))

            if has_brightness_kwarg:
                target_pct = (
                    int(kwargs[ATTR_BRIGHTNESS_PCT])
                    if ATTR_BRIGHTNESS_PCT in kwargs
                    else eight_bits_to_percent(int(kwargs[ATTR_BRIGHTNESS]))
                )
                if target_pct == 0 and int(kwargs.get(ATTR_BRIGHTNESS, 0)) > 0:
                    target_pct = 1  # brightness 1..2 of 255 is "on at minimum", not off

                if target_pct == 0:
                    await self.async_turn_off(**kwargs)
                    return
                start_pct = (
                    self._attr_brightness_pct
                    if self._attr_is_on
                    and self._attr_brightness_pct is not None
                    # The minimum shown for an unverified level 0 is not a level the light has:
                    # the requested level is sent.
                    and not (
                        self._attr_brightness_pct == _LEVEL_ZERO_ON_PCT
                        and self._level_zero_shown_on
                        and not self._level_zero_on
                    )
                    else 0
                )

                try:
                    await self._cancel_fade_robustly()
                except (Exception, asyncio.CancelledError):
                    if combined_ct_brightness:
                        _rollback_ct_br()
                        self._restore_combined_ct_brightness_on_abort()
                    raise
                clamped_target = max(1, min(100, int(target_pct)))
                owns_ct_br = (
                    tok_ct_br is not None
                    and self._pending_cmd_brightness_pct is not None
                    and self._pending_cmd_brightness_pct[2] is tok_ct_br
                )

                if self._should_use_software_stepped(transition):
                    if self.actuator_locked is True:
                        await self._send_with_pending_brightness(
                            OWNLightingCommand.set_brightness(self._full_where, clamped_target),
                            clamped_target,
                            prior_pending=prev_pending_br,
                            use_prior_pending=owns_ct_br,
                            fallback_last_on_cancel=combined_ct_brightness,
                        )
                        return
                    try:
                        handled_inst = await self._maybe_instant_brightness(
                            start_pct, clamped_target, is_on=True
                        )
                        if not handled_inst and transition < 0.05:
                            handled_inst = True
                            self._fade_engine.last_instant_written = (
                                await self._fade_engine.set_brightness_instant(clamped_target)
                            )
                    except (Exception, asyncio.CancelledError):
                        if combined_ct_brightness:
                            _rollback_ct_br()
                            self._restore_combined_ct_brightness_on_abort()
                        raise
                    if handled_inst:
                        written_inst = self._fade_engine.last_instant_written
                        if written_inst is None:
                            _rollback_ct_br()
                        else:
                            tok_inst_br: list[Any] = [_PENDING_UNSENT]
                            self._pending_cmd_brightness_pct = (
                                clamped_target,
                                time.monotonic() + _PENDING_CMD_QUEUE_MAX_S,
                                tok_inst_br,
                            )
                            self._anchor_pending_cmd(
                                "_pending_cmd_brightness_pct",
                                clamped_target,
                                tok_inst_br,
                                written_inst,
                                fallback_last_on_cancel=combined_ct_brightness,
                            )
                        return

                    if self._pending_cmd_brightness_pct is None or owns_ct_br:
                        self._pending_cmd_brightness_pct = None
                    fade_task = self._fade_engine.start_fade(start_pct, clamped_target, transition)
                    if combined_ct_brightness:
                        self._combined_ct_fade_task = fade_task
                        fade_task.add_done_callback(self._restore_combined_ct_brightness_on_abort)
                    return

                # native path (echo-confirmed)
                cmd_br = (
                    OWNLightingCommand.set_brightness(
                        self._full_where, clamped_target, int(transition)
                    )
                    if ATTR_TRANSITION in kwargs
                    else OWNLightingCommand.set_brightness(self._full_where, clamped_target)
                )
                await self._send_with_pending_brightness(
                    cmd_br,
                    clamped_target,
                    prior_pending=prev_pending_br,
                    use_prior_pending=owns_ct_br,
                    fallback_last_on_cancel=combined_ct_brightness,
                )
                return
            else:
                # transition-only (no brightness kwarg)
                target_pct = self._last_brightness_pct or 100
                start_pct = (
                    self._attr_brightness_pct
                    if self._attr_is_on and self._attr_brightness_pct is not None
                    else 0
                )

                await self._cancel_fade_robustly()

                if self._should_use_software_stepped(transition):
                    if self.actuator_locked is True:
                        await self._send_switch_on(
                            OWNLightingCommand.set_brightness(self._full_where, target_pct)
                        )
                        return
                    if await self._maybe_instant_brightness(start_pct, target_pct, is_on=True):
                        return

                    self._fade_engine.start_fade(start_pct, target_pct, transition)
                    return

                # native switch_on with speed (echo-confirmed)
                await self._send_switch_on(
                    OWNLightingCommand.switch_on(self._full_where, int(transition))
                )
                return
        else:
            # plain on path (preserved)
            await self._send_switch_on(OWNLightingCommand.switch_on(self._full_where))
            if self.actuator_locked is not True and (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.RGB in self._attr_supported_color_modes
            ):
                await self.async_update()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        status_on, level_zero_on, pending = (
            self._status_on, self._level_zero_on, self._level_zero_pending
        )
        prev_pending_brightness = self._pending_cmd_brightness_pct
        prev_pending_ct = self._pending_cmd_color_temp
        # An MH200 reports the off that follows as a DIMENSION 1 level 0 only (``*#1*62*1*100*2##``,
        # tests/fixtures/traces/dimmer_on_at_level_0_2026_11_20/review_frames.json:12).
        self._status_on = None
        self._level_zero_on = False
        self._level_zero_pending = False
        self._pending_cmd_brightness_pct = None
        self._pending_cmd_color_temp = None
        self._reset_brightness_refresh_guard()

        try:
            if (
                ATTR_TRANSITION in kwargs
                and self._attr_supported_features & LightEntityFeature.TRANSITION
            ):
                transition = float(kwargs[ATTR_TRANSITION])
                start_pct = (
                    self._attr_brightness_pct
                    if self._attr_is_on and self._attr_brightness_pct is not None
                    else 0
                )

                await self._cancel_fade_robustly()

                if self._should_use_software_stepped(transition):
                    if self.actuator_locked is True:
                        await self._gateway_handler.send(
                            OWNLightingCommand.switch_off(self._full_where)
                        )
                        return
                    if await self._maybe_instant_brightness(start_pct, 0, is_on=False):
                        return
                    self._fade_engine.start_fade(start_pct, 0, transition)
                    return

                # native
                await self._gateway_handler.send(
                    OWNLightingCommand.switch_off(self._full_where, int(transition))
                )
                return

            if ATTR_FLASH in kwargs and self._attr_supported_features & LightEntityFeature.FLASH:
                if kwargs[ATTR_FLASH] == FLASH_SHORT:
                    await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 0.5))
                    return
                elif kwargs[ATTR_FLASH] == FLASH_LONG:
                    await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 1.5))
                    return

            # plain off (preserved)
            await self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))
        except (Exception, asyncio.CancelledError):
            # A switch-off that was never sent ends nothing, unless the light has reported since.
            if self._status_on is None:
                self._status_on = status_on
                self._level_zero_on = level_zero_on
                self._level_zero_pending = pending and self._level_zero_check_task is not None
                if self._pending_cmd_brightness_pct is None:
                    self._pending_cmd_brightness_pct = prev_pending_brightness
                if self._pending_cmd_color_temp is None:
                    self._pending_cmd_color_temp = prev_pending_ct
            raise

    @callback
    def _learn_dimmer_kind(self, message: OWNLightingEvent) -> None:
        """Record whether an exact-level frame came from a fine or a coarse-only dimmer.

        Only a Dimension 1 report of a lit dimmer counts: its speed slot is where a
        coarse-only unit puts the 240 sentinel (#767), while Dimension 4 carries a
        different trailing value and an off report says nothing about the kind.
        """
        if getattr(message, "dimension", None) != 1 or not message.brightness:
            return
        speed = getattr(message, "transition", None)
        if speed == _COARSE_ONLY_SPEED or getattr(message, "is_coarse_dimmer", None) is True:
            self._coarse_speed_seen = True
        else:
            self._exact_level_seen = True

    @staticmethod
    def _coarse_preset_pct(message: Any, preset: int) -> int:
        """Map a coarse WHAT 2..10 preset message to its clamped 0..100% level."""
        preset_pct = getattr(message, "brightness_preset_percent", None)
        if not isinstance(preset_pct, int) or isinstance(preset_pct, bool):
            preset_pct = COARSE_PRESET_TO_PERCENT.get(preset, preset * 10)
        return max(0, min(100, preset_pct))

    def _is_fine_dimmer(self, message: OWNLightingEvent) -> bool:
        """True when this light has reported exact levels and nothing marks it coarse-only."""
        return (
            self._exact_level_seen
            and not self._coarse_speed_seen
            and getattr(message, "is_coarse_dimmer", None) is not True
        )

    def _reset_brightness_refresh_guard(self) -> None:
        """Forget the preset and level of the last refreshes: the next echo may ask again."""
        self._brightness_refresh_preset = None
        self._brightness_refresh_next_preset = None
        self._brightness_refresh_level = None
        self._brightness_refresh_repeats = 0
        self._brightness_refresh_again = False
        self._last_preset_echo_at = None

    def _request_brightness_refresh(self, force: bool = False) -> bool:
        """Ask Dimension 1 after a preset echo on a fine dimmer, at most once a second (#851).

        Returns whether a read was asked; only one read is out at a time.
        """
        hass = getattr(self, "hass", None)
        if hass is None:
            return False
        now = time.monotonic()
        if not force and now < self._brightness_refresh_after:
            return False
        if self._brightness_refresh_out:
            return False
        self._brightness_refresh_after = now + _BRIGHTNESS_REFRESH_WINDOW_S
        self._brightness_refresh_out = True
        self._exact_level_since_refresh = False
        task = hass.async_create_task(
            self._async_brightness_refresh(),
            name=f"myhome_light_{self._where}_brightness_refresh",
        )
        if not task.done():
            self._brightness_refresh_task = task
        elif isinstance(task, asyncio.Task) and (task.cancelled() or task.result() is False):
            return False
        return True

    async def _async_brightness_refresh(self) -> bool:
        """Send the Dimension 1 read and wait until it closes; ask once more if the preset moved meanwhile."""
        aborted = False
        try:
            async with asyncio.timeout(_BRIGHTNESS_REFRESH_TIMEOUT_S):
                written = await self._gateway_handler.send_status_request(
                    OWNLightingCommand.get_brightness(self._full_where)
                )
                if isinstance(written, asyncio.Future):
                    await asyncio.wait([written])
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            aborted = True
            raise
        except Exception as err:
            aborted = True
            LOGGER.debug(
                "%s light %s: Dimension 1 brightness refresh failed: %r",
                self._gateway_handler.log_id,
                self._full_where,
                err,
            )
        finally:
            if self._brightness_refresh_task is asyncio.current_task():
                self._brightness_refresh_task = None
            self._brightness_refresh_out = False
            self._brightness_refresh_after = time.monotonic() + _BRIGHTNESS_REFRESH_WINDOW_S
            if aborted:
                self._brightness_refresh_again = False
                self._brightness_refresh_next_preset = None
            elif self._brightness_refresh_again:
                self._brightness_refresh_again = False
                next_preset = self._brightness_refresh_next_preset
                self._brightness_refresh_next_preset = None
                if next_preset is not None:
                    self._brightness_refresh_preset = next_preset
                    self._brightness_refresh_level = (
                        self._exact_level_pct
                        if self._exact_level_pct is not None
                        else (self._attr_brightness_pct if self._actuator_locked is True else None)
                    )
                    self._brightness_refresh_repeats = 1
                self._request_brightness_refresh(force=True)
        return not aborted

    def _request_status_check(self, background: bool) -> bool:
        """Ask the light for its on / off status; return whether a check was started.

        One check at a time. A background check (a confirmed dimmer) at most once per
        ``_LEVEL_ZERO_STATUS_CHECK_INTERVAL_S``; any other at most ``_LEVEL_ZERO_CHECK_BURST``
        times in that interval. A check that a status frame has settled while its task still
        runs is followed by a new one, sent when that task has ended.
        """
        hass = getattr(self, "hass", None)
        if hass is None:
            return False
        previous = self._level_zero_check_task
        if previous is not None and self._level_zero_pending:
            return False
        now = time.monotonic()
        self._level_zero_check_times = [
            at for at in self._level_zero_check_times if now - at < _LEVEL_ZERO_STATUS_CHECK_INTERVAL_S
        ]
        if background:
            if now < self._status_check_after:
                return False
        elif len(self._level_zero_check_times) >= _LEVEL_ZERO_CHECK_BURST:
            return False
        self._level_zero_check_times.append(now)
        self._status_check_after = now + _LEVEL_ZERO_STATUS_CHECK_INTERVAL_S
        self._level_zero_pending = True
        self._level_zero_shown_on = False
        task = hass.async_create_task(
            self._async_level_zero_check(previous),
            name=f"myhome_light_{self._where}_status_check",
        )
        if task.done():
            # Started eagerly and failed before its first wait (the send queue is shut down): the
            # check has settled itself and holds no slot; this level 0 cannot be verified.
            if not task.cancelled() and task.exception() is not None:
                LOGGER.debug(
                    "%s light %s: status request not queued: %r",
                    self._gateway_handler.log_id,
                    self._full_where,
                    task.exception(),
                )
            return False
        self._level_zero_check_task = task
        return True

    async def _async_level_zero_check(self, previous: asyncio.Task[None] | None = None) -> None:
        """Send the status request of a level 0 check and wait until the gateway has closed it.

        Then settle the check: a status frame of the light may have settled it meanwhile. A check
        that follows ``previous`` (settled, still running) sends its request when that has ended.
        """
        answered = False
        try:
            if previous is not None:
                await asyncio.wait([previous])
            written: Any = None
            timed_out = False
            confirmed = self._level_zero_on
            # A request given up earlier and closed before this one began has had its replies.
            self._level_zero_abandoned = [f for f in self._level_zero_abandoned if not f.done()]
            try:
                # The bound covers the wait for room in the send queue too; it never cancels the
                # delivery future, which belongs to the command pool.
                async with asyncio.timeout(_LEVEL_ZERO_CHECK_TIMEOUT_S):
                    written = await self._gateway_handler.send_status_request(
                        OWNLightingCommand.status(self._full_where)
                    )
                    if not confirmed:
                        # An "on" status received before the request was queued answers nothing.
                        self._level_zero_on = False
                    if isinstance(written, asyncio.Future):
                        await asyncio.wait([written])
                        self._level_zero_abandoned = [
                            f for f in self._level_zero_abandoned if not f.done()
                        ]
            except TimeoutError:
                timed_out = True
                if written is None and not confirmed:
                    self._level_zero_on = False
                if isinstance(written, asyncio.Future) and not written.done():
                    self._level_zero_abandoned.append(written)
            if isinstance(written, asyncio.Future):
                answered = written.done() and not written.cancelled() and written.exception() is None
            else:
                answered = not timed_out
            if answered:
                await asyncio.sleep(_LEVEL_ZERO_CHECK_GRACE_S)
                if (
                    isinstance(written, asyncio.Future)
                    and self._level_zero_check_task is asyncio.current_task()
                    # Closed with no frame on the command session: the pool dropped no copy.
                    and getattr(written, "_empty_reply", False) is not True
                ):
                    self._hidden_level_zero_answer(written.result())
        except asyncio.CancelledError:
            if self._level_zero_check_task is asyncio.current_task():
                self._level_zero_pending = False
            raise
        finally:
            # A check followed by a newer one leaves the light to that one.
            if self._level_zero_check_task is asyncio.current_task():
                self._level_zero_check_task = None
                self._settle_level_zero(answered)

    @callback
    def _hidden_level_zero_answer(self, written_at: Any) -> None:
        """Confirm a check whose "on" answer the command pool dropped as a copy.

        The pool hands no reply identical to a frame received just before the request was written
        (``_LEVEL_ZERO_HIDDEN_ANSWER_S``): a confirming "on" status that close is taken as the answer.
        """
        if (
            self._level_zero_pending
            and self._status_on is True
            and self._status_confirms
            and isinstance(written_at, (int, float))
            and not isinstance(written_at, bool)
            and written_at - _LEVEL_ZERO_HIDDEN_ANSWER_S <= self._status_on_at <= written_at
        ):
            self._confirm_level_zero()
            self._level_zero_pending = False

    @callback
    def _confirm_level_zero(self) -> None:
        """The light answered a check with "on": it is on at level 0 (``_LEVEL_ZERO_ON_PCT``)."""
        self._level_zero_on = True
        self._status_check_after = time.monotonic() + _LEVEL_ZERO_STATUS_CHECK_INTERVAL_S
        if self._level_zero_fine:
            self._exact_level_seen = True

    @callback
    def _settle_level_zero(self, answered: bool) -> None:
        """End a level 0 check that no status frame of the light has settled: the light is off."""
        if not self._level_zero_pending:
            return
        self._level_zero_pending = False
        if self._level_zero_on and not answered:
            # A confirmed dimmer whose verification could not be made keeps its state.
            return
        shown_on = self._level_zero_on or self._level_zero_shown_on
        self._level_zero_on = False
        self._status_on = None
        if not (shown_on and self._attr_is_on):
            return
        # Off as for an off status: a software fade must not switch it on again.
        self._cancel_fade_if_active()
        self._attr_is_on = False
        self._brightness_restored = False
        self._brightness_from_coarse_preset = False
        self._exact_level_pct = None
        self._reset_brightness_refresh_guard()
        if not self._is_mode_forbidden(ColorMode.BRIGHTNESS):
            # An on / off only light locked against levels keeps no level (#288).
            self._attr_brightness_pct = 0
            self._attr_brightness = 0
        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon
        self._publish_state()

    def _note_on_off_status(self, message: OWNLightingEvent, is_no_load: bool) -> None:
        """Keep what the light itself last said about being on or off.

        Its status frame (``*1*0*<where>##``, ``*1*1*<where>##``, a level ``2..10``) and the no
        load report. A DIMENSION 4 level 0 counts as "off" too: it is what an F418 answers a
        level read with while it is off (F454, F455, F461; the one that is on at level 0
        answers in DIMENSION 1). Other level reports and dim steps say nothing here.
        A plain on (``WHAT 1``) or the lowest preset (``WHAT 2``), with no ``#`` parameter (an
        on at a speed, ``*1*1#0*32##``, is a command), answering a level 0 check confirms "on at
        level 0"; an off, a no load report, a DIMENSION 4 level 0, a level above 0 or another
        "on" ``WHAT`` ends the confirmation.
        """
        if is_no_load:
            self._status_on = False
            self._status_confirms = False
            self._level_zero_on = False
            self._level_zero_pending = False
        elif getattr(message, "dimension", None) is None:
            if message.is_on is None or getattr(message, "what", None) in (30, 31, "30", "31"):
                return
            # An off status this close before an "on": a reply the command pool dropped as a copy
            # may be either of them, so that "on" is not taken as a hidden answer.
            after_off = (
                self._status_on is False
                and time.monotonic() - self._status_on_at <= _LEVEL_ZERO_HIDDEN_ANSWER_S
            )
            self._status_on = message.is_on
            if message.is_on:
                confirms = not getattr(message, "what_param", None) and (
                    getattr(message, "what", None) == 1
                    or getattr(message, "brightness_preset", None) == 2
                )
                # A request given up earlier has just been closed: this "on" may be its reply,
                # which answers nothing; the check that is out still decides.
                late_reply = any(
                    f.done() and not f.cancelled() for f in self._level_zero_abandoned
                )
                answers_check = (
                    self._level_zero_check_task is not None
                    and self._level_zero_pending
                    and not late_reply
                )
                if confirms and answers_check:
                    self._confirm_level_zero()
                elif not confirms:
                    self._level_zero_on = False
                if not late_reply:
                    self._level_zero_pending = False
                self._status_confirms = confirms and not after_off
            else:
                self._status_confirms = False
                self._level_zero_on = False
                self._level_zero_pending = False
        elif getattr(message, "dimension", None) == 4 and message.brightness == 0:
            self._status_on = False
            self._status_confirms = False
            self._level_zero_on = False
            self._level_zero_pending = False
        else:
            if message.brightness:
                self._level_zero_on = False
                self._level_zero_pending = False
            return
        self._status_on_at = time.monotonic()

    def _level_zero_while_on(self, message: OWNLightingEvent) -> bool:
        """Whether a DIMENSION 1 level 0 is shown as a dimmer that is on (at 1 %).

        An F418 that is on can report level 0 (F461, F455); an MH200 reports an off with a
        ramp the same way. After an "on" status the light is asked once: an "on" answer
        confirms (on at every later level 0, verified in the background), anything else is
        off. Until the answer it is on only when the "on" status is at most
        ``_LEVEL_ZERO_STATUS_FRESH_S`` old; a level 0 that cannot be verified is off.
        """
        if getattr(message, "dimension", None) != 1 or message.brightness != 0:
            return False
        self._level_zero_fine = (
            getattr(message, "transition", None) != _COARSE_ONLY_SPEED
            and getattr(message, "is_coarse_dimmer", None) is not True
        )
        if self._status_on is not True:
            return False
        if self._level_zero_on:
            self._request_status_check(background=True)
            return True
        shown_on = time.monotonic() - self._status_on_at <= _LEVEL_ZERO_STATUS_FRESH_S
        if self._level_zero_check_task is not None and self._level_zero_pending:
            # Still unanswered: this level 0 is judged by the end of that check, and shown on
            # only by the age of the "on" status now.
            self._level_zero_shown_on = shown_on
            return shown_on
        # A check that a status frame has settled says nothing about this level 0: a new one.
        if not self._request_status_check(background=False):
            return False
        self._level_zero_shown_on = shown_on
        return shown_on

    def handle_event(self, message: OWNLightingEvent) -> None:
        """Handle an event message.

        During an active software fade we keep optimistic state unless the bus
        reports a significant change (is_on=False or brightness diff >=10pp or 0).
        This prevents wall switches or echoes from ruining the visible fade.
        """
        if getattr(message, "is_translation", None) is True:
            return
        if _ForeignAddresses.is_sensor_frame(message):
            # A detector sharing this address (Legrand 067226 + F411, #777) reports motion,
            # end of movement and dimensions 5-7 here; the binary_sensor and sensor platforms
            # route them to this light's address key too. None of them is the light's state.
            return
        if self._actuator_locked is True and (
            getattr(message, "is_general", False) is True
            or getattr(message, "is_area", False) is True
            or getattr(message, "is_group", False) is True
        ):
            return

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        prev_is_on = self._attr_is_on
        prev_brightness_pct = self._attr_brightness_pct
        prev_color_temp = self._attr_color_temp
        prev_hs_color = self._attr_hs_color
        was_restored = self._is_on_restored
        was_brightness_restored = self._brightness_restored
        was_coarse_preset = self._brightness_from_coarse_preset
        was_fine = self._is_fine_dimmer(message)
        unknown_state = getattr(message, "unknown_state", None)
        is_no_load = unknown_state == 19
        preset = getattr(message, "brightness_preset", None)
        has_preset = isinstance(preset, int) and not isinstance(preset, bool)
        update_bus_state_complete = False
        level_zero_while_on = self._level_zero_while_on(message)
        self._note_on_off_status(message, is_no_load)

        if is_no_load:
            self._pending_cmd_brightness_pct = None
            self._pending_cmd_color_temp = None
            self._attr_is_on = False
            self._is_on_restored = False
            self._brightness_restored = False
            self._brightness_from_coarse_preset = False
            self._exact_level_pct = None
            self._reset_brightness_refresh_guard()
            was_no_load = self._attr_extra_state_attributes.get("no_load", False)
            self._attr_extra_state_attributes["no_load"] = True
            self._attr_extra_state_attributes.pop("unknown_state", None)
            if not was_no_load:
                LOGGER.info(
                    "%s light %s reports no load / open circuit (WHAT 19)",
                    self._gateway_handler.log_id,
                    self._full_where,
                )
            else:
                LOGGER.debug(
                    "%s light %s reports no load / open circuit (WHAT 19)",
                    self._gateway_handler.log_id,
                    self._full_where,
                )
        elif message.is_on is not None and getattr(message, "what", None) not in (30, 31, "30", "31"):
            # A WHAT 30/31 dim step is a relative command, not a state: OWNd 2.0.0b11
            # still reports it as `is_on True`, and a step from 0 % may leave the light off (#367).
            self._attr_is_on = True if level_zero_while_on else message.is_on
            self._is_on_restored = False
            if not self._attr_is_on:
                self._brightness_restored = False
                self._brightness_from_coarse_preset = False
            update_bus_state_complete = True
            # A status "on" without a level (``*1*1*0010##``) left the light shown on at 0 %
            # after a level 0: show the confirmed minimum or the last known level instead.
            if (
                message.is_on
                and message.brightness is None
                and not has_preset
                and getattr(message, "dimension", None) is None
                and (
                    self._attr_brightness_pct == 0
                    # or the minimum shown for a level 0 that was not confirmed
                    or (
                        self._attr_brightness_pct == _LEVEL_ZERO_ON_PCT
                        and self._level_zero_shown_on
                        and not self._level_zero_on
                    )
                )
                and not self._fade_engine.is_fading
            ):
                restored = (
                    _LEVEL_ZERO_ON_PCT
                    if self._level_zero_on
                    else max(_LEVEL_ZERO_ON_PCT, self._last_brightness_pct)
                )
                self._attr_brightness_pct = restored
                self._attr_brightness = percent_to_eight_bits(restored)
                if self._is_fine_dimmer(message):
                    self._request_brightness_refresh()
            if not self._attr_is_on:
                self._exact_level_pct = None
                self._reset_brightness_refresh_guard()
            self._attr_extra_state_attributes.pop("unknown_state", None)
            if message.is_on and self._attr_extra_state_attributes.pop("no_load", None):
                LOGGER.info(
                    "%s light %s load reconnected / recovered",
                    self._gateway_handler.log_id,
                    self._full_where,
                )
        elif isinstance(unknown_state, int):
            # A WHAT outside the WHO 1 table leaves is_on None: keep a state seen on the
            # bus or set from Home Assistant, but not one restored at startup - that may
            # be the "on" a fault left behind before OWNd knew better.
            if self._is_on_restored:
                self._attr_is_on = None
                self._is_on_restored = False
            if self._attr_extra_state_attributes.get("unknown_state") != unknown_state:
                LOGGER.warning(
                    "%s light %s reports unknown lighting WHAT %s; %s",
                    self._gateway_handler.log_id,
                    self._full_where,
                    unknown_state,
                    "its state is unknown"
                    if self._attr_is_on is None
                    else "keeping its last state",
                )
            self._attr_extra_state_attributes["unknown_state"] = unknown_state

        is_fading = self._fade_engine.is_fading

        # Always cancel fade on physical off (pure on/off events, no-load, or brightness=0)
        if is_fading and not self._attr_is_on and (is_no_load or message.is_on is False):
            self._cancel_fade_if_active()

        raw_val = getattr(message, "value", None)
        hs = getattr(message, "hs", None)
        has_hs = is_valid_hs_tuple(hs, raw_val)
        rgb = getattr(message, "rgb", None)
        has_rgb = is_valid_rgb_tuple(rgb, raw_val)
        raw_ct = getattr(message, "color_temp", None)
        has_color_temp = isinstance(raw_ct, int) and not isinstance(raw_ct, bool) and raw_ct > 1
        has_level = message.brightness is not None or has_preset
        pending_ct = self._pending_cmd_color_temp
        pending_br = self._pending_cmd_brightness_pct
        if has_level or has_hs or has_rgb or (
            update_bus_state_complete and _pending_cmd_written(pending_br)
        ):
            self._pending_cmd_brightness_pct = None
        if has_hs or has_rgb or (
            update_bus_state_complete and _pending_cmd_written(pending_ct)
        ):
            self._pending_cmd_color_temp = None
            pending_ct = None

        # A dimension the light is locked out of carries no truth in any of its
        # fields: the gateway is replaying a value once written to an address
        # that cannot use it, so even the HSV "value" is not this light's
        # brightness.  The real level always arrives on Dimension 1.
        if (has_hs or has_rgb) and self._is_mode_forbidden(ColorMode.HS):
            self._log_locked_out(message, "12")
            has_hs = has_rgb = False
        elif has_color_temp and self._is_mode_forbidden(ColorMode.COLOR_TEMP):
            self._log_locked_out(message, "14")
            has_color_temp = False
        elif has_level and self._is_mode_forbidden(ColorMode.BRIGHTNESS):
            self._log_locked_out(message, "1")
            has_level = False

        # Auto-promote to HS when HSV color data is received (Dimension 12)
        if has_hs or has_rgb:
            if ColorMode.HS not in self._attr_supported_color_modes:
                LOGGER.info(
                    "Auto-detected HSV color for light %s, adding HS mode.",
                    self._where,
                )
            self._promote_color_mode(ColorMode.HS)
            if has_hs:
                self._attr_hs_color = (
                    float(cast(int, message.hue)),
                    float(cast(int, message.saturation)),
                )
                if has_rgb:
                    self._attr_rgb_color = cast(
                        "tuple[int, int, int]", tuple(cast("tuple[int, int, int]", message.rgb))
                    )
                else:
                    self._attr_rgb_color = color_hs_to_RGB(*self._attr_hs_color)
            else:
                self._attr_rgb_color = cast(
                    "tuple[int, int, int]", tuple(cast("tuple[int, int, int]", message.rgb))
                )
                self._attr_hs_color = color_RGB_to_hs(*self._attr_rgb_color)

            raw_v = getattr(message, "value", None)
            if isinstance(raw_v, (int, float)) and not isinstance(raw_v, bool):
                val = int(raw_v)
                self._attr_brightness_pct = val
                self._attr_brightness = percent_to_eight_bits(val)
                self._attr_is_on = val > 0
                self._is_on_restored = False
                self._brightness_restored = False
                self._brightness_from_coarse_preset = False
                if val > 0:
                    self._last_brightness_pct = val

        # Auto-promote to tunable white when color temperature data is received
        elif has_color_temp:
            if ColorMode.COLOR_TEMP not in self._attr_supported_color_modes:
                LOGGER.info(
                    "Auto-detected tunable white for light %s, adding COLOR_TEMP mode.",
                    self._where,
                )
            self._promote_color_mode(ColorMode.COLOR_TEMP)
            new_ct = cast(int, message.color_temp)
            self._attr_color_temp = new_ct
            self._attr_color_temp_kelvin = color_temperature_mired_to_kelvin(new_ct)
            any_sweep = any(
                getattr(h, "sweep_active", False) is True
                for h in self._bus_gateways_for_who1()
            )
            confirmed_ct_step = (
                pending_ct is not None
                and time.monotonic() < pending_ct[1]
                and getattr(message, "_from_status_request", False) is not True
                and not any_sweep
                and prev_color_temp is not None
                and new_ct != prev_color_temp
                and abs(new_ct - pending_ct[0]) <= 5
            )
            from_poll = False if confirmed_ct_step else self._is_from_poll(message, 14)
            if not from_poll:
                self._pending_cmd_color_temp = None
            if (
                pending_ct is not None
                and not from_poll
                and time.monotonic() < pending_ct[1]
                and not (
                    prev_color_temp is not None
                    and new_ct == prev_color_temp
                    and (
                        (
                            self._actuator_locked is True
                            and (pending_ct[0] != prev_color_temp or prev_is_on is not True)
                        )
                        or (prev_is_on is not True and abs(pending_ct[0] - prev_color_temp) > 5)
                    )
                )
            ):
                self._attr_is_on = True
                self._is_on_restored = False
                cur_br_pending = self._pending_cmd_brightness_pct
                br_in_flight = cur_br_pending is not None and time.monotonic() < cur_br_pending[1]
                if (
                    not self._attr_brightness_pct
                    and self._last_brightness_pct
                    and not self._fade_engine.is_fading
                    and not br_in_flight
                ):
                    self._apply_brightness_state(self._last_brightness_pct, is_on=True)

        # Auto-promote to dimmable when brightness data is received (always) or dimmer reports no load
        elif (has_level or is_no_load) and not self._is_mode_forbidden(ColorMode.BRIGHTNESS):
            if (
                ColorMode.BRIGHTNESS not in self._attr_supported_color_modes
                and ColorMode.COLOR_TEMP not in self._attr_supported_color_modes
                and ColorMode.HS not in self._attr_supported_color_modes
                and ColorMode.RGB not in self._attr_supported_color_modes
            ):
                LOGGER.info(
                    "Auto-detected dimmer for light %s, upgrading to BRIGHTNESS mode.",
                    self._where,
                )
                self._promote_color_mode(ColorMode.BRIGHTNESS)

        # Evaluate after capability auto-promotion so an ONOFF entity promoted to
        # BRIGHTNESS by a coarse WHAT 2..10 preset still polls Dimension 1 (#466, #612).
        # An "off" frame is complete for a dimmer too: there is no brightness to ask for.
        # A coarse WHAT 2..10 frame is NOT complete even if OWNd >= 2.0.0b12 populates
        # `brightness` on a cached coarse dimmer: Dimension 1 still holds exact 1..100%.
        if update_bus_state_complete and (
            not message.is_on
            or not (
                set(self._attr_supported_color_modes or ())
                - {ColorMode.ONOFF, ColorMode.UNKNOWN}
            )
            or (getattr(message, "brightness", None) is not None and not has_preset)
        ):
            self._bus_state_complete = True

        coarse_dim1_refinement = False
        coarse_quantized_start = False
        raw_fade_start = getattr(self._fade_engine, "fade_start_pct", None)
        fade_start = (
            raw_fade_start
            if isinstance(raw_fade_start, int) and not isinstance(raw_fade_start, bool)
            else None
        )
        raw_pending_step = getattr(self._fade_engine, "pending_first_step_pct", None)
        pending_step = (
            raw_pending_step
            if isinstance(raw_pending_step, int) and not isinstance(raw_pending_step, bool)
            else None
        )

        if (
            (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.RGB in self._attr_supported_color_modes
            )
            and has_level
            and message.brightness is not None
            and not has_preset
        ):
            # A dimmer that is on at level 0 is shown at the lowest level, never at 0.
            level = _LEVEL_ZERO_ON_PCT if level_zero_while_on else message.brightness
            from_poll_dim1 = self._is_from_poll(message, 1)
            coarse_base = (
                was_coarse_preset
                and prev_is_on is True
                and not self._coarse_speed_seen
                and getattr(message, "is_coarse_dimmer", None) is not True
            )
            if is_fading:
                coarse_quantized_start = (
                    coarse_base
                    and self._is_own_point_event(message)
                    and fade_start is not None
                    and pending_step is not None
                    and abs(level - fade_start) <= 15
                    and (
                        self._actuator_locked is True
                        or from_poll_dim1
                        or abs(level - pending_step) > 1
                    )
                )
                if coarse_quantized_start:
                    coarse_dim1_refinement = True
                    self._fade_engine.fade_start_pct = level
                    self._apply_brightness_state(level, is_on=True)
                else:
                    # Precise policy during fade: only apply significant physical changes.
                    # Pre-09W29 coarse dimmers (is_coarse_dimmer=True, #767) quantize levels
                    # onto BTicino's coarse ladder where 86% -> WHAT 10 -> 100% spans 14 pp.
                    current_opt = (
                        pending_step
                        if pending_step is not None
                        else (self._attr_brightness_pct or 0)
                    )
                    reported = level
                    cancel_delta = (
                        25 if getattr(message, "is_coarse_dimmer", None) is True else 10
                    )
                    if reported == 0 or abs(reported - current_opt) >= cancel_delta:
                        self._cancel_fade_if_active()
                        self._apply_brightness_state(reported)
                    # else: keep optimistic state
            else:
                coarse_dim1_refinement = (
                    coarse_base
                    and prev_brightness_pct is not None
                    and abs(level - prev_brightness_pct) <= 15
                )
                self._attr_brightness_pct = level
                self._attr_brightness = percent_to_eight_bits(level)
                self._brightness_restored = False
                self._brightness_from_coarse_preset = False
                if message.brightness > 0:
                    self._last_brightness_pct = message.brightness
            if getattr(message, "dimension", None) in (1, 4) and level > 0:
                self._exact_level_pct = level
                self._exact_level_since_refresh = True
            self._learn_dimmer_kind(message)
        elif has_level and has_preset and not is_fading:
            # Coarse WHAT 2..10 presets map to BTicino's non-linear dimmerLevelTo100 curve
            # (2->1%, 3->10%, 4->20%, 5->30%, 6->40%, 7->50%, 8->60%, 9->75%, 10->100%),
            # corroborated on physical F414 and F418U2 dimmers (#466, #767).
            # Ignored while software_stepped fade is active — including under OWNd >= 2.0.0b12
            # where is_coarse_dimmer=True also populates message.brightness on *1*2..10*WHERE##
            # (86% -> WHAT 10 -> 100% has a 14 pp quantization jump that would self-cancel fades).
            mapped = self._coarse_preset_pct(message, cast(int, preset))
            has_dimmer_capability = bool(
                set(self._attr_supported_color_modes or ())
                - {ColorMode.ONOFF, ColorMode.UNKNOWN}
            )
            locked_hold_preset = (
                self._actuator_locked is True
                and prev_is_on is True
                and prev_brightness_pct is not None
                and not self._coarse_speed_seen
                and getattr(message, "is_coarse_dimmer", None) is not True
                and (was_fine or ((was_restored or was_brightness_restored) and has_dimmer_capability))
                and abs(mapped - prev_brightness_pct) <= 15
            )
            if not self._is_fine_dimmer(message) and not locked_hold_preset:
                self._apply_brightness_state(mapped)
                if not self._coarse_speed_seen and getattr(message, "is_coarse_dimmer", None) is not True:
                    self._brightness_from_coarse_preset = True
            else:
                now = time.monotonic()
                quiet = (
                    self._last_preset_echo_at is None
                    or now - self._last_preset_echo_at > _BRIGHTNESS_REFRESH_QUIET_S
                )
                self._last_preset_echo_at = now
                in_window = self._brightness_refresh_out or now < self._brightness_refresh_after
                # A MyHomeServer1 / 3488 answers *#1*WHERE*1## with the level and with the
                # preset frame (capture of 2026-11-21 in tests/test_light_dimension1_refresh_loop.py),
                # so that frame is the answer to this light's read, not a new echo.
                if in_window and preset == self._brightness_refresh_preset:
                    if (
                        not locked_hold_preset
                        and not self._brightness_refresh_again
                        and not (
                            self._exact_level_since_refresh and self._exact_level_pct is not None
                        )
                    ):
                        self._apply_brightness_state(mapped)
                        self._brightness_from_coarse_preset = True
                        self._bus_state_complete = False
                elif self._brightness_refresh_out:
                    if not locked_hold_preset:
                        self._apply_brightness_state(mapped)
                        self._brightness_from_coarse_preset = True
                    self._bus_state_complete = False
                    self._brightness_refresh_again = True
                    self._brightness_refresh_next_preset = cast(int, preset)
                else:
                    # The same answer reaches every instance on the bus, so a read that
                    # brings nothing new is repeated at most _BRIGHTNESS_REFRESH_REPEATS
                    # times.
                    ref_level = (
                        self._exact_level_pct
                        if self._exact_level_pct is not None
                        else (prev_brightness_pct if locked_hold_preset else None)
                    )
                    same = (
                        preset == self._brightness_refresh_preset
                        and ref_level is not None
                        and ref_level == self._brightness_refresh_level
                    )
                    if not (
                        same
                        and not quiet
                        and self._brightness_refresh_repeats >= _BRIGHTNESS_REFRESH_REPEATS
                    ):
                        # On a fine dimmer WHAT 2..10 is a quantised view of the level: an F461
                        # answers *#1*WHERE## with *1*7*WHERE## for anything between about 44 and
                        # 57 %, and the F418's own ladder is 1, 8, 16, 24, 34, 47, 63, 79, 100 %
                        # (#851). The mapped value is shown at once (a wall press did change the
                        # level, #828) and Dimension 1 is asked for the exact one, unless a locked
                        # dimmer is holding its known exact level against a poll reply (#976).
                        if not locked_hold_preset:
                            self._apply_brightness_state(mapped)
                            self._brightness_from_coarse_preset = True
                        self._bus_state_complete = False
                        changed_preset = (
                            self._brightness_refresh_preset is not None
                            and preset != self._brightness_refresh_preset
                        )
                        if self._request_brightness_refresh(force=changed_preset):
                            self._brightness_refresh_repeats = (
                                self._brightness_refresh_repeats + 1 if (same and not quiet) else 1
                            )
                            self._brightness_refresh_preset = cast(int, preset)
                            self._brightness_refresh_level = ref_level

        if self._fade_engine.is_fading and self._is_own_point_event(message):
            fading_down = (
                fade_start is not None
                and pending_step is not None
                and pending_step < fade_start
            )
            step1_confirmed_by_event = False
            if has_level and message.brightness is not None and not has_preset:
                reported_lvl = _LEVEL_ZERO_ON_PCT if level_zero_while_on else message.brightness
                if not coarse_quantized_start:
                    if prev_is_on is not True or fade_start is None:
                        step1_confirmed_by_event = reported_lvl > 0
                    elif fading_down:
                        step1_confirmed_by_event = 0 < reported_lvl < fade_start
                    else:
                        step1_confirmed_by_event = reported_lvl > fade_start
            elif has_level and has_preset:
                clamped_p = self._coarse_preset_pct(message, cast(int, preset))
                progressed = (
                    True
                    if prev_is_on is not True or fade_start is None
                    else (clamped_p < fade_start if fading_down else clamped_p > fade_start)
                )
                if not progressed:
                    if not (
                        self._coarse_speed_seen
                        or getattr(message, "is_coarse_dimmer", None) is True
                    ):
                        self._request_brightness_refresh()
                elif (
                    prev_is_on is not True
                    or fade_start is None
                    or abs(clamped_p - fade_start) > 15
                    or self._coarse_speed_seen
                    or getattr(message, "is_coarse_dimmer", None) is True
                ):
                    step1_confirmed_by_event = True
                else:
                    self._request_brightness_refresh()
            elif update_bus_state_complete:
                step1_confirmed_by_event = prev_is_on is False and message.is_on is True
            if step1_confirmed_by_event:
                self._fade_engine.notify_bus_echo()

        if (
            self._actuator_locked is True
            and not is_no_load
            and not isinstance(unknown_state, int)
            and getattr(message, "what", None) not in (30, 31, "30", "31")
            and self._is_own_point_event(message)
        ):
            is_on_changed = (
                prev_is_on is not None
                and self._attr_is_on is not None
                and self._attr_is_on != prev_is_on
            )
            brightness_changed = (
                not coarse_dim1_refinement
                and prev_brightness_pct is not None
                and self._attr_brightness_pct is not None
                and self._attr_is_on is True
                and self._attr_brightness_pct != prev_brightness_pct
            )
            ct_changed = (
                prev_color_temp is not None
                and self._attr_color_temp is not None
                and self._attr_color_temp != prev_color_temp
            )
            hs_changed = (
                prev_hs_color is not None
                and self._attr_hs_color is not None
                and self._attr_hs_color != prev_hs_color
            )
            if is_on_changed or brightness_changed or ct_changed or hs_changed:
                self._clear_actuator_lock()

        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon

        self._publish_state()
