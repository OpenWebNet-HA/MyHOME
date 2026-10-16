"""Support for MyHome locks (door entry electric strikes, WHO=6)."""
from __future__ import annotations

import hmac
import inspect
from typing import Any

from homeassistant.components.lock import (  # type: ignore[attr-defined, unused-ignore]
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
from OWNd.message import OWNCommand

try:
    from OWNd.message import OWNDoorEntryCommand
except ImportError:  # pragma: no cover - fallback on released OWNd 2.0.0b10
    class OWNDoorEntryCommand:  # type: ignore[no-redef]
        """Fallback stub when running on released OWNd lacking WHO 6 commands."""

        @classmethod
        def open_lock(cls, where: str | int = 0) -> Any:
            frame = f"*6*10*{where}##"
            parsed = OWNCommand.parse(frame)
            return parsed if parsed is not None else OWNCommand(frame)

from .const import (
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    CONF_PULSE_DURATION,
    CONF_WHO,
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
    """Set up the locks of a gateway: registry entries, then myhome.yaml."""
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    mac = config_entry.data[CONF_MAC]
    gateway = runtime.gateway

    def registry_address(entry: er.RegistryEntry) -> Address | None:
        who, device_id = parse_unique_id(entry.unique_id or "", gateway.mac, mac)
        if (who if who is not None else "6") != "6":
            return None
        return Address.from_device_id(device_id)

    def build(ctx: DeviceContext) -> MyHOMELock:
        cfg = ctx.cfg
        name_val = cfg.get(CONF_NAME)
        name = str(name_val) if name_val else f"Lock {ctx.suffix}"
        raw_entity_name = cfg.get(CONF_ENTITY_NAME)
        entity_name = str(raw_entity_name) if raw_entity_name is not None else None
        manufacturer = str(cfg.get(CONF_MANUFACTURER, "BTicino"))
        model = str(cfg.get(CONF_DEVICE_MODEL, "Door Entry Lock"))
        code = str(cfg[CONF_CODE]) if CONF_CODE in cfg else None
        pulse = float(cfg.get(CONF_PULSE_DURATION, DEFAULT_LOCK_DURATION))
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

    def accept(ctx: DeviceContext) -> bool:
        if ctx.address.where in ("4100", "0", ""):
            return False
        if ctx.source == "yaml":
            return str(ctx.cfg.get(CONF_WHO, "6")) == "6"
        return True

    # Locks come only from myhome.yaml and the entity registry, never from bus traffic:
    # a lock release seen on the bus would otherwise add a code-less entity that anyone
    # with dashboard access can unlock.
    PlatformDiscovery(
        hass,
        config_entry,
        async_add_entities,
        platform=PLATFORM,
        who="6",
        event_type=None,
        build=build,
        announce=True,
        registry_address=registry_address,
        accept=accept,
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
    """Representation of a MyHOME door entry electric lock strike (WHO=6)."""

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

        write_fut = await self._gateway_handler.send(
            OWNDoorEntryCommand.open_lock(self._full_where)
        )
        if inspect.isawaitable(write_fut):
            await write_fut

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
        is_lock_open = getattr(message, "is_lock_open", False) or (
            getattr(message, "who", None) == 6 and getattr(message, "_what", None) in (10, 22)
        )
        if is_lock_open:
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
        await super().async_will_remove_from_hass()
