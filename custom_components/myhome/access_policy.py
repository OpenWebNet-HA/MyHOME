"""Human-in-the-loop access policy for impulse gates and garage doors.

A WHO=1 impulse relay wired to a gate or garage motor has no notion of
direction: every pulse advances the motor's own open / stop / close / stop
cycle. Home Assistant is not a safety function (force limitation and photocells
in the motor are), so this module only makes sure Home Assistant never *causes*
a dangerous movement:

* Nothing moves on a service call. ``cover.open_cover`` / ``close_cover`` /
  ``stop_cover`` - from a dashboard, voice assistant, widget or automation -
  only create an approval request for an allowed Home Assistant user.
* The request is approved on that user's own phone through an actionable
  notification with ``authenticationRequired`` (iOS, Android 12+: the phone
  must be unlocked), bound to a single-use 128-bit random nonce, the user, the
  door and the direction. A replayed or forged approval does nothing.
* A pulse is only an *open* when the state sensor proves the door is closed.
  In every other state the same pulse can close the door, so the close policy
  applies: confirmed safety devices, a requester on site (or a camera image in
  the approval), a pre-warning that a wall-button press cancels, and a watchdog
  that latches a fault - never an automatic retry - when the door does not
  reach the expected state.
* Anything unknown fails closed: an unavailable sensor, a missing person
  entity, an unavailable block switch.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from homeassistant.const import (
    STATE_HOME,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import CALLBACK_TYPE, Context, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOWED_USERS,
    CONF_APPROVAL_TIMEOUT,
    CONF_APPROVERS,
    CONF_CAMERA,
    CONF_CLOSE_BLOCK_ENTITY,
    CONF_PREWARN_LIGHT,
    CONF_PREWARN_SECONDS,
    CONF_REMOTE_CLOSE,
    CONF_SAFETY_CHECK_DAYS,
    CONF_SAFETY_DEVICES_VERIFIED,
    CONF_WATCHDOG_MARGIN,
    EVENT_ACCESS_PREWARNING,
    LOGGER,
)

EVENT_NOTIFICATION_ACTION = "mobile_app_notification_action"
ACTION_PREFIX = "MYHOME_ACCESS_"
NOTIFICATION_GROUP = "myhome_access"

DEFAULT_SAFETY_CHECK_DAYS = 31
DEFAULT_PREWARN_SECONDS = 5.0
DEFAULT_WATCHDOG_MARGIN = 10.0
DEFAULT_APPROVAL_TIMEOUT = 60.0
DEFAULT_MAX_FAILED_PINS = 3
DEFAULT_LOCKOUT_SECONDS = 300.0


class Intent(StrEnum):
    """What the requester asked for."""

    OPEN = "open"
    CLOSE = "close"
    STOP = "stop"


class Effect(StrEnum):
    """What a pulse can physically do given the known door state."""

    OPEN = "open"
    MAY_CLOSE = "may_close"


class Phase(StrEnum):
    """Lifecycle of one access operation."""

    IDLE = "idle"
    PENDING = "pending_approval"
    PREWARNING = "prewarning"
    MONITORING = "monitoring"
    FAULT = "fault"


class RemoteClose(StrEnum):
    """When a pulse that can close the door may be sent from Home Assistant."""

    NEVER = "never"
    AT_HOME = "at_home"
    WITH_CAMERA = "with_camera"


class AccessDenied(ServiceValidationError):
    """An access request or approval was refused by the policy."""


@dataclass(frozen=True)
class AccessConfig:
    """Validated access policy of one impulse cover (see ``validate.access_schema``)."""

    allowed_users: tuple[str, ...] = ()
    approvers: Mapping[str, str] = field(default_factory=dict)
    remote_close: RemoteClose = RemoteClose.NEVER
    safety_devices_verified: date | None = None
    safety_check_days: int = DEFAULT_SAFETY_CHECK_DAYS
    camera: str | None = None
    prewarn_light: str | None = None
    prewarn_seconds: float = DEFAULT_PREWARN_SECONDS
    close_block_entity: str | None = None
    watchdog_margin: float = DEFAULT_WATCHDOG_MARGIN
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT
    pin_code: str | None = None
    max_failed_pins: int = DEFAULT_MAX_FAILED_PINS
    lockout_seconds: float = DEFAULT_LOCKOUT_SECONDS

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any] | None, pin_code: str | None = None) -> AccessConfig:
        """Build the policy from the ``access`` block of a cover (``None``: nobody is allowed)."""
        cfg = cfg or {}
        verified = cfg.get(CONF_SAFETY_DEVICES_VERIFIED)
        return cls(
            allowed_users=tuple(str(u) for u in cfg.get(CONF_ALLOWED_USERS, ())),
            approvers={str(k): str(v) for k, v in (cfg.get(CONF_APPROVERS) or {}).items()},
            remote_close=RemoteClose(str(cfg.get(CONF_REMOTE_CLOSE, RemoteClose.NEVER))),
            safety_devices_verified=verified if isinstance(verified, date) else None,
            safety_check_days=int(cfg.get(CONF_SAFETY_CHECK_DAYS, DEFAULT_SAFETY_CHECK_DAYS)),
            camera=cfg.get(CONF_CAMERA),
            prewarn_light=cfg.get(CONF_PREWARN_LIGHT),
            prewarn_seconds=float(cfg.get(CONF_PREWARN_SECONDS, DEFAULT_PREWARN_SECONDS)),
            close_block_entity=cfg.get(CONF_CLOSE_BLOCK_ENTITY),
            watchdog_margin=float(cfg.get(CONF_WATCHDOG_MARGIN, DEFAULT_WATCHDOG_MARGIN)),
            approval_timeout=float(cfg.get(CONF_APPROVAL_TIMEOUT, DEFAULT_APPROVAL_TIMEOUT)),
            pin_code=pin_code,
        )

    def safety_check_due(self) -> date | None:
        """Date the periodic photocell / force test falls due (``None``: never confirmed)."""
        if self.safety_devices_verified is None:
            return None
        return self.safety_devices_verified + timedelta(days=self.safety_check_days)


def classify(intent: Intent, state_sensor: str | None, sensor_state: str | None) -> Effect:
    """Decide what a pulse can do, or refuse it.

    Only a door the sensor reports *closed* is opened by a pulse. Without a
    sensor the direction is unknown, so every pulse can close the door. A
    configured sensor that does not report ``on`` / ``off`` refuses everything.
    """
    if state_sensor is None:
        return Effect.MAY_CLOSE
    if sensor_state not in (STATE_ON, STATE_OFF):
        raise AccessDenied(f"State sensor {state_sensor} is not reporting ({sensor_state}); refusing to pulse")
    if sensor_state == STATE_OFF:
        if intent is Intent.OPEN:
            return Effect.OPEN
        raise AccessDenied("The door is closed; a pulse would open it")
    if intent is Intent.OPEN:
        raise AccessDenied("The door is not closed; a pulse could close it")
    return Effect.MAY_CLOSE


def _same(a: str | None, b: str | None) -> bool:
    """Constant-time comparison of two secrets (``None`` never matches)."""
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass
class PendingRequest:
    """One approval request in flight. The nonce never leaves memory except to the approver's phone."""

    nonce: str
    user_id: str
    intent: Intent
    effect: Effect
    created: float
    notify_service: str
    at_home: bool


