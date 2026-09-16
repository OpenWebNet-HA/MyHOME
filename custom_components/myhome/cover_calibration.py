"""Experimental, socket-owned measurement of one standard cover's travel times.

The bus starts the monotonic clock; the operator confirms the physical endpoints.
A queued Stop is never represented as an acknowledged or physically verified stop.
"""
from __future__ import annotations

import asyncio
import copy
import logging
from time import monotonic as monotonic
from typing import Any, cast
from uuid import uuid4

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.websocket_api.decorators import (
    async_response,
    require_admin,
    websocket_command,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, callback
from OWNd.message import OWNAutomationCommand

from .cover_profile_provenance import EVIDENCE, evidence, unknown_provenance
from .cover_profiles import (
    DATA_KEY,
    ProfileError,
    get_store,
    snapshot,
    target,
    travel_time,
    write_profile,
)

LOGGER = logging.getLogger(__name__)

WS_START = "myhome/cover_calibration/start"
WS_ACTION = "myhome/cover_calibration/action"
LEASE_SECONDS = 20
START_SECONDS = 10
MAX_TRAVEL_SECONDS = 600
STOP_QUEUE_SECONDS = 30
TERMINAL = {"interrupted", "cancelled", "saved"}


class CalibrationSession:
    """One live controller; transient measurements never survive restart."""

    mode = "guided"
    travel_seconds = MAX_TRAVEL_SECONDS
    travel_reason = "travel_timeout"

    def __init__(self, hass: Any, store: Any, entry: Any, cover: Any, connection: Any, subscription_id: Any, *, direction: Any=None, profile: dict[str, Any] | None=None) -> None:
        self.hass, self.store, self.cover = hass, store, cover
        self.entry_id = entry.entry_id
        self.connection = connection
        self.subscription_id = subscription_id
        self.id = uuid4().hex
        self.revision = store.data["revision"]
        self.sequence = 0
        self.phase = "confirm_closed"
        self.reason: str | None = None
        self.values: dict[str, float] = {}
        self.provenance: dict[str, Any] = {}
        self.direction = direction
        if direction:
            profile = cast(dict[str, Any], profile)
            retained = "closing" if direction == "opening" else "opening"
            self.values[f"{retained}_time"] = profile[f"{retained}_time"]
            self.provenance[retained] = copy.deepcopy(profile["provenance"][retained])
            self.phase = "confirm_closed" if direction == "opening" else "confirm_open"
        self.started_at: float | None = None
        self.armed = False
        self.stop_requested = False
        self.listener = True
        self.lease: asyncio.TimerHandle | None = None
        self.deadline: asyncio.TimerHandle | None = None
        self.settle: asyncio.TimerHandle | None = None
        self.shutdown = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_shutdown)
        self.touch()

    @callback
    def _on_shutdown(self, _event: Any) -> None:
        # HA removes a one-shot listener before invoking its callback.
        self.shutdown = None
        self.close("shutdown")

    @property
    def active(self) -> Any:
        return self.phase not in TERMINAL

    def view(self) -> Any:
        return {"entry_id": self.entry_id, "entity_id": self.cover.entity_id,
                "session_id": self.id, "sequence": self.sequence, "revision": self.revision,
                "phase": self.phase, "mode": self.mode, "reason": self.reason, "values": dict(self.values),
                "elapsed": round(monotonic() - self.started_at, 2) if self.started_at is not None else None,
                "stop_requested": self.stop_requested,
                **({"direction": self.direction} if self.direction else {})}

    def emit(self) -> None:
        self.sequence += 1
        if self.listener:
            self.connection.send_event(self.subscription_id, self.view())

    def touch(self) -> None:
        if self.lease:
            self.lease.cancel()
        self.lease = self.hass.loop.call_later(LEASE_SECONDS, self.close, "heartbeat_timeout")

    def arm_deadline(self, seconds: Any, reason: str) -> None:
        if self.deadline:
            self.deadline.cancel()
        self.deadline = self.hass.loop.call_later(seconds, self.interrupt, reason)

    def queue_stop(self) -> None:
        expires = monotonic() + STOP_QUEUE_SECONDS
        try:
            self.cover._gateway_handler.async_queue_calibration(
                OWNAutomationCommand.stop_shutter(self.cover._full_where),
                lambda: monotonic() <= expires, self.store.calibration_command_lock,
            )
        except asyncio.QueueFull:
            self.stop_requested = False
            self.reason = "stop_queue_full"
            LOGGER.warning("Calibration Stop could not be queued for %s; use the physical control", self.cover.entity_id)
        else:
            self.stop_requested = True

    def interrupt(self, reason: str, send_stop: Any=True) -> None:
        if not self.active or self.phase == "saving":
            return
        self.phase, self.reason = "interrupted", reason
        self.values.clear()
        self.provenance.clear()
        self.started_at = None
        if self.deadline:
            self.deadline.cancel()
        if self.settle:
            self.settle.cancel()
        if send_stop:
            self.queue_stop()
        self.emit()

    def close(self, reason: str="cancelled") -> None:
        if not self.listener:
            return
        # Invalidate queued motion before releasing the socket/store ownership.
        self.interrupt(reason)
        if self.phase != "saved":
            self.phase, self.reason = "cancelled", reason
        self.emit()
        self.listener = False
        if self.lease:
            self.lease.cancel()
        if self.deadline:
            self.deadline.cancel()
        if self.shutdown is not None:
            unsubscribe, self.shutdown = self.shutdown, None
            unsubscribe()
        if self.store.calibration is self:
            self.store.calibration = None
        if self.cover._calibration is self:
            self.cover._calibration = None

    def move(self, direction: Any) -> None:
        expected = "confirm_closed" if direction == "open" else "confirm_open"
        if self.phase != expected:
            raise ProfileError("calibration_step")
        # The action itself confirms the starting endpoint and stationary state.
        self.confirm_position(0 if direction == "open" else 100)
        self.queue_move(direction)

    def queue_move(self, direction: Any) -> Any:
        """Use the same guarded queue for guided and automatic movements."""
        token = self._motion_token = object()
        self.phase = f"starting_{direction}"
        self.armed = False
        self.started_at = None
        self.stop_requested = False
        expires = monotonic() + START_SECONDS
        self.arm_deadline(START_SECONDS, "start_timeout")

        def guard() -> Any:
            if self._motion_token is not token or self.phase != f"starting_{direction}" or monotonic() > expires:
                return False
            self.armed = True
            return True

        command = OWNAutomationCommand.raise_shutter if direction == "open" else OWNAutomationCommand.lower_shutter
        try:
            self.cover._gateway_handler.async_queue_calibration(
                command(self.cover._full_where), guard, self.store.calibration_command_lock,
            )
        except asyncio.QueueFull as error:
            self.interrupt("command_queue_full", send_stop=False)
            raise ProfileError("command_queue_full") from error
        self.emit()

    def on_event(self, event: Any) -> None:
        if self.phase not in {"starting_open", "starting_close", "opening", "closing"}:
            if self.active and (event.is_opening or event.is_closing):
                self.interrupt("unexpected_movement")
            return
        opening = self.phase in {"starting_open", "opening"}
        expected = event.is_opening if opening else event.is_closing
        opposite = event.is_closing if opening else event.is_opening
        if self.phase.startswith("starting_"):
            if expected and self.armed:
                self.phase = "opening" if opening else "closing"
                self.started_at = monotonic()
                self.arm_deadline(self.travel_seconds, self.travel_reason)
                self.emit()
            elif expected or opposite:
                self.interrupt("unexpected_movement")
        elif not expected:
            self.interrupt("unexpected_movement" if opposite else "unexpected_stop")

    def endpoint(self) -> None:
        if self.phase not in {"opening", "closing"}:
            raise ProfileError("calibration_step")
        direction = "opening" if self.phase == "opening" else "closing"
        elapsed = round(monotonic() - cast(float, self.started_at), 2)
        try:
            elapsed = travel_time(elapsed)
        except vol.Invalid as error:
            self.interrupt("invalid_measurement")
            raise ProfileError("invalid_profile") from error
        self.values[f"{direction}_time"] = elapsed
        self.provenance[direction] = evidence("guided", self.cover.unique_id)
        self.phase = "confirm_open" if direction == "opening" and not self.direction else "review"
        self.started_at = None
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.queue_stop()
        # The operator explicitly confirmed this physical endpoint. Bus Stop alone
        # cannot establish it. Keep the existing travel settings until Save.
        self.confirm_position(100 if direction == "opening" else 0)
        self.emit()

    def confirm_position(self, position: Any) -> None:
        cover = self.cover
        cover._cancel_stop_task()
        cover._attr_current_cover_position = cover._start_position = position
        cover._move_start_time = None
        cover._attr_is_opening = cover._attr_is_closing = False
        cover._attr_is_closed = position == 0
        cover.async_write_ha_state()

    async def action(self, msg: dict[str, Any]) -> Any:
        action = msg["action"]
        if action == "cancel":
            self.close()
            return self.view()
        if action == "stop":
            self.interrupt("stopped", send_stop=False)
            self.queue_stop()
            self.emit()
            return self.view()
        if action == "heartbeat":
            self.touch()
            return self.view()
        if not self.active or msg.get("sequence") != self.sequence:
            raise ProfileError("calibration_step")
        entry, entity = target(self.hass, self.entry_id, self.cover.entity_id)
        if not snapshot(self.hass, self.store, entry, entity)["writable"]:
            self.interrupt("cover_unavailable")
            raise ProfileError("cover_unavailable")
        if action == "run":
            if self.mode != "automatic" or self.phase != "confirm_automatic":
                raise ProfileError("calibration_step")
            self.queue_move("open")
        elif action in {"open", "close", "endpoint"} and self.mode != "guided":
            raise ProfileError("calibration_step")
        elif action in {"open", "close"}:
            self.move(action)
        elif action == "endpoint":
            self.endpoint()
        elif action == "save":
            if self.phase != "review":
                raise ProfileError("calibration_step")
            self.phase = "saving"
            self.emit()
            try:
                revision = await self.save_profiles(msg)
            except (ProfileError, vol.Invalid, OSError):
                if self.store.calibration is self:
                    self.phase = "review"
                    self.emit()
                raise
            self.revision = revision
            self.phase = "saved"
            self.emit()
            self.close()
        return self.view()


    async def save_profiles(self, msg: dict[str, Any]) -> Any:
        result = await write_profile(self.hass, {
            "entry_id": self.entry_id, "entity_id": self.cover.entity_id,
            "revision": self.revision, "action": "save", "profile_id": None,
            "profile": {"name": msg.get("name", ""), **self.values},
        }, calibration=self)
        return result["revision"]


