"""Support for MyHome lights."""

import asyncio
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.light import (
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
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
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

from .const import (
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
    SERVICE_TURN_ON_TIMED,
    TRANSITION_MODE_AUTO,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
    build_timed_turn_on_command,
    eight_bits_to_percent,
    normalize_where,
    percent_to_eight_bits,
)
from .data import MyHOMEConfigEntry
from .discovery import Address, DeviceContext, KnownDevices, PlatformDiscovery, parse_unique_id
from .gateway import MyHOMEGatewayHandler
from .light_dali import DaliFeatureLock
from .light_fade import SoftwareFadeEngine
from .light_group import MyHOMELightGroup, _color_modes_from_flags
from .myhome_device import MyHOMEEntity

PARALLEL_UPDATES = 0

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
        if ctx.source == "bus" and not dimmable and not lock_features:
            # Auto-detect a dimmer from the first frame that carries a level
            dimmable = (
                ctx.message.brightness is not None or ctx.message.brightness_preset is not None
            )
        kwargs = {"lock_features": lock_features}
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
        # A light created in an earlier session for an address that is really a switch or sensor
        return foreign.owns(ctx.address, ctx.key)

    def accept(ctx: DeviceContext) -> bool:
        return not foreign.owns(ctx.address, ctx.key)

    @callback
    def route_foreign(message: Any, address: Address, known: KnownDevices) -> bool:
        """Frames of switch / sensor addresses are never lights.

        Motion and illuminance frames are delivered by the binary_sensor and
        sensor platforms themselves; switches do not listen to the bus, so
        their frames are published from here.
        """
        if foreign.is_sensor_frame(message):
            foreign.mark_sensor(address)
            known.discard(address.key)
            return True
        if foreign.owns(address, address.key):
            runtime.router.publish(
                "1", (address.key, address.where, normalize_where(address.where)), message
            )
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

    platform = entity_platform.current_platform.get()
    if platform is not None:
        platform.async_register_entity_service(
            SERVICE_TURN_ON_TIMED,
            {
                vol.Optional("duration"): vol.Coerce(float),
                vol.Optional("hours", default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=255)
                ),
                vol.Optional("minutes", default=0): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=59)
                ),
                vol.Optional("seconds", default=0): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=59)
                ),
            },
            "async_turn_on_timed",
        )


class _ForeignAddresses:
    """WHO=1 addresses that belong to the switch or sensor platforms, not to a light."""

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
        self.sensors: set[str] = set()

        for dev_id, cfg in runtime.platforms.get("switch", {}).items():
            address = Address.from_config(dev_id, cfg)
            self.switches.update(
                {str(dev_id), address.where, address.clean_key, address.clean_where}
            )
        for platform, default_who in (("binary_sensor", "25"), ("sensor", "1")):
            for dev_id, cfg in runtime.platforms.get(platform, {}).items():
                if str(cfg.get(CONF_WHO, default_who)) != "1":
                    continue
                address = Address.from_config(dev_id, cfg)
                self._add_sensor(str(dev_id), address.where, address.clean_where)

        try:
            registry = er.async_get(hass)
            entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
        except Exception:
            entries = []
        for entry in entries:
            who, device_id = parse_unique_id(entry.unique_id or "", gateway_mac, entry_mac)
            if entry.domain == "switch":
                self.switches.update({device_id, device_id.split("#4#")[0].split("-")[-1]})
            elif entry.domain in ("binary_sensor", "sensor"):
                if who == "1":
                    dev = device_id.split("-")[0]
                elif "-motion" in entry.unique_id or "-illuminance" in entry.unique_id:
                    dev = (
                        entry.unique_id.replace(f"{gateway_mac}-", "", 1).replace(
                            f"{entry_mac}-", "", 1
                        )
                    ).split("-")[0]
                else:
                    continue
                self._add_sensor(dev, dev.split("#4#")[0].split("-")[-1])

    def _add_sensor(self, *wheres: str) -> None:
        for where in wheres:
            self.sensors.update({where, normalize_where(where)})

    def mark_sensor(self, address: Address) -> None:
        self._add_sensor(address.where, address.key, address.clean_where)

    def owns(self, address: Address, key: str) -> bool:
        candidates = {key, address.where, address.clean_where}
        if candidates & self.switches:
            return True
        candidates |= {normalize_where(address.where), normalize_where(address.clean_where)}
        return bool(candidates & self.sensors)

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
        )


async def async_unload_entry(hass: HomeAssistant, config_entry: MyHOMEConfigEntry) -> bool:
    """Unload light platform."""
    return True


