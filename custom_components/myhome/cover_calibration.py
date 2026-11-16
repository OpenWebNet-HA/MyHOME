"""Experimental, backend-owned measurement of one standard cover's travel times.

The bus starts the monotonic clock; the operator confirms the physical endpoints.
A stop status reported by the bus before that confirmation dates the end of the run,
but only the confirmation records it. A queued Stop is never represented as an
acknowledged or physically verified stop.
"""
from __future__ import annotations

import asyncio
import collections
import copy
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import monotonic as monotonic
from typing import TYPE_CHECKING, Any, Callable, cast
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from OWNd.message import OWNAutomationCommand

from .const import CONF_COVER_TRAVEL_TIMES, DOMAIN
from .cover_calibration_reservation import CalibrationReservation
from .cover_geometry import UNKNOWN
from .cover_motion import ECHO_WINDOW, MotionPosition
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
from .cover_settings import ROLL_SOURCES, precision_class, profile_source, roll_settings
from .cover_translation import event_age
from .data import MyHOMERuntimeData
from .typing_compat import as_any

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

LOGGER = logging.getLogger(__name__)

WS_START = "myhome/cover_calibration/start"
WS_ACTION = "myhome/cover_calibration/action"
# A client that sends no client_id keeps the original contract: a heartbeat lease,
# and cancellation when its subscription ends.
LEASE_SECONDS = 20
# A client that sends client_id (one per browser tab) is recognised on every socket.
# Presence tells every reader whether the owner is still there (`owner_present`), and
# an automatic step starts a new movement only while it holds; otherwise the cycle
# pauses and keeps its measurements until the owner continues it. The lease hands the
# cover back after inactivity and is never renewed by a heartbeat.
PRESENCE_SECONDS = 45
IDLE_LEASE_SECONDS = 1800
MOVED_LEASE_SECONDS = 600
START_SECONDS = 10
MAX_TRAVEL_SECONDS = 600
STOP_QUEUE_SECONDS = 30
TERMINAL = {"interrupted", "cancelled", "saved"}
# The runs the bus starts after an armed command, and the phase each one times once it has.
RUNS = {"starting_open": "opening", "starting_close": "closing", "starting_lift": "lifting"}
RISING = {"starting_open", "opening", "starting_lift", "lifting"}
# The runs back to an end stop that the session ends by itself (nothing is measured), and their direction.
BACK = {"reclosing": "close", "reopening": "open", "rehoming": "close"}
# The rolls of a basic calibration's profile, and the largest a profile stores (GEOMETRY).
ROLLS = ("opening_roll", "closing_roll")
ROLL_MAX = 5.0
# Basic calibration with slats: the lift-off is the Stop and the cover stays where it stopped. Only a repeat of the
# lift run (or a lift-off refused as too late) closes it again: the Stop, this pause, then the close back to the rest;
# the lift run then starts only from the operator's `open`, never by itself.
RECLOSE_SETTLE_SECONDS = 1.0
# The close back to the rest ends at its stop status or, at the latest, after the seconds the motor rose times
# RECLOSE_RISE_SHARE plus RECLOSE_RISE_SECONDS (a few centimetres take a few seconds, not a full run), and never later
# than the full closing time plus its margin (as the panel's positioning: max(4 s, 25 %)). A timer
# actuator reports its stop only at its timer: the session ends the close at that limit with a Stop of its own.
RECLOSE_RISE_SHARE = 1.5
RECLOSE_RISE_SECONDS = 2.0
RECLOSE_MARGIN_SECONDS = 4.0
RECLOSE_MARGIN_SHARE = 0.25
# In the basic calibration a full run ends at whichever comes first, the operator's press or the actuator's own stop:
# on that stop the session confirms the run by itself, timed to it. Not on a stop at an actuator's relay timer (60 s,
# or 120 s with configurator M=1, #837): there the motor ran on past the end stop, and the operator's press is the end.
TIMER_STOP_WINDOWS = ((59.0, 65.0), (119.0, 125.0))
# A stop status this soon after the run's anchor (the bus's movement status) is the actuator stopping at the end stop
# the cover already stood at. The gateway's own interlock Stop precedes the movement status (in bus traces 0.07-0.16 s
# after the write, the movement at 0.56-0.67 s), and no full run of a shutter lasts 2 s (the shortest measured: about
# 12 s); an evolved actuator reports its stop 0.3 s after the motor stops.
ALREADY_THERE_SECONDS = 2.0
# How long after one of our status requests (`*#2*WHERE##`, a general or area one, written by the integration: the
# poll after the startup sweep, an entity update) the actuator's reply may still arrive. A request sent before the
# run's anchor can be answered "stopped" after it (the actuator answered before its motor started):
# that stop is the reply, and says nothing of the motor now. Replies come in tenths of a second; the margin covers the
# time Home Assistant takes to hand the frame over.
STATUS_REPLY_SECONDS = 1.0


@dataclass(eq=False)
class Subscriber:
    """One subscription reading a session whose clients identify themselves."""

    connection: Any
    subscription_id: Any
    client_id: str
    token: str = field(default_factory=lambda: uuid4().hex)


