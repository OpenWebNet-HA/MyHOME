"""Support for MyHome locks (door entry electric strikes, WHO=6)."""
from __future__ import annotations

import asyncio
import hmac
import inspect
from collections.abc import Sequence
from typing import Any, NoReturn

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
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
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
    DOMAIN,
    LOGGER,
)
from .data import get_runtime_data
from .discovery import (
    Address,
    DeviceContext,
    PlatformDiscovery,
    parse_unique_id,
)
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

PLATFORM = Platform.LOCK
PARALLEL_UPDATES = 0
DEFAULT_LOCK_DURATION = 5.0
LOCK_WRITE_TIMEOUT = 15.0


def _gateway_supports_who6(gateway: Any) -> bool:
    """Return False only when ``gateway``'s own profile omits WHO=6 support."""
    for attr in ("profile_supports_who", "_profile_supports_who"):
        fn = getattr(gateway, attr, None)
        if callable(fn):
            try:
                res = fn(6)
                return res if isinstance(res, bool) else bool(res)
            except Exception:
                return True
    return True


def _gateway_supports_door_entry(gateway: Any) -> bool:
    """Return False only when the gateway (and its configured shared-bus topology) omits WHO=6 support."""
    delegated = getattr(gateway, "delegated_away_whos", None)
    if isinstance(delegated, (set, frozenset, list, tuple)) and 6 in delegated:
        sec_fn = getattr(gateway, "_get_secondary_for_who", None)
        sec = sec_fn(6) if callable(sec_fn) else None
        if sec is None or _gateway_supports_who6(sec):
            return True
    return _gateway_supports_who6(gateway)


def _effective_send_gateway(gateway: Any) -> Any:
    """Return the gateway handler that actually executed a WHO=6 send at runtime."""
    delegated = getattr(gateway, "delegated_away_whos", None)
    if isinstance(delegated, (set, frozenset, list, tuple)) and 6 in delegated:
        sec_fn = getattr(gateway, "_get_secondary_for_who", None)
        sec = sec_fn(6) if callable(sec_fn) else None
        if (
            sec is not None
            and getattr(sec, "is_connected", True) is not False
            and _gateway_supports_who6(sec)
        ):
            return sec
    if getattr(gateway, "is_connected", True) is False:
        sb_fn = getattr(gateway, "_get_standby_gateway", None)
        standby = sb_fn() if callable(sb_fn) else None
        if (
            standby is not None
            and getattr(standby, "is_connected", False) is True
            and _gateway_supports_who6(standby)
        ):
            return standby
    return gateway


