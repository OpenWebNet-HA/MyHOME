"""Support for MyHome locks (door entry electric strikes WHO=6 and impulse locks WHO=1)."""
from __future__ import annotations

import hmac
from typing import Any

from homeassistant.components.lock import (
    LockEntity,
    LockEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_CODE,
    CONF_MAC,
    CONF_NAME,
    Platform,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from OWNd.message import (
    OWNDoorEntryCommand,
    OWNDoorEntryEvent,
    OWNLightingCommand,
)

from .const import (
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    CONF_PULSE_DURATION,
    CONF_WHO,
    DEFAULT_PULSE_DURATION,
    LOGGER,
)
from .data import get_runtime_data
from .discovery import (
    Address,
    DeviceContext,
    PlatformDiscovery,
    default_known_keys,
    parse_unique_id,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PLATFORM = Platform.LOCK
PARALLEL_UPDATES = 0
DEFAULT_LOCK_DURATION = 5.0


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> bool:
    """Set up the locks of a gateway: registry entries, then myhome.yaml, then bus discovery."""
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    mac = config_entry.data[CONF_MAC]
    gateway = runtime.gateway

    def lock_registry_address(target_who: str):
        def address_of(entry: er.RegistryEntry) -> Address | None:
            who, device_id = parse_unique_id(entry.unique_id or "", gateway.mac, mac)
            entry_who = who if who is not None else "6"
            if entry_who != target_who:
                return None
            return Address.from_device_id(device_id)
        return address_of

    def build_who6(ctx: DeviceContext) -> MyHOMELock:
        cfg = ctx.cfg
        name_val = cfg.get(CONF_NAME)
        name = str(name_val) if name_val else f"Lock {ctx.suffix}"
        raw_entity_name = cfg.get(CONF_ENTITY_NAME)
        entity_name = str(raw_entity_name) if raw_entity_name is not None else None
        manufacturer = str(cfg.get(CONF_MANUFACTURER, "BTicino"))
        model = str(cfg.get(CONF_DEVICE_MODEL, "Door Entry Lock"))
        code = str(cfg[CONF_CODE]) if CONF_CODE in cfg else (str(cfg["code"]) if "code" in cfg else None)
        return MyHOMELock(
            hass=hass,
            name=name,
            entity_name=entity_name,
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            interface=ctx.address.interface,
            code=code,
            pulse_duration=DEFAULT_LOCK_DURATION,
            manufacturer=manufacturer,
            model=model,
            gateway=runtime.gateway,
        )

    def build_who1(ctx: DeviceContext) -> MyHOMELock:
        cfg = ctx.cfg
        name_val = cfg.get(CONF_NAME)
        name = str(name_val) if name_val else f"Impulse Lock {ctx.suffix}"
        raw_entity_name = cfg.get(CONF_ENTITY_NAME)
        entity_name = str(raw_entity_name) if raw_entity_name is not None else None
        manufacturer = str(cfg.get(CONF_MANUFACTURER, "BTicino"))
        model = str(cfg.get(CONF_DEVICE_MODEL, "Impulse Relay Lock"))
        code = str(cfg[CONF_CODE]) if CONF_CODE in cfg else (str(cfg["code"]) if "code" in cfg else None)
        pulse = float(cfg.get(CONF_PULSE_DURATION, cfg.get("pulse_duration", DEFAULT_PULSE_DURATION)))
        return MyHOMELock(
            hass=hass,
            name=name,
            entity_name=entity_name,
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            interface=ctx.address.interface,
            code=code,
            pulse_duration=pulse,
            manufacturer=manufacturer,
            model=model,
            gateway=runtime.gateway,
        )

    def accept_who6(ctx: DeviceContext) -> bool:
        if ctx.address.where in ("4100", "0", ""):
            return False
        if ctx.source == "yaml":
            return str(ctx.cfg.get(CONF_WHO, "6")) == "6"
        if ctx.source == "bus":
            msg = ctx.message
            if not isinstance(msg, OWNDoorEntryEvent) or not msg.is_lock_open:
                return False
        return True

    def accept_who1(ctx: DeviceContext) -> bool:
        if ctx.source == "yaml":
            return str(ctx.cfg.get(CONF_WHO, "6")) == "1"
        return True

    PlatformDiscovery(
        hass,
        config_entry,
        async_add_entities,
        platform=PLATFORM,
        who="6",
        event_type=OWNDoorEntryEvent,
        build=build_who6,
        announce=True,
        registry_address=lock_registry_address("6"),
        accept=accept_who6,
        known_keys=lambda ctx: [*default_known_keys(ctx), ctx.address.where, ctx.address.clean_where],
    ).start(listen=True)

    PlatformDiscovery(
        hass,
        config_entry,
        async_add_entities,
        platform=PLATFORM,
        who="1",
        event_type=None,
        build=build_who1,
        announce=True,
        registry_address=lock_registry_address("1"),
        accept=accept_who1,
        known_keys=lambda ctx: [*default_known_keys(ctx), ctx.address.where, ctx.address.clean_where],
    ).start(listen=False)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload lock platform entries."""
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    configured_locks = runtime.platforms[PLATFORM]
    for lock_key in list(configured_locks.keys()):
        del runtime.platforms[PLATFORM][lock_key]

    return True


class MyHOMELock(MyHOMEEntity, LockEntity):
    """Representation of a MyHOME door entry electric lock strike (WHO=6) or impulse lock (WHO=1)."""

    _poll_on_add = False
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(
        self,
        hass: HomeAssistant | None,
        name: str,
        entity_name: str | None,
        device_id: str,
        who: str,
        where: str,
        interface: str | None,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        code: str | None = None,
        pulse_duration: float = DEFAULT_LOCK_DURATION,
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
        self._code = code
        self._pulse_duration = pulse_duration
        if self._code:
            self._attr_code_format = r"^\d+$"
        self._attr_is_locked = True
        self._relock_unsub: CALLBACK_TYPE | None = None
        self._pulse_off_unsub: CALLBACK_TYPE | None = None

        self._attr_extra_state_attributes = {
            "where": self._where,
            "who": self._who,
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["interface"] = self._interface

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the device (resets momentary pulse)."""
        self._cancel_auto_relock()
        self._attr_is_locked = True
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door strike momentarily."""
        if self._code:
            code = kwargs.get("code")
            if code is None or not hmac.compare_digest(str(code), str(self._code)):
                raise ServiceValidationError(f"Invalid code for {self._display_name}")

        if self._who == "1":
            await self._gateway_handler.send(OWNLightingCommand.switch_on(self._full_where))

            if self._pulse_off_unsub is not None:
                self._pulse_off_unsub()
                self._pulse_off_unsub = None

            @callback
            def _turn_off(_now: Any = None) -> None:
                self._pulse_off_unsub = None
                if self.hass is not None:
                    self.hass.async_create_task(
                        self._gateway_handler.send(OWNLightingCommand.switch_off(self._full_where))
                    )

            if self.hass is not None:
                self._pulse_off_unsub = async_call_later(self.hass, self._pulse_duration, _turn_off)
        else:
            await self._gateway_handler.send(OWNDoorEntryCommand.open_lock(self._full_where))

        self._attr_is_locked = False
        self.async_write_ha_state()
        self._schedule_auto_relock()

    async def async_open(self, **kwargs: Any) -> None:
        """Open (unlatch/release) the door strike."""
        await self.async_unlock(**kwargs)

    @callback
    def handle_event(self, message: Any) -> None:
        """Handle an incoming lock event message."""
        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            getattr(message, "human_readable_log", str(message)),
        )
        if getattr(message, "is_translation", None) is True:
            return
        if self._who == "1":
            is_on = getattr(message, "is_on", False) or getattr(message, "_what", None) == 1
            if is_on:
                self._attr_is_locked = False
                self._publish_state()
                self._schedule_auto_relock()
        else:
            if getattr(message, "is_lock_open", False):
                self._attr_is_locked = False
                self._publish_state()
                self._schedule_auto_relock()

    @callback
    def _schedule_auto_relock(self) -> None:
        self._cancel_auto_relock()
        if self.hass is not None:
            self._relock_unsub = async_call_later(
                self.hass, self._pulse_duration, self._async_auto_relock
            )

    @callback
    def _cancel_auto_relock(self) -> None:
        if self._relock_unsub is not None:
            self._relock_unsub()
            self._relock_unsub = None

    @callback
    def _async_auto_relock(self, _now: Any = None) -> None:
        self._relock_unsub = None
        self._attr_is_locked = True
        self._publish_state()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_auto_relock()
        if self._pulse_off_unsub is not None:
            self._pulse_off_unsub()
            self._pulse_off_unsub = None
        await super().async_will_remove_from_hass()