class CalibrationSession:
    """One owner, any number of readers; transient measurements never survive restart."""

    mode = "guided"
    travel_seconds = MAX_TRAVEL_SECONDS
    travel_reason = "travel_timeout"
    geometry_provenance: dict[str, Any] | None = None
    measured_travel: float | None = None
    safe_phases = {"confirm_closed", "confirm_open", "confirm_automatic", "review", "saving"}

    def __init__(self, hass: Any, store: Any, entry: Any, cover: Any, connection: Any, subscription_id: Any, *, direction: Any=None, profile: dict[str, Any] | None=None, client_id: str | None=None, slats: bool | None=None) -> None:
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
        # Basic calibration of a cover with slats: after the closing run, the lift run from the rest takes `lift`,
        # the seconds from the motor start to the moment the bottom edge leaves it (`samples["lift"]`, saved as the
        # slat time). That press is the Stop, and review follows with the cover where it stopped. None: never
        # asked (one direction, automatic): a save then leaves the slat time as it was.
        self.slats = slats
        self.samples: dict[str, float] = {}
        self.lift_evidence: dict[str, Any] | None = None
        # After an accepted lift-off: "stopping" until its Stop is written, "written" until the bus reports the stop
        # (at most ECHO_WINDOW after the write); meanwhile the lift run's own statuses may still arrive.
        self.lift_stop: str | None = None
        # The cover stopped at the lift-off and has not moved since: the edge on the sill, the slats open.
        self.lifted = False
        # A run back to an end stop (`reclosing`, `reopening`): "stopping" (a Stop, then a pause), "starting", "running".
        self.back_stage: str | None = None
        # The seconds the motor rose in the lift run being closed back (see `reclose_seconds`).
        self.risen: float | None = None
        # The bus reported the motor's stop during a run that the operator confirms
        # (see `stop_awaits_endpoint`): seconds from the motor start, rounded as `endpoint`.
        self.motor_stopped = False
        self.stopped_elapsed: float | None = None
        # The direction (True: opening) and the time a full run was confirmed, to recognise its own late statuses.
        self.run_end: tuple[bool, float] | None = None
        # The sequence of the view that published that stop (see `press_across_stop`).
        self.stop_sequence: int | None = None
        # The sequence of the view that published a run ended at the actuator's own stop (see `press_after_arrival`).
        self.arrival_sequence: int | None = None
        self.armed = False
        self.stop_requested = False
        self.listener = True
        self.closed = False
        self.client_id = client_id
        # Which client started it ("wizard": the Calibration tab's wizard): a second tab opens it in the same view.
        self.origin: str | None = None
        self.attachment = uuid4().hex
        # Used only when the client identifies itself (client_id).
        self.owner = client_id
        self.owner_seen = monotonic()
        self.known: set[str] = {client_id} if client_id else set()
        self.subscribers: dict[str, Subscriber] = {}
        # A cycle paused without its owner: the step that would have started, and how.
        self.next_step: dict[str, Any] | None = None
        # L-25: what the owner's panel shows (its screen and the choices made on it), published to the readers as is.
        # It is the panel's own description: the session never reads it and it changes nothing of the session.
        self.shown: dict[str, Any] | None = None
        self.paused_at: datetime | None = None
        self.resume_step: tuple[Callable[[], None], Callable[[], None]] | None = None
        self.moved = False
        self.lease_seconds = IDLE_LEASE_SECONDS
        self.lease_expires_at: datetime | None = None
        self.lease: asyncio.TimerHandle | None = None
        self.deadline: asyncio.TimerHandle | None = None
        self.settle: asyncio.TimerHandle | None = None
        self.reservation = CalibrationReservation(self, lambda: monotonic(), STATUS_REPLY_SECONDS)
        # When our status requests reaching this cover were sent, and when the session learnt of the run's movement
        # (see `answers_status_request`).
        self.status_asked: list[float] = []
        self.movement_seen: float | None = None
        listeners = getattr(cover._gateway_handler, "automation_status_listeners", None)
        if isinstance(listeners, set):
            listeners.add(self.status_requested)
        self.shutdown = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_shutdown)
        self.touch()

    @callback
    def _on_shutdown(self, _event: Any) -> None:
        # HA removes a one-shot listener before invoking its callback.
        self.shutdown = None
        self.close("shutdown")
        self.reservation.completed()

    @property
    def active(self) -> Any:
        return self.phase not in TERMINAL

    def view(self) -> Any:
        return {"entry_id": self.entry_id, "entity_id": self.cover.entity_id,
                "session_id": self.id, "sequence": self.sequence, "revision": self.revision,
                "phase": self.phase, "mode": self.mode, "reason": self.reason, "values": dict(self.values),
                # What ended each run timed in `values`: the operator's press (`press`) or the actuator's own stop
                # (`bus_stop`), as its provenance says; only the directions that have it.
                "ended_by": {direction: item["ended_by"] for direction, item in self.provenance.items()
                             if isinstance(item, dict) and item.get("ended_by")},
                "elapsed": round(monotonic() - self.started_at, 2) if self.started_at is not None else None,
                "stop_requested": self.stop_requested,
                "motor_stopped": self.motor_stopped, "stopped_elapsed": self.stopped_elapsed,
                "travel_cm": self.store.data["covers"].get(self.cover.unique_id, {}).get("travel_cm"),
                "reference_travel_cm": (self.store.profile(self.cover.unique_id) or {}).get("reference_travel_cm"),
                "waiting_for_stop": self.closed and self.reservation.pending,
                **({"recoverable": not self.closed, "attached": self.present() and not self.closed, "attachment": None,
                    "owner_present": self.present() and not self.closed,
                    "next_step": dict(self.next_step) if self.next_step else None,
                    "paused_at": self.paused_at.isoformat() if self.paused_at else None,
                    "recovery_seconds": self.lease_seconds, "owner": False, "read_only": True,
                    "idle_expires_at": None if self.closed or self.lease_expires_at is None
                    else self.lease_expires_at.isoformat()} if self.client_id else {}),
                # A calibration always saves into a profile: a new one, or the one the cover follows. The cover's
                # own values (`cover`) stay for the manual overrides only.
                "save_modes": self.save_modes(),
                "slats": bool(self.slats), "samples": dict(self.samples), "lift_taken": "lift" in self.samples,
                "reclose_seconds": self.reclose_seconds() if self.phase == "reclosing" else None,
                "reopen_seconds": self.reopen_seconds() if self.phase == "reopening" else None,
                "rehome_seconds": self.rehome_seconds() if self.phase == "rehoming" else None,
                # For the waiting screen of a run back: its stage, and with `elapsed` (from the bus's start) and the
                # seconds above, its progress.
                "back_stage": self.back_stage if self.phase in BACK else None,
                **({"origin": self.origin} if self.origin else {}),
                **({"screen": copy.deepcopy(self.shown)} if self.shown is not None else {}),
                **({"direction": self.direction} if self.direction else {})}

    def save_modes(self) -> list[str]:
        """Where a review may be saved: always a new profile; the profile the cover follows (`shared`) only from its
        source: a cover that follows a profile measured on another one, or made by hand, saves a profile of its own,
        and the others keep theirs."""
        profile = self.store.profile(self.cover.unique_id)
        return ["new", "shared"] if profile_source(profile, self.cover.unique_id) else ["new"]

    def present(self) -> bool:
        """The owner sent a heartbeat or a verb recently. It never decides who may act."""
        return self.owner is not None and monotonic() - self.owner_seen < PRESENCE_SECONDS

    def unattended(self, step: dict[str, Any], check: Callable[[], None], start: Callable[[], None]) -> bool:
        """An automatic step starts a new movement only while an identified owner is present.

        Otherwise the cycle pauses: every measurement is kept, nothing moves, and only the
        owner's `continue` starts `step` again: `check` raises without changing anything,
        `start` moves. Stop is written only while a movement may still run. The lease keeps
        running; when it ends the session expires.
        """
        if not self.client_id or self.present():
            return False
        self.resume_step = (check, start)
        self.next_step, self.paused_at = step, dt_util.utcnow()
        self.phase, self.reason = "paused", "owner_absent"
        if self.reservation.pending:
            self.queue_stop()
        self.emit()
        return True

    def proceed(self) -> None:
        """`continue`: the owner starts the step a paused cycle stopped before, with its checks.

        A check that fails leaves the pause and its measurements as they are: nothing has moved.
        """
        if self.phase != "paused":
            raise ProfileError("calibration_step")
        check, start = cast(tuple[Callable[[], None], Callable[[], None]], self.resume_step)
        check()
        self.reason = None
        self.resume_step = self.next_step = self.paused_at = None
        start()

    def overlay(self, subscriber: Subscriber, attached: bool = True) -> dict[str, Any]:
        """What differs between readers: ownership, their token, their live subscription.

        `owner_present` is the same for every reader and stays in the view.
        """
        owner = subscriber.client_id == self.owner
        return {"owner": owner, "read_only": not owner, "attachment": subscriber.token,
                "attached": attached and not self.closed}

    def send_to(self, subscriber: Subscriber) -> None:
        subscriber.connection.send_event(subscriber.subscription_id, {**self.view(), **self.overlay(subscriber)})

    def emit(self) -> None:
        """The only door of a transition: sequence, lease and every reader move together."""
        self.sequence += 1
        if not self.client_id:
            if self.listener:
                self.connection.send_event(self.subscription_id, self.view())
            return
        self.touch()
        for subscriber in list(self.subscribers.values()):
            self.send_to(subscriber)
        self.notify_list()

    def notify_list(self) -> None:
        """L-107: the panel's lists learn of every transition (a session started, moved on, closed) at once.

        Sent on the profiles subscription they already hold, without data: each list reads the session again.
        """
        from homeassistant.helpers.dispatcher import async_dispatcher_send

        from .cover_profiles import WS_SUBSCRIBE

        async_dispatcher_send(self.hass, f"{WS_SUBSCRIBE}:{self.entry_id}", {
            "entry_id": self.entry_id, "revision": self.store.data["revision"], "kind": "session"})

    def announce(self, connection: Any, subscription_id: Any) -> None:
        """A new session's first view is a transition; a replayed subscription only reads it."""
        if self.sequence == 0:
            self.emit()
            return
        self.send_to(next(item for item in self.subscribers.values()
                          if item.connection is connection and item.subscription_id == subscription_id))

    def touch(self) -> None:
        """Legacy clients: the heartbeat lease. Identified clients: the inactivity lease."""
        if self.client_id and self.closed:
            return
        if self.lease:
            self.lease.cancel()
        if not self.client_id:
            self.lease = self.hass.loop.call_later(LEASE_SECONDS, self.detach, self.attachment, "heartbeat_timeout")
            return
        self.lease_seconds = MOVED_LEASE_SECONDS if self.moved else IDLE_LEASE_SECONDS
        self.lease_expires_at = dt_util.utcnow() + timedelta(seconds=self.lease_seconds)
        self.lease = self.hass.loop.call_later(self.lease_seconds, self.expire)

    def expire(self) -> None:
        """Hand the cover back after inactivity; write Stop only while a movement may run."""
        self.close("expired", send_stop=self.reservation.pending)

    def subscribe(self) -> None:
        """Bind cleanup to this attachment, never to a later controller."""
        if not self.client_id:
            self.connection.subscriptions[self.subscription_id] = self.close
            return
        self.add_subscriber(self.connection, self.subscription_id, self.client_id, self.attachment)

    def add_subscriber(self, connection: Any, subscription_id: Any, client_id: str, token: str | None = None) -> Subscriber:
        subscriber = Subscriber(connection, subscription_id, client_id, token or uuid4().hex)
        self.subscribers[subscriber.token] = subscriber
        self.known.add(client_id)
        connection.subscriptions[subscription_id] = lambda: self.drop(subscriber.token)
        return subscriber

    def drop(self, token: str) -> None:
        """A lost socket or an unsubscribe takes the reader away and changes nothing else."""
        self.subscribers.pop(token, None)

    def detach(self, token: str, reason: str="disconnected") -> None:
        """Legacy clients end with their subscription; identified clients only stop reading."""
        if self.client_id:
            self.drop(token)
            return
        if self.closed or not self.listener or token != self.attachment:
            return
        self.close(reason)

    def attach(self, connection: Any, subscription_id: Any, client_id: str, *,
               claim: bool = False, sequence: int | None = None) -> tuple[Subscriber, bool]:
        """Add a reader; ownership moves only on a claim made against the current sequence.

        A subscription replayed after a reconnection carries an older sequence, so it
        never takes the session from its owner, and it does not make the owner present:
        only a heartbeat, a verb or a claim does. Nothing is sent to the bus here.
        """
        if self.closed or not self.client_id:
            raise ProfileError("calibration_expired")
        subscriber = self.add_subscriber(connection, subscription_id, client_id)
        changed = claim and sequence == self.sequence and client_id != self.owner
        if changed:
            self.owner, self.owner_seen = client_id, monotonic()
            # The previous owner's screen is not the new owner's: readers wait for the new owner's first `show`.
            self.shown = None
        return subscriber, changed

    def same_request(self, msg: dict[str, Any]) -> bool:
        """A replayed start names the same target, mode and direction as this session."""
        return bool(msg.get("entity_id") == self.cover.entity_id and msg.get("mode", "guided") == self.mode
                    and msg.get("direction") == self.direction
                    and msg.get("keep_times", False) == getattr(self, "keep_times", False))

    def leave(self, subscriber: Subscriber) -> None:
        """A reader goes away. The owner ends the session only if nothing has moved yet."""
        self.drop(subscriber.token)
        if subscriber.client_id != self.owner:
            return
        if not self.active or (not self.moved and self.phase in self.safe_phases):
            self.close("left", send_stop=False)
        else:
            self.owner_seen = float("-inf")

    async def perform(self, subscriber: Subscriber, msg: dict[str, Any]) -> Any:
        """Who may send what, for identified clients. The verbs themselves are unchanged.

        Stop is accepted from every reader: it is a safety control. Every other verb,
        Save included, belongs to the owner. A heartbeat never takes ownership and never
        renews the lease; every accepted verb does, Stop from a reader included.
        """
        action = msg["action"]
        owner = subscriber.client_id == self.owner
        if action == "heartbeat":
            if owner:
                self.owner_seen = monotonic()
            return {**self.view(), **self.overlay(subscriber)}
        if action == "detach":
            self.leave(subscriber)
            return {**self.view(), **self.overlay(subscriber, attached=False)}
        if action == "show":
            # L-25: the owner's screen to its readers. Not a transition: no sequence, no lease, nothing to the bus.
            if not owner:
                raise ProfileError("calibration_owned")
            self.shown = msg.get("screen") or None
            for item in list(self.subscribers.values()):
                if item.client_id != self.owner:
                    self.send_to(item)
            return {**self.view(), **self.overlay(subscriber)}
        if not owner and action != "stop":
            raise ProfileError("calibration_owned")
        if owner:
            self.owner_seen = monotonic()
        result = await self.action(msg)
        self.touch()
        return {**result, **self.overlay(subscriber)}

    def arm_deadline(self, seconds: Any, reason: str) -> None:
        if self.deadline:
            self.deadline.cancel()
        self.deadline = self.hass.loop.call_later(seconds, self.interrupt, reason)

    def queue_stop(self) -> Any:
        expires = monotonic() + STOP_QUEUE_SECONDS
        generation = self.reservation.generation
        try:
            written = self.cover._gateway_handler.async_queue_calibration(
                OWNAutomationCommand.stop_shutter(self.cover._full_where),
                # During HA shutdown retain the best-effort Stop even after
                # runtime ownership/timers have been cleaned up.
                lambda: ((self.store.calibration is self or self.hass.state == CoreState.stopping)
                         and generation == self.reservation.generation
                         and monotonic() <= expires), self.store.calibration_command_lock,
            )
        except asyncio.QueueFull:
            self.stop_requested = False
            self.reason = "stop_queue_full"
            LOGGER.warning("Calibration Stop could not be queued for %s; use the physical control", self.cover.entity_id)
        else:
            self.stop_requested = True
            return written

    def interrupt(self, reason: str, send_stop: Any=True) -> None:
        if not self.active or self.phase == "saving":
            return
        self.phase, self.reason = "interrupted", reason
        self.values.clear()
        self.provenance.clear()
        self.samples.clear()
        self.lift_evidence = self.back_stage = self.lift_stop = self.risen = None
        self.lifted = False
        self.started_at = None
        self.forget_motor_stop()
        self.resume_step = self.next_step = self.paused_at = None
        if self.deadline:
            self.deadline.cancel()
        if self.settle:
            self.settle.cancel()
        if send_stop:
            self.queue_stop()
        self.emit()

    def close(self, reason: str="cancelled", *, send_stop: bool=True) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            # Invalidate queued motion before releasing the socket/store ownership.
            self.interrupt(reason, send_stop=send_stop)
            if self.phase != "saved":
                self.phase, self.reason = "cancelled", reason
            self.emit()
        finally:
            self.listener = False
            self.subscribers.clear()
            if self.lease:
                self.lease.cancel()
            if self.deadline:
                self.deadline.cancel()
            if not self.reservation.pending:
                self.release()

    def release(self) -> None:
        """Release a closed client only after its outstanding movement is bounded."""
        listeners = getattr(self.cover._gateway_handler, "automation_status_listeners", None)
        if isinstance(listeners, set):
            listeners.discard(self.status_requested)
        if self.shutdown is not None:
            unsubscribe, self.shutdown = self.shutdown, None
            unsubscribe()
        released = self.store.calibration is self
        if released:
            self.store.calibration = None
        if self.cover._calibration is self:
            self.cover._calibration = None
        if released and self.client_id:
            # L-107: the gateway is free (possibly after the last Stop): the lists drop the session at once.
            self.notify_list()

    def move(self, direction: Any) -> None:
        expected = "confirm_closed" if direction == "open" else "confirm_open"
        if self.phase != expected:
            raise ProfileError("calibration_step")
        # The action itself confirms the starting endpoint and stationary state.
        self.reason = None
        self.confirm_position(0 if direction == "open" else 100)
        self.queue_move(direction)

    def queue_move(self, direction: Any, phase: str | None = None) -> Any:
        """Use the same guarded queue for guided and automatic movements.

        `phase` names a movement that is not one of the two full runs (`starting_lift`, `reclosing`).
        """
        token = self._motion_token = object()
        self.moved = True
        self.lifted = False
        self.reservation.generation += 1
        starting = phase or f"starting_{direction}"
        self.phase = starting
        self.armed = False
        self.started_at = None
        self.forget_motor_stop()
        self.stop_requested = False
        expires = monotonic() + START_SECONDS
        self.arm_deadline(START_SECONDS, "start_timeout")

        def guard() -> Any:
            if self._motion_token is not token or self.phase != starting or monotonic() > expires:
                return False
            self.armed = True
            self.reservation.dispatched()
            return True

        command = OWNAutomationCommand.raise_shutter if direction == "open" else OWNAutomationCommand.lower_shutter
        try:
            written = self.cover._gateway_handler.async_queue_calibration(
                command(self.cover._full_where), guard, self.store.calibration_command_lock,
            )
        except asyncio.QueueFull as error:
            self.interrupt("command_queue_full", send_stop=False)
            raise ProfileError("command_queue_full") from error
        self.emit()
        return written

    def on_event(self, event: Any) -> None:
        """A bus event of this cover, from the cover's `handle_event`. Whatever goes wrong in the session stays in it:
        the session ends (its Stop written), and the cover still handles the event as its own."""
        try:
            self.bus_event(event)
        except Exception:  # noqa: BLE001 - nothing of the session may break the cover's handling of the bus.
            LOGGER.exception("Calibration of %s could not handle %s", self.cover.entity_id, event)
            try:
                self.interrupt("internal_error")
            except Exception:  # noqa: BLE001
                LOGGER.exception("Calibration of %s could not be interrupted", self.cover.entity_id)

    def bus_event(self, event: Any) -> None:
        self.reservation.observe(event)
        if self.closed:
            return
        if self.phase in BACK:
            self.back_event(event)
            return
        if self.lift_stop is not None:
            self.lift_stop_event(event)
            return
        if self.phase == "confirm_lift" and event.is_closing:
            # At its rest, after the closing run or a close back: the close's own late statuses (or a wall switch's
            # close) move nothing further down. Only `open` starts the lift run.
            return
        # The direction of a full run that has just ended (True: opening), None once ECHO_WINDOW has passed.
        late = self.run_end[0] if self.run_end and monotonic() - self.run_end[1] < ECHO_WINDOW else None
        if self.phase not in {*RUNS, *RUNS.values()} and late is not None and (event.is_opening if late else event.is_closing):
            # The run just ended at the actuator's own stop; a status of that run still on its way (a reply to a
            # status sweep sent before the stop) changes nothing.
            return
        if self.phase not in {*RUNS, *RUNS.values()}:
            if self.active and (event.is_opening or event.is_closing):
                self.interrupt("unexpected_movement")
            return
        opening = self.phase in RISING
        expected = event.is_opening if opening else event.is_closing
        opposite = event.is_closing if opening else event.is_opening
        if self.phase in RUNS:
            if expected and self.armed:
                self.phase = RUNS[self.phase]
                # A command translation standing for a missing status dates the run on its arrival.
                self.started_at = monotonic() - event_age(event)
                self.arm_deadline(self.travel_seconds, self.travel_reason)
                # When the session learnt of the movement; the anchor itself may be dated earlier.
                self.movement_seen = monotonic()
                self.emit()
            elif expected or opposite:
                self.interrupt("unexpected_movement")
            elif event.state == 0:
                self.stop_before_movement(event)
        elif opposite or (expected and self.motor_stopped):
            # Once the motor has stopped the run expects no movement at all: a new one in
            # either direction (a wall switch, a scenario) leaves its dated stop meaningless.
            self.interrupt("unexpected_movement")
        elif not expected:
            if event.state == 0 and self.is_status_reply(event):
                # The reply to a status request: as read on the command session (the gateway marks it; its copy on
                # the monitor session comes too), or one of ours sent before the run's start (the actuator answered
                # before its motor started). Not its stop: neither "already at the end stop" nor the end.
                return
            if event.state == 0 and self.basic_run() and monotonic() - cast(float, self.started_at) < ALREADY_THERE_SECONDS:
                self.already_there()
            elif event.state == 0 and self.stop_awaits_endpoint():
                # A stop status this close to the motor start is the gateway relaying a
                # stop around our own direction command, not the end of the travel: it
                # must not date the run, and the real end stop will. Calibration commands
                # bypass the cover's own command path, so its echo window is never open
                # for them; the same ECHO_WINDOW is measured here from the run's anchor,
                # which follows the write. No real travel is that short.
                if monotonic() - cast(float, self.started_at) >= ECHO_WINDOW:
                    self.motor_stop()
            else:
                self.interrupt("unexpected_stop")

    def status_requested(self, message: Any) -> None:
        """A status request of ours (WHO 2) is queued for the gateway: it counts from now (never after its write, which
        the gateway reports only once answered) if it reaches this cover's actuator: its own point, the general address,
        its area, or a group (whose members are not known here)."""
        where = str(getattr(message, "where", ""))
        point = str(self.cover._where)
        if (where in {point, "0", "00"} or where.startswith("#")
                or (len(where) == 1 and len(point) == 2 and point[0] == where)):
            # The reservation counts every one until the gateway is released, even after the session closes.
            self.reservation.asked()
            if not self.closed:
                self.status_asked.append(monotonic())

    def stop_before_movement(self, event: Any) -> None:
        """A stop status before the run's movement is learnt: as the monitor session reads it (unmarked, not a command
        translation), it may be the reply to one of our status requests, and answers the oldest still open, so the
        actuator's own stop after the movement is never taken for that reply. It changes nothing else."""
        if getattr(event, "myhome_status_reply", False) is True or getattr(event, "translated", False) is True:
            return
        now = monotonic()
        self.status_asked = [asked for asked in self.status_asked if now - asked <= STATUS_REPLY_SECONDS]
        if self.status_asked:
            self.status_asked.pop(0)

    def is_status_reply(self, event: Any) -> bool:
        """A stop status is the reply to a status request, not the motor stopping: the copy read on the command session,
        which the gateway marks and which may come late, or the monitor session's copy of a reply to one of our
        requests sent before the movement (`answers_status_request`, which it consumes: evaluated once per event)."""
        return getattr(event, "myhome_status_reply", False) is True or self.answers_status_request()

    def answers_status_request(self) -> bool:
        """A stop status in a run is the reply to one of our status requests, sent before the session learnt of the
        run's movement (the bus's movement status; a movement dated earlier, as a command translation standing for a
        status never relayed, still counts from when it was learnt) and not answered yet: the actuator may have
        answered "stopped" before its motor started. Each request answers once: the actuator's own stop after it (at
        the end stop it already stood at, or at the arrival) still counts. A request sent after that is answered
        with what the motor does then: its reply is the truth and counts as any stop status. Since the reply can come at
        most STATUS_REPLY_SECONDS after the request, an older one is forgotten."""
        now = monotonic()
        self.status_asked = [asked for asked in self.status_asked if now - asked <= STATUS_REPLY_SECONDS]
        seen = self.movement_seen if self.movement_seen is not None else self.started_at
        for asked in self.status_asked:
            if asked <= cast(float, seen):
                self.status_asked.remove(asked)
                return True
        return False

    def stop_awaits_endpoint(self) -> bool:
        """The run ends with a motor stop that the operator then confirms with `endpoint`.

        Guided `opening`/`closing` runs: the operator is told to confirm when the motor
        stops by itself, so the actuator's own stop status usually arrives first. Automatic
        mode handles its stop statuses before reaching here. In the `lifting` run of a basic
        calibration the stop is dated the same way (`motor_stopped`), but nothing confirms it:
        the lift-off can no longer be marked there, and `repeat` closes the cover and runs it again.
        """
        return self.mode == "guided"

    def motor_stop(self) -> None:
        """A stop status in a confirmed run: date it and wait for `endpoint`.

        It is not an endpoint by itself: nothing is recorded or confirmed, the run's
        deadline stays armed (no confirmation still ends in its timeout) and the operator
        can still discard the run with Stop. A repeated stop status changes nothing; one
        after `endpoint` reaches a phase that ignores it. On a timer actuator the stop
        status near 60 s is its relay releasing: that is still the time the operator saw
        and is asked to confirm.
        """
        if self.motor_stopped:
            return
        self.motor_stopped = True
        self.stopped_elapsed = round(monotonic() - cast(float, self.started_at), 2)
        if self.stop_arrives():
            # The actuator stopped by itself at the end stop: the run is over, timed to that stop.
            try:
                self.arrive()
            except ProfileError:
                return  # Not a possible time: already interrupted (`invalid_measurement`).
            self.arrival_sequence = self.sequence
            return
        self.emit()
        self.stop_sequence = self.sequence

    def at_relay_timer(self) -> bool:
        """The stop came when an actuator's relay timer runs out (#837): the motor may have run on past the end stop."""
        return any(low <= cast(float, self.stopped_elapsed) <= high for low, high in TIMER_STOP_WINDOWS)

    def stop_arrives(self) -> bool:
        """The actuator's own stop ends the run by itself (`arrive`) instead of waiting for the operator's `endpoint`:
        in the basic calibration's full runs, unless it came at a relay timer."""
        return self.basic_run() and not self.at_relay_timer()

    def arrive(self) -> None:
        """The run ends at the actuator's stop, as if `endpoint` had been sent at once."""
        self.endpoint()

    def basic_run(self) -> bool:
        """A full run of the basic calibration (the guided session started with `slats`, both directions): the runs whose
        actuator's own stop ends them and that refuse a start at the end stop. Other guided sessions (one
        direction, older clients) keep waiting for the operator's confirmation."""
        return self.basic_session() and self.phase in {"opening", "closing"}

    def basic_session(self) -> bool:
        """The basic calibration: the guided session started with `slats`, both directions."""
        return self.mode == "guided" and self.slats is not None and not self.direction

    def already_there(self) -> None:
        """The actuator stopped right after the start: the cover was already at the end stop it was sent to (HA's
        state was stale). Nothing is measured; the run is refused (`already_at_end`) and the session waits where it
        started, Stop written; `repeat` takes the cover to where the run starts (the ascent from the rest, the descent
        from the top) and the run can be made again."""
        cast(asyncio.TimerHandle, self.deadline).cancel()
        opening = self.phase == "opening"
        self.started_at = None
        self.forget_motor_stop()
        self.queue_stop()
        self.confirm_position(100 if opening else 0)
        self.phase = "confirm_closed" if opening else "confirm_open"
        self.reason = "already_at_end"
        self.emit()

    def press_across_stop(self, msg: dict[str, Any]) -> bool:
        """An `endpoint` sent before the stop's view reached the client still confirms the run.

        The stop status publishes a new view, so a press made while that frame or that
        view was in flight carries the sequence just before it. It is accepted only for
        `endpoint`, with exactly that sequence, and while nothing else has changed since the
        stop (no other transition, ownership change included, has moved the sequence on).
        """
        return (msg["action"] == "endpoint" and self.motor_stopped and self.sequence == self.stop_sequence
                and msg.get("sequence") == self.sequence - 1)

    def press_after_arrival(self, msg: dict[str, Any]) -> bool:
        """An `endpoint` sent before the view of a run ended at the actuator's own stop reached the client: the press
        has nothing left to confirm and is answered with the view, not refused. Only with exactly the sequence just
        before that view, and while nothing else has changed since."""
        return (msg["action"] == "endpoint" and self.arrival_sequence is not None and self.sequence == self.arrival_sequence
                and msg.get("sequence") == self.sequence - 1)

    def forget_motor_stop(self) -> None:
        self.motor_stopped = False
        self.stopped_elapsed = self.stop_sequence = None

    def run_time(self) -> tuple[float, str]:
        """The measured seconds of a confirmed run, and what ended them (provenance `ended_by`).

        Confirmed after the bus reported the stop: the motor had certainly stopped by then,
        so the stop is the end and the operator's reaction is left out. Confirmed earlier:
        the confirmation is the end, and a stop status arriving afterwards is ignored.
        """
        if self.motor_stopped:
            return cast(float, self.stopped_elapsed), "bus_stop"
        return round(monotonic() - cast(float, self.started_at), 2), "press"

    def endpoint(self) -> None:
        if self.phase not in {"opening", "closing"}:
            raise ProfileError("calibration_step")
        direction = "opening" if self.phase == "opening" else "closing"
        elapsed, ended_by = self.run_time()
        try:
            elapsed = travel_time(elapsed)
        except vol.Invalid as error:
            self.interrupt("invalid_measurement")
            raise ProfileError("invalid_profile") from error
        self.values[f"{direction}_time"] = elapsed
        self.provenance[direction] = evidence("guided", self.cover.unique_id, ended_by)
        self.run_end = (direction == "opening", monotonic())
        # A closing run repeated after the lift-off keeps it while it is still shorter than both full runs.
        if "lift" in self.samples and self.samples["lift"] >= min(self.values["opening_time"], self.values["closing_time"]):
            self.samples.pop("lift")
            self.lift_evidence = None
        self.phase = ("confirm_open" if direction == "opening" and not self.direction
                      else "confirm_lift" if self.slats and "lift" not in self.samples else "review")
        self.started_at = None
        self.forget_motor_stop()
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.queue_stop()
        # The operator explicitly confirmed this physical endpoint. Bus Stop alone
        # cannot establish it, even when it dated the run. Keep the existing travel
        # settings until Save.
        self.confirm_position(100 if direction == "opening" else 0)
        self.emit()

    def start_lift(self) -> None:
        """Basic calibration with slats: from the rest the cover rises until the operator marks the lift-off."""
        self.confirm_position(0)
        self.reason = None
        self.queue_move("open", "starting_lift")

    def mark_lift(self) -> None:
        """The bottom edge left its rest: this press is the Stop of the lift run.

        Timed from the same anchor as the full runs (the bus's movement status), to the press. An
        accepted lift-off leads to review with the cover where it stopped: nothing closes it again.
        A lift-off that is not shorter than both full times is rejected
        (`lift_not_shorter`): nothing is kept, the session closes the cover again and the lift run
        can be run again. After a stop status from the bus the lift-off can no longer be marked.
        """
        if self.phase != "lifting" or self.motor_stopped:
            raise ProfileError("calibration_step")
        lift = round(monotonic() - cast(float, self.started_at), 2)
        accepted = lift < min(self.values["opening_time"], self.values["closing_time"])
        self.reason = None if accepted else "lift_not_shorter"
        if accepted:
            self.samples["lift"] = lift
            self.lift_evidence = evidence("guided", self.cover.unique_id)
            self.stop_at_lift_off()
            return
        self.risen = lift
        self.stop_and_go_back("reclosing")
        raise ProfileError("lift_not_shorter")

    def stop_at_lift_off(self) -> None:
        """The accepted lift-off: Stop on the bus, and review with the cover where it stopped.

        The Stop must still reach the bus: never written (`not_delivered`) or not within
        STOP_QUEUE_SECONDS (`not_stopped`), the session ends as in a run. Until the bus reports
        the stop, or ECHO_WINDOW after the write, the lift run's own statuses change nothing.
        """
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.phase, self.lift_stop = "review", "stopping"
        self.started_at = None
        self.forget_motor_stop()
        token = self._motion_token = object()
        written = self.queue_stop()
        if written is None:
            self.interrupt("stop_queue_full", send_stop=False)
            return
        self.arm_deadline(STOP_QUEUE_SECONDS, "not_stopped")
        written.add_done_callback(lambda future: self.lift_off_stop_written(future, token))
        self.confirm_lifted()
        self.emit()

    def lift_off_stop_written(self, future: asyncio.Future[float], token: object) -> None:
        if self._motion_token is not token or self.lift_stop != "stopping" or self.closed:
            return
        if future.cancelled():
            self.interrupt("not_delivered")
            return
        self.lift_stop = "written"
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.deadline = self.hass.loop.call_later(max(0.0, future.result() + ECHO_WINDOW - monotonic()),
                                                  self.lift_off_stopped)

    def lift_off_stopped(self) -> None:
        """The motor stopped at the lift-off: from now on any movement is foreign, as in every review."""
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.lift_stop = None

    def lift_stop_event(self, event: Any) -> None:
        """The bus after the lift-off's Stop: a close is foreign; a rise is the lift run's own.

        A rise after the Stop was written means the motor still runs: only its stop status ends the
        wait now, within STOP_QUEUE_SECONDS (`not_stopped`), as after the geometry session's Stops.
        """
        if event.is_closing:
            self.interrupt("unexpected_movement")
        elif event.is_opening:
            if self.lift_stop == "written":
                self.arm_deadline(STOP_QUEUE_SECONDS, "not_stopped")
        elif event.state == 0 and self.lift_stop == "written" and not self.is_status_reply(event):
            # A reply to a status request is not the lift-off's stop: the actuator's own stop status still comes.
            self.lift_off_stopped()

    def confirm_lifted(self) -> None:
        """At the lift-off: 0 % (the edge on its sill) with the slats open, as far as the cover's model knows slats."""
        self.confirm_position(0)
        self.lifted = True
        motion = self.cover._motion
        if motion.model is not None:
            motion.position = MotionPosition(0.0, 1.0)

    def repeat_lift(self) -> None:
        """Run the lift run again: the session closes the cover back (Stop first), then waits for `open` (`confirm_lift`).

        From review the cover is where the lift-off stopped it; during the lift run (also after a
        stop status from the bus) it is stopped first. The full runs stay. The lift run never starts by
        itself, after a repeat either: only the operator's `open` starts it.
        """
        if not self.slats or self.phase not in {"lifting", "review"}:
            raise ProfileError("calibration_step")
        if self.phase == "review":
            risen = self.samples["lift"]
        elif self.motor_stopped:
            risen = cast(float, self.stopped_elapsed)
        else:
            risen = round(monotonic() - cast(float, self.started_at), 2)
        self.samples.pop("lift", None)
        self.lift_evidence = None
        self.risen = risen
        self.stop_and_go_back("reclosing")

    def repeat_closing(self) -> None:
        """Run the closing run again, and only it: the session opens the cover back, then `confirm_open`.

        Once the closing run is confirmed (`confirm_lift`, or review). The opening time, its evidence
        and a lift-off already marked stay; the closing time goes until the closing run is confirmed
        again. Nothing is measured while the cover opens back.
        """
        refused = self.phase == "confirm_open" and self.reason == "already_at_end"
        if self.direction == "opening" or (self.phase not in {"confirm_lift", "review"} and not refused):
            raise ProfileError("calibration_step")
        self.values.pop("closing_time", None)
        self.provenance.pop("closing", None)
        self.reason = None
        self.stop_and_go_back("reopening")

    def repeat_opening(self) -> None:
        """The opening run again, and only it: the session closes the cover back to the rest by itself (`rehoming`),
        then `confirm_closed`. Once the opening run is confirmed (`confirm_open`), or refused because the cover was
        already at the top (`already_at_end`). The opening time and its evidence go."""
        refused = self.phase == "confirm_closed" and self.reason == "already_at_end"
        if self.direction == "closing" or (self.phase != "confirm_open" and not refused) \
                or (self.phase == "confirm_open" and "opening_time" not in self.values):
            raise ProfileError("calibration_step")
        self.values.pop("opening_time", None)
        self.provenance.pop("opening", None)
        self.reason = None
        self.stop_and_go_back("rehoming")

    def rehome_seconds(self) -> float:
        """How long the close back to the rest may last: the closing time in use and its margin."""
        closing = self.values.get("closing_time") or travel_time(self.cover.resolve_cover_settings(
            self.store.profile(self.cover.unique_id))["closing"]["value"] or MAX_TRAVEL_SECONDS)
        return round(min(MAX_TRAVEL_SECONDS, closing + max(RECLOSE_MARGIN_SECONDS, RECLOSE_MARGIN_SHARE * closing)), 2)

    def reclose_seconds(self) -> float:
        """How long the close back to the rest may last: the rise it undoes with its margin, at most a full close."""
        closing = self.values["closing_time"]
        full = closing + max(RECLOSE_MARGIN_SECONDS, RECLOSE_MARGIN_SHARE * closing)
        return round(min(full, cast(float, self.risen) * RECLOSE_RISE_SHARE + RECLOSE_RISE_SECONDS), 2)

    def reopen_seconds(self) -> float:
        """How long the open back to the top may last: the confirmed opening time and its margin."""
        opening = self.values["opening_time"]
        return round(opening + max(RECLOSE_MARGIN_SECONDS, RECLOSE_MARGIN_SHARE * opening), 2)

    def stop_and_go_back(self, phase: str) -> None:
        """A run back to an end stop (`reclosing`, `reopening`): Stop on the bus, then, once it is written and
        after a pause, the run back."""
        if self.deadline:
            self.deadline.cancel()
        self.phase, self.back_stage, self.lift_stop = phase, "stopping", None
        self.lifted = False
        self.started_at = None
        self.forget_motor_stop()
        self._motion_token = object()
        written = self.queue_stop()
        if written is None:
            self.interrupt("stop_queue_full", send_stop=False)
            return
        self.once_stopped(written, self.go_back)
        self.emit()

    def once_stopped(self, written: asyncio.Future[float], then: Callable[[], None]) -> None:
        """Once this Stop is written (within STOP_QUEUE_SECONDS) and after a pause, `then`: never a reversal on the fly."""
        token = self._motion_token
        self.arm_deadline(STOP_QUEUE_SECONDS, "not_stopped")

        def done(future: asyncio.Future[float]) -> None:
            if self._motion_token is not token or self.phase not in BACK:
                return
            if future.cancelled():
                self.interrupt("not_delivered")
                return
            cast(asyncio.TimerHandle, self.deadline).cancel()
            self.settle = self.hass.loop.call_later(RECLOSE_SETTLE_SECONDS, then)
        written.add_done_callback(done)

    def go_back(self) -> None:
        """After the pause: the run back, through the same guarded queue as every run."""
        self.settle = None
        if self.phase not in BACK or self.store.calibration is not self:
            return
        self.back_stage = "starting"
        try:
            self.queue_move(BACK[self.phase], self.phase)
        except ProfileError:
            return  # Already interrupted (`command_queue_full`).

    def back_seconds(self) -> float:
        return {"reclosing": self.reclose_seconds, "rehoming": self.rehome_seconds}.get(self.phase, self.reopen_seconds)()

    def back_event(self, event: Any) -> None:
        """A run back to an end stop, as the bus reports it.

        Until it starts, the previous run's own statuses may still arrive (its movement, its stop)
        and change nothing; one in our direction before ours is foreign. Once it runs, the opposite
        direction is foreign, and a stop status ends it, unless it is the gateway's echo of our own
        command (ECHO_WINDOW). In the basic calibration (the close to the rest before the ascent, and the runs back
        of a repeat) a stop within ALREADY_THERE_SECONDS of the movement is the actuator stopping at the end stop the
        cover already stood at, as in the full runs: the run back is over at once. A reply to one of our status
        requests (`answers_status_request`) is not that stop.
        """
        opening = self.phase == "reopening"
        expected = event.is_opening if opening else event.is_closing
        opposite = event.is_closing if opening else event.is_opening
        if expected:
            if self.back_stage == "starting" and self.armed:
                self.back_stage = "running"
                # The movement learnt now: our status requests sent before it may still be answered "stopped".
                self.started_at = self.movement_seen = monotonic()
                cast(asyncio.TimerHandle, self.deadline).cancel()
                self.deadline = self.hass.loop.call_later(self.back_seconds(), self.back_done)
                self.emit()
            elif self.back_stage != "running":
                self.interrupt("unexpected_movement")
        elif self.back_stage == "running":
            elapsed = monotonic() - cast(float, self.started_at)
            if opposite:
                self.interrupt("unexpected_movement")
            elif event.state != 0:
                return
            elif (elapsed >= ECHO_WINDOW or self.basic_session() and elapsed < ALREADY_THERE_SECONDS) \
                    and not self.is_status_reply(event):
                self.back_done()
        elif event.state == 0:
            self.stop_before_movement(event)

    def back_done(self) -> None:
        """At the end stop: its stop status came, or the time allowed for the run back has passed.

        Stop is written either way (a timer actuator keeps its relay on). Opened back: the closing
        run waits for the operator (`confirm_open`). Closed back (a repeat, or a lift-off refused): the
        lift run waits for the operator's `open` (`confirm_lift`): nothing more moves by itself.
        """
        cast(asyncio.TimerHandle, self.deadline).cancel()
        self.started_at = None
        if self.queue_stop() is None:
            # No Stop could be queued: a timer actuator's relay may still be on. Nobody waits for the operator.
            self.interrupt("stop_queue_full", send_stop=False)
            return
        opened = self.phase == "reopening"
        self.confirm_position(100 if opened else 0)
        rehomed = self.phase == "rehoming"
        self.back_stage = self.risen = None
        self.phase = "confirm_open" if opened else "confirm_closed" if rehomed else "confirm_lift"
        self.emit()

    def confirm_position(self, position: Any) -> None:
        cover = self.cover
        cover._cancel_stop_task()
        cover._attr_current_cover_position = cover._start_position = position
        cover._move_start_time = None
        cover._motion.confirm(position)
        cover._attr_is_opening = cover._attr_is_closing = False
        cover._attr_is_closed = position == 0
        cover.async_write_ha_state()

    async def action(self, msg: dict[str, Any]) -> Any:
        action = msg["action"]
        if action == "cancel":
            self.close()
            return self.view()
        if action == "stop":
            # In review the endpoints are fixed by the user's taps: Stop is still written, the
            # completed measurements stay even before the bus confirms the last stop, and
            # Cancel remains the way to discard them. A paused cycle keeps them as well and
            # stays paused. During a run Stop invalidates it.
            if self.phase not in {"review", "paused"}:
                self.interrupt("stopped", send_stop=False)
            self.queue_stop()
            self.emit()
            return self.view()
        if action == "heartbeat":
            self.touch()
            return self.view()
        if action == "detach":
            self.detach(self.attachment)
            return self.view()
        if self.active and self.press_after_arrival(msg):
            return self.view()
        if not self.active or (msg.get("sequence") != self.sequence and not self.press_across_stop(msg)):
            raise ProfileError("calibration_step")
        entry, entity = target(self.hass, self.entry_id, self.cover.entity_id)
        if not snapshot(self.hass, self.store, entry, entity)["writable"]:
            # A refused `continue` keeps the pause and its measurements: nothing has moved.
            if action != "continue":
                self.interrupt("cover_unavailable")
            raise ProfileError("cover_unavailable")
        if action == "continue":
            self.proceed()
        elif self.mode == "geometry" and action not in {"save", "preview_save"}:
            self.geometry_action(msg)
        elif action == "run":
            if self.mode != "automatic" or self.phase != "confirm_automatic":
                raise ProfileError("calibration_step")
            self.queue_move("open")
        elif action in {"open", "close", "endpoint", "lift", "repeat"} and self.mode != "guided":
            raise ProfileError("calibration_step")
        elif action == "open" and self.phase == "confirm_lift":
            self.start_lift()
        elif action in {"open", "close"}:
            self.move(action)
        elif action == "endpoint":
            self.endpoint()
        elif action == "lift":
            self.mark_lift()
        elif action == "repeat" and msg.get("direction") == "closing":
            self.repeat_closing()
        elif action == "repeat" and msg.get("direction") == "opening":
            self.repeat_opening()
        elif action == "repeat" and "direction" not in msg:
            self.repeat_lift()
        elif action == "repeat":
            raise ProfileError("calibration_step")
        elif action in {"next", "reading", "check"}:
            raise ProfileError("calibration_step")
        elif action == "preview_save":
            if self.phase != "review" or msg.get("save_mode") != "shared":
                raise ProfileError("calibration_step")
            if "shared" not in self.view()["save_modes"]:
                raise ProfileError("profile_not_source")  # Only the profile's source updates it.
            preview = await self.shared_save(msg, preview=True)
            return {**self.view(), "save_preview": preview}
        elif action == "save":
            if self.phase != "review":
                raise ProfileError("calibration_step")
            if msg.get("save_mode") == "shared" and "shared" not in self.view()["save_modes"]:
                raise ProfileError("profile_not_source")  # Only the profile's source updates it.
            if msg.get("save_mode", "new") not in self.view()["save_modes"]:
                raise ProfileError("invalid_profile")
            self.phase = "saving"
            self.emit()
            try:
                revision = await self.save_profiles(msg)
            except (ProfileError, vol.Invalid, OSError):
                if self.store.calibration is self and not self.closed:
                    self.phase = "review"
                    self.emit()
                raise
            self.revision = revision
            self.phase = "saved"
            self.emit()
            self.close()
        return self.view()


    async def shared_save(self, msg: dict[str, Any], *, preview: bool = False) -> Any:
        """`shared`: the measurement saved into the profile the cover follows, after a revision-bound preview."""
        from .cover_calibration_save import save_measurement
        return await save_measurement(self, msg, preview=preview)

    def geometry_action(self, msg: dict[str, Any]) -> None:
        """Only the geometry subclass implements these protocol actions."""
        raise ProfileError("calibration_step")

    async def save_profiles(self, msg: dict[str, Any]) -> Any:
        if msg.get("save_mode", "new") != "new":
            from .cover_calibration_save import save_measurement
            revision = await save_measurement(self, msg)
            # Saved into the profile the cover follows: the same re-anchoring as a new profile.
            self._apply_saved_times()
            return revision
        travel = self.store.data["covers"].get(self.cover.unique_id, {}).get("travel_cm")
        geometry = self.base_geometry()
        result = await write_profile(self.hass, {
            "entry_id": self.entry_id, "entity_id": self.cover.entity_id,
            "revision": self.revision, "action": "save", "profile_id": None,
            "profile": {"name": self.profile_name(msg), **self.values,
                        **({"reference_travel_cm": travel} if travel is not None else {}),
                        **({"geometry": geometry} if geometry is not None else {})},
        }, calibration=self)
        self._apply_saved_times()
        return result["revision"]

    def profile_name(self, msg: dict[str, Any]) -> str:
        """A new profile is named as the panel says (an empty name is refused by the store), or, without a name, after
        its cover (the name the panel offers)."""
        if "name" in msg:
            return str(msg["name"])
        from homeassistant.helpers import entity_registry as er
        record = er.async_get(self.hass).async_get(self.cover.entity_id)
        state = self.hass.states.get(self.cover.entity_id)
        # The name Home Assistant shows (as the panel's cards), else the registry's, else the entity id.
        name = (state and state.attributes.get("friendly_name")) or (record and (record.name or record.original_name))
        return str(name or self.cover.entity_id).strip()[:64] or self.cover.entity_id[:64]

    def shared_geometry(self, profile: dict[str, Any], travel: float | None) -> tuple[dict[str, float], dict[str, Any]] | None:
        """What a basic calibration saved into the profile the cover follows gives its geometry; None: nothing.

        The lift-off measured here is the profile's slat time (scaled back to the profile's reference travel, as the
        times are), with the profile's rolls (1 where it has none) and their evidence, so it is never lost, on a
        refinement too; without slats it is 0, except on a profile whose rolls were both measured or entered (a
        refinement): that one keeps its whole geometry. A session that was never asked about slats (one direction,
        automatic) changes nothing.
        """
        if self.slats is None or self.direction:
            return None
        geometry, evidence_ = profile.get("geometry"), profile.get("geometry_provenance", {})
        if not self.slats and geometry and all(evidence_.get(key, UNKNOWN)["source"] in ROLL_SOURCES for key in ROLLS):
            # A refinement's whole geometry stays when no lift-off was measured here. With one, its slat time is the
            # lift-off just measured (rescaled as the times), and the rolls the tape measured stay.
            return None
        if self.slats:
            lift = self.samples.get("lift")
            if lift is None:
                raise ProfileError("lift_missing")
            slat_evidence = dict(cast(dict[str, Any], self.lift_evidence))
        else:
            lift, slat_evidence = 0.0, evidence("guided", self.cover.unique_id)
        reference = profile.get("reference_travel_cm")
        ratio = travel / reference if reference is not None and travel is not None else 1.0
        rolls = {key: float(geometry[key]) if geometry else 1.0 for key in ROLLS}
        return ({"slat_time_s": lift / ratio, **rolls},
                {"slat_time_s": slat_evidence,
                 **{key: copy.deepcopy(evidence_.get(key, UNKNOWN)) if geometry else dict(UNKNOWN) for key in ROLLS}})

    def base_geometry(self) -> dict[str, float] | None:
        """The geometry of a basic calibration saved as a new profile, or None for a times-only profile.

        With slats the lift-off of the lift run is the slat time; without, there is none (0). The
        rolls are not measured: a roll that a previous refinement measured (guided) or that was
        entered (manual) stays as this cover uses it now (a new calibration measures the times again
        and keeps the refinement), the evidence with it; any other is 1, the model's linear case
        (after the slats the edge follows the time), recorded as unknown evidence, never as a
        measurement. "Without slats" said in this session is a slat time of 0 (with rolls 1 the
        cover moves exactly as the linear model); only a session never asked about slats (None)
        without such a roll saves a times-only profile.
        """
        profile = self.store.profile(self.cover.unique_id)
        rolls = roll_settings(self.cover.resolve_cover_settings(profile), profile)
        kept = {key: rolls[key] for key in ROLLS if key in rolls and rolls[key]["provenance"]["source"] in ROLL_SOURCES}
        if self.slats:
            lift = self.samples.get("lift")
            if lift is None:
                raise ProfileError("lift_missing")
            if lift >= min(self.values["opening_time"], self.values["closing_time"]):
                raise ProfileError("lift_not_shorter")
            # Dated by its own run, as the times are.
            slat, slat_evidence = lift, dict(cast(dict[str, Any], self.lift_evidence))
        elif kept or self.slats is False:
            # Without slats, said in this session (or as the refinement of a cover without slats): none.
            slat, slat_evidence = 0.0, evidence("guided", self.cover.unique_id)
        else:
            return None
        self.geometry_provenance = {"slat_time_s": slat_evidence, **{
            key: copy.deepcopy(kept[key]["provenance"]) if key in kept else dict(UNKNOWN) for key in ROLLS}}
        # A roll scaled to a taller cover may exceed what a profile stores (1-5): the nearest it can.
        return {"slat_time_s": slat, **{key: min(ROLL_MAX, float(kept[key]["value"])) if key in kept else 1.0
                                        for key in ROLLS}}

    def _apply_saved_times(self) -> None:
        """L-36: as the geometry session: in review the motor is still at the end stop the operator confirmed, so the
        new times apply now and that end stop stays the anchor."""
        position = self.cover._attr_current_cover_position
        self.cover._attr_is_opening = self.cover._attr_is_closing = False
        self.cover._move_start_time = None
        self.cover._apply_pending_cover_profile()
        if self.lifted:
            # Saved where the lift-off stopped it, the slats open, in the model just applied.
            self.confirm_lifted()
        elif position in (0, 100):
            self.confirm_position(position)


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