def ready_cover(hass: Any, store: Any, entry_id: str, entity_id: str) -> Any:
    """Validate the same target and stationary state for every calibration mode."""
    entry, entity = target(hass, entry_id, entity_id)
    view = snapshot(hass, store, entry, entity)
    if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.stopped):
        raise ProfileError("cover_unavailable")
    if not view["writable"]:
        raise ProfileError(view["reason"])
    cover = store.covers[entity.unique_id]
    if cover.native_calibration_busy():
        raise ProfileError("calibration_busy")
    if (cover._attr_is_opening or cover._attr_is_closing or cover._move_start_time is not None
            or cover._pending_profile or cover._stop_task):
        raise ProfileError("calibration_moving")
    return cover


async def begin(hass: Any, connection: Any, msg: dict[str, Any]) -> Any:
    entry, entity = target(hass, msg["entry_id"], msg["entity_id"])
    store = get_store(hass, entry.entry_id)
    async with store.lock:
        await store.load()
        cover = ready_cover(hass, store, entry.entry_id, msg["entity_id"])
        if store.calibration is not None:
            raise ProfileError("calibration_busy")
        if msg["revision"] != store.data["revision"]:
            raise ProfileError("revision_conflict")
        options = {}
        if "direction" in msg:
            direction = vol.In(["opening", "closing"])(msg["direction"])
            if msg.get("mode", "guided") != "guided":
                raise ProfileError("invalid_profile")
            retained = "closing" if direction == "opening" else "opening"
            setting = cover.resolve_cover_settings(store.profile(cover.unique_id))[retained]
            # Resolve committed values even without an assignment. A native record
            # may lack the origin/date required by profile evidence; do not invent
            # a measurement or overwrite that original fallback record.
            try:
                provenance = EVIDENCE(setting["provenance"])
            except vol.Invalid:
                provenance = unknown_provenance()[retained]
            options = {"direction": direction, "profile": {
                f"{retained}_time": travel_time(setting["value"]),
                "provenance": {retained: provenance},
            }}
        session_type: type[CalibrationSession] = CalibrationSession
        if msg.get("mode", "guided") == "automatic":
            from .cover_calibration_automatic import AutomaticCalibrationSession
            session_type = AutomaticCalibrationSession
        session = session_type(hass, store, entry, cover, connection, msg["id"], **options)
        store.calibration = cover._calibration = session
        connection.subscriptions[msg["id"]] = session.close
        return session