def _gateway_model_label(gateway: Any) -> str:
    """Human-readable gateway model/name for logs and error placeholders."""
    for attr in ("model", "name", "mac"):
        val = getattr(gateway, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return "MyHOME gateway"


def warn_if_locks_unsupported(gateway: Any, entities: Sequence[Any]) -> bool:
    """Warn once when WHO=6 locks are configured on a gateway without WHO=6 support."""
    if not entities or _gateway_supports_door_entry(gateway):
        return False
    if getattr(gateway, "_who6_locks_unsupported_warned", False) is True:
        return False
    wheres = ", ".join(
        sorted(str(getattr(ent, "_full_where", getattr(ent, "_where", "?"))) for ent in entities)
    )
    LOGGER.warning(
        "%s Configured WHO=6 door-entry lock(s) (%s) on gateway %s, whose firmware omits "
        "door-entry support (bt_vct) and does not bridge the 2-wire AV door-entry bus "
        "(e.g. HomeTouch 3488). Unlock commands (*6*10*WHERE##) will be NACKed unless WHO 6 "
        "is delegated to an A/V gateway (F454 / F453AV) on a shared bus or the strike/gate "
        "is driven by an MH-bus relay (WHO=1 impulse cover).",
        gateway.log_id,
        wheres,
        _gateway_model_label(gateway),
    )
    try:
        gateway._who6_locks_unsupported_warned = True
    except Exception:  # pragma: no cover - defensive against frozen stand-ins
        pass
    return True


def _consume_future_exception(fut: asyncio.Future[Any]) -> None:
    """Retrieve any exception on an unawaited write future so the event loop stays clean."""
    if not fut.cancelled():
        fut.exception()


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
        manufacturer = str(cfg.get(CONF_MANUFACTURER) or "BTicino")
        model = str(cfg.get(CONF_DEVICE_MODEL) or "Door Entry Lock")
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
        if ctx.source == "bus" or not ctx.cfg or ctx.address.where in ("4100", "0", ""):
            return False
        return str(ctx.cfg.get(CONF_WHO, "6")) == "6"

    # Locks come only from myhome.yaml and the entity registry, never from bus traffic:
    # a lock release seen on the bus would otherwise add a code-less entity that anyone
    # with dashboard access can unlock.
    entities = PlatformDiscovery(
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
    ).start(listen=True)

    runtime.entities[PLATFORM] = {
        str(getattr(ent, "_full_where", getattr(ent, "_where", "?"))): ent for ent in entities
    }
    warn_if_locks_unsupported(gateway, entities)

    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Unload lock platform entries."""
    runtime = get_runtime_data(config_entry)
    if runtime is None or PLATFORM not in runtime.platforms:
        return True

    runtime.entities.pop(PLATFORM, None)
    try:
        runtime.gateway._who6_locks_unsupported_warned = False  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - defensive against frozen stand-ins
        pass
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

    def _raise_unlock_rejected(
        self,
        frame: str,
        cause: BaseException | None = None,
        *,
        target_gw: Any = None,
    ) -> NoReturn:
        """Raise a translated HomeAssistantError when the gateway NACKs or drops WHO=6 unlock."""
        effective_gw = target_gw if target_gw is not None else _effective_send_gateway(self._gateway_handler)
        gw_label = _gateway_model_label(effective_gw)
        if not _gateway_supports_who6(effective_gw):
            raise HomeAssistantError(
                f"{self._display_name}: gateway {gw_label} rejected WHO=6 door-entry unlock "
                f"(`{frame}`). Automation-only gateways omit door-entry support (bt_vct) and "
                f"do not bridge the 2-wire door-entry AV bus.",
                translation_domain=DOMAIN,
                translation_key="lock_gateway_who6_unsupported",
                translation_placeholders={
                    "name": self._display_name,
                    "gateway": gw_label,
                    "frame": frame,
                },
            ) from cause
        raise HomeAssistantError(
            f"{self._display_name}: gateway {gw_label} rejected or dropped the WHO=6 door-entry "
            f"unlock command (`{frame}`)",
            translation_domain=DOMAIN,
            translation_key="lock_command_rejected",
            translation_placeholders={
                "name": self._display_name,
                "gateway": gw_label,
                "frame": frame,
            },
        ) from cause

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door strike momentarily."""
        if self._code:
            code = kwargs.get("code")
            if code is None or not hmac.compare_digest(str(code), str(self._code)):
                raise ServiceValidationError(f"Invalid code for {self._display_name}")

        cmd = OWNDoorEntryCommand.open_lock(self._full_where)
        frame = str(cmd)
        target_gw = _effective_send_gateway(self._gateway_handler)
        try:
            write_fut: Any = await self._gateway_handler.send(cmd)
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"{self._display_name}: door-entry unlock command failed: {err}",
                translation_domain=DOMAIN,
                translation_key="lock_command_failed",
                translation_placeholders={"name": self._display_name, "error": str(err)},
            ) from err

        if inspect.isawaitable(write_fut):
            try:
                if isinstance(write_fut, asyncio.Future):
                    write_fut.add_done_callback(_consume_future_exception)
                    done, _ = await asyncio.wait((write_fut,), timeout=LOCK_WRITE_TIMEOUT)
                    if not done:
                        raise TimeoutError
                    await write_fut
                else:
                    await asyncio.wait_for(write_fut, LOCK_WRITE_TIMEOUT)
            except TimeoutError as err:
                raise HomeAssistantError(
                    f"{self._display_name}: door-entry unlock command was not delivered to the bus "
                    f"within {LOCK_WRITE_TIMEOUT:.0f} s",
                    translation_domain=DOMAIN,
                    translation_key="lock_command_timeout",
                    translation_placeholders={
                        "name": self._display_name,
                        "timeout": f"{LOCK_WRITE_TIMEOUT:.0f}",
                    },
                ) from err
            except asyncio.CancelledError as err:
                cur_task = asyncio.current_task()
                externally_cancelled = (
                    (cur_task is not None and cur_task.cancelling() > 0)
                    or (isinstance(write_fut, asyncio.Future) and not write_fut.cancelled())
                )
                if externally_cancelled:
                    raise
                self._raise_unlock_rejected(frame, err, target_gw=target_gw)
            except HomeAssistantError:
                raise
            except Exception as err:
                raise HomeAssistantError(
                    f"{self._display_name}: door-entry unlock command failed: {err}",
                    translation_domain=DOMAIN,
                    translation_key="lock_command_failed",
                    translation_placeholders={"name": self._display_name, "error": str(err)},
                ) from err

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
        what_pub = getattr(message, "what", None)
        what = what_pub if isinstance(what_pub, (int, str)) else getattr(message, "_what", None)
        is_lock_open = getattr(message, "is_lock_open", False) or (
            getattr(message, "who", None) == 6 and what in (10, 22)
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
