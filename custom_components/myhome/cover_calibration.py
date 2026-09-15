"""Experimental, socket-owned measurement of one standard cover's travel times.

The bus starts the monotonic clock; the operator confirms the physical endpoints.
A queued Stop is never represented as an acknowledged or physically verified stop.
"""
from __future__ import annotations

import asyncio
import logging
from time import monotonic
from uuid import uuid4

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, callback
from OWNd.message import OWNAutomationCommand

from .cover_profile_provenance import evidence
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

    def __init__(self, hass, store, entry, cover, connection, subscription_id):
        self.hass, self.store, self.cover = hass, store, cover
        self.entry_id = entry.entry_id
        self.connection = connection
        self.subscription_id = subscription_id
        self.id = uuid4().hex
        self.revision = store.data["revision"]
        self.sequence = 0
        self.phase = "confirm_closed"
        self.reason = None
        self.values = {}
        self.provenance = {}
        self.started_at = None
        self.armed = False
        self.stop_requested = False
        self.listener = True
        self.lease = None
        self.deadline = None
        self.settle = None
        self.shutdown = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_shutdown)
        self.touch()

    @callback
    def _on_shutdown(self, _event):
        # HA removes a one-shot listener before invoking its callback.
        self.shutdown = None
        self.close("shutdown")

    @property
    def active(self):
        return self.phase not in TERMINAL

    def view(self):
        return {"entry_id": self.entry_id, "entity_id": self.cover.entity_id,
                "session_id": self.id, "sequence": self.sequence, "revision": self.revision,
                "phase": self.phase, "mode": self.mode, "reason": self.reason, "values": dict(self.values),
                "elapsed": round(monotonic() - self.started_at, 2) if self.started_at is not None else None,
                "stop_requested": self.stop_requested}

    def emit(self):
        self.sequence += 1
        if self.listener:
            self.connection.send_event(self.subscription_id, self.view())

    def touch(self):
        if self.lease:
            self.lease.cancel()
        self.lease = self.hass.loop.call_later(LEASE_SECONDS, self.close, "heartbeat_timeout")

    def arm_deadline(self, seconds, reason):
        if self.deadline:
            self.deadline.cancel()
        self.deadline = self.hass.loop.call_later(seconds, self.interrupt, reason)

    def queue_stop(self):
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

    def interrupt(self, reason, send_stop=True):
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

    def close(self, reason="cancelled"):
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

    def move(self, direction):
        expected = "confirm_closed" if direction == "open" else "confirm_open"
        if self.phase != expected:
            raise ProfileError("calibration_step")
        # The action itself confirms the starting endpoint and stationary state.
        self.confirm_position(0 if direction == "open" else 100)
        self.queue_move(direction)

    def queue_move(self, direction):
        """Use the same guarded queue for guided and automatic movements."""
        token = self._motion_token = object()
        self.phase = f"starting_{direction}"
        self.armed = False
        self.started_at = None
        self.stop_requested = False
        expires = monotonic() + START_SECONDS
        self.arm_deadline(START_SECONDS, "start_timeout")

        def guard():
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

    def on_event(self, event):
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

    def endpoint(self):
        if self.phase not in {"opening", "closing"}:
            raise ProfileError("calibration_step")
        direction = "opening" if self.phase == "opening" else "closing"
        elapsed = round(monotonic() - self.started_at, 2)
        try:
            elapsed = travel_time(elapsed)
        except vol.Invalid as error:
            self.interrupt("invalid_measurement")
            raise ProfileError("invalid_profile") from error
        self.values[f"{direction}_time"] = elapsed
        self.provenance[direction] = evidence("guided", self.cover.unique_id)
        self.phase = "confirm_open" if direction == "opening" else "review"
        self.started_at = None
        self.deadline.cancel()
        self.queue_stop()
        # The operator explicitly confirmed this physical endpoint. Bus Stop alone
        # cannot establish it. Keep the existing travel settings until Save.
        self.confirm_position(100 if direction == "opening" else 0)
        self.emit()

    def confirm_position(self, position):
        cover = self.cover
        cover._cancel_stop_task()
        cover._attr_current_cover_position = cover._start_position = position
        cover._move_start_time = None
        cover._attr_is_opening = cover._attr_is_closing = False
        cover._attr_is_closed = position == 0
        cover.async_write_ha_state()

    async def action(self, msg):
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


    async def save_profiles(self, msg):
        result = await write_profile(self.hass, {
            "entry_id": self.entry_id, "entity_id": self.cover.entity_id,
            "revision": self.revision, "action": "save", "profile_id": None,
            "profile": {"name": msg.get("name", ""), **self.values},
        }, calibration=self)
        return result["revision"]


def ready_cover(hass, store, entry_id, entity_id):
    """Validate the same target and stationary state for every calibration mode."""
    entry, entity = target(hass, entry_id, entity_id)
    view = snapshot(hass, store, entry, entity)
    if hass.state in (CoreState.stopping, CoreState.final_write, CoreState.stopped):
        raise ProfileError("cover_unavailable")
    if not view["writable"]:
        raise ProfileError(view["reason"])
    cover = store.covers[entity.unique_id]
    if (cover._attr_is_opening or cover._attr_is_closing or cover._move_start_time is not None
            or cover._pending_profile or cover._stop_task):
        raise ProfileError("calibration_moving")
    return cover


async def begin(hass, connection, msg):
    entry, entity = target(hass, msg["entry_id"], msg["entity_id"])
    store = get_store(hass, entry.entry_id)
    async with store.lock:
        await store.load()
        cover = ready_cover(hass, store, entry.entry_id, msg["entity_id"])
        if store.calibration is not None:
            raise ProfileError("calibration_busy")
        if msg["revision"] != store.data["revision"]:
            raise ProfileError("revision_conflict")
        session_type = CalibrationSession
        if msg.get("mode", "guided") == "automatic":
            from .cover_calibration_automatic import AutomaticCalibrationSession
            session_type = AutomaticCalibrationSession
        session = session_type(hass, store, entry, cover, connection, msg["id"])
        store.calibration = cover._calibration = session
        connection.subscriptions[msg["id"]] = session.close
        return session


@websocket_api.websocket_command({
    vol.Required("type"): WS_START, vol.Required("entry_id"): str,
    vol.Required("entity_id"): str, vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Optional("mode"): vol.In(["guided", "automatic"]),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_start(hass, connection, msg):
    try:
        session = await begin(hass, connection, msg)
    except (ProfileError, vol.Invalid, OSError) as error:
        send_error(connection, msg, error)
        return
    connection.send_result(msg["id"])
    session.emit()


def send_error(connection, msg, error):
    code = str(error) if isinstance(error, ProfileError) else "invalid_profile" if isinstance(error, vol.Invalid) else "storage_error"
    connection.send_error(msg["id"], code, code)


@websocket_api.websocket_command({
    vol.Required("type"): WS_ACTION, vol.Required("entry_id"): str,
    vol.Required("session_id"): str,
    vol.Required("action"): vol.In(["run", "open", "close", "endpoint", "stop", "cancel", "save", "heartbeat"]),
    vol.Optional("sequence"): vol.All(int, vol.Range(min=0)),
    vol.Optional("name"): str,
    vol.Optional("names"): vol.All([str], vol.Length(min=1, max=20)),
})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_action(hass, connection, msg):
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
def register_api(hass):
    from .cover_calibration_batch import ws_batch_start, ws_targets
    websocket_api.async_register_command(hass, ws_batch_start)
    websocket_api.async_register_command(hass, ws_targets)
    websocket_api.async_register_command(hass, ws_start)
    websocket_api.async_register_command(hass, ws_action)