@websocket_command({
    vol.Required("type"): WS_START, vol.Required("entry_id"): str,
    vol.Required("entity_id"): str, vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Optional("mode"): vol.In(["guided", "automatic"]),
    vol.Optional("direction"): vol.In(["opening", "closing"]),
})
@require_admin
@async_response
async def ws_start(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    try:
        session = await begin(hass, connection, msg)
    except (ProfileError, vol.Invalid, OSError) as error:
        send_error(connection, msg, error)
        return
    connection.send_result(msg["id"])
    session.emit()


def send_error(connection: Any, msg: dict[str, Any], error: Any) -> None:
    code = str(error) if isinstance(error, ProfileError) else "invalid_profile" if isinstance(error, vol.Invalid) else "storage_error"
    connection.send_error(msg["id"], code, code)


@websocket_command({
    vol.Required("type"): WS_ACTION, vol.Required("entry_id"): str,
    vol.Required("session_id"): str,
    vol.Required("action"): vol.In(["run", "open", "close", "endpoint", "stop", "cancel", "save", "heartbeat"]),
    vol.Optional("sequence"): vol.All(int, vol.Range(min=0)),
    vol.Optional("name"): str,
    vol.Optional("names"): vol.All([str], vol.Length(min=1, max=20)),
})
@require_admin
@async_response
async def ws_action(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    store = hass.data.get(DATA_KEY, {}).get(msg["entry_id"])
    session = store.calibration if store else None
    if session is None or session.id != msg["session_id"] or session.connection is not connection:
        connection.send_error(msg["id"], "calibration_expired", "Calibration session is not owned by this connection")
        return
    try:
        result = await session.action(msg)
    except (ProfileError, vol.Invalid, OSError) as error:
        send_error(connection, msg, error)
    else:
        connection.send_result(msg["id"], result)


@callback
def register_api(hass: Any) -> None:
    from .cover_calibration_batch import ws_batch_start, ws_targets
    websocket_api.async_register_command(hass, ws_batch_start)
    websocket_api.async_register_command(hass, ws_targets)
    websocket_api.async_register_command(hass, ws_start)
    websocket_api.async_register_command(hass, ws_action)