def replay(store: Any, connection: Any, msg: dict[str, Any]) -> Any:
    """A client repeating its own start reads its live session; no command is sent.

    A replay that names its session (`session_id`) never creates another one.
    """
    session = store.calibration
    if (session is None or session.closed or msg.get("client_id") not in session.known
            or not session.same_request(msg) or msg.get("session_id", session.id) != session.id):
        if "session_id" in msg:
            raise ProfileError("calibration_expired")
        return None
    session.attach(connection, msg["id"], msg["client_id"])
    return session


async def begin(hass: Any, connection: Any, msg: dict[str, Any]) -> Any:
    entry, entity = target(hass, msg["entry_id"], msg["entity_id"])
    store = get_store(hass, entry.entry_id)
    async with store.lock:
        await store.load()
        replayed = replay(store, connection, msg)
        if replayed is not None:
            return replayed
        if store.calibration is not None:
            raise ProfileError("calibration_busy")
        cover = ready_cover(hass, store, entry.entry_id, msg["entity_id"])
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
                if setting["origin"] == "native_fallback" and setting["provenance"]["origin_unique_id"] is None:
                    raise vol.Invalid("Native evidence has no recorded origin")
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
        if msg.get("keep_times") and (msg.get("mode") != "geometry" or precision_class(
                cover.resolve_cover_settings(store.profile(cover.unique_id)),
                store.data["covers"].get(cover.unique_id), store.profile(cover.unique_id)) == "none"):
            # The fine-tuning on the times already saved needs times measured (or inherited) to keep.
            raise ProfileError("invalid_profile")
        if msg.get("mode") == "geometry":
            from .cover_calibration_geometry import GeometryCalibrationSession
            session_type = GeometryCalibrationSession
            options = {"slats": msg.get("slats", True), "keep_times": msg.get("keep_times", False)}
        elif "slats" in msg:
            # Basic calibration: the guided session over both runs, and the lift run after them if the cover has slats.
            if msg.get("mode", "guided") != "guided" or "direction" in msg:
                raise ProfileError("invalid_profile")
            options = {"slats": msg["slats"]}
        if msg.get("home") and "slats" not in msg:
            raise ProfileError("invalid_profile")
        session = session_type(hass, store, entry, cover, connection, msg["id"], client_id=msg.get("client_id"), **options)
        session.origin = msg.get("origin")
        store.calibration = cover._calibration = session
        session.subscribe()
        if msg.get("home"):
            # The basic calibration starts from the rest the session itself has reached: it closes the cover first
            # (`rehoming`: the stop status at the bottom, or the closing time in use and its margin), never trusting a
            # "closed" Home Assistant may have kept from before; then `confirm_closed`, the ascent ready.
            session.stop_and_go_back("rehoming")
        return session