class MyHOMELight(MyHOMEEntity, LightEntity):
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

        self._on_icon = icon_on
        self._off_icon = icon

        if self._off_icon is not None:
            self._attr_icon = self._off_icon

        self._attr_is_on = None
        # True while is_on is only what Home Assistant restored at startup: not
        # worth keeping against a fault report (an actuator stuck at WHAT 19).
        self._is_on_restored = False
        self._attr_brightness: int | None = None
        self._attr_brightness_pct: int | None = None

        self._last_brightness_pct: int = 100

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

    async def async_restore_last_state(self, last_state: State) -> None:
        """Restore previous state attributes and color modes."""
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

    async def _set_brightness_instant(self, pct: int) -> None:
        """Send set_brightness with transition=0."""
        await self._gateway_handler.send(
            OWNLightingCommand.set_brightness(self._full_where, pct, 0)
        )

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
        if pct > 0:
            self._last_brightness_pct = pct

    def _next_fade_id(self) -> int:
        return self._fade_engine.next_fade_id()

    def _cancel_fade_if_active(self) -> None:
        self._fade_engine.cancel_fade_if_active()

    async def _cancel_fade_robustly(self) -> None:
        await self._fade_engine.cancel_fade_robustly()

    async def async_will_remove_from_hass(self) -> None:
        await self._cancel_fade_robustly()
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
            target_pct = eight_bits_to_percent(brightness)

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
            await self._gateway_handler.send(
                OWNLightingCommand.set_brightness(self._full_where, target_pct)
            )
            self._apply_brightness_state(target_pct, is_on=True)

        cmd = build_timed_turn_on_command(
            self._full_where,
            duration=duration,
            hours=hours,
            minutes=minutes,
            seconds=seconds,
        )
        await self._gateway_handler.send(cmd)
        self._attr_is_on = True
        self._is_on_restored = False
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""

        if "timer" in kwargs or "duration" in kwargs:
            dur = kwargs.get("timer", kwargs.get("duration"))
            await self.async_turn_on_timed(
                duration=dur,
                hours=kwargs.get("hours", 0),
                minutes=kwargs.get("minutes", 0),
                seconds=kwargs.get("seconds", 0),
                brightness=kwargs.get(ATTR_BRIGHTNESS),
                brightness_pct=kwargs.get(ATTR_BRIGHTNESS_PCT),
            )
            return

        if ATTR_FLASH in kwargs and self._attr_supported_features & LightEntityFeature.FLASH:
            if kwargs[ATTR_FLASH] == FLASH_SHORT:
                await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 0.5))
                return
            elif kwargs[ATTR_FLASH] == FLASH_LONG:
                await self._gateway_handler.send(OWNLightingCommand.flash(self._full_where, 1.5))
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
            if ATTR_BRIGHTNESS in kwargs:
                v = eight_bits_to_percent(kwargs[ATTR_BRIGHTNESS])
            elif ATTR_BRIGHTNESS_PCT in kwargs:
                v = kwargs[ATTR_BRIGHTNESS_PCT]
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
            await self._gateway_handler.send(cmd)
            self._attr_hs_color = (round(float(h), 1), round(float(s), 1))
            self._attr_rgb_color = (int(r), int(g), int(b))
            self._attr_color_mode = ColorMode.HS
            self._apply_brightness_state(v_int, is_on=True)
            self.async_schedule_update_ha_state()
            return

        # Color temperature control (DALI Tunable White)
        if (
            ATTR_COLOR_TEMP_KELVIN in kwargs or ATTR_COLOR_TEMP in kwargs
        ) and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                target_kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
                target_mireds = color_temperature_kelvin_to_mired(target_kelvin)
            else:
                target_mireds = int(kwargs[ATTR_COLOR_TEMP])
                target_kelvin = color_temperature_mired_to_kelvin(target_mireds)

            await self._gateway_handler.send(
                OWNLightingCommand.set_color_temperature(self._full_where, target_mireds)
            )
            self._attr_color_temp = target_mireds
            self._attr_color_temp_kelvin = target_kelvin
            self._attr_color_mode = ColorMode.COLOR_TEMP

            if ATTR_BRIGHTNESS not in kwargs and ATTR_BRIGHTNESS_PCT not in kwargs:
                self._attr_is_on = True
                self._is_on_restored = False
                if self._attr_brightness is None and self._last_brightness_pct:
                    self._apply_brightness_state(self._last_brightness_pct, is_on=True)
                self.async_schedule_update_ha_state()
                return

        # Original combined condition preserved for compatibility
        if (
            (ATTR_BRIGHTNESS in kwargs or ATTR_BRIGHTNESS_PCT in kwargs)
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

            if ATTR_BRIGHTNESS in kwargs or ATTR_BRIGHTNESS_PCT in kwargs:
                target_pct = (
                    int(kwargs[ATTR_BRIGHTNESS_PCT])
                    if ATTR_BRIGHTNESS_PCT in kwargs
                    else eight_bits_to_percent(int(kwargs[ATTR_BRIGHTNESS]))
                )

                if target_pct == 0:
                    await self.async_turn_off(**kwargs)
                    return
                start_pct = (
                    self._attr_brightness_pct
                    if self._attr_is_on and self._attr_brightness_pct is not None
                    else 0
                )

                await self._cancel_fade_robustly()

                if self._should_use_software_stepped(transition):
                    if await self._maybe_instant_brightness(start_pct, target_pct, is_on=True):
                        return

                    self._fade_engine.start_fade(start_pct, target_pct, transition)
                    return

                # native path (exact pre-existing)
                if ATTR_TRANSITION in kwargs:
                    await self._gateway_handler.send(
                        OWNLightingCommand.set_brightness(
                            self._full_where, target_pct, int(transition)
                        )
                    )
                else:
                    await self._gateway_handler.send(
                        OWNLightingCommand.set_brightness(self._full_where, target_pct)
                    )
                if target_pct > 0:
                    self._last_brightness_pct = target_pct
                self._apply_brightness_state(target_pct, is_on=True)
                self.async_schedule_update_ha_state()
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
                    if await self._maybe_instant_brightness(start_pct, target_pct, is_on=True):
                        return

                    self._fade_engine.start_fade(start_pct, target_pct, transition)
                    return

                # native switch_on with speed
                await self._gateway_handler.send(
                    OWNLightingCommand.switch_on(self._full_where, int(transition))
                )
                self._apply_brightness_state(target_pct, is_on=True)
                self.async_schedule_update_ha_state()
                return
        else:
            # plain on path (preserved)
            await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where))
            if (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.RGB in self._attr_supported_color_modes
            ):
                await self.async_update()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""

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

    @callback
    def handle_event(self, message: OWNLightingEvent) -> None:
        """Handle an event message.

        During an active software fade we keep optimistic state unless the bus
        reports a significant change (is_on=False or brightness diff >=10pp or 0).
        This prevents wall switches or echoes from ruining the visible fade.
        """
        if getattr(message, "is_translation", None) is True:
            return

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            message.human_readable_log,
        )
        if message.is_on is not None:
            self._attr_is_on = message.is_on
            self._is_on_restored = False

        # A WHAT outside the WHO 1 table (e.g. 19 from an MH200 actuator with a
        # WHO 1001 fault) leaves is_on None: keep a state seen on the bus or set
        # from Home Assistant, but not one restored at startup - that may be the
        # "on" a fault left behind before OWNd knew better (light 74, #456).
        unknown_state = getattr(message, "unknown_state", None)
        if isinstance(unknown_state, int):
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
        elif message.is_on is not None:
            self._attr_extra_state_attributes.pop("unknown_state", None)

        is_fading = self._fade_engine.is_fading

        # Always cancel fade on physical off (pure on/off events or brightness=0)
        if is_fading and not self._attr_is_on:
            self._cancel_fade_if_active()

        hs = getattr(message, "hs", None)
        has_hs = isinstance(hs, (tuple, list)) and len(hs or ()) == 2
        rgb = getattr(message, "rgb", None)
        has_rgb = isinstance(rgb, (tuple, list)) and len(rgb or ()) == 3
        has_color_temp = isinstance(getattr(message, "color_temp", None), int)
        has_level = message.brightness is not None or message.brightness_preset is not None

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

            if isinstance(getattr(message, "value", None), (int, float)):
                val = int(cast(int, message.value))
                self._attr_brightness_pct = val
                self._attr_brightness = percent_to_eight_bits(val)
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
            self._attr_color_temp = cast(int, message.color_temp)
            self._attr_color_temp_kelvin = color_temperature_mired_to_kelvin(
                cast(int, message.color_temp)
            )

        # Auto-promote to dimmable when brightness data is received (always)
        elif has_level:
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

        if (
            (
                ColorMode.BRIGHTNESS in self._attr_supported_color_modes
                or ColorMode.COLOR_TEMP in self._attr_supported_color_modes
                or ColorMode.HS in self._attr_supported_color_modes
                or ColorMode.RGB in self._attr_supported_color_modes
            )
            and has_level
            and message.brightness is not None
        ):
            if is_fading:
                # Precise policy during fade: only apply significant physical changes
                current_opt = self._attr_brightness_pct or 0
                reported = message.brightness
                if reported == 0 or abs(reported - current_opt) >= 10:
                    self._cancel_fade_if_active()
                    self._apply_brightness_state(reported)
                # else: keep optimistic state
            else:
                self._attr_brightness_pct = message.brightness
                self._attr_brightness = percent_to_eight_bits(message.brightness)
                if message.brightness > 0:
                    self._last_brightness_pct = message.brightness

        if self._off_icon is not None and self._on_icon is not None:
            self._attr_icon = self._on_icon if self._attr_is_on else self._off_icon

        self._publish_state()
