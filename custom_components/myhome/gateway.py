"""Code to handle a MyHome Gateway."""
from __future__ import annotations

import asyncio
import collections
import inspect
import time
from collections.abc import Iterable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from OWNd.connection import OWNCommandSession, OWNEventSession, OWNGateway, OWNSession
from OWNd.message import OWNCommand
from OWNd.profiles import GenericGatewayProfile, get_gateway_profile

from .bus_monitor import BusMonitor
from .const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    LOGGER,
)
from .device_health import DeviceHealth
from .gateway_events import GatewayEventDispatcher
from .gateway_resync import LightingResyncManager
from .gateway_sessions import (
    COMMAND_SESSION_IDLE_TIMEOUT,
    EVENT_INACTIVITY_TIMEOUT,
    EVENT_READY_TIMEOUT,
    EVENT_RESTART_BACKOFF_MAX,
    EVENT_RESTART_BACKOFF_MIN,
    EVENT_STALL_TIMEOUT,
    CommandPriorityQueue,
    CommandWorkerPool,
    EventSessionRunner,
    FrameScheduler,
    _cancel_written,
    _resolve_written,
    _session_is_open,
)
from .identity import (
    GatewayIdentityEvidence,
    GatewayIdentityMixin,
    GatewayIdentityResolution,
    read_who13,
    read_who1013,
    resolve_gateway_identity,
)
from .repairs import (
    async_create_identity_corrected_issue,
    async_create_identity_issue,
    async_create_unconfigured_timezone_issue,
    async_create_unknown_model_issue,
    async_delete_identity_issue,
    async_delete_unconfigured_timezone_issue,
    async_delete_unknown_model_issue,
)
from .topology import GatewayTopologyMixin

__all__ = [
    "AVAILABILITY_GRACE",
    "COMMAND_SESSION_IDLE_TIMEOUT",
    "CommandPriorityQueue",
    "CommandWorkerPool",
    "EVENT_INACTIVITY_TIMEOUT",
    "EVENT_READY_TIMEOUT",
    "EVENT_RESTART_BACKOFF_MAX",
    "EVENT_RESTART_BACKOFF_MIN",
    "EVENT_STALL_TIMEOUT",
    "EventSessionRunner",
    "FrameScheduler",
    "GatewayEventDispatcher",
    "GatewayIdentityEvidence",
    "GatewayIdentityMixin",
    "GatewayIdentityResolution",
    "GatewayTopologyMixin",
    "GenericGatewayProfile",
    "LightingResyncManager",
    "MyHOMEGatewayHandler",
    "OWNCommandSession",
    "OWNEventSession",
    "OWNGateway",
    "OWNSession",
    "_cancel_written",
    "_resolve_written",
    "_session_is_open",
    "async_call_later",
    "async_create_identity_corrected_issue",
    "async_create_identity_issue",
    "async_create_unconfigured_timezone_issue",
    "async_create_unknown_model_issue",
    "async_delete_identity_issue",
    "async_delete_unconfigured_timezone_issue",
    "async_delete_unknown_model_issue",
    "async_dispatcher_send",
    "command_session_default",
    "command_session_limit",
    "dr",
    "er",
    "get_gateway_profile",
    "read_who13",
    "read_who1013",
    "resolve_gateway_identity",
    "time",
]


def command_session_limit(model: str | None) -> int | None:
    """Return how many command sessions a known gateway model accepts at once."""
    profile = get_gateway_profile(model)
    if isinstance(profile, GenericGatewayProfile):
        return None
    return int(profile.max_command_sessions)


def command_session_default(model: str | None) -> int:
    """Return how many command sessions a new entry should start with."""
    profile = get_gateway_profile(model)
    default = int(getattr(profile, "default_command_sessions", 1))
    limit = command_session_limit(model)
    return max(1, min(default, limit) if limit is not None else 1)


AVAILABILITY_GRACE = 60
BUS_QUIET_PERIOD = 0.75
BUS_QUIET_CAP = 15.0
PACED_WRITE_TIMEOUT = 60.0
DISCOVERY_REQUESTS: tuple[tuple[int, str], ...] = (
    (1, "*#1*0##"),
    (2, "*#2*0##"),
    (4, "*#4*0##"),
    (5, "*#5*0##"),
    (16, "*#16*0*5##"),
    (18, "*#18*51*51##"),
    (18, "*#18*51*113##"),
)