@websocket_command(as_any({
    vol.Required("type"): WS_START, vol.Required("entry_id"): str,
    vol.Required("entity_id"): str, vol.Required("revision"): vol.All(int, vol.Range(min=0)),
    vol.Optional("mode"): vol.In(["guided", "automatic", "geometry"]),
    vol.Optional("direction"): vol.In(["opening", "closing"]),
    vol.Optional("slats"): bool,
    vol.Optional("keep_times"): bool,
    vol.Optional("home"): bool,
    vol.Optional("client_id"): vol.All(str, vol.Length(min=1, max=64)),
    vol.Optional("session_id"): vol.All(str, vol.Length(min=1, max=64)),
    vol.Optional("origin"): vol.In(["wizard"]),
}))
@require_admin
@async_response
async def ws_start(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    try:
        session = await begin(hass, connection, msg)
    except (ProfileError, vol.Invalid, OSError) as error:
        send_error(connection, msg, error)
        return
    connection.send_result(msg["id"])
    session.announce(connection, msg["id"])


def send_error(connection: Any, msg: dict[str, Any], error: Any) -> None:
    code = str(error) if isinstance(error, ProfileError) else "invalid_profile" if isinstance(error, vol.Invalid) else "storage_error"
    connection.send_error(msg["id"], code, code)


def shown_screen(value: dict[str, Any]) -> dict[str, Any]:
    """L-25: a panel's screen description: plain data, small (it travels with every view)."""
    if len(json.dumps(value)) > 2048:
        raise vol.Invalid("screen too large")
    return value


@websocket_command(as_any({
    vol.Required("type"): WS_ACTION, vol.Required("entry_id"): str,
    vol.Required("session_id"): str,
    vol.Required("action"): vol.In(["run", "open", "close", "endpoint", "stop", "cancel", "save", "preview_save", "heartbeat", "detach", "next", "lift", "reading", "repeat", "continue", "check", "show"]),
    vol.Optional("screen"): vol.All(dict, shown_screen),
    vol.Optional("reading_cm"): vol.Any(int, float),
    vol.Optional("direction"): vol.In(["opening", "closing"]),
    vol.Optional("target_cm"): vol.Any(int, float),
    vol.Optional("attachment"): str,
    vol.Optional("sequence"): vol.All(int, vol.Range(min=0)),
    vol.Optional("name"): str,
    vol.Optional("save_mode"): vol.In(["new", "cover", "shared"]),
    vol.Optional("confirmation"): str,
    vol.Optional("names"): vol.All([str], vol.Length(min=1, max=20)),
}))
@require_admin
@async_response
async def ws_action(hass: Any, connection: Any, msg: dict[str, Any]) -> None:
    store = hass.data.get(DATA_KEY, {}).get(msg["entry_id"])
    session = store.calibration if store else None
    subscriber: Subscriber | None = None
    if session is None or session.id != msg["session_id"]:
        valid = False
    elif session.client_id:
        subscriber = session.subscribers.get(msg.get("attachment", ""))
        valid = subscriber is not None and subscriber.connection is connection
    else:
        valid = session.connection is connection and session.listener
    if session is None or not valid:
        connection.send_error(msg["id"], "calibration_expired", "Calibration session is not owned by this connection")
        return
    try:
        result = await (session.perform(subscriber, msg) if subscriber else session.action(msg))
    except (ProfileError, vol.Invalid, OSError) as error:
        send_error(connection, msg, error)
    else:
        connection.send_result(msg["id"], result)


@callback
def register_api(hass: Any) -> None:
    from .cover_calibration_batch import ws_batch_start, ws_targets
    from .cover_calibration_recovery import ws_resume
    websocket_api.async_register_command(hass, ws_resume)
    websocket_api.async_register_command(hass, ws_batch_start)
    websocket_api.async_register_command(hass, ws_targets)
    websocket_api.async_register_command(hass, ws_start)
    websocket_api.async_register_command(hass, ws_action)


# Native calibrate_cover service compatibility and per-gateway trace hub.
def _gateway_key(gateway: Any) -> str:
    """Return a unique key for the gateway (MAC address or object id)."""
    return str(getattr(gateway, "mac", "") or id(gateway))


def _normalize_mac(mac: Any) -> str | None:
    """One spelling for a gateway MAC so frames and requests compare equal."""
    if mac is None or str(mac).strip() == "":
        return None
    return dr.format_mac(str(mac))


def _calibration_lock(gateway: Any) -> asyncio.Lock:
    """Return the per-gateway calibration lock, rebinding if event loop changed."""
    return get_calibration_hub(gateway).lock


def _record_calibration_frame(gateway: Any, direction: str, raw: str, **extra: Any) -> None:
    """Record a frame during active calibration to the trace buffer."""
    hub = get_calibration_hub(gateway)
    hub.record_frame(direction, raw, **extra)


def get_last_calibration_trace(gateway_mac: str | None = None, hass: Any = None) -> list[dict[str, Any]]:
    """Return in-memory trace frames captured during recent cover calibrations.

    Traces are stored on each gateway's CoverCalibrationHub. When gateway_mac is
    provided, only that gateway's trace is returned (None is the only unfiltered
    read; a gateway without a MAC gets the frames recorded without one).
    """
    hubs = CoverCalibrationHub.all_hubs(hass)

    if gateway_mac is None:
        combined: list[dict[str, Any]] = []
        for hub in hubs:
            combined.extend(hub.get_trace())
        combined.sort(key=lambda f: f.get("timestamp", 0.0))
        return combined

    wanted = _normalize_mac(gateway_mac)
    for hub in hubs:
        if hub.mac == wanted:
            return hub.get_trace()
    return []


async def async_stop_cover_calibration(hass: Any = None, gateway_mac: str | None = None) -> bool:
    """Stop active and queued cover calibrations on one or all gateways."""
    stopped_any = False
    wanted_mac = _normalize_mac(gateway_mac) if gateway_mac else None

    for hub in CoverCalibrationHub.all_hubs(hass):
        if wanted_mac and hub.mac != wanted_mac:
            continue
        if await hub.async_stop():
            stopped_any = True

    return stopped_any


def _stored_calibration(config_entry: Any, device_id: str) -> dict[str, Any] | None:
    """Return the persisted calibration for a cover, if any.

    Records are keyed by the canonical device id (`04`, `04#4#01`); a record a
    migration could not move is still found under its 4-digit spelling (#147).
    """
    options = getattr(config_entry, "options", None) or {}
    stored = options.get(CONF_COVER_TRAVEL_TIMES) or {}
    entry = stored.get(str(device_id))
    if entry is None:
        base, sep, rest = str(device_id).partition("#")
        if len(base) == 2 and base[0] == "0" and base[1] != "0":
            entry = stored.get(f"00{base}{sep}{rest}")
    return dict(entry) if isinstance(entry, dict) else None


class CalibrationInterrupted(HomeAssistantError):
    """A wall-switch or scenario command interfered with a calibration run."""

    def __init__(self, name: str, cause: str) -> None:
        super().__init__(
            f"{name}: {cause}",
            translation_domain=DOMAIN,
            translation_key="calibration_interrupted",
            translation_placeholders={"name": name, "cause": cause},
        )


class CalibrationCutoff(HomeAssistantError):
    """A calibration run ended at the actuator's run-time limit, not at the end stop."""

    def __init__(self, name: str, direction: str, seconds: float, limit: float) -> None:
        self.limit = limit
        msg = (
            f"{name}: the {direction} run ended after {seconds:.1f} s, at the actuator's {limit:.0f} s "
            "run-time limit, not at the end stop; the actuator cannot measure this shutter "
            "- use the stopwatch (Stop & Save) or set travel_time manually"
        )
        placeholders = {"name": name, "seconds": f"{seconds:.1f}", "limit": f"{limit:.0f}"}
        if direction == "close":
            super().__init__(
                msg,
                translation_domain=DOMAIN,
                translation_key="calibration_actuator_cutoff_close",
                translation_placeholders=placeholders,
            )
        else:
            super().__init__(
                msg,
                translation_domain=DOMAIN,
                translation_key="calibration_actuator_cutoff_open",
                translation_placeholders=placeholders,
            )


class CoverCalibrationHub:
    """Encapsulates calibration state and serialization per gateway."""

    _registry: dict[str, CoverCalibrationHub] = {}

    def __init__(self, gateway: Any) -> None:
        self.gateway: MyHOMEGatewayHandler = gateway
        self.trace: collections.deque[dict[str, Any]] = collections.deque(maxlen=1000)
        self._lock: asyncio.Lock | None = None
        self._active_cover: Any | None = None
        self._queued_covers: set[Any] = set()
        self._register()

    def _register(self) -> None:
        CoverCalibrationHub._registry[self.key] = self
        if self.mac:
            CoverCalibrationHub._registry[self.mac] = self

    def _unregister(self) -> None:
        if CoverCalibrationHub._registry.get(self.key) is self:
            CoverCalibrationHub._registry.pop(self.key, None)
        if self.mac and CoverCalibrationHub._registry.get(self.mac) is self:
            CoverCalibrationHub._registry.pop(self.mac, None)

    @classmethod
    def all_hubs(cls, hass: Any = None) -> list[CoverCalibrationHub]:
        """Return all active hubs from hass config entries and local registry."""
        hubs: list[CoverCalibrationHub] = []
        seen: set[int] = set()
        if hass is not None and hasattr(hass, "config_entries"):
            for entry in hass.config_entries.async_entries(DOMAIN):
                runtime = getattr(entry, "runtime_data", None)
                hub = getattr(runtime, "calibration_hub", None)
                if isinstance(hub, CoverCalibrationHub) and id(hub) not in seen:
                    hubs.append(hub)
                    seen.add(id(hub))
        for hub in list(cls._registry.values()):
            if id(hub) not in seen:
                hubs.append(hub)
                seen.add(id(hub))
        return hubs

    @classmethod
    def reset_for_tests(cls) -> None:
        """Reset all hubs and clear the registry (for test fixtures)."""
        for hub in list(cls._registry.values()):
            hub.cleanup()
        cls._registry.clear()





    @property
    def mac(self) -> str | None:
        """Return the normalized gateway MAC address."""
        return _normalize_mac(getattr(self.gateway, "mac", None))

    @property
    def key(self) -> str:
        """Return a unique key for the gateway."""
        return _gateway_key(self.gateway)

    @property
    def lock(self) -> asyncio.Lock:
        """Return the asyncio.Lock, dynamically rebinding if the running loop changed."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._lock is not None:
            bound_loop = getattr(self._lock, "_bound_loop", None) or getattr(self._lock, "_loop", None)
            if (bound_loop is not None and bound_loop.is_closed()) or (
                current_loop is not None and bound_loop is not current_loop
            ):
                self._lock = None

        if self._lock is None:
            self._lock = asyncio.Lock()
            if current_loop is not None:
                setattr(self._lock, "_bound_loop", current_loop)

        return self._lock

    @property
    def active_cover(self) -> Any | None:
        """Return the currently calibrating cover for this gateway."""
        return self._active_cover

    @active_cover.setter
    def active_cover(self, cover: Any | None) -> None:
        self._active_cover = cover

    @property
    def is_calibrating(self) -> bool:
        """Return True if any cover on this gateway is actively calibrating."""
        return bool(self._active_cover is not None and getattr(self._active_cover, "_calibrating", False))

    @property
    def queued_covers(self) -> set[Any]:
        """Return set of covers waiting for calibration lock on this gateway."""
        return self._queued_covers

    def record_frame(self, direction: str, raw: str, **extra: Any) -> None:
        """Record a calibration frame to this hub's trace buffer."""
        now = dt_util.utcnow()
        frame = {
            "timestamp": time.time(),
            "iso_time": now.isoformat(),
            "gateway_mac": self.mac,
            "direction": direction,
            "raw": str(raw).strip(),
            **extra,
        }
        self.trace.append(frame)

    def get_trace(self) -> list[dict[str, Any]]:
        """Return the in-memory trace frames recorded for this gateway."""
        return list(self.trace)

    async def async_stop(self) -> bool:
        """Stop active and queued calibrations on this gateway."""
        stopped_any = False
        queued = list(self.queued_covers)
        self.queued_covers.clear()
        for c in queued:
            c._calibration_interrupted = "Calibration stopped by user"
            c._fire_calibration_event("failed", error="Calibration stopped by user")
            stopped_any = True

        active = self.active_cover
        if active is not None and getattr(active, "_calibrating", False):
            active._calibration_interrupted = "Calibration stopped by user"
            active._settles_on_timer = True
            active._motor_started.set()
            active._stopped_event.set()
            try:
                await active.async_stop_cover()
            except Exception as err:
                LOGGER.warning("Error stopping cover %s: %s", active.entity_id, err)
            stopped_any = True
            self.active_cover = None

        return stopped_any

    def cleanup(self) -> None:
        """Clean up all references when gateway unloads."""
        self._active_cover = None
        self._queued_covers.clear()
        self._lock = None
        self.trace.clear()
        self._unregister()


def get_calibration_hub(gateway: Any) -> CoverCalibrationHub:
    """Return or create the CoverCalibrationHub for a gateway handler."""
    entry = getattr(gateway, "config_entry", None)
    runtime = getattr(entry, "runtime_data", None) if entry is not None else None
    if isinstance(runtime, MyHOMERuntimeData):
        if runtime.calibration_hub is None:
            hub = getattr(gateway, "_calibration_hub", None)
            if not isinstance(hub, CoverCalibrationHub):
                hub = CoverCalibrationHub(gateway)
            runtime.calibration_hub = hub
        return runtime.calibration_hub

    if runtime is not None:
        raw_hub = getattr(runtime, "calibration_hub", None)
        if isinstance(raw_hub, CoverCalibrationHub):
            return raw_hub

    # Fallback when runtime_data is not available (e.g. mock objects in unit tests)
    hub = getattr(gateway, "_calibration_hub", None)
    if not isinstance(hub, CoverCalibrationHub):
        hub = CoverCalibrationHub(gateway)
        try:
            gateway._calibration_hub = hub
        except Exception:  # pragma: no cover - defensive
            pass
    return hub
