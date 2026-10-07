"""Support for MyHome covers."""
from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.components.cover import (
    DOMAIN as PLATFORM,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_NAME,
    STATE_CLOSED,
    STATE_OPEN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from OWNd.message import (
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNMessage,
)

from .const import (
    CALIBRATION_CUTOFF_MAX,
    CALIBRATION_CUTOFF_MIN,
    CALIBRATION_MAX_RUN,
    CALIBRATION_MIN_RUN,
    CALIBRATION_RUN_TIMEOUT,
    CALIBRATION_SETTLE,
    CONF_ADVANCED_SHUTTER,
    CONF_COVER_TRAVEL_TIMES,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_NAME,
    CONF_MANUFACTURER,
    CONF_MEMBERS,
    CONF_SLAT_TILT,
    CONF_SLAT_TIME,
    CONF_TRAVEL_TIME,
    DEFAULT_SLAT_TIME,
    DEFAULT_TRAVEL_TIME,
    DOMAIN,
    EVENT_COVER_CALIBRATION,
    LOGGER,
    MAX_SLAT_TIME,
    MIN_SLAT_TIME,
    SERVICE_CALIBRATE_COVER,
    SERVICE_RESET_COVER_TRAVEL_TIME,
    SERVICE_SET_COVER_TRAVEL_TIME,
    SERVICE_STOP_COVER_CALIBRATION,
)
from .cover_calibration import (
    CalibrationInterrupted,
    CoverCalibrationHub,
    _calibration_lock,
    _gateway_key,
    _normalize_mac,
    _record_calibration_frame,
    _stored_calibration,
    async_stop_cover_calibration,
    get_calibration_hub,
    get_last_calibration_trace,
)
from .cover_motion import (
    ECHO_WINDOW,
    MIN_TILT_PULSE,
    MOTOR_START_DELAY,
    WRITE_TIMEOUT,
    compute_freeze_position,
    compute_interpolated_position,
    is_in_echo_window,
    travel_for,
)
from .discovery import Address, DeviceContext, PlatformDiscovery, default_known_keys
from .gateway import MyHOMEGatewayHandler
from .myhome_device import MyHOMEEntity

if TYPE_CHECKING:
    from .cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover

PARALLEL_UPDATES = 0


def _clamp_slat_time(value: Any, fallback: float = DEFAULT_SLAT_TIME) -> float:
    """A slat time within MIN_SLAT_TIME..MAX_SLAT_TIME; ``fallback`` when the value is unusable."""
    try:
        seconds = float(value) if value is not None else float(fallback)
    except (TypeError, ValueError):
        seconds = float(fallback)
    return max(MIN_SLAT_TIME, min(MAX_SLAT_TIME, seconds))


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the covers of a gateway (WHO=2): registry, myhome.yaml, then bus discovery."""
    from .cover_scope import CoverFamily, CoverScope, MyHOMEScopeCover

    runtime = config_entry.runtime_data
    if runtime.calibration_hub is None:
        runtime.calibration_hub = get_calibration_hub(runtime.gateway)
    family = CoverFamily()

    def build(ctx: DeviceContext) -> MyHOMECover:
        cfg = ctx.cfg
        kwargs: dict[str, Any] = {}
        if ctx.source != "yaml":
            # Registry / discovered covers carry their measured calibration; yaml covers
            # start from the configured travel time.
            kwargs["travel_time_source"] = "yaml" if CONF_TRAVEL_TIME in cfg else "default"
            kwargs["calibration"] = _stored_calibration(config_entry, ctx.key)
        elif (stored := _stored_calibration(config_entry, ctx.key)) and stored.get(CONF_SLAT_TIME) is not None:
            # yaml covers keep their configured travel time, but a slat time set
            # with myhome.set_cover_travel_time wins until it is reset.
            kwargs["calibration"] = {CONF_SLAT_TIME: stored[CONF_SLAT_TIME]}
        cls: type[MyHOMECover] = MyHOMECover
        if (scope := CoverScope.of(ctx.address.where, ctx.address.interface, cfg.get(CONF_MEMBERS) or ())) is not None:
            cls = MyHOMEScopeCover
            kwargs["scope"] = scope
        cover = cls(
            hass=hass,
            name=cfg.get(CONF_NAME) or f"Cover {ctx.suffix}",
            entity_name=cfg.get(CONF_ENTITY_NAME),  # type: ignore
            device_id=ctx.key,
            who=ctx.who,
            where=ctx.address.where,
            interface=ctx.address.interface,  # type: ignore
            advanced=cfg.get(CONF_ADVANCED_SHUTTER, cfg.get("advanced_shutter", False)),
            slat_tilt=bool(cfg.get(CONF_SLAT_TILT, False)),
            slat_time=_clamp_slat_time(cfg.get(CONF_SLAT_TIME)),
            manufacturer=cfg.get(CONF_MANUFACTURER, "BTicino"),
            model=cfg.get(CONF_DEVICE_MODEL, "Shutter / Cover"),
            gateway=runtime.gateway,
            travel_time=int(cfg.get(CONF_TRAVEL_TIME, DEFAULT_TRAVEL_TIME)),
            **kwargs,
        )
        family.add(cover)
        return cover

    @callback
    def relay_general(message) -> None:  # type: ignore
        """A general command (WHERE=0) moves every cover."""
        runtime.router.publish("2", ("general",), message)

    @callback
    def relay_scope(message, address: Address) -> None:  # type: ignore
        """An area or group command moves the covers in it, and is the scope cover's own frame."""
        runtime.router.publish("2", [address.key, *family.keys_moved_by(address.where, address.interface)], message)

    PlatformDiscovery(
        hass, config_entry, async_add_entities,
        platform=PLATFORM, who="2", event_type=(OWNAutomationEvent, OWNAutomationCommand), build=build, announce=True,
        on_general=relay_general, on_scope=relay_scope,
        known_keys=lambda ctx: [*default_known_keys(ctx), "general"],
    ).start()

    SCHEMA_SET_COVER_TRAVEL_TIME = {
        vol.Optional("travel_time"): vol.Coerce(float),
        vol.Optional("travel_time_down"): vol.Coerce(float),
        vol.Optional("travel_time_up"): vol.Coerce(float),
        vol.Optional(CONF_SLAT_TIME): vol.All(vol.Coerce(float), vol.Range(min=MIN_SLAT_TIME, max=MAX_SLAT_TIME)),
        vol.Optional("copied_from"): cv.string,
    }

    platform = entity_platform.current_platform.get()
    if platform is not None:
        platform.async_register_entity_service(SERVICE_CALIBRATE_COVER, {}, "async_calibrate")
        platform.async_register_entity_service(SERVICE_STOP_COVER_CALIBRATION, {}, "async_stop_calibration")
        platform.async_register_entity_service(
            SERVICE_SET_COVER_TRAVEL_TIME,
            SCHEMA_SET_COVER_TRAVEL_TIME,  # type: ignore
            "async_set_travel_time",
        )
        platform.async_register_entity_service(
            SERVICE_RESET_COVER_TRAVEL_TIME,
            {},
            "async_reset_travel_time",
        )


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:  # pylint: disable=unused-argument
    """Unload cover platform."""
    runtime = getattr(config_entry, "runtime_data", None)
    if runtime is not None and runtime.calibration_hub is not None:
        runtime.calibration_hub.cleanup()
        runtime.calibration_hub = None
    return True


class MyHOMECover(MyHOMEEntity, CoverEntity):
    _attr_device_class = CoverDeviceClass.SHUTTER

    def __init__(  # type: ignore
        self,
        hass,
        name: str,
        entity_name: str,
        device_id: str,
        who: str,
        where: str,
        interface: str,
        advanced: bool,
        manufacturer: str,
        model: str,
        gateway: MyHOMEGatewayHandler,
        travel_time: int = DEFAULT_TRAVEL_TIME,
        travel_time_source: str = "default",
        calibration: dict | None = None,  # type: ignore
        slat_tilt: bool = False,
        slat_time: float = DEFAULT_SLAT_TIME,
    ):
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
        #: Set on a MyHOMEScopeCover; ``None`` for a point-to-point cover.
        self.scope: CoverScope | None = None
        self._family: CoverFamily | None = None
        self._advanced = advanced
        self._slat_tilt = slat_tilt
        self._slat_time = _clamp_slat_time(slat_time)
        self._is_tilting: bool = False
        self._tilt_start_time: float | None = None
        self._tilt_initial_position: int = 0
        self._tilt_target_position: int = 0
        self._tilt_duration: float = 0.0
        self._tilt_direction: str | None = None
        self._attr_current_cover_tilt_position: int | None = None
        # Tilt requests run one at a time; a newer target replaces one still waiting.
        self._tilt_lock = asyncio.Lock()
        self._tilt_request_seq = 0
        # Delivery futures of the running pulse's direction frame and of its
        # stop while that still waits in the send queue.
        self._pulse_written: Any = None
        self._pulse_stop_written: asyncio.Future[Any] | None = None
        # The scope cover whose pulse moves this blind's slats (see _follow_pulse).
        self._tilt_follows: MyHOMECover | None = None
        # Direction-aware travel times. `_travel_time` stays the closing (down) time
        # for compatibility; a stored calibration overrides yaml / default values.
        base_travel = float(travel_time) if travel_time else float(DEFAULT_TRAVEL_TIME)
        self._travel_time = travel_time if travel_time else DEFAULT_TRAVEL_TIME
        self._travel_time_down = base_travel
        self._travel_time_up = base_travel
        self._calibration_source = travel_time_source
        self._calibrated_at: str | None = None
        self._copied_from: str | None = None
        if calibration and calibration.get("slat_time") is not None:
            self._slat_time = _clamp_slat_time(calibration["slat_time"], self._slat_time)
        if calibration and calibration.get("down") and calibration.get("up"):
            self._travel_time_down = float(calibration["down"])
            self._travel_time_up = float(calibration["up"])
            self._travel_time = int(round(self._travel_time_down))
            self._calibration_source = str(calibration.get("source") or "measured")
            self._calibrated_at = calibration.get("measured_at")
            self._copied_from = calibration.get("copied_from") or None
        self._calibrating = False
        self._calibration_interrupted: str | None = None
        self._stopped_event: asyncio.Event = asyncio.Event()
        self._last_stop_at: float | None = None
        self._last_run: dict[str, Any] = {}  # the last completed run as measured, see _freeze_position
        self._motion_started_at: str | None = None

        # Both advanced and standard covers support SET_POSITION (standard via travel time estimation)
        self._attr_supported_features = (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        self._gateway_handler = gateway

        self._attr_extra_state_attributes = {
            "A": where[: len(where) // 2],
            "PL": where[len(where) // 2 :],
        }
        if self._interface is not None:
            self._attr_extra_state_attributes["Int"] = self._interface
        if self._slat_tilt:
            self._enable_slat_tilt()
        if not self._advanced:
            self._refresh_travel_attributes()

        self._attr_current_cover_position = 50
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_is_closed = False

        self._move_start_time: float | None = None
        # Anchor of the current run (motor start), untouched by the set_position
        # re-anchor: the last completed run is measured from it (stopwatch).
        self._run_started_at: float | None = None
        self._start_position = 50
        self._stop_task: asyncio.Task[None] | None = None

        # Echo model state (see ECHO_WINDOW): which command of ours is in
        # flight, until when relayed frames count as its echo, when the motor
        # was observed to start, and a generation counter so a stale auto-stop
        # timer never acts on a later run.
        self._pending_cmd: str | None = None
        self._echo_until: float | None = None
        self._motor_started: asyncio.Event = asyncio.Event()
        self._run_generation: int = 0

    @property
    def calibration_hub(self) -> CoverCalibrationHub:
        """Return the CoverCalibrationHub for this cover's gateway."""
        return get_calibration_hub(self._gateway_handler)

    # ── Travel-time helpers ──────────────────────────────────────────────

    def _travel_for(self, opening: bool) -> float:
        """Full-travel seconds for the given direction (motors are often slower going up)."""
        return travel_for(self._travel_time_up, self._travel_time_down, opening)

    def _refresh_travel_attributes(self) -> None:
        attrs = self._attr_extra_state_attributes
        attrs["travel_time"] = int(round(self._travel_time_down))
        attrs["travel_time_down"] = round(self._travel_time_down, 2)
        attrs["travel_time_up"] = round(self._travel_time_up, 2)
        attrs["calibration_source"] = self._calibration_source
        attrs["calibrated_at"] = self._calibrated_at
        # The motor-start anchor of the current run (wall clock) and the last
        # completed run as the backend measured it, motor start to stop write /
        # actuator stop: the card's stopwatch anchors on the former and saves the
        # latter, so the number never includes the queue wait or the
        # click-to-stop-frame delay.
        attrs["motion_started_at"] = self._motion_started_at
        attrs["last_run_seconds"] = self._last_run.get("seconds")
        attrs["last_run_direction"] = self._last_run.get("direction")
        attrs["last_run_ended_at"] = self._last_run.get("ended_at")
        attrs["copied_from"] = self._copied_from

    # ── Echo model helpers ───────────────────────────────────────────────

    def _begin_command(self, cmd: str) -> None:
        """Open an echo window for a command we are about to queue.

        Until the write time is known the window is bounded from enqueue by the
        worst queue wait plus the echo window: a frame that is never written
        (queue flushed on shutdown, refused connection) must not leave the
        window open, or every later stop frame would be taken for an echo.
        """
        self._run_generation += 1
        self._pending_cmd = cmd
        self._echo_until = time.monotonic() + WRITE_TIMEOUT + ECHO_WINDOW
        self._motor_started = asyncio.Event()

    def _track_write(self, written) -> None:  # type: ignore
        """Anchor the echo window (and the clock) on the real write time."""
        if not isinstance(written, asyncio.Future):
            # No delivery information (legacy gateway object): bound the window
            # from enqueue so an external frame can never be mistaken for an
            # echo indefinitely.
            self._echo_until = time.monotonic() + ECHO_WINDOW
            return
        generation = self._run_generation

        @callback
        def _on_written(fut: asyncio.Future) -> None:  # type: ignore
            if generation != self._run_generation:
                return
            if fut.cancelled() or fut.exception() is not None:
                # The frame never reached the bus: abort motion if this was an open/close
                # command so the entity does not stay stuck in an opening/closing state.
                # A slat pulse's own auto-stop task settles a failed pulse.
                if self._pending_cmd in ("open", "close") and not self._is_tilting:
                    self._abort_motion()
                else:
                    self._end_echo_window()
                return
            write_ts = fut.result()
            self._echo_until = write_ts + ECHO_WINDOW
            if self._is_tilting:
                # A slat pulse never moves the curtain: no travel clock to start.
                return
            if self._pending_cmd in ("open", "close") and not self._motor_started.is_set():
                # Provisional anchor: the motor starts shortly after the write.
                # The direction echo re-anchors precisely if the gateway relays it.
                self._anchor_run(write_ts)
            elif self._pending_cmd == "stop":
                # Motion stops within ~0.1 s of the write, not at enqueue time.
                self._freeze_position(write_ts)
                self._motor_started.set()
            # The estimate changed (clock started, or frozen): show it. Not every
            # gateway relays the stop status that would otherwise repaint it.
            self._publish_state()

        written.add_done_callback(_on_written)

    def _in_echo_window(self, now: float) -> bool:
        return is_in_echo_window(self._echo_until, now)

    def _end_echo_window(self) -> None:
        self._pending_cmd = None
        self._echo_until = None

    def _anchor_run(self, at: float) -> None:
        """Anchor the running estimate and the run measurement on the motor start."""
        self._move_start_time = at
        self._run_started_at = at
        started = dt_util.utcnow() - timedelta(seconds=max(0.0, time.monotonic() - at))
        self._motion_started_at = started.isoformat(timespec="milliseconds")
        self._refresh_travel_attributes()

    def _freeze_position(self, at: float) -> None:
        """Turn the running estimate into a fixed position as of ``at``."""
        is_opening = bool(self._attr_is_opening)
        is_closing = bool(self._attr_is_closing)
        if self._run_started_at is not None:
            self._last_run = {
                "seconds": round(max(0.0, at - self._run_started_at), 2),
                "direction": "open" if is_opening else "close" if is_closing else None,
                "ended_at": dt_util.utcnow().isoformat(timespec="milliseconds"),
            }
            self._run_started_at = None
            self._motion_started_at = None
            self._refresh_travel_attributes()
        frozen = compute_freeze_position(
            self._start_position,
            self._move_start_time,
            at,
            self._travel_for(is_opening),
            is_opening,
            is_closing,
            self._attr_current_cover_position,
        )
        self._attr_current_cover_position = frozen
        if self._move_start_time is not None:
            self._start_position = frozen
            self._move_start_time = None
        self._attr_is_opening = False
        self._attr_is_closing = False
        if self._attr_current_cover_position is not None:
            self._attr_is_closed = (self._attr_current_cover_position == 0)

    def _abort_motion(self) -> None:
        """Abort motion state when a command frame failed to reach the bus."""
        self._cancel_stop_task()
        self._end_echo_window()
        self._move_start_time = None
        self._run_started_at = None
        self._motion_started_at = None
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._is_tilting = False
        if self._attr_current_cover_position is not None:
            self._start_position = self._attr_current_cover_position
            self._attr_is_closed = (self._attr_current_cover_position == 0)
        self._refresh_travel_attributes()
        self._publish_state()

    def _abort_write(self, is_tilting: bool) -> None:
        """Undo the local state of a direction command that never reached the bus."""
        if is_tilting:
            self._abort_tilt()
        elif not self._advanced:
            self._abort_motion()

    async def _await_motion_anchor(self, written) -> float:  # type: ignore
        """Wait for our frame to be written and for the motor-start echo.

        Returns the monotonic time motion is anchored on: the direction echo
        if the gateway relayed one inside the window, else the write time.
        Raises HomeAssistantError if the frame was cancelled, failed, or timed out.
        """
        write_ts: float | None = None
        if isinstance(written, asyncio.Future):
            try:
                write_ts = await asyncio.wait_for(asyncio.shield(written), WRITE_TIMEOUT)
            except TimeoutError as err:
                self._abort_motion()
                raise HomeAssistantError(
                    f"{self._display_name}: direction command was not delivered to the bus within {WRITE_TIMEOUT:.0f} s",
                    translation_domain=DOMAIN,
                    translation_key="command_delivery_timeout",
                    translation_placeholders={"name": self._display_name, "timeout": f"{WRITE_TIMEOUT:.0f}"},
                ) from err
            except asyncio.CancelledError:
                if not written.cancelled():
                    raise  # our own task was cancelled (new command, entity removed): stop here
                self._abort_motion()
                raise HomeAssistantError(
                    f"{self._display_name}: direction command delivery was cancelled before reaching the bus",
                    translation_domain=DOMAIN,
                    translation_key="command_delivery_cancelled",
                    translation_placeholders={"name": self._display_name},
                )
            except Exception as err:
                self._abort_motion()
                raise HomeAssistantError(
                    f"{self._display_name}: direction command delivery failed: {err}",
                    translation_domain=DOMAIN,
                    translation_key="command_delivery_failed",
                    translation_placeholders={"name": self._display_name, "error": str(err)},
                ) from err
        # The window ends ECHO_WINDOW after the write; computed here from the write
        # time itself, as this task may run before the future's callbacks did.
        deadline = (write_ts if write_ts is not None else time.monotonic()) + ECHO_WINDOW
        remaining = deadline - time.monotonic()
        if remaining > 0 and not self._motor_started.is_set():
            try:
                await asyncio.wait_for(self._motor_started.wait(), remaining)
            except TimeoutError:
                pass
        if not self._motor_started.is_set() and write_ts is not None and self._move_start_time == write_ts:
            # No direction status relayed (not every gateway does): the motor
            # started a measured MOTOR_START_DELAY after the write, not at it.
            self._anchor_run(write_ts + MOTOR_START_DELAY)
        if self._move_start_time is None:
            self._anchor_run(time.monotonic())
        return self._move_start_time  # type: ignore

    def _cancel_stop_task(self) -> None:
        """Cancel any running scheduled auto-stop task."""
        if self._stop_task is not None:
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if self._stop_task is not current and not self._stop_task.done():
                self._stop_task.cancel()
            self._stop_task = None

    @property
    def current_cover_position(self) -> int | None:
        """Return current cover position (interpolated if moving)."""
        if not self._advanced:
            is_opening = bool(self._attr_is_opening)
            is_closing = bool(self._attr_is_closing)
            return compute_interpolated_position(
                self._attr_current_cover_position,
                self._start_position,
                self._move_start_time,
                time.monotonic(),
                self._travel_for(is_opening),
                is_opening,
                is_closing,
            )
        return self._attr_current_cover_position

    def _enable_slat_tilt(self) -> None:
        """Enable Venetian blind slat / louvre tilt capabilities."""
        self._slat_tilt = True
        self._attr_device_class = CoverDeviceClass.BLIND
        current_features = self._attr_supported_features or CoverEntityFeature(0)
        self._attr_supported_features = (
            current_features
            | CoverEntityFeature.SET_TILT_POSITION
            | CoverEntityFeature.OPEN_TILT
            | CoverEntityFeature.CLOSE_TILT
            | CoverEntityFeature.STOP_TILT
        )
        if getattr(self, "_attr_extra_state_attributes", None) is not None:
            self._attr_extra_state_attributes["slat_tilt"] = True
            self._attr_extra_state_attributes["slat_time"] = self._slat_time

    @property
    def current_cover_tilt_position(self) -> int | None:
        """Return the current tilt position of the cover."""
        return self._attr_current_cover_tilt_position

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        return self._attr_is_opening  # type: ignore

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        return self._attr_is_closing  # type: ignore

    @property
    def is_closed(self) -> bool:
        """Return if the cover is closed."""
        if self.current_cover_position is not None:
            return self.current_cover_position == 0
        return self._attr_is_closed  # type: ignore

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        if self._advanced:
            # Advanced covers query live position from bus; do not restore stale state
            self._register_availability_listener()
            await self.async_update()
        else:
            await super().async_added_to_hass()
        if self._family is not None:
            self._family.membership_changed()

    async def async_restore_last_state(self, last_state: Any) -> None:
        """Restore cover position and closure state."""
        if not self._advanced:
            restored = False
            last_pos = last_state.attributes.get(ATTR_CURRENT_POSITION)
            if last_pos is not None:
                try:
                    self._attr_current_cover_position = max(0, min(100, int(round(float(last_pos)))))
                    self._start_position = self._attr_current_cover_position
                    self._attr_is_closed = (self._attr_current_cover_position == 0)
                    restored = True
                except (ValueError, TypeError):
                    restored = False
            if not restored:
                if last_state.state in (STATE_CLOSED, "closed"):
                    self._attr_current_cover_position = 0
                    self._start_position = 0
                    self._attr_is_closed = True
                elif last_state.state in (STATE_OPEN, "open"):
                    self._attr_current_cover_position = 100
                    self._start_position = 100
                    self._attr_is_closed = False

        if self._slat_tilt:
            last_tilt = last_state.attributes.get(ATTR_CURRENT_TILT_POSITION)
            if last_tilt is not None:
                try:
                    self._attr_current_cover_tilt_position = max(0, min(100, int(round(float(last_tilt)))))
                except (ValueError, TypeError):
                    pass

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        self._run_generation += 1  # a run in flight must not act on a removed entity
        self._cancel_stop_task()
        await super().async_will_remove_from_hass()


    # ── Calibration ──────────────────────────────────────────────────────

    def _interrupt_calibration(self, reason: str) -> None:
        if self._calibrating and not self._calibration_interrupted:
            self._calibration_interrupted = reason
            self._stopped_event.set()

    def _fire_calibration_event(self, phase: str, **data) -> None:  # type: ignore
        extra = dict(data)
        run_direction = extra.pop("direction", None)
        _record_calibration_frame(
            self._gateway_handler,
            direction="event",
            raw=f"phase:{phase}",
            phase=phase,
            entity_id=self.entity_id,
            where=self._full_where,
            movement_direction=run_direction,
            **extra,
        )
        bus = getattr(getattr(self, "hass", None), "bus", None)
        if bus is None:
            return
        payload = {"entity_id": self.entity_id, "where": self._full_where, "name": self._display_name, "phase": phase, **data}
        try:
            bus.async_fire(EVENT_COVER_CALIBRATION, payload)
        except Exception as err:  # pragma: no cover - defensive
            LOGGER.debug("Could not fire calibration event: %s", err)

    async def _calibration_run(self, direction: str) -> float:
        """Drive one full run and return its measured duration (motor start -> actuator stop)."""
        self._stopped_event = asyncio.Event()
        self._calibration_interrupted = None
        self._fire_calibration_event("run", direction=direction)
        written = await self._async_move(direction)
        anchor = await self._await_motion_anchor(written)
        if self._calibration_interrupted:
            raise CalibrationInterrupted(self._display_name, self._calibration_interrupted)
        deadline = time.monotonic() + CALIBRATION_RUN_TIMEOUT
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                await asyncio.wait_for(self._stopped_event.wait(), remaining)
            except TimeoutError as err:
                raise HomeAssistantError(
                    f"{self._display_name}: no stop status from the actuator within {CALIBRATION_RUN_TIMEOUT:.0f} s "
                    "- it may not report status; set travel_time manually",
                    translation_domain=DOMAIN,
                    translation_key="calibration_no_stop_status",
                    translation_placeholders={"name": self._display_name, "timeout": f"{CALIBRATION_RUN_TIMEOUT:.0f}"},
                ) from err
            if self._calibration_interrupted:
                raise CalibrationInterrupted(self._display_name, self._calibration_interrupted)
            stop_at = self._last_stop_at if self._last_stop_at is not None else time.monotonic()
            elapsed = max(0.0, stop_at - anchor)
            # If a stop frame arrives less than 0.15s after anchor (e.g. trailing relay echo on MH200),
            # ignore it and keep waiting for the real actuator limit stop.
            if elapsed < 0.15:
                LOGGER.debug(
                    "%s Ignoring premature stop echo for %s during calibration (%.2f s < 0.15 s); continuing run.",
                    self._gateway_handler.log_id, self._full_where, elapsed,
                )
                self._stopped_event.clear()
                continue
            if CALIBRATION_CUTOFF_MIN <= elapsed <= CALIBRATION_CUTOFF_MAX:
                # The actuator stopped itself at its run-time limit, not at the end
                # stop: storing this would make a 14 s shutter a 61 s one (#319).
                # Fail now rather than after three such runs.
                raise HomeAssistantError(
                    f"{self._attr_name}: the {direction} run ended after {elapsed:.1f} s, at the actuator's "
                    "60 s run-time limit, not at the end stop; the actuator cannot measure this shutter "
                    "- use the stopwatch (Stop & Save) or set travel_time manually"
                )
            return elapsed

    async def async_calibrate(self) -> dict:  # type: ignore
        """Measure this cover's travel times on the bus and store them.

        Sequence: open to the end stop (position becomes known), close and time
        the run, open and time the run. Serialized per gateway. The measured
        values are the actuator's run times, which equal the physical travel
        when the installer calibrated the actuator (the usual case).
        """
        if self._advanced:
            raise HomeAssistantError(
                f"{self._display_name} reports its position; calibration is not needed",
                translation_domain=DOMAIN,
                translation_key="cover_reports_position",
                translation_placeholders={"name": self._display_name},
            )
        if self._calibrating:
            raise HomeAssistantError(
                f"{self._display_name} is already being calibrated",
                translation_domain=DOMAIN,
                translation_key="calibration_in_progress",
                translation_placeholders={"name": self._display_name},
            )

        hub = self.calibration_hub
        lock = hub.lock
        self._calibration_interrupted = None

        if lock.locked():
            # Another cover of this gateway is running: tell the UI we are waiting, not moving.
            hub.queued_covers.add(self)
            self._fire_calibration_event("queued")

        try:
            async with lock:
                hub.queued_covers.discard(self)
                if self._calibration_interrupted:
                    self._fire_calibration_event("failed", error=self._calibration_interrupted)  # type: ignore
                    raise CalibrationInterrupted(self._display_name, self._calibration_interrupted)
                hub.active_cover = self
                self._calibrating = True
                self._cancel_stop_task()
                self._fire_calibration_event("start")
                try:
                    await self._calibration_run("open")          # reach the top: known position
                    await asyncio.sleep(CALIBRATION_SETTLE)
                    down = await self._calibration_run("close")
                    await asyncio.sleep(CALIBRATION_SETTLE)
                    up = await self._calibration_run("open")
                except HomeAssistantError as err:
                    self._fire_calibration_event("failed", error=str(err))
                    raise
                finally:
                    self._calibrating = False
                    hub.active_cover = None
        finally:
            hub.queued_covers.discard(self)

        for label, value in (("down", down), ("up", up)):
            if not CALIBRATION_MIN_RUN <= value <= CALIBRATION_MAX_RUN:
                msg = f"{self._display_name}: implausible {label} run of {value:.1f} s; not stored"
                self._fire_calibration_event("failed", error=msg)
                raise HomeAssistantError(
                    msg,
                    translation_domain=DOMAIN,
                    translation_key="calibration_implausible_run",
                    translation_placeholders={"name": self._display_name, "direction": label, "seconds": f"{value:.1f}"},
                )

        self._travel_time_down = round(down, 2)
        self._travel_time_up = round(up, 2)
        self._travel_time = int(round(down))
        self._calibration_source = "measured"
        self._copied_from = None
        self._calibrated_at = dt_util.utcnow().isoformat(timespec="seconds")
        # The sequence ends with the cover fully open.
        self._attr_current_cover_position = 100
        self._start_position = 100
        self._attr_is_closed = False
        self._refresh_travel_attributes()
        result = {
            "down": self._travel_time_down,
            "up": self._travel_time_up,
            "measured_at": self._calibrated_at,
        }
        self._persist_calibration(result)
        if self.hass is not None:
            self.async_write_ha_state()
        self._fire_calibration_event("done", **result)
        LOGGER.info(
            "%s Cover %s calibrated: down %.1f s, up %.1f s.",
            self._gateway_handler.log_id, self._full_where, down, up,
        )
        return result

    async def async_stop_calibration(self) -> None:
        """Stop calibration on this cover's gateway."""
        gw_mac = getattr(self._gateway_handler, "mac", None)
        await async_stop_cover_calibration(self.hass or self._hass, gateway_mac=gw_mac)

    async def async_set_travel_time(
        self,
        travel_time: float | None = None,
        travel_time_down: float | None = None,
        travel_time_up: float | None = None,
        slat_time: float | None = None,
        copied_from: str | None = None,
    ) -> dict:  # type: ignore
        """Set the physical travel times by hand, or copy them from another cover.

        ``copied_from`` records the entity the times were taken from; the source
        is then reported as ``copied`` instead of ``manual``.
        """
        has_travel = travel_time is not None or travel_time_down is not None or travel_time_up is not None
        if self._advanced and (has_travel or slat_time is None):
            raise HomeAssistantError(
                f"{self._display_name} reports its position; travel time cannot be set",
                translation_domain=DOMAIN,
                translation_key="cover_reports_position",
                translation_placeholders={"name": self._display_name},
            )

        if not has_travel and slat_time is None:
            raise ServiceValidationError(
                "At least travel_time or travel_time_down/up or slat_time must be specified",
                translation_domain=DOMAIN,
                translation_key="travel_time_missing",
            )

        if has_travel:
            down = travel_time_down if travel_time_down is not None else travel_time
            up = travel_time_up if travel_time_up is not None else (travel_time if travel_time is not None else down)

            if down is None:
                down = self._travel_time_down
            assert down is not None and up is not None

            for field, value in (("travel_time_down", down), ("travel_time_up", up)):
                if not CALIBRATION_MIN_RUN <= value <= CALIBRATION_MAX_RUN:
                    raise ServiceValidationError(
                        f"{field} must be between {CALIBRATION_MIN_RUN:.0f}s and {CALIBRATION_MAX_RUN:.0f}s",
                        translation_domain=DOMAIN,
                        translation_key="travel_time_out_of_range",
                        translation_placeholders={
                            "field": field, "min": f"{CALIBRATION_MIN_RUN:.0f}", "max": f"{CALIBRATION_MAX_RUN:.0f}",
                        },
                    )

            self._travel_time_down = round(float(down), 2)
            self._travel_time_up = round(float(up), 2)
            self._travel_time = int(round(self._travel_time_down))

        if slat_time is not None:
            if not MIN_SLAT_TIME <= slat_time <= MAX_SLAT_TIME:
                raise ServiceValidationError(
                    f"slat_time must be between {MIN_SLAT_TIME:.1f}s and {MAX_SLAT_TIME:.1f}s",
                    translation_domain=DOMAIN,
                    translation_key="travel_time_out_of_range",
                    translation_placeholders={
                        "field": "slat_time", "min": f"{MIN_SLAT_TIME:.1f}", "max": f"{MAX_SLAT_TIME:.1f}",
                    },
                )
            self._slat_time = round(float(slat_time), 2)
            if getattr(self, "_attr_extra_state_attributes", None) is not None:
                self._attr_extra_state_attributes["slat_time"] = self._slat_time

        if has_travel:
            self._copied_from = str(copied_from) if copied_from else None
            self._calibration_source = "copied" if self._copied_from else "manual"
            self._calibrated_at = dt_util.utcnow().isoformat(timespec="seconds")

        result = {
            "down": self._travel_time_down,
            "up": self._travel_time_up,
            "measured_at": self._calibrated_at,
            "source": self._calibration_source,
        }
        if self._slat_tilt or slat_time is not None:
            result["slat_time"] = self._slat_time
        if self._copied_from:
            result["copied_from"] = self._copied_from
        if has_travel:
            self._persist_calibration(result)
        else:
            # Slat time only: keep the stored record's travel times untouched (and
            # do not turn the current defaults into a stored calibration).
            self._persist_calibration({**(self._stored_record() or {}), "slat_time": self._slat_time})
        self._refresh_travel_attributes()
        if self.hass is not None:
            self.async_write_ha_state()

        self._fire_calibration_event("done", **result)
        LOGGER.info(
            "%s Cover %s %s travel time set: down %.1f s, up %.1f s.",
            self._gateway_handler.log_id, self._full_where, self._calibration_source,
            self._travel_time_down, self._travel_time_up,
        )
        return result

    async def async_reset_travel_time(self) -> None:
        """Reset travel times back to default or YAML configuration."""
        if self._advanced and not self._slat_tilt:
            raise HomeAssistantError(
                f"{self._display_name} reports its position; calibration is not applicable",
                translation_domain=DOMAIN,
                translation_key="cover_reports_position",
                translation_placeholders={"name": self._display_name},
            )

        entry = getattr(self._gateway_handler, "config_entry", None)
        hass = self.hass or self._hass
        if entry is not None and getattr(entry, "entry_id", None) and hass is not None and hasattr(hass, "config_entries"):
            options = dict(getattr(entry, "options", None) or {})
            stored = dict(options.get(CONF_COVER_TRAVEL_TIMES) or {})
            if str(self._device_id) in stored:
                del stored[str(self._device_id)]
                options[CONF_COVER_TRAVEL_TIMES] = stored
                hass.config_entries.async_update_entry(entry, options=options)

        cfg = self._device_config() or {}
        if self._advanced:
            # A position-reporting blind has no travel times, only the slat time
            # myhome.set_cover_travel_time may have stored.
            self._slat_time = _clamp_slat_time(cfg.get(CONF_SLAT_TIME))
            self._attr_extra_state_attributes["slat_time"] = self._slat_time
            if self.hass is not None:
                self.async_write_ha_state()
            LOGGER.info(
                "%s Cover %s slat time reset to %.1f s.", self._gateway_handler.log_id, self._full_where, self._slat_time
            )
            return
        if CONF_TRAVEL_TIME in cfg:
            base_travel = float(cfg[CONF_TRAVEL_TIME])
            source = "yaml"
        else:
            base_travel = float(DEFAULT_TRAVEL_TIME)
            source = "default"

        self._travel_time_down = base_travel
        self._travel_time_up = base_travel
        self._travel_time = int(round(base_travel))
        self._calibration_source = source
        self._calibrated_at = None
        self._copied_from = None
        # The stored record (and any slat time set with the service) is gone too.
        self._slat_time = _clamp_slat_time(cfg.get(CONF_SLAT_TIME))
        if self._slat_tilt:
            self._attr_extra_state_attributes["slat_time"] = self._slat_time

        self._refresh_travel_attributes()
        if self.hass is not None:
            self.async_write_ha_state()

        self._fire_calibration_event(
            "reset",
            down=self._travel_time_down,
            up=self._travel_time_up,
            source=source,
        )
        LOGGER.info(
            "%s Cover %s travel times reset to %s (%.1f s).",
            self._gateway_handler.log_id, self._full_where, source, base_travel,
        )

    def _stored_record(self) -> dict | None:  # type: ignore
        """The persisted calibration record of this cover, if any."""
        return _stored_calibration(getattr(self._gateway_handler, "config_entry", None), str(self._device_id))

    def _persist_calibration(self, result: dict) -> None:  # type: ignore
        """Store the measurement in the config entry options (survives restarts, applies to discovered covers)."""
        entry = getattr(self._gateway_handler, "config_entry", None)
        hass = self.hass or self._hass
        if entry is None or hass is None or not hasattr(hass, "config_entries"):
            return
        try:
            options = dict(getattr(entry, "options", None) or {})
            stored = dict(options.get(CONF_COVER_TRAVEL_TIMES) or {})
            stored[str(self._device_id)] = result
            options[CONF_COVER_TRAVEL_TIMES] = stored
            hass.config_entries.async_update_entry(entry, options=options)
        except Exception as err:  # pragma: no cover - defensive
            LOGGER.warning("%s Could not persist calibration for %s: %s", self._gateway_handler.log_id, self._full_where, err)

    async def async_update(self) -> None:
        """Update the entity.

        Only used by the generic entity update service.
        """
        if self._advanced:
            await self._gateway_handler.send_status_request(
                OWNAutomationCommand.get_shutter_status(self._full_where)
            )
        else:
            await self._gateway_handler.send_status_request(
                OWNAutomationCommand.status(self._full_where)
            )

    async def async_open_cover(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Open the cover."""
        await self._async_move("open")

    async def async_close_cover(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Close cover."""
        await self._async_move("close")

    async def _async_move(self, direction: str, is_tilting: bool = False) -> asyncio.Future[Any] | None:
        """Queue a direction command and return its delivery future."""
        self._cancel_stop_task()
        if not is_tilting or self._tilt_follows is not None:
            # A curtain command, or a blind's own pulse, takes the motor over.
            self._end_pulse_for_takeover()
        self._is_tilting = is_tilting
        if direction == "open":
            command = OWNAutomationCommand.raise_shutter(self._full_where)
        else:
            command = OWNAutomationCommand.lower_shutter(self._full_where)
        if is_tilting:
            # A slat pulse opens an echo window like any command (the gateway
            # relays a stop status before the direction status, which must not
            # end the pulse), but leaves the curtain's travel clock alone.
            self._begin_command(direction)
            self._attr_is_opening = direction == "open"
            self._attr_is_closing = direction == "close"
        elif not self._advanced:
            self._start_position = self.current_cover_position if self.current_cover_position is not None else (0 if direction == "open" else 100)
            # The clock starts when the frame is written (see _track_write),
            # not now: with a busy queue the motor is still idle for a while.
            self._move_start_time = None
            self._run_started_at = None
            self._motion_started_at = None  # the previous run's anchor is not this run's
            self._refresh_travel_attributes()
            self._attr_is_opening = direction == "open"
            self._attr_is_closing = direction == "close"
            self._attr_is_closed = False
            self._begin_command(direction)
        written = await self._gateway_handler.send(command)
        if self._calibrating or self.calibration_hub.is_calibrating:
            _record_calibration_frame(self._gateway_handler, "tx", str(command), direction_action=direction, entity_id=self.entity_id)
        if is_tilting or not self._advanced:
            self._track_write(written)
        if isinstance(written, asyncio.Future) and written.done():
            if written.cancelled():
                self._abort_write(is_tilting)
                raise HomeAssistantError(
                    f"{self._display_name}: direction command delivery was cancelled before reaching the bus",
                    translation_domain=DOMAIN,
                    translation_key="command_delivery_cancelled",
                    translation_placeholders={"name": self._display_name},
                )
            if (exc := written.exception()) is not None:
                self._abort_write(is_tilting)
                raise HomeAssistantError(
                    f"{self._display_name}: direction command delivery failed: {exc}",
                    translation_domain=DOMAIN,
                    translation_key="command_delivery_failed",
                    translation_placeholders={"name": self._display_name, "error": str(exc)},
                ) from exc
        if self.hass is not None:
            self.async_write_ha_state()
        return written

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        if ATTR_POSITION not in kwargs:
            return
        target_position = kwargs[ATTR_POSITION]
        if self._advanced:
            # The level command takes the motor over: a running slat pulse must
            # not stop it with its own auto-stop.
            self._cancel_stop_task()
            self._end_pulse_for_takeover()
            if target_position <= 0:
                await self._gateway_handler.send(
                    OWNAutomationCommand.lower_shutter(self._full_where)
                )
            else:
                await self._gateway_handler.send(
                    OWNAutomationCommand.set_shutter_level(
                        self._full_where, target_position
                    )
                )
            return

        if self._calibrating:
            raise HomeAssistantError(
                f"{self.entity_id} is being calibrated; try again when it has finished",
                translation_domain=DOMAIN,
                translation_key="cover_busy_calibrating",
                translation_placeholders={"entity_id": str(self.entity_id)},
            )

        curr_pos = self.current_cover_position if self.current_cover_position is not None else 50
        diff = target_position - curr_pos
        if diff == 0:
            if self._is_tilting or self._attr_is_opening or self._attr_is_closing:
                # Already there while the motor runs (a slat pulse, or a run passing
                # this level): stop it here. Only cancelling the running auto-stop
                # would leave the motor running.
                await self.async_stop_cover()
            return
        self._cancel_stop_task()

        travel_fraction = abs(diff) / 100.0
        run_duration = travel_fraction * self._travel_for(diff > 0)

        written = await self._async_move("open" if diff > 0 else "close")
        generation = self._run_generation

        async def _auto_stop() -> None:
            try:
                anchor = await self._await_motion_anchor(written)
                if generation != self._run_generation:
                    return  # a newer command superseded this run
                await asyncio.sleep(max(0.0, run_duration - (time.monotonic() - anchor)))
                if generation != self._run_generation:
                    return
                # By the model we are at the target now; the motor keeps
                # running until the stop frame is written, so re-anchor the
                # run here and let the stop's write time freeze the estimate
                # (target plus whatever the queue delay added).
                self._start_position = target_position
                self._attr_current_cover_position = target_position
                self._move_start_time = time.monotonic()
                await self.async_stop_cover()
                if self.hass is not None:
                    self.async_write_ha_state()
            except HomeAssistantError as err:
                LOGGER.error("%s Auto-stop aborted for %s: %s", self._gateway_handler.log_id, self._full_where, err)
            except asyncio.CancelledError:
                pass

        self._stop_task = asyncio.create_task(_auto_stop())

    async def async_stop_cover(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Stop the cover."""
        was_tilting = self._is_tilting
        at = kwargs.get("at")
        self._cancel_stop_task()
        if not self._advanced and not was_tilting:
            # The estimate keeps running until the stop frame is actually
            # written (_track_write freezes it then); if delivery never
            # happens the next status frame will correct us.
            self._begin_command("stop")
        generation = self._run_generation
        queued_at = time.monotonic()
        cmd = OWNAutomationCommand.stop_shutter(self._full_where)
        written = await self._gateway_handler.send(cmd)
        if self._calibrating or self.calibration_hub.is_calibrating:
            _record_calibration_frame(self._gateway_handler, "tx", str(cmd), action="stop", entity_id=self.entity_id)
        if not self._advanced and not was_tilting:
            self._track_write(written)
            if not isinstance(written, asyncio.Future):
                self._freeze_position(time.monotonic())  # type: ignore
        elif was_tilting and generation == self._run_generation:
            # A pulse started while this stop was being queued is not ours to settle.
            if isinstance(written, asyncio.Future) and not written.done():
                self._pulse_stop_written = written
            self._settle_pulse_on_write(written, generation, max(at, queued_at) if at is not None else queued_at)
        if self.hass is not None:
            self.async_write_ha_state()

    def _settle_pulse_on_write(self, written: Any, generation: int, not_before: float) -> None:
        """Settle the slat angle when the stop frame reaches the bus.

        The send queue is FIFO: behind other frames (a scope fan-out, a busy
        gateway) the stop is written late and the motor turns the slats until
        then, so the scheduled stop time would understate the angle.
        """

        def _settle(fut: asyncio.Future[Any] | None = None) -> None:
            if fut is not None and self._pulse_stop_written is fut:
                self._pulse_stop_written = None
            if generation != self._run_generation or not self._is_tilting:
                return  # a newer command, or a stop status, already ended the pulse
            stopped_at: float | None = None
            if fut is not None and not fut.cancelled() and fut.exception() is None:
                result = fut.result()
                stopped_at = float(result) if isinstance(result, (int, float)) else None
            elif fut is not None:
                LOGGER.error(
                    "%s Stop for the slat pulse of %s was not delivered; the motor may still run.",
                    self._gateway_handler.log_id, self._full_where,
                )
            # The stop cannot leave before it was queued (nor the auto-stop before it was due).
            stopped_at = max(stopped_at if stopped_at is not None else time.monotonic(), not_before)
            self._apply_tilt_stop(stopped_at)
            self._publish_state()

        if isinstance(written, asyncio.Future) and not written.done():
            written.add_done_callback(_settle)
        else:
            _settle(written if isinstance(written, asyncio.Future) else None)

    def _interpolated_tilt(self, at: float) -> int | None:
        """The tilt angle ``at`` a moment of the running pulse (the last known angle if none runs).

        The slats turn at a full rotation per ``slat_time`` for as long as the
        motor runs: past the target too, when the stop reaches the bus late.
        """
        if self._tilt_start_time is None or self._slat_time <= 0:
            return self.current_cover_tilt_position
        turned = max(0.0, at - self._tilt_start_time) / self._slat_time * 100.0
        return max(0, min(100, int(round(self._tilt_initial_position + self._tilt_step() * turned))))

    def _tilt_step(self) -> int:
        """+1 while the running pulse opens the slats, -1 while it closes them."""
        return 1 if self._tilt_direction == "open" else -1

    def _carry_overshoot_into_curtain(self, at: float) -> None:
        """Motor time past a full slat rotation moves the curtain (timed covers).

        The slats turn first and the curtain follows, so a stop that reached the
        bus after the slats were fully turned leaves the curtain moved by the rest.
        """
        if self._advanced or self._tilt_start_time is None or self._attr_current_cover_position is None:
            return
        opening = self._tilt_step() > 0
        to_end = (100 - self._tilt_initial_position if opening else self._tilt_initial_position) / 100.0 * self._slat_time
        excess = (at - self._tilt_start_time) - to_end
        if excess <= 0:
            return
        moved = excess / self._travel_for(opening) * 100.0
        position = max(0, min(100, int(round(self._attr_current_cover_position + (moved if opening else -moved)))))
        LOGGER.debug(
            "%s Slat pulse of %s ran %.2f s past a full rotation: curtain %s -> %s.",
            self._gateway_handler.log_id, self._full_where, excess, self._attr_current_cover_position, position,
        )
        self._attr_current_cover_position = self._start_position = position
        self._attr_is_closed = position == 0

    def _abort_tilt(self) -> None:
        """Forget a tilt pulse that never reached the bus; the tilt angle stays as it was."""
        self._end_echo_window()
        self._is_tilting = False
        self._tilt_start_time = None
        self._tilt_direction = None
        self._tilt_follows = None
        self._pulse_written = None
        self._attr_is_opening = False
        self._attr_is_closing = False
        if self.hass is not None:
            self.async_write_ha_state()

    def _apply_tilt_stop(self, at: float | None = None) -> None:
        """Interpolate and finalize tilt position when tilting stops."""
        if self._tilt_start_time is not None:
            stopped_at = at if at is not None else time.monotonic()
            self._attr_current_cover_tilt_position = self._interpolated_tilt(stopped_at)
            self._carry_overshoot_into_curtain(stopped_at)
        # The pulse is over: a direction frame from now on is a keypad press, not its echo.
        self._end_echo_window()
        self._is_tilting = False
        self._tilt_start_time = None
        self._tilt_direction = None
        self._tilt_follows = None
        self._pulse_written = None
        self._attr_is_opening = False
        self._attr_is_closing = False

    def _pulse_started(self) -> None:
        """Hook: the running pulse's motor start (``_tilt_start_time``) is now known."""

    def _follow_pulse(self, leader: MyHOMECover, direction: str) -> None:
        """Follow a scope cover's slat pulse: its frames turn this blind's slats.

        Without this, the scope's direction and stop frames reach the member as
        an ordinary curtain run and nudge its position estimate. The leader
        times the pulse (_pulse_started) and settles it (_apply_tilt_stop).
        """
        self._cancel_stop_task()
        self._end_pulse_for_takeover()
        current = self.current_cover_tilt_position
        self._tilt_initial_position = current if current is not None else (0 if self.is_closed else 50)
        self._tilt_target_position = 100 if direction == "open" else 0
        self._tilt_direction = direction
        self._tilt_start_time = None
        self._tilt_follows = leader
        self._is_tilting = True
        # The relayed stop status before the direction status belongs to the pulse.
        self._begin_command(direction)
        self._attr_is_opening = direction == "open"
        self._attr_is_closing = direction == "close"
        self._publish_state()

    async def _async_wait_pulse_stop_written(self) -> None:
        """Let a slat pulse's queued stop reach the bus first.

        The queue is FIFO, so a new direction frame could not leave before that
        stop anyway; waiting settles the angle at the stop's real write, and the
        next pulse starts from there instead of from an estimate.
        """
        pending = self._pulse_stop_written
        if pending is None or pending.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(pending), WRITE_TIMEOUT)
        except asyncio.CancelledError:
            if not pending.cancelled():
                raise
        except Exception:  # noqa: BLE001 - timeout or failed delivery: _settle_pulse_on_write reports it
            pass

    async def _async_wait_pulse_end(self) -> None:
        """Wait until the running slat pulse has stopped and its stop is on the bus."""
        if (task := self._stop_task) is not None and not task.done():
            await asyncio.wait({task}, timeout=WRITE_TIMEOUT + MAX_SLAT_TIME + ECHO_WINDOW)
        await self._async_wait_pulse_stop_written()

    def _end_pulse_for_takeover(self) -> None:
        """A curtain or level command takes the motor over from a running slat pulse."""
        if self._is_tilting:
            self._run_generation += 1  # the pulse's queued stop must not settle anything now
            self._apply_tilt_stop(time.monotonic())

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Move the cover tilt to a specific position."""
        if ATTR_TILT_POSITION not in kwargs:
            return
        target_tilt = max(0, min(100, int(round(float(kwargs[ATTR_TILT_POSITION])))))
        if self._calibrating:
            raise HomeAssistantError(
                f"{self.entity_id} is being calibrated; try again when it has finished",
                translation_domain=DOMAIN,
                translation_key="cover_busy_calibrating",
                translation_placeholders={"entity_id": str(self.entity_id)},
            )
        self._tilt_request_seq += 1
        request = self._tilt_request_seq
        async with self._tilt_lock:
            await self._async_wait_pulse_stop_written()
            if request != self._tilt_request_seq:
                return  # a newer target arrived meanwhile and is sent instead
            await self._async_start_tilt(target_tilt)

    async def _async_start_tilt(self, target_tilt: int) -> None:
        """Start, retarget or end the slat pulse that brings the slats to ``target_tilt``."""
        was_tilting = self._is_tilting
        if not was_tilting and (self._attr_is_opening or self._attr_is_closing):
            # The slats already turned with the curtain run, and a pulse would
            # leave the curtain's travel estimate behind: stop the run first.
            raise HomeAssistantError(
                f"{self._display_name} is moving; stop it before tilting the slats",
                translation_domain=DOMAIN,
                translation_key="cover_busy_moving",
                translation_placeholders={"name": self._display_name},
            )
        curr_tilt = self._interpolated_tilt(time.monotonic()) if was_tilting else self.current_cover_tilt_position
        if curr_tilt is None:
            curr_tilt = 0 if self.is_closed else 50
        diff = target_tilt - curr_tilt
        run_duration = abs(diff) / 100.0 * self._slat_time
        if run_duration < MIN_TILT_PULSE:
            # Nothing to do, or a pulse too short for the motor to answer.
            if was_tilting:
                # Already there while the motor still runs: end the pulse here.
                await self.async_stop_cover()
            return
        # _async_move cancels the running auto-stop only once the new command is
        # on its way, so a failure cannot leave a running pulse without its stop.

        direction = "open" if diff > 0 else "close"
        if (
            was_tilting
            and direction == self._tilt_direction
            and self._tilt_follows is None
            and self._pulse_written is not None
            and self._stop_task is not None
            and not self._stop_task.done()
        ):
            # Same direction while the motor still runs (a slider being dragged):
            # move the pulse's stop and send nothing.
            self._cancel_stop_task()
            self._tilt_target_position = target_tilt
            self._tilt_duration = abs(target_tilt - self._tilt_initial_position) / 100.0 * self._slat_time
            self._start_pulse_auto_stop(self._pulse_written, self._run_generation, self._motor_started)
            if self.hass is not None:
                self.async_write_ha_state()
            return

        self._tilt_initial_position = curr_tilt
        self._tilt_target_position = target_tilt
        self._tilt_duration = run_duration
        self._tilt_direction = direction
        self._tilt_start_time = None

        try:
            written = await self._async_move(direction, is_tilting=True)
        except Exception:
            self._is_tilting = False
            self._tilt_direction = None
            raise

        # _tilt_start_time stays None (no slat travel counted) until the frame is
        # written: on a busy queue the motor is still idle for a while.
        self._pulse_written = written
        self._start_pulse_auto_stop(written, self._run_generation, self._motor_started)
        if self.hass is not None:
            self.async_write_ha_state()

    def _start_pulse_auto_stop(self, written: Any, generation: int, motor_started: asyncio.Event) -> None:
        """Stop the running pulse once it has turned the slats for ``_tilt_duration``."""

        async def _auto_stop_tilt() -> None:
            try:
                write_ts: float | None = None
                if isinstance(written, asyncio.Future):
                    try:
                        write_ts = await asyncio.wait_for(asyncio.shield(written), WRITE_TIMEOUT)
                    except asyncio.CancelledError:
                        if not written.cancelled():
                            raise  # this task was superseded: never go on to stop the new run
                        write_ts = None
                        failure: Exception | None = HomeAssistantError("tilt command was cancelled before reaching the bus")
                    except Exception as err:  # noqa: BLE001 - timeout or delivery failure
                        write_ts = None
                        failure = err
                    else:
                        failure = None
                    if failure is not None:
                        # The motor never started: nothing to stop, nothing moved.
                        if generation == self._run_generation:
                            self._abort_tilt()
                        LOGGER.error(
                            "%s Tilt pulse for %s was not delivered: %s", self._gateway_handler.log_id, self._full_where, failure
                        )
                        return
                    write_ts = float(write_ts) if isinstance(write_ts, (int, float)) else None
                if generation != self._run_generation:
                    return
                # The motor starts well after the write (MOTOR_START_DELAY), and a
                # pulse timed from the write would lose that much slat travel:
                # time it from the direction status (_handle_echo sets the start),
                # else from the write plus the measured delay. Wait for that status
                # no longer than the stop is due without it, or a gateway that never
                # relays it would stretch every short pulse to the echo window.
                if self._tilt_start_time is None or not motor_started.is_set():
                    self._tilt_start_time = (
                        write_ts + MOTOR_START_DELAY if write_ts is not None else time.monotonic()
                    )
                    self._pulse_started()
                if not motor_started.is_set():
                    wait = self._tilt_start_time + self._tilt_duration - time.monotonic()
                    if wait > 0:
                        try:
                            await asyncio.wait_for(motor_started.wait(), wait)
                        except TimeoutError:
                            pass
                if generation != self._run_generation:
                    return
                anchor = self._tilt_start_time if self._tilt_start_time is not None else time.monotonic()
                remaining = max(0.0, self._tilt_duration - (time.monotonic() - anchor))
                if remaining > 0:
                    await asyncio.sleep(remaining)
                if generation != self._run_generation:
                    return
                at = (self._tilt_start_time + self._tilt_duration) if self._tilt_start_time is not None else None
                await self.async_stop_cover(at=at)
                if self.hass is not None:
                    self.async_write_ha_state()
            except HomeAssistantError as err:
                LOGGER.error("%s Auto-stop tilt aborted for %s: %s", self._gateway_handler.log_id, self._full_where, err)
                self._apply_tilt_stop()
            except asyncio.CancelledError:
                pass

        self._stop_task = asyncio.create_task(_auto_stop_tilt())

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Open cover tilt."""
        await self.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 100})

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Close cover tilt."""
        await self.async_set_cover_tilt_position(**{ATTR_TILT_POSITION: 0})

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Stop cover tilt."""
        await self.async_stop_cover(**kwargs)

    def _handle_echo(self, message: OWNMessage, now: float) -> bool:
        """Consume frames the gateway relays for our own in-flight command.

        Returns True when the frame was an echo and needs no further handling.
        """
        # Slat pulses rely on their auto-stop on every cover, advanced or not.
        if (self._advanced and not self._is_tilting) or not self._in_echo_window(now) or self._pending_cmd is None:
            return False
        is_opening = bool(getattr(message, "is_opening", False))
        is_closing = bool(getattr(message, "is_closing", False))
        is_stop = not is_opening and not is_closing
        if self._pending_cmd in ("open", "close"):
            matches = (is_opening and self._pending_cmd == "open") or (
                is_closing and self._pending_cmd == "close"
            )
            if matches:
                # The relayed direction status marks the real motor start.
                if self._is_tilting:
                    if self._tilt_follows is None:
                        # The pulse is timed from here (a follower takes its leader's timing).
                        self._tilt_start_time = now
                        self._pulse_started()
                else:
                    self._anchor_run(now)
                self._motor_started.set()
                self._end_echo_window()
                LOGGER.debug("%s Motor start echo for %s; clock anchored.", self._gateway_handler.log_id, self._full_where)
                return True
            if is_stop:
                # Gateway relays a stop status before or shortly after our direction frame,
                # before the motor starts: an echo, not a keypad stop.
                LOGGER.debug("%s Ignoring stop echo for %s.", self._gateway_handler.log_id, self._full_where)
                return True
            # Opposite direction inside the window: somebody else took over.
            self._end_echo_window()
            return False
        # Pending stop: the relayed stop confirms it, anything else is external.
        self._end_echo_window()
        return False

    @callback
    def handle_event(self, message: OWNMessage) -> None:
        """Handle an event message."""
        if getattr(message, "is_translation", None) is True:
            return

        dimension = getattr(message, "dimension", getattr(message, "_dimension", None))
        if dimension == 11:
            # Dimension 11 is absolute shutter position (Go to level).
            # Handles both command writes (*#2*WHERE*#11#001*LEVEL##) and
            # event-session reflections (*#2*WHERE*#11#001#1*LEVEL##).
            # The motor needs time to get there and a keypad can stop it midway,
            # so the frame never changes state: the position comes from the
            # actuator's own DIM 10 / status reports.
            return

        # Ignore status queries/requests (e.g. *#2*...##) - they are polls, not state transitions
        if getattr(message, "_family", None) == "REQUEST" or getattr(message, "_message_type", None) == "STATUS_REQUEST":
            return

        # shutterLevel 255 means "unknown position", never a level. OWNd releases
        # after 2.0.0b8 report it as is_position_unknown; older ones pass 255 through.
        position = getattr(message, "current_position", None)
        position_unknown = getattr(message, "is_position_unknown", False) is True or (
            isinstance(position, int) and not 0 <= position <= 100
        )
        if position_unknown:
            position = None

        is_opening = bool(getattr(message, "is_opening", False))
        is_closing = bool(getattr(message, "is_closing", False))

        # Ignore frames with no what and no movement/position (e.g. unknown queries)
        if (
            getattr(message, "_what", None) is None
            and position is None
            and not position_unknown
            and not is_opening
            and not is_closing
        ):
            return

        # Ignore general commands (WHERE=0) and groups/areas during active calibration
        if self._calibrating and (
            getattr(message, "is_general", False)
            or getattr(message, "is_area", False) is True
            or getattr(message, "is_group", False) is True
            or str(getattr(message, "where", "")) == "0"
        ):
            LOGGER.debug("%s Ignoring general frame during calibration of %s: %s", self._gateway_handler.log_id, self._full_where, message)
            return

        # Record frame to recent calibration trace if calibration is active on this gateway
        if self._calibrating or self.calibration_hub.is_calibrating:
            _record_calibration_frame(
                self._gateway_handler,
                direction="rx",
                raw=str(getattr(message, "raw", getattr(message, "_raw", str(message)))),
                who=getattr(message, "who", getattr(message, "_who", 2)),
                where=getattr(message, "where", getattr(message, "_where", self._full_where)),
                what=getattr(message, "what", getattr(message, "_what", None)),
                entity_id=self.entity_id,
            )

        LOGGER.debug(
            "%s %s",
            self._gateway_handler.log_id,
            getattr(message, "human_readable_log", str(message)),
        )
        now = time.monotonic()
        moving = bool(is_opening or is_closing)
        if moving and position is not None:
            # While the motor runs the actuator repeats the level it started from
            # (*#2*0112*10*12*25*…## until *#2*0112*10*10*0*…##): the status
            # decides, and the level is only the last known position.
            if self._advanced:
                self._attr_current_cover_position = position
            position = None
        if position is None and self._handle_echo(message, now):
            self._publish_state()
            return
        if position is not None:
            if not self._is_tilting:
                # A status report during a slat pulse (a poll reply, a DIM 10 level)
                # must not cancel the auto-stop: the motor would run on.
                self._cancel_stop_task()
            previous_position = self._attr_current_cover_position
            self._attr_current_cover_position = position
            if not self._advanced:
                self._start_position = position
            self._move_start_time = None
            self._attr_is_opening = False
            self._attr_is_closing = False
            if self._slat_tilt and not self._is_tilting and position != previous_position and position in (0, 100):
                # The curtain reached an end stop, so the slats turned all the way.
                # A repeated report of the same level says nothing about the slats:
                # they may have been tilted open on a lowered blind.
                self._attr_current_cover_tilt_position = position
            if getattr(message, "is_closed", None) is not None:
                self._attr_is_closed = message.is_closed
            else:
                self._attr_is_closed = (self._attr_current_cover_position == 0)
        elif is_opening:
            if self._is_tilting and self._tilt_direction == "open":
                # Movement frame caused by our tilt pulse: preserve tilt state, do not anchor linear travel
                self._attr_is_opening = True
                self._attr_is_closing = False
            else:
                if self._is_tilting:
                    self._cancel_stop_task()
                    self._run_generation += 1
                    self._apply_tilt_stop(now)
                if self._calibrating and not self._attr_is_opening:
                    self._interrupt_calibration("an external open command arrived")
                if self._attr_is_closing:
                    self._cancel_stop_task()
                    self._run_generation += 1
                if not self._advanced and not self._attr_is_opening:
                    self._start_position = self.current_cover_position if self.current_cover_position is not None else 0
                    self._anchor_run(now)
                self._attr_is_opening = True
                self._attr_is_closing = False
                self._attr_is_closed = False
        elif is_closing:
            if self._is_tilting and self._tilt_direction == "close":
                # Movement frame caused by our tilt pulse: preserve tilt state, do not anchor linear travel
                self._attr_is_opening = False
                self._attr_is_closing = True
            else:
                if self._is_tilting:
                    self._cancel_stop_task()
                    self._run_generation += 1
                    self._apply_tilt_stop(now)
                if self._calibrating and not self._attr_is_closing:
                    self._interrupt_calibration("an external close command arrived")
                if self._attr_is_opening:
                    self._cancel_stop_task()
                    self._run_generation += 1
                if not self._advanced and not self._attr_is_closing:
                    self._start_position = self.current_cover_position if self.current_cover_position is not None else 100
                    self._anchor_run(now)
                self._attr_is_opening = False
                self._attr_is_closing = True
                self._attr_is_closed = False
        else:
            # Stopped (state == 0 or other): a genuine stop ends any timed run.
            was_tilting = self._is_tilting
            was_opening = self._attr_is_opening
            was_closing = self._attr_is_closing
            self._is_tilting = False
            self._cancel_stop_task()
            self._run_generation += 1
            self._last_stop_at = now
            self._stopped_event.set()
            if was_tilting:
                self._apply_tilt_stop(now)
            else:
                run_started_at = self._run_started_at
                if not self._advanced:
                    self._freeze_position(now)
                if self._slat_tilt and (was_opening or was_closing):
                    # Physical Venetian actuators rotate slats in the first slat_time of
                    # travel. Only a curtain run turns them: the relayed stop of our own
                    # slat pulse arrives with nothing running and must not snap them.
                    run_duration = (now - run_started_at) if run_started_at is not None else (self._slat_time + 1.0)
                    if was_opening:
                        if run_duration >= self._slat_time:
                            self._attr_current_cover_tilt_position = 100
                        else:
                            fraction = run_duration / self._slat_time
                            curr = self.current_cover_tilt_position if self.current_cover_tilt_position is not None else 0
                            self._attr_current_cover_tilt_position = min(100, int(round(curr + (100 - curr) * fraction)))
                    elif was_closing:
                        if run_duration >= self._slat_time:
                            self._attr_current_cover_tilt_position = 0
                        else:
                            fraction = run_duration / self._slat_time
                            curr = self.current_cover_tilt_position if self.current_cover_tilt_position is not None else 100
                            self._attr_current_cover_tilt_position = max(0, int(round(curr - curr * fraction)))
                    if self._attr_current_cover_position == 0:
                        self._attr_current_cover_tilt_position = 0
                    elif self._attr_current_cover_position == 100:
                        self._attr_current_cover_tilt_position = 100
            self._attr_is_opening = False
            self._attr_is_closing = False
            if getattr(message, "is_closed", None) is not None:
                self._attr_is_closed = message.is_closed
            elif self._attr_current_cover_position is not None:
                self._attr_is_closed = (self._attr_current_cover_position == 0)

        if position_unknown and self._advanced:
            # The actuator itself does not know where it is: stop showing a stale level.
            self._attr_current_cover_position = None
            if not (self._attr_is_opening or self._attr_is_closing):
                self._attr_is_closed = None

        self._publish_state()


def __getattr__(name: str) -> Any:
    if name in ("CoverFamily", "CoverScope", "MyHOMEScopeCover"):
        from . import cover_scope

        return getattr(cover_scope, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals().keys(), "CoverFamily", "CoverScope", "MyHOMEScopeCover"])


__all__ = [
    "CalibrationInterrupted",
    "CoverCalibrationHub",
    "CoverFamily",
    "CoverScope",
    "ECHO_WINDOW",
    "MOTOR_START_DELAY",
    "MyHOMECover",
    "MyHOMEScopeCover",
    "WRITE_TIMEOUT",
    "_calibration_lock",
    "_gateway_key",
    "_normalize_mac",
    "_record_calibration_frame",
    "_stored_calibration",
    "async_setup_entry",
    "async_stop_cover_calibration",
    "async_unload_entry",
    "compute_freeze_position",
    "compute_interpolated_position",
    "get_calibration_hub",
    "get_last_calibration_trace",
    "is_in_echo_window",
    "travel_for",
]