class AccessController:
    """Approval, pre-warning and watchdog state machine of one impulse cover."""

    def __init__(
        self,
        hass: HomeAssistant | None,
        *,
        name: str,
        config: AccessConfig,
        state_sensor: str | None,
        travel_time: float,
        send_pulse: Callable[[Intent, Effect], Awaitable[bool]],
        publish: Callable[[], None],
        entity_id: Callable[[], str | None],
    ) -> None:
        self.hass = hass
        self._name = name
        self.config = config
        self._state_sensor = state_sensor
        self._travel_time = travel_time
        self._send_pulse = send_pulse
        self._publish = publish
        self._entity_id = entity_id

        self.phase = Phase.IDLE
        self.fault_reason: str | None = None
        self._pending: PendingRequest | None = None
        self._unsub_action: CALLBACK_TYPE | None = None
        self._unsub_expiry: CALLBACK_TYPE | None = None
        self._unsub_watchdog: CALLBACK_TYPE | None = None
        self._cancel_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._failed_pins: dict[str, tuple[int, float]] = {}

    # ── Public API ───────────────────────────────────────────────────────

    def attributes(self) -> dict[str, Any]:
        """State attributes; never contains a nonce or a PIN."""
        due = self.config.safety_check_due()
        return {
            "access_phase": str(self.phase),
            "remote_close": str(self.config.remote_close),
            "safety_check_due": due.isoformat() if due else None,
            "fault_reason": self.fault_reason,
            "pin_protected": self.config.pin_code is not None,
        }

    @callback
    def _settle(self) -> None:
        """Return to rest: FAULT while a fault is latched, else IDLE."""
        self.phase = Phase.FAULT if self.fault_reason is not None else Phase.IDLE
        self._publish()

    async def async_request(self, intent: Intent, context: Context | None) -> None:
        """Create an approval request for ``intent``; raises :class:`AccessDenied` when refused."""
        hass = self._require_hass()
        user_id = context.user_id if context is not None else None
        if self.phase in (Phase.PENDING, Phase.PREWARNING, Phase.MONITORING):
            raise AccessDenied(f"{self._name}: another operation is in progress ({self.phase})")
        self._check_user(user_id)
        assert user_id is not None  # _check_user refuses None
        self._check_lockout(user_id)
        effect = classify(intent, self._state_sensor, self._sensor_state())
        at_home = self._is_home(user_id)
        if effect is Effect.MAY_CLOSE:
            self._check_close_allowed(at_home)

        pending = PendingRequest(
            nonce=secrets.token_urlsafe(16),
            user_id=user_id,
            intent=intent,
            effect=effect,
            created=time.monotonic(),
            notify_service=self.config.approvers[user_id],
            at_home=at_home,
        )
        self._pending = pending
        self.phase = Phase.PENDING
        self._unsub_action = hass.bus.async_listen(EVENT_NOTIFICATION_ACTION, self._on_action)
        self._unsub_expiry = async_call_later(hass, self.config.approval_timeout, self._on_expired)
        try:
            await self._notify_approval(pending)
        except Exception as err:
            self._clear_pending()
            self._settle()
            raise HomeAssistantError(f"{self._name}: could not send the approval request: {err}") from err
        LOGGER.info("%s: %s requested by user %s, waiting for approval", self._name, intent, user_id)
        self._publish()

    @callback
    def cancel(self, reason: str) -> bool:
        """Cancel a pending request or a running pre-warning. Returns True if something was cancelled."""
        if self.phase is Phase.PENDING and self._pending is not None:
            pending = self._pending
            self._clear_pending()
            self._settle()
            self._spawn(self._notify(pending.notify_service, f"{self._name}: request cancelled ({reason})"))
            return True
        if self.phase is Phase.PREWARNING and self._cancel_event is not None:
            self._cancel_event.set()
            return True
        return False

    async def async_cancel(self, context: Context | None) -> None:
        """Service entry: an allowed user cancels the pending request or pre-warning."""
        user_id = context.user_id if context is not None else None
        if user_id is not None:
            self._check_user(user_id)
        if not self.cancel("cancelled by user"):
            raise AccessDenied(f"{self._name}: nothing to cancel")

    async def async_acknowledge_fault(self, context: Context | None) -> None:
        """Clear a latched fault; only an allowed user who is at home (on site) may do so."""
        user_id = context.user_id if context is not None else None
        self._check_user(user_id)
        assert user_id is not None
        if self.fault_reason is None:
            raise AccessDenied(f"{self._name}: there is no fault to acknowledge")
        if self.phase is not Phase.FAULT:
            raise AccessDenied(f"{self._name}: wait until the current operation has finished")
        if not self._is_home(user_id):
            raise AccessDenied(f"{self._name}: a fault can only be acknowledged on site (person must be home)")
        LOGGER.warning("%s: fault '%s' acknowledged by user %s", self._name, self.fault_reason, user_id)
        self.fault_reason = None
        self._settle()

    async def async_shutdown(self) -> None:
        """Cancel every timer, listener and task (entity removal)."""
        self._clear_pending()
        self._cancel_watchdog()
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # pragma: no cover - teardown only
                pass
        self._task = None

    # ── Policy checks ────────────────────────────────────────────────────

    def _require_hass(self) -> HomeAssistant:
        if self.hass is None:
            raise HomeAssistantError(f"{self._name}: not attached to Home Assistant")
        return self.hass

    def _check_user(self, user_id: str | None) -> None:
        if user_id is None:
            raise AccessDenied(
                f"{self._name}: automations, scripts and integrations may not operate this door; "
                "a person must request it"
            )
        if user_id not in self.config.allowed_users or user_id not in self.config.approvers:
            raise AccessDenied(f"{self._name}: this user is not allowed to operate this door")

    def _check_lockout(self, user_id: str) -> None:
        fails, until = self._failed_pins.get(user_id, (0, 0.0))
        if fails >= self.config.max_failed_pins and time.monotonic() < until:
            raise AccessDenied(f"{self._name}: too many wrong PINs; try again later")

    def _check_close_allowed(self, at_home: bool) -> None:
        cfg = self.config
        if self.phase is Phase.FAULT:
            raise AccessDenied(
                f"{self._name}: the last movement ended in a fault ({self.fault_reason}); acknowledge it on site first"
            )
        if cfg.remote_close is RemoteClose.NEVER:
            raise AccessDenied(f"{self._name}: a pulse here can close the door, and closing from Home Assistant is disabled")
        due = cfg.safety_check_due()
        if due is None:
            raise AccessDenied(f"{self._name}: the door's safety devices (photocells, force limit) are not confirmed")
        if dt_util.now().date() > due:
            raise AccessDenied(f"{self._name}: the periodic safety test was due on {due.isoformat()}")
        if cfg.close_block_entity is not None:
            block = self.hass.states.get(cfg.close_block_entity) if self.hass is not None else None
            if block is None or block.state != STATE_OFF:
                raise AccessDenied(
                    f"{self._name}: closing is blocked by {cfg.close_block_entity} "
                    f"({block.state if block is not None else 'missing'})"
                )
        if not at_home:
            if cfg.remote_close is RemoteClose.AT_HOME:
                raise AccessDenied(f"{self._name}: a pulse that can close the door needs you at home, in sight of the door")
            if cfg.camera is None:
                raise AccessDenied(f"{self._name}: closing from away needs a camera image of the door")

    def _sensor_state(self) -> str | None:
        if self._state_sensor is None or self.hass is None:
            return None
        state = self.hass.states.get(self._state_sensor)
        return state.state if state is not None else None

    def _is_home(self, user_id: str) -> bool:
        if self.hass is None:
            return False
        for person in self.hass.states.async_all("person"):
            if person.attributes.get("user_id") == user_id:
                return person.state == STATE_HOME
        return False

    # ── Approval flow ────────────────────────────────────────────────────

    def _action_ids(self, pending: PendingRequest) -> tuple[str, str]:
        return f"{ACTION_PREFIX}APPROVE_{pending.nonce}", f"{ACTION_PREFIX}DENY_{pending.nonce}"

    def _tag(self) -> str:
        return f"{NOTIFICATION_GROUP}_{self._entity_id() or self._name}"

    def _describe(self, pending: PendingRequest) -> str:
        if pending.effect is Effect.OPEN:
            return "open"
        if self._state_sensor is None:
            return "pulse (direction unknown: it can close)"
        return "stop" if pending.intent is Intent.STOP else "close"

    async def _user_name(self, user_id: str) -> str:
        hass = self._require_hass()
        user = await hass.auth.async_get_user(user_id)
        return user.name if user is not None and user.name else "unknown user"

    async def _notify_approval(self, pending: PendingRequest) -> None:
        approve, deny = self._action_ids(pending)
        what = self._describe(pending)
        closing = pending.effect is Effect.MAY_CLOSE
        approve_action: dict[str, Any] = {
            "action": approve,
            "title": "Area clear - " + what if closing else what.capitalize(),
            "authenticationRequired": True,
            "destructive": closing,
        }
        if self.config.pin_code is not None:
            approve_action.update(
                {"behavior": "textInput", "textInputButtonTitle": approve_action["title"], "textInputPlaceholder": "PIN"}
            )
        message = f"{await self._user_name(pending.user_id)} asks to {what} {self._name}."
        if closing:
            message += " Approve only if the door area is clear."
        message += f" Expires in {int(self.config.approval_timeout)} s."
        data: dict[str, Any] = {
            "actions": [approve_action, {"action": deny, "title": "Cancel"}],
            "tag": self._tag(),
            "group": NOTIFICATION_GROUP,
            "ttl": 0,
            "priority": "high",
            "push": {"interruption-level": "time-sensitive"},
        }
        if self.config.camera is not None:
            data["image"] = f"/api/camera_proxy/{self.config.camera}"
        await self._call_notify(
            pending.notify_service, {"title": f"{self._name}: approve {what}?", "message": message, "data": data}
        )

    async def _call_notify(self, service: str, payload: dict[str, Any]) -> None:
        hass = self._require_hass()
        domain, _, name = service.partition(".")
        await hass.services.async_call(domain, name, payload, blocking=True)

    async def _notify(self, service: str, message: str) -> None:
        """Best-effort status message; a failing notifier never changes the outcome."""
        try:
            await self._call_notify(service, {"title": self._name, "message": message})
        except Exception as err:  # noqa: BLE001 - status messages are best effort
            LOGGER.warning("%s: could not send status notification: %s", self._name, err)

    async def _notify_all(self, message: str) -> None:
        for service in sorted(set(self.config.approvers.values())):
            await self._notify(service, message)

    async def _clear_notification(self, service: str) -> None:
        try:
            await self._call_notify(service, {"message": "clear_notification", "data": {"tag": self._tag()}})
        except Exception as err:  # noqa: BLE001 - clearing is cosmetic
            LOGGER.debug("%s: could not clear notification: %s", self._name, err)

    @callback
    def _on_expired(self, _now: Any = None) -> None:
        self._unsub_expiry = None
        if self.phase is not Phase.PENDING or self._pending is None:
            return
        pending = self._pending
        self._clear_pending()
        self._settle()
        self._spawn(self._clear_notification(pending.notify_service))
        LOGGER.info("%s: approval request expired", self._name)

    @callback
    def _on_action(self, event: Event) -> None:
        pending = self._pending
        action = event.data.get("action")
        if pending is None or not isinstance(action, str) or not action.startswith(ACTION_PREFIX):
            return
        approve, deny = self._action_ids(pending)
        is_approve, is_deny = _same(action, approve), _same(action, deny)
        if not (is_approve or is_deny):
            return  # another door's request, or a stale one
        if event.context.user_id != pending.user_id:
            # mobile_app fires notification actions with the registration's user;
            # an approval from anyone else is ignored and the request stays pending.
            LOGGER.warning("%s: approval from a different user ignored", self._name)
            return
        self._clear_pending()
        if is_deny:
            self._settle()
            LOGGER.info("%s: request denied on the phone", self._name)
            return
        if self.config.pin_code is not None:
            reply = event.data.get("reply_text")
            if reply is None:
                reply = event.data.get("textInput")
            if reply is None:
                reply = event.data.get("reply")
            if reply is None:
                # The app returned no text at all (no input shown, or a client that
                # ignores `behavior: textInput`): nothing was guessed, so it is no failed
                # attempt and cannot lock the approver out. Nothing moves either way.
                self._settle()
                self._spawn(self._notify(pending.notify_service, f"{self._name}: no PIN received, nothing moved"))
                LOGGER.warning("%s: approval carried no PIN text; the notification app did not offer text input", self._name)
                return
            if not _same(str(reply), self.config.pin_code):
                self._register_failed_pin(pending.user_id)
                self._settle()
                self._spawn(self._notify(pending.notify_service, f"{self._name}: wrong PIN, nothing moved"))
                return
            self._failed_pins.pop(pending.user_id, None)
        self._spawn(self._async_execute(pending))

    def _register_failed_pin(self, user_id: str) -> None:
        fails, _ = self._failed_pins.get(user_id, (0, 0.0))
        fails += 1
        self._failed_pins[user_id] = (fails, time.monotonic() + self.config.lockout_seconds)
        LOGGER.warning("%s: wrong PIN from user %s (%d)", self._name, user_id, fails)

    def _clear_pending(self) -> None:
        if self._unsub_action is not None:
            self._unsub_action()
            self._unsub_action = None
        if self._unsub_expiry is not None:
            self._unsub_expiry()
            self._unsub_expiry = None
        self._pending = None

    def _spawn(self, coro: Awaitable[None]) -> None:
        hass = self._require_hass()
        task: asyncio.Task[Any] = hass.async_create_task(coro)  # type: ignore[arg-type]
        if isinstance(task, asyncio.Task):
            self._task = task

    # ── Execution ────────────────────────────────────────────────────────

    def _revalidate(self, pending: PendingRequest) -> None:
        """Every check again on the state of *now*: the door may have moved since the request."""
        if time.monotonic() - pending.created > self.config.approval_timeout:
            raise AccessDenied(f"{self._name}: the approval arrived too late")
        self._check_user(pending.user_id)
        effect = classify(pending.intent, self._state_sensor, self._sensor_state())
        if effect is not pending.effect:
            raise AccessDenied(f"{self._name}: the door state changed since the request")
        if effect is Effect.MAY_CLOSE:
            self._check_close_allowed(self._is_home(pending.user_id))

    async def _async_execute(self, pending: PendingRequest) -> None:
        try:
            user = await self._user_name(pending.user_id)
            what = self._describe(pending)
            self._revalidate(pending)
            if pending.effect is Effect.MAY_CLOSE:
                self.phase = Phase.PREWARNING
                self._publish()
                if await self._prewarn():
                    raise AccessDenied(f"{self._name}: cancelled during the warning")
                self._revalidate(pending)
            if not await self._send_pulse(pending.intent, pending.effect):
                raise AccessDenied(
                    f"{self._name}: the pulse was dropped (too soon after previous one or write failed)"
                )
            LOGGER.info("%s: %s executed for user %s", self._name, what, pending.user_id)
            await self._notify_all(f"{self._name}: {what} by {user}")
            self._start_watchdog(pending)
        except AccessDenied as err:
            self._settle()
            await self._notify(pending.notify_service, f"Not executed: {err}")
            return
        except Exception as err:
            LOGGER.exception("%s: unexpected error during execution: %s", self._name, err)
            self._settle()
            await self._notify(pending.notify_service, f"Execution failed: {err}")
            return

    async def _prewarn(self) -> bool:
        """Flash the warning light and announce; return True when cancelled."""
        hass = self._require_hass()
        self._cancel_event = asyncio.Event()
        hass.bus.async_fire(
            EVENT_ACCESS_PREWARNING,
            {"entity_id": self._entity_id(), "name": self._name, "seconds": self.config.prewarn_seconds},
        )
        light = self.config.prewarn_light
        original = hass.states.get(light) if light is not None else None
        steps = max(1, int(round(self.config.prewarn_seconds)))
        interval = self.config.prewarn_seconds / steps
        cancelled = False
        try:
            for _ in range(steps):
                if light is not None:
                    await self._light_call("toggle", light)
                try:
                    await asyncio.wait_for(self._cancel_event.wait(), interval)
                    cancelled = True
                    break
                except TimeoutError:
                    continue
        finally:
            if light is not None and original is not None and original.state in (STATE_ON, STATE_OFF):
                await self._light_call("turn_on" if original.state == STATE_ON else "turn_off", light)
            self._cancel_event = None
        return cancelled

    async def _light_call(self, service: str, entity_id: str) -> None:
        try:
            await self._require_hass().services.async_call("light", service, {"entity_id": entity_id}, blocking=True)
        except Exception as err:  # noqa: BLE001 - the warning light is a courtesy, not a safety function
            LOGGER.warning("%s: warning light %s failed: %s", self._name, entity_id, err)

    def _start_watchdog(self, pending: PendingRequest) -> None:
        hass = self._require_hass()
        if self._state_sensor is None:
            self._settle()
            self._spawn(self._notify_all(f"{self._name}: the result cannot be verified (no state sensor)"))
            return
        if pending.intent is Intent.STOP:
            self._settle()
            return
        expected = STATE_ON if pending.effect is Effect.OPEN else STATE_OFF
        timeout = self.config.watchdog_margin
        if pending.effect is Effect.MAY_CLOSE:
            timeout += self._travel_time
        self.phase = Phase.MONITORING
        self._publish()

        @callback
        def _expired(_now: Any = None) -> None:
            self._unsub_watchdog = None
            if self._sensor_state() == expected:
                self._settle()
                return
            if pending.effect is Effect.OPEN:
                self._settle()
                self._spawn(self._notify_all(f"{self._name}: did not start opening"))
                return
            self.fault_reason = "did not close: possible obstacle or reversal"
            self._settle()
            LOGGER.error("%s: %s", self._name, self.fault_reason)
            self._spawn(
                self._notify_all(
                    f"ALARM {self._name}: {self.fault_reason}. Closing from Home Assistant is blocked "
                    "until the fault is acknowledged on site."
                )
            )

        self._unsub_watchdog = async_call_later(hass, timeout, _expired)

    def _cancel_watchdog(self) -> None:
        if self._unsub_watchdog is not None:
            self._unsub_watchdog()
            self._unsub_watchdog = None