class MyHOMEGatewayHandler(GatewayTopologyMixin, GatewayIdentityMixin):
    """Manages a single MyHOME Gateway."""

    device_registry_id: str | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        generate_events: bool = False,
        broadcast_resync: bool = True,
    ) -> None:
        """Initialize the MyHOME Gateway handler."""
        build_info = {
            "address": config_entry.data.get(CONF_HOST),
            "port": config_entry.data.get(CONF_PORT, 20000),
            "password": config_entry.data.get(CONF_PASSWORD),
            "ssdp_location": config_entry.data.get(CONF_SSDP_LOCATION, ""),
            "ssdp_st": config_entry.data.get(CONF_SSDP_ST, ""),
            "deviceType": config_entry.data.get(CONF_DEVICE_TYPE, ""),
            "friendlyName": config_entry.data.get(CONF_FRIENDLY_NAME, ""),
            "manufacturer": config_entry.data.get(CONF_MANUFACTURER, ""),
            "manufacturerURL": config_entry.data.get(CONF_MANUFACTURER_URL, ""),
            "modelName": config_entry.data.get(CONF_NAME, "Generic"),
            "modelNumber": config_entry.data.get(CONF_FIRMWARE, ""),
            "serialNumber": config_entry.data.get(CONF_MAC, ""),
            "UDN": config_entry.data.get(CONF_UDN, ""),
        }
        self.hass = hass
        self.config_entry = config_entry
        self.generate_events = generate_events
        self.gateway = OWNGateway(build_info)
        self.is_connected = False
        self._available = False
        self._failover_active = False
        self._setup_at = time.monotonic()
        self._unavailable_timer: CALLBACK_TYPE | None = None
        self.listening_worker: asyncio.Task[None] | None = None
        self.bus_monitor = BusMonitor()
        self.device_health = DeviceHealth(self)
        self.device_registry_id = None
        self.broadcast_resync = broadcast_resync
        self.has_central_unit = False

        self._who13: dict[str, Any] = {
            "code": None, "model": None, "model_official": None, "model_observed": None,
            "firmware": None, "kernel": None, "distribution": None,
        }
        self._who1013: dict[str, Any] = {
            "code": None, "model": None, "names": (), "pending": False,
            "n_conf": None, "brand": None, "line": None,
        }
        self._identity_conflict: str | None = None
        self._identity_resolution: GatewayIdentityResolution | None = None

        self._event_dispatcher = GatewayEventDispatcher(self)
        self._resync_manager = LightingResyncManager(self)
        self._event_runner = EventSessionRunner(self)
        self._command_pool = CommandWorkerPool(
            self,
            event_session_ready=self._event_runner.event_session_ready,
        )

        self._cen_devices: set[tuple[int, Any]] = self._event_dispatcher.cen_devices
        self._resync_timers: dict[str, CALLBACK_TYPE] = self._resync_manager.resync_timers
        self._resync_group_echoes: dict[str, int] = self._resync_manager.resync_group_echoes
        self._recent_ptp: collections.deque[tuple[float, str, str | None]] = self._resync_manager.recent_ptp
        self._sender_stop: asyncio.Event = self._command_pool.sender_stop
        self._initial_discovery_done: asyncio.Event = asyncio.Event()
        # Paced sweeps in flight; close_listener() bumps the epoch so a sweep that was
        # running at the time cannot end one started after the reconnect.
        self._active_sweeps: int = 0
        self._sweep_epoch: int = 0
        self._sweep_idle: asyncio.Event = asyncio.Event()
        self._sweep_idle.set()
        self._last_event_frame_at: float = 0.0

    @property
    def send_buffer(self) -> FrameScheduler | asyncio.Queue[Any]:
        """Return the send queue."""
        return self._command_pool.send_buffer

    @send_buffer.setter
    def send_buffer(self, value: FrameScheduler | asyncio.Queue[Any]) -> None:
        self._command_pool.send_buffer = value

    @property
    def sending_workers(self) -> list[asyncio.Task[None]]:
        """Return the sending workers list."""
        return self._command_pool.sending_workers

    @sending_workers.setter
    def sending_workers(self, value: list[asyncio.Task[None]]) -> None:
        self._command_pool.sending_workers = value

    @property
    def _terminate_listener(self) -> bool:
        """Whether the listener task is terminating."""
        return self._event_runner._terminate_listener

    @_terminate_listener.setter
    def _terminate_listener(self, value: bool) -> None:
        self._event_runner._terminate_listener = value

    @property
    def _terminate_sender(self) -> bool:
        """Whether the sender task is terminating."""
        return self._command_pool._terminate_sender

    @_terminate_sender.setter
    def _terminate_sender(self, value: bool) -> None:
        self._command_pool._terminate_sender = value

    @property
    def _event_session_ready(self) -> asyncio.Event:
        """Event set when the event session is established."""
        return self._event_runner._event_session_ready

    def _ensure_cen_device(self, who: int, object_id: int | str, model: str | None = None) -> None:
        """Ensure CEN/CEN+ scenario unit is registered in device registry."""
        self._event_dispatcher.ensure_cen_device(who, object_id, model=model)

    @property
    def mac(self) -> str:
        """Return normalized MAC address."""
        serial = self.gateway.serial
        if serial:
            formatted = dr.format_mac(serial)
            if formatted:
                return formatted
        return serial or ""

    @property
    def unique_id(self) -> str:
        """Return gateway unique ID."""
        return self.mac

    @property
    def id(self) -> str | None:
        """Return gateway ID."""
        return self.mac

    @property
    def log_id(self) -> str:
        """Return logging prefix."""
        return str(self.gateway.log_id)

    @property
    def manufacturer(self) -> str:
        """Return manufacturer name."""
        mfg = self.gateway.manufacturer
        if isinstance(mfg, (list, tuple)):
            return str(mfg[0]) if mfg else "BTicino S.p.A."
        return str(mfg) if mfg else "BTicino S.p.A."

    @property
    def name(self) -> str:
        """Return gateway name."""
        return f"{self.gateway.model_name} Gateway"

    @property
    def model(self) -> str:
        """Return gateway model name."""
        return str(self.gateway.model_name)

    @property
    def firmware(self) -> str | None:
        """Return gateway firmware version."""
        firmware: str | None = self.gateway.firmware
        return firmware

    @property
    def supports_zone_status(self) -> bool:
        """Whether the gateway supports point-to-point zone status queries (*#4*Z##)."""
        norm = self.model.upper()
        return not any(
            k in norm
            for k in (
                "F455",
                "F454",
                "MH200",
                "MH201",
                "MH202",
                "F452",
                "F453",
                "F458",
                "F459",
                "H4684",
            )
        )

    @property
    def profile(self) -> Any:
        """Return gateway profile."""
        return self.gateway.profile

    @property
    def command_session_idle_timeout(self) -> float:
        """Idle timeout before releasing the command session socket."""
        profile = getattr(self.gateway, "profile", None)
        profile_timeout = getattr(profile, "command_session_idle_timeout", None) if profile else None
        idle_timeout_default = float(COMMAND_SESSION_IDLE_TIMEOUT)
        return float(profile_timeout) if profile_timeout is not None else idle_timeout_default

    async def test(self) -> dict[str, Any]:
        """Test gateway connection."""
        result: dict[str, Any] = await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()
        return result

    @callback
    def _on_event_connection_state_change(self, connected: bool) -> None:
        """Gate commands and publish sustained event-session availability."""
        was_connected = self.is_connected
        self.is_connected = connected
        self._event_runner.is_connected = connected
        self._update_event_watchdog(progress=False)
        if connected:
            if self._failover_active:
                self._clear_failover()
                LOGGER.info(
                    "%s Primary gateway reconnected; warm standby failover deactivated, returning to primary gateway.",
                    self.log_id,
                )
            self._event_session_ready.set()
            self._who1013["pending"] = False
            if self._unavailable_timer is not None:
                self._unavailable_timer()
                self._unavailable_timer = None
            if not self._available:
                self._available = True
                LOGGER.info("%s Gateway available again.", self.log_id)
                self._notify_availability()
            if not was_connected and self._initial_discovery_done.is_set():
                runtime = getattr(self.config_entry, "runtime_data", None)
                expose_mgr = getattr(runtime, "expose_manager", None)
                sync_fn = getattr(expose_mgr, "async_sync_all", None)
                if inspect.iscoroutinefunction(sync_fn):
                    self.hass.async_create_task(sync_fn())
            return

        self._event_session_ready.clear()
        if self._terminate_listener:
            return
        if self._available and self._unavailable_timer is None:
            LOGGER.warning(
                "%s Gateway connection lost; marking unavailable in %ss if not recovered.",
                self.log_id,
                AVAILABILITY_GRACE,
            )
            self._unavailable_timer = async_call_later(
                self.hass,
                AVAILABILITY_GRACE,
                self._mark_unavailable,
            )

    @callback
    def _mark_unavailable(self, _now: Any) -> None:
        """Mark the gateway unavailable after the reconnect grace period."""
        if self._unavailable_timer is not None:
            self._unavailable_timer()
            self._unavailable_timer = None
        if self.is_connected or not self._available:
            return
        self._available = False
        self._evaluate_failover()
        if self.available:  # carried by the warm standby; entities stay available
            return
        LOGGER.warning(
            "%s Gateway unavailable (outage exceeded %ss).",
            self.log_id,
            AVAILABILITY_GRACE,
        )
        self._notify_availability()

    @callback
    def _update_event_watchdog(self, *, progress: bool) -> None:
        """Arm the stall deadline while disconnected, disarm it while connected."""
        self._event_runner._update_event_watchdog(progress=progress)

    async def listening_loop(self) -> None:
        """Run the event session, recreating it whenever it dies or stalls."""
        await self._event_runner.listening_loop()

    def profile_supports_who(self, who: int) -> bool:
        """Return whether the startup sweep asks this gateway's profile about a WHO."""
        return self._profile_supports_who(who)

    def _profile_supports_who(self, who: int) -> bool:
        """Return whether the gateway profile advertises a WHO subsystem (True when unknown)."""
        profile = getattr(self.gateway, "profile", None)
        supports = getattr(profile, "supports_who", None)
        if not callable(supports):
            return True
        try:
            return bool(supports(who))
        except Exception:  # pragma: no cover - defensive against foreign profile objects
            return True

    async def _process_message(self, message: Any) -> None:
        """Process a received message and dispatch to Home Assistant."""
        await self._event_dispatcher.process_message(message)

    async def sending_loop(self, worker_id: int) -> None:
        """Run sending loop for worker."""
        await self._command_pool.sending_loop(worker_id)

    @property
    def initial_discovery_pending(self) -> bool:
        """Whether the startup sweep is still running."""
        return not self._initial_discovery_done.is_set()

    @property
    def sweep_active(self) -> bool:
        """Whether a paced bus sweep is currently running."""
        return self._active_sweeps > 0

    def _begin_sweep(self) -> int:
        """Mark a paced sweep as active so newly discovered entities defer polls.

        Returns the epoch to hand back to ``_end_sweep``.
        """
        self._active_sweeps += 1
        self._sweep_idle.clear()
        return self._sweep_epoch

    def _end_sweep(self, epoch: int | None = None) -> None:
        """Mark a paced sweep as completed and unblock deferred polls when idle.

        A sweep begun before close_listener() reset the count (an older ``epoch``)
        no longer counts.
        """
        if epoch is not None and epoch != self._sweep_epoch:
            return
        self._active_sweeps = max(0, self._active_sweeps - 1)
        if self._active_sweeps == 0:
            self._sweep_idle.set()

    def note_event_frame(self) -> None:
        """Record that a frame just arrived on the event session."""
        self._last_event_frame_at = time.monotonic()

    async def _wait_for_bus_quiet(self, since: float) -> None:
        """Wait until no event frame arrived for BUS_QUIET_PERIOD since ``since``, at most BUS_QUIET_CAP."""
        deadline = time.monotonic() + BUS_QUIET_CAP
        while True:
            now = time.monotonic()
            if now >= deadline:
                LOGGER.debug("%s Bus still busy after %.0fs; sending the next request", self.log_id, BUS_QUIET_CAP)
                return
            quiet_since = max(since, self._last_event_frame_at)
            remaining = quiet_since + BUS_QUIET_PERIOD - now
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, deadline - now))

    async def _wait_for_write(self, written: Any, message: OWNCommand) -> None:
        """Wait, at most PACED_WRITE_TIMEOUT, until a queued frame was written or dropped."""
        if not isinstance(written, asyncio.Future):
            return
        await asyncio.wait({written}, timeout=PACED_WRITE_TIMEOUT)
        if not written.done():
            LOGGER.warning(
                "%s `%s` not written after %.0fs; sending the next request anyway",
                self.log_id,
                message,
                PACED_WRITE_TIMEOUT,
            )

    async def send_paced(self, frames: Iterable[str], *, status_request: bool = True) -> None:
        """Send general requests one at a time, each once the bus finished answering the last."""
        epoch = self._begin_sweep()
        try:
            for frame in frames:
                cmd = OWNCommand.parse(frame)
                if cmd is None:
                    continue
                written = await (self.send_status_request(cmd) if status_request else self.send(cmd))
                await self._wait_for_write(written, cmd)
                await self._wait_for_bus_quiet(time.monotonic())
        finally:
            self._end_sweep(epoch)

    def _discovery_frames(self) -> list[str]:
        """The startup general requests this gateway sends, after topology and profile gates."""
        frames: list[str] = []
        for who, frame in DISCOVERY_REQUESTS:
            if getattr(self, "is_follower", False) is True and who not in getattr(self, "delegated_whos", set()):
                LOGGER.debug("%s Skipping WHO=%s discovery: follower gateway on shared bus.", self.log_id, who)
                continue
            if who in getattr(self, "delegated_away_whos", set()):
                LOGGER.debug("%s Skipping WHO=%s discovery: delegated to a secondary gateway.", self.log_id, who)
                continue
            if not self._profile_supports_who(who):
                LOGGER.debug(
                    "%s Skipping WHO=%s discovery: not supported by %s profile.",
                    self.log_id,
                    who,
                    self.gateway.model_name,
                )
                continue
            frames.append(frame)
        return frames

    async def initial_discovery(self) -> None:
        """Send the startup sweep that discovers devices missing from the config."""
        try:
            await self.send_paced(self._discovery_frames())
        finally:
            self._initial_discovery_done.set()
        runtime = getattr(self.config_entry, "runtime_data", None)
        expose_mgr = getattr(runtime, "expose_manager", None)
        sync_fn = getattr(expose_mgr, "async_sync_all", None)
        if inspect.iscoroutinefunction(sync_fn):
            await sync_fn()

    async def wait_for_initial_discovery(self, timeout: float | None = None) -> None:
        """Wait until startup initial discovery and any active paced sweep finish."""
        if self._initial_discovery_done.is_set() and self._sweep_idle.is_set():
            return
        if timeout is None:
            timeout = len(DISCOVERY_REQUESTS) * (PACED_WRITE_TIMEOUT + BUS_QUIET_CAP)
        try:
            async with asyncio.timeout(timeout):
                if not self._initial_discovery_done.is_set():
                    await self._initial_discovery_done.wait()
                if not self._sweep_idle.is_set():
                    await self._sweep_idle.wait()
        except TimeoutError:
            LOGGER.debug("%s Timed out waiting for initial discovery", self.log_id)

    async def close_listener(self) -> bool:
        """Close event listener and cancel pending actions."""
        LOGGER.info("%s Closing event listener", self.log_id)
        if self._unavailable_timer is not None:
            self._unavailable_timer()
        self._resync_manager.cancel_all()
        self._unavailable_timer = None
        self.is_connected = False
        self._available = False
        self._clear_failover()
        self._active_sweeps = 0
        self._sweep_epoch += 1
        self._sweep_idle.set()
        self._initial_discovery_done.set()
        if self.is_standby:
            primary = self._get_primary_gateway()
            if primary is not None and not primary._available:
                primary._evaluate_failover()
                async_dispatcher_send(self.hass, primary.availability_signal)

        self._event_runner.close()
        self._command_pool.close()
        return True

    async def send(self, message: OWNCommand, *, is_cancel: bool = False) -> asyncio.Future[float]:
        """Queue a command; the returned future resolves to the monotonic write time."""
        delegated = self._delegated_target(message)
        if delegated is not None:
            if is_cancel:
                return await delegated.send(message, is_cancel=True)
            return await delegated.send(message)
        standby = self._failover_target(message)
        if standby is not None:
            if is_cancel:
                return await standby.send(message, is_cancel=True)
            return await standby.send(message)
        if is_cancel:
            return await self._command_pool.send(message, is_cancel=True)
        return await self._command_pool.send(message)

    async def send_fade_step(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue an intermediate software fade step; superseded by newer steps or commands."""
        delegated = self._delegated_target(message)
        if delegated is not None:
            return await delegated.send_fade_step(message)
        standby = self._failover_target(message)
        if standby is not None:
            return await standby.send_fade_step(message)
        return await self._command_pool.send_fade_step(message)

    async def send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a status request; the returned future resolves to the monotonic write time."""
        delegated = self._delegated_target(message)
        if delegated is not None:
            return await delegated.send_status_request(message)
        standby = self._failover_target(message)
        if standby is not None:
            return await standby.send_status_request(message)
        return await self._command_pool.send_status_request(message)

    def _known_light_areas(self) -> list[str]:
        """Return list of known light areas."""
        return self._resync_manager.known_light_areas()

    def _schedule_resync(self, message: Any) -> None:
        """Schedule a debounced resync."""
        self._resync_manager.schedule_resync(message)

    async def _resync_broadcast(self, where: str) -> None:
        """Execute a resync broadcast."""
        await self._resync_manager.execute_resync(where)

    _execute_resync = _resync_broadcast
