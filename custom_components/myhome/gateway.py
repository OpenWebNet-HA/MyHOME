"""Code to handle a MyHome Gateway."""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Any, cast

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
from OWNd.message import OWNCommand, OWNGatewayEvent
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
    DOMAIN,
    IDENTIFICATION_MANUAL,
    IDENTIFICATION_SERIAL,
    IDENTIFICATION_SSDP,
    IDENTIFICATION_UNKNOWN,
    IDENTIFICATION_WHO13,
    LOGGER,
    ROLE_STANDBY,
    SHARED_BUS_EVIDENCE_COUNT,
    SHARED_BUS_EVIDENCE_WINDOW_S,
    SHARED_BUS_RX_WINDOW_S,
    SHARED_BUS_TX_ECHO_S,
    TOPOLOGY_SHARED,
    WHO1013_BRANDS,
    WHO1013_LINES,
)
from .gateway_events import GatewayEventDispatcher
from .gateway_resync import LightingResyncManager
from .gateway_sessions import (
    COMMAND_SESSION_IDLE_TIMEOUT,
    EVENT_READY_TIMEOUT,
    EVENT_RESTART_BACKOFF_MAX,
    EVENT_RESTART_BACKOFF_MIN,
    EVENT_STALL_TIMEOUT,
    CommandWorkerPool,
    EventSessionRunner,
    _cancel_written,
    _resolve_written,
    _session_is_open,
)
from .identity import (
    GatewayIdentityEvidence,
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
from .topology import (
    delegated_away_whos,
    entry_delegated_whos,
    entry_is_follower,
    entry_primary_mac,
    entry_role,
    entry_topology,
)

__all__ = [
    "AVAILABILITY_GRACE",
    "COMMAND_SESSION_IDLE_TIMEOUT",
    "CommandWorkerPool",
    "EVENT_READY_TIMEOUT",
    "EVENT_RESTART_BACKOFF_MAX",
    "EVENT_RESTART_BACKOFF_MIN",
    "EVENT_STALL_TIMEOUT",
    "EventSessionRunner",
    "GatewayEventDispatcher",
    "GenericGatewayProfile",
    "LightingResyncManager",
    "MyHOMEGatewayHandler",
    "OWNCommandSession",
    "OWNEventSession",
    "OWNGateway",
    "OWNSession",
    "_StatusRequestLogFilter",
    "_cancel_written",
    "_resolve_written",
    "_session_is_open",
    "async_call_later",
    "async_dispatcher_send",
    "command_session_limit",
    "dr",
    "er",
    "get_gateway_profile",
    "time",
]


class _StatusRequestLogFilter(logging.Filter):
    """Downgrade spurious status-request retry errors to DEBUG.

    OWNd < 2.0.0b8 logged intermediate status-request retries (*#...##) as ERROR
    instead of DEBUG when the gateway NACKed uninstalled optional subsystems
    (issue #406, OpenWebNet-HA/OWNd#43).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if (
            record.levelno == logging.ERROR
            and "Could not send message `*#" in record.getMessage()
        ):
            record.levelno = logging.DEBUG
            record.levelname = "DEBUG"
        return True


LOGGER.addFilter(_StatusRequestLogFilter())


def command_session_limit(model: str | None) -> int | None:
    """Return how many command sessions a known gateway model accepts at once.

    Returns ``None`` for a model OWNd has no profile for: the generic profile's
    limit of 1 is a safe default, not a measured limit, so it must not override
    what the user configured.  An MH200N given 3 sessions stops answering new
    ones and its event session goes quiet (issue #425).
    """
    profile = get_gateway_profile(model)
    if isinstance(profile, GenericGatewayProfile):
        return None
    return int(profile.max_command_sessions)


AVAILABILITY_GRACE = 60


class MyHOMEGatewayHandler:
    """Manages a single MyHOME Gateway."""

    # Device registry id of the gateway device; set once the entry's device exists.
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
        self.device_registry_id = None
        self.broadcast_resync = broadcast_resync

        # Identity evidence, recorded as observed and exported in diagnostics, the
        # WebSocket info payload and every trace (see identification()). What the
        # integration believes is decided in one place from all of it: _resolve_identity.
        self._who13: dict[str, Any] = {
            "code": None, "model": None, "model_official": None, "model_observed": None,
            "firmware": None, "kernel": None, "distribution": None,
        }
        # WHO=1013 dimension 1: asked once when WHO=13 answered a code shared by
        # several models; `pending` until it answers or the session reconnects. A reply
        # is OBJECT_MODEL * N_CONF * BRAND * LINE - only the first decides the identity,
        # the rest is recorded for diagnostics.
        self._who1013: dict[str, Any] = {
            "code": None, "model": None, "names": (), "pending": False,
            "n_conf": None, "brand": None, "line": None,
        }
        self._identity_conflict: str | None = None
        self._identity_resolution: GatewayIdentityResolution | None = None

        # Decomposed runners and managers
        self._event_dispatcher = GatewayEventDispatcher(self)
        self._resync_manager = LightingResyncManager(self)
        self._event_runner = EventSessionRunner(self)
        self._command_pool = CommandWorkerPool(
            self,
            event_session_ready=self._event_runner.event_session_ready,
        )

        # Expose shared containers for backward compatibility
        self._cen_devices: set[tuple[int, Any]] = self._event_dispatcher.cen_devices
        self._resync_timers: dict[str, CALLBACK_TYPE] = self._resync_manager.resync_timers
        self._resync_group_echoes: dict[str, int] = self._resync_manager.resync_group_echoes
        self._recent_ptp: collections.deque[tuple[float, str, str | None]] = self._resync_manager.recent_ptp
        self._sender_stop: asyncio.Event = self._command_pool.sender_stop

    @property
    def send_buffer(self) -> asyncio.Queue[Any]:
        """Return the send queue."""
        return self._command_pool.send_buffer

    @send_buffer.setter
    def send_buffer(self, value: asyncio.Queue[Any]) -> None:
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

    def _ensure_cen_device(self, who: int, object_id: int | str) -> None:
        """Ensure CEN/CEN+ scenario unit is registered in device registry."""
        self._event_dispatcher.ensure_cen_device(who, object_id)

    @property
    def identification_source(self) -> str:
        """How the configured model was established (SSDP > manual > serial > WHO=13)."""
        data = getattr(self.config_entry, "data", None) or {}
        if data.get("transport_type") == "serial":
            return IDENTIFICATION_SERIAL
        if data.get(CONF_SSDP_LOCATION) or data.get(CONF_UDN):
            return IDENTIFICATION_SSDP
        model_source = data.get("model_source")
        if model_source == IDENTIFICATION_WHO13:
            return IDENTIFICATION_WHO13
        if model_source == IDENTIFICATION_MANUAL:
            # The owner picked the model in the options flow; that choice outranks
            # any WHO=13 label applied earlier.
            return IDENTIFICATION_MANUAL
        model = data.get(CONF_NAME)
        if model and str(model).strip().lower() not in ("", "generic", "gateway", "unknown"):
            return IDENTIFICATION_MANUAL
        return IDENTIFICATION_UNKNOWN

    def identification(self) -> dict[str, Any]:
        """Evidence behind the model label, for diagnostics and trace exports."""
        data = getattr(self.config_entry, "data", None) or {}
        return {
            "model": self.model,
            "source": self.identification_source,
            "configured_model": data.get(CONF_NAME),
            "ssdp_model": data.get(CONF_NAME) if self.identification_source == IDENTIFICATION_SSDP else None,
            "ssdp_location": data.get(CONF_SSDP_LOCATION) or None,
            "who13_code": self._who13["code"],
            "who13_model": self._who13["model"],
            "who13_model_official": self._who13["model_official"],
            "who13_model_observed": self._who13["model_observed"],
            "who13_firmware": self._who13["firmware"],
            "who13_kernel": self._who13["kernel"],
            "who13_distribution": self._who13["distribution"],
            "who1013_code": self._who1013["code"],
            "who1013_model": self._who1013["model"],
            # The same product under another brand (Legrand's 003598 for a BTicino
            # F454), never an order code: the owner's box may carry this name.
            "who1013_other_names": list(self._who1013["names"]),
            "who1013_n_conf": self._who1013["n_conf"],
            "who1013_brand": self._describe_who1013("brand", WHO1013_BRANDS),
            "who1013_line": self._describe_who1013("line", WHO1013_LINES),
            "profile": type(self.profile).__name__ if self.profile is not None else None,
            "conflict": self._identity_conflict,
        }

    def _describe_who1013(self, field: str, table: dict[str, str]) -> str | None:
        """Render a WHO=1013 metadata value as ``code (meaning)``, or the bare code.

        Only values actually observed are in the tables, so an unseen one still
        reaches diagnostics instead of being dropped as unrecognised.
        """
        value = self._who1013[field]
        if value is None:
            return None
        meaning = table.get(str(value))
        return f"{value} ({meaning})" if meaning else str(value)

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
        return cast(str | None, self.gateway.firmware)

    @property
    def profile(self) -> Any:
        """Return gateway profile."""
        return self.gateway.profile

    @property
    def command_session_idle_timeout(self) -> float:
        """Idle timeout before releasing the command session socket.

        Uses the gateway profile's custom timeout if configured; otherwise falls
        back to COMMAND_SESSION_IDLE_TIMEOUT.
        """
        profile = getattr(self.gateway, "profile", None)
        profile_timeout = getattr(profile, "command_session_idle_timeout", None) if profile else None
        idle_timeout_default = float(COMMAND_SESSION_IDLE_TIMEOUT)
        return float(profile_timeout) if profile_timeout is not None else idle_timeout_default

    @property
    def available(self) -> bool:
        """Return the grace-filtered gateway availability."""
        if self._available:
            return True
        standby = self._get_standby_gateway()
        if standby is not None and standby.available:
            return True
        return False

    def is_who_available(self, who: str | int) -> bool:
        """Return True if this gateway (or its failover) can currently handle the given WHO."""
        if self._available:
            return True
        standby = self._get_standby_gateway()
        if standby is not None and standby.available:
            return standby._profile_supports_who(int(who))
        return False

    @property
    def availability_signal(self) -> str:
        """Return the dispatcher signal for availability changes."""
        return f"{DOMAIN}_{self.mac}_availability"

    @property
    def bus_topology(self) -> str:
        """Bus topology for this gateway: 'standalone' or 'shared'."""
        return entry_topology(self.config_entry)

    @property
    def gateway_role(self) -> str:
        """Role of this gateway: 'primary', 'secondary' or 'standby'."""
        return entry_role(self.config_entry)

    @property
    def is_follower(self) -> bool:
        """Return True if this gateway is a secondary or standby gateway on a shared bus."""
        return entry_is_follower(self.config_entry)

    @property
    def is_standby(self) -> bool:
        """Return True if this gateway is configured as a warm standby failover."""
        return self.bus_topology == TOPOLOGY_SHARED and self.gateway_role == ROLE_STANDBY

    @property
    def is_primary(self) -> bool:
        """Return True if this gateway acts as primary (or standalone) on its bus."""
        return not self.is_follower

    @property
    def failover_active(self) -> bool:
        """Return True if failover to standby is currently active."""
        return self._failover_active

    @property
    def primary_gateway_mac(self) -> str | None:
        """The primary gateway MAC if this gateway is secondary."""
        return entry_primary_mac(self.config_entry)

    @property
    def delegated_whos(self) -> set[int]:
        """Subsystems (WHOs) this secondary gateway discovers for the bus."""
        return entry_delegated_whos(self.config_entry)

    @property
    def delegated_away_whos(self) -> set[int]:
        """Subsystems this primary leaves to its secondaries: no sweep, no new entities."""
        if self.is_follower or not getattr(self, "hass", None):
            return set()
        return delegated_away_whos(self.hass, self.mac)

    @property
    def bus_group(self) -> str:
        """The configured bus this gateway belongs to (the primary's MAC)."""
        return self.primary_gateway_mac or self.mac

    def _get_standby_gateway(self) -> "MyHOMEGatewayHandler" | None:
        """Find the standby gateway configured for this primary gateway."""
        if not getattr(self, "hass", None):
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            gw: MyHOMEGatewayHandler | None = getattr(runtime_data, "gateway", None)
            if (
                gw is not None
                and gw.bus_topology == TOPOLOGY_SHARED
                and gw.gateway_role == ROLE_STANDBY
                and gw.primary_gateway_mac == self.mac
            ):
                return gw
        return None

    def _get_primary_gateway(self) -> "MyHOMEGatewayHandler" | None:
        """Find the configured primary gateway for this secondary/standby gateway."""
        if not getattr(self, "hass", None) or not self.primary_gateway_mac:
            return None
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            gw: MyHOMEGatewayHandler | None = getattr(runtime_data, "gateway", None)
            if gw is not None and gw.mac == self.primary_gateway_mac:
                return gw
        return None

    def _record_failover_active(self, standby: "MyHOMEGatewayHandler") -> None:
        """Record that failover to standby is currently active and raise repair issue."""
        if self._failover_active:
            return
        self._failover_active = True
        LOGGER.warning(
            "%s Primary gateway offline; warm standby %s now carries its traffic.",
            self.log_id,
            standby.log_id,
        )
        from .repairs import async_create_failover_issue
        async_create_failover_issue(
            self.hass,
            self.mac,
            standby.mac,
            self.name,
            standby.name,
        )

    def _clear_failover(self) -> None:
        """Forget an active failover and resolve its repair issue."""
        if not self._failover_active:
            return
        self._failover_active = False
        from .repairs import async_delete_failover_issue
        async_delete_failover_issue(self.hass, self.mac)

    def _bridge_to_primary(self, message: Any) -> None:
        """Hand a bus frame to the offline primary's entities (warm standby only).

        Only the primary's own standby bridges: a secondary on the same bus sees
        the same frame, and bridging from both would deliver it twice. Frames of
        the gateway itself (WHO=13/1013) describe this gateway, not the bus.
        """
        if not self.is_standby or getattr(message, "who", None) in (13, 1013):
            return
        primary_gw = self._get_primary_gateway()
        if primary_gw is None or primary_gw.is_connected or primary_gw._get_standby_gateway() is not self:
            return
        primary_gw._evaluate_failover()
        async_dispatcher_send(self.hass, f"myhome_message_{primary_gw.mac}", message)

    async def test(self) -> dict[str, Any]:
        """Test gateway connection."""
        result: dict[str, Any] = await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()
        return result

    @callback
    def _on_event_connection_state_change(self, connected: bool) -> None:
        """Gate commands and publish sustained event-session availability."""
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
            return

        self._event_session_ready.clear()
        if self._terminate_listener:
            return
        if self._available and self._unavailable_timer is None:
            LOGGER.warning(
                "%s Gateway connection lost; marking unavailable in %ss "
                "if not recovered.",
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

    def _outage_confirmed(self) -> bool:
        """Down past the reconnect grace, or not up yet a grace period after setup.

        A dropped event session that recovers within the grace is routine; the
        failover issue is only raised for an outage that outlived it.
        """
        return (
            not self.is_connected
            and not self._available
            and time.monotonic() - self._setup_at >= AVAILABILITY_GRACE
        )

    @callback
    def _evaluate_failover(self) -> None:
        """Raise or clear the failover issue from the primary's and standby's state."""
        standby = self._get_standby_gateway()
        if standby is not None and standby._available and self._outage_confirmed():
            self._record_failover_active(standby)
        else:
            self._clear_failover()

    @callback
    def _notify_availability(self) -> None:
        """Notify all entities bound to this gateway, and any primary backed by this standby."""
        async_dispatcher_send(self.hass, self.availability_signal)
        if self.is_standby:
            primary = self._get_primary_gateway()
            if primary is not None and not primary._available:
                primary._evaluate_failover()
                async_dispatcher_send(self.hass, primary.availability_signal)

    @callback
    def _update_event_watchdog(self, *, progress: bool) -> None:
        """Arm the stall deadline while disconnected, disarm it while connected."""
        self._event_runner._update_event_watchdog(progress=progress)

    async def listening_loop(self) -> None:
        """Run the event session, recreating it whenever it dies or stalls."""
        await self._event_runner.listening_loop()

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

    def _record_tx(self, written_at: float, message: Any) -> None:
        """Remember a written frame for shared-bus detection."""
        if not getattr(self, "hass", None):
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        recent_tx = domain_data.setdefault("_recent_tx", collections.deque(maxlen=50))
        recent_tx.append((written_at, self.mac, self.bus_group, str(message).strip()))

    def _correlate_shared_bus_traffic(self, message: Any) -> None:
        """Correlate bus traffic with other gateways to detect unconfigured shared buses.

        Gateways configured on the same bus (one primary and the secondaries or
        standby pointing at it) are expected to see the same frames; any other
        pair seeing them is a bus nobody told Home Assistant about.
        """
        if not getattr(self, "hass", None) or getattr(message, "who", None) in (13, 1013):
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        now = time.monotonic()
        raw_msg = str(message).strip()
        group = self.bus_group

        # 0. The echo of a frame this gateway wrote proves nothing: two isolated buses
        #    get identical frames whenever an automation sends the same command to both.
        recent_tx = domain_data.get("_recent_tx") or ()
        if any(
            tx_mac == self.mac and tx_frame == raw_msg and now - tx_time <= SHARED_BUS_TX_ECHO_S
            for tx_time, tx_mac, _tx_group, tx_frame in recent_tx
        ):
            return

        # 1. Another gateway wrote this exact frame just now (TX -> RX echo)
        for tx_time, tx_mac, tx_group, tx_frame in recent_tx:
            if tx_group != group and tx_frame == raw_msg and now - tx_time <= SHARED_BUS_TX_ECHO_S:
                self._record_shared_bus_evidence(tx_mac, now, is_tx_echo=True)
                return

        # 2. Another gateway received this exact frame at the same moment (physical event)
        recent_rx = domain_data.setdefault("_recent_rx", collections.deque(maxlen=50))
        for rx_time, rx_mac, rx_group, rx_frame in recent_rx:
            if rx_group != group and rx_frame == raw_msg and now - rx_time <= SHARED_BUS_RX_WINDOW_S:
                self._record_shared_bus_evidence(rx_mac, now, is_tx_echo=False)
                return
        recent_rx.append((now, self.mac, group, raw_msg))

    def _record_shared_bus_evidence(self, other_mac: str, now: float, is_tx_echo: bool = False) -> None:
        """Count one correlated frame; raise the repair issue on enough recent ones."""
        from homeassistant.helpers import device_registry as dr
        my_mac = dr.format_mac(str(self.mac))
        other_mac = dr.format_mac(str(other_mac))

        if not other_mac or other_mac == my_mac:
            return
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        evidence_map = domain_data.setdefault("_shared_bus_evidence", {})
        pair_key = tuple(sorted([my_mac, other_mac]))
        seen = evidence_map.setdefault(pair_key, collections.deque(maxlen=SHARED_BUS_EVIDENCE_COUNT))
        seen.append((now, is_tx_echo))
        # Only evidence inside one window counts: coincidences spread over days do not add up.
        # Require at least one TX->RX echo to prevent false positives on standalone buses (issue #459).
        if len(seen) == SHARED_BUS_EVIDENCE_COUNT and seen[-1][0] - seen[0][0] <= SHARED_BUS_EVIDENCE_WINDOW_S:
            if any(is_tx for _, is_tx in seen):
                seen.clear()
                from .repairs import async_create_shared_bus_issue
                async_create_shared_bus_issue(self.hass, pair_key[0], pair_key[1])

    async def _process_message(self, message: Any) -> None:
        """Process a received message and dispatch to Home Assistant."""
        await self._event_dispatcher.process_message(message)

    def _handle_gateway_diagnostics(self, message: OWNGatewayEvent) -> None:
        """Handle WHO=13 Gateway Management diagnostic telemetry."""
        dim = getattr(message, "dimension", getattr(message, "_dimension", None))
        dim_val = getattr(message, "dimension_value", getattr(message, "_dimension_value", []))

        # ── Dimension 0 & 22: Time & Timezone ────────────────────────────────
        if dim in (0, 22) and dim_val:
            # Check if timezone is 999. In both dimension 0 and 22, dim_val[3] carries the timezone.
            # The OWNd < 2.0.0b7 compat shim clears the time_zone property, but leaves dim_val[3] as "999".
            if len(dim_val) > 3 and str(dim_val[3]) == "999":
                if self.config_entry:
                    async_create_unconfigured_timezone_issue(self.hass, self.config_entry.entry_id, self.config_entry.title)
            elif len(dim_val) > 3 and str(dim_val[3]) != "":
                if self.config_entry:
                    async_delete_unconfigured_timezone_issue(self.hass, self.config_entry.entry_id)

        # ── Dimension 15: Device type (MODEL REQUEST) ────────────────────
        if dim == 15 and dim_val:
            self._handle_device_type(str(dim_val[0]))

        # ── Dimensions 23 / 24: kernel and distribution, corroborating evidence ──
        elif dim in (23, 24) and dim_val:
            self._who13["kernel" if dim == 23 else "distribution"] = ".".join(str(v) for v in dim_val)

        # ── Dimension 16: Firmware Version ───────────────────────────────
        elif dim == 16:
            fw = getattr(message, "firmware_version", getattr(message, "_firmware_version", None))
            if fw:
                self._who13["firmware"] = fw
            if fw and fw != self.gateway.firmware:
                LOGGER.info(
                    "%s Auto-detected gateway firmware `%s` via WHO=13 Dimension 16.",
                    self.log_id,
                    fw,
                )
                self.gateway.firmware = fw
                if self.config_entry is not None:
                    new_data = dict(self.config_entry.data)
                    if new_data.get(CONF_FIRMWARE) != fw:
                        new_data[CONF_FIRMWARE] = fw
                        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                if self.device_registry_id:
                    dev_reg = dr.async_get(self.hass)
                    dev_reg.async_update_device(self.device_registry_id, sw_version=fw)

    def _handle_device_type(self, raw_code: str) -> None:
        """Record a WHO=13 dimension-15 reply and let the resolver decide what it means."""
        reading = read_who13(raw_code)
        self._who13["code"] = raw_code
        self._who13["model"] = reading.canonical if reading.known else None
        self._who13["model_official"] = reading.canonical if reading.certain else None
        self._who13["model_observed"] = " / ".join(reading.models) if reading.known and not reading.certain else None
        self._resolve_identity()

    def _handle_gateway_identity_diagnostics(self, message: Any) -> None:
        """Record a WHO=1013 dimension-1 (OBJECT_MODEL) reply and let the resolver decide."""
        dim_val = getattr(message, "dimension_value", getattr(message, "_dimension_value", []))
        if not dim_val or not isinstance(dim_val, list):
            return
        reading = read_who1013(str(dim_val[0]))
        self._who1013["code"] = reading.code
        self._who1013["model"] = reading.canonical if reading.known else None
        self._who1013["names"] = reading.alternative_names if reading.known else ()
        # OBJECT_MODEL * N_CONF * BRAND * LINE; a shorter reply simply leaves the
        # missing fields unset rather than shifting the ones that did arrive.
        for index, field in enumerate(("n_conf", "brand", "line"), start=1):
            self._who1013[field] = str(dim_val[index]) if len(dim_val) > index else None
        self._who1013["pending"] = False
        self._resolve_identity()

    def _identity_evidence(self) -> GatewayIdentityEvidence:
        """Everything observed so far, each source kept apart."""
        source = self.identification_source
        configured = str(self.gateway.model_name or "") or None
        return GatewayIdentityEvidence(
            manual=configured if source == IDENTIFICATION_MANUAL else None,
            technical=configured if source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL) else None,
            technical_source=source if source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL) else None,
            prior_label=configured if source == IDENTIFICATION_WHO13 else None,
            who13_code=self._who13["code"],
            who1013_code=self._who1013["code"],
        )

    def _resolve_identity(self) -> None:
        """Decide the effective model from the evidence and bring everything in step with it.

        Idempotent: the same evidence yields the same verdict, so a re-broadcast of a
        WHO=13 reply neither repeats a correction nor flaps a repair issue.
        """
        configured = str(self.gateway.model_name or "")
        resolution = resolve_gateway_identity(self._identity_evidence())
        entry_id = getattr(self.config_entry, "entry_id", None)
        entry_id = entry_id if isinstance(entry_id, str) else None
        changed = resolution != self._identity_resolution
        self._identity_resolution = resolution

        # A shared WHO=13 code is the cue to ask WHO=1013 - once per answer.
        if resolution.request_who1013:
            if changed:
                LOGGER.info(
                    "%s WHO=13 reports device type %s (seen on multiple modern gateways); "
                    "keeping model `%s` until WHO=1013 answers.",
                    self.log_id, self._who13["code"], configured,
                )
            self._request_object_model()

        # Codes in no table: keep the model, ask for a trace. Withdrawn once every
        # code the gateway answered is known.
        unknown = resolution.unknown_code
        if unknown:
            if changed:
                LOGGER.info(
                    "%s The gateway reports model code %s, unknown to the OpenWebNet tables and to field "
                    "evidence; keeping model `%s`. Please attach a trace to an issue so it can be documented.",
                    self.log_id, unknown, configured,
                )
            if entry_id:
                async_create_unknown_model_issue(self.hass, entry_id, unknown)
        elif entry_id:
            async_delete_unknown_model_issue(self.hass, entry_id)

        # The effective model.
        model = resolution.model or configured
        if model and model.lower() != configured.lower():
            reading = resolution.corrected_reading
            LOGGER.warning(
                "%s Gateway model `%s` set from %s (was `%s`, source %s).",
                self.log_id, model, reading.describe() if reading else "in-band evidence", configured,
                self.identification_source,
            )
            self._apply_model(model)
            if resolution.corrected_from and reading is not None and entry_id:
                async_create_identity_corrected_issue(
                    self.hass, entry_id, resolution.corrected_from, model, reading.raw
                )

        # A certain contradiction of an SSDP / serial identity: kept, but the owner is asked.
        reading = resolution.conflict_reading
        if resolution.conflict and reading is not None:
            if changed:
                LOGGER.warning("%s Gateway identity mismatch: %s.", self.log_id, resolution.conflict)
            self._set_conflict(
                resolution.conflict, entry_id,
                who13_model=reading.canonical, raw_code=reading.raw, source=resolution.source, official=reading.certain,
            )
        else:
            self._set_conflict(None, entry_id)
        self._sync_device_registry_model(model)

    def _apply_model(self, model: str) -> None:
        """Make ``model`` the entry's model: handler, profile, log id, config entry and title."""
        self.gateway.model_name = model
        self.gateway.model = model
        self.gateway.profile = get_gateway_profile(model)
        self.gateway._log_id = f"[{model} gateway - {self.gateway.host}]"
        self._trim_sending_workers(model)
        new_data = dict(self.config_entry.data)
        if new_data.get(CONF_NAME) == model:
            return
        new_data[CONF_NAME] = model
        new_data["model_source"] = IDENTIFICATION_WHO13
        update_kwargs: dict[str, Any] = {"data": new_data}
        if str(getattr(self.config_entry, "title", "")).endswith("Gateway"):
            update_kwargs["title"] = f"{model} Gateway"
        self.hass.config_entries.async_update_entry(self.config_entry, **update_kwargs)

    def _trim_sending_workers(self, model: str) -> None:
        """Stop the command workers a corrected model has no sessions for."""
        limit = command_session_limit(model)
        if limit is None or len(self.sending_workers) <= limit:
            return
        LOGGER.warning(
            "%s The %s accepts at most %d command session(s); stopping %d of %d command workers.",
            self.log_id,
            model,
            limit,
            len(self.sending_workers) - limit,
            len(self.sending_workers),
        )
        for worker in self.sending_workers[limit:]:
            worker.cancel()
        del self.sending_workers[limit:]

    def _request_object_model(self) -> None:
        """Queue ``*#1013*0*1##`` (Gateway Diagnostic, dimension 1 OBJECT_MODEL) once.

        Sent as a status request: OWNd retries a NACK once and logs both attempts at
        DEBUG, and the delivery future is cancelled, which nobody awaits. Not repeated
        while an answer is pending; a reconnect of the event session clears that.
        """
        if self._who1013["pending"]:
            return
        cmd = OWNCommand.parse("*#1013*0*1##")
        if cmd is None:
            return
        LOGGER.debug("%s Requesting WHO=1013 dimension 1 (OBJECT_MODEL) to settle the model.", self.log_id)
        try:
            self.send_buffer.put_nowait(
                {"message": cmd, "written": self.hass.loop.create_future(), "is_status_request": True}
            )
        except asyncio.QueueFull:
            LOGGER.warning("%s Cannot queue the WHO=1013 request: send buffer full.", self.log_id)
            return
        self._who1013["pending"] = True

    def _set_conflict(self, conflict: str | None, entry_id: str | None, **issue: Any) -> None:
        """Track the identity conflict and keep the repair issue in step with it."""
        changed = conflict != self._identity_conflict
        self._identity_conflict = conflict
        if not entry_id:
            return
        if conflict:
            if changed:
                async_create_identity_issue(
                    self.hass, entry_id, str(self.gateway.model_name or ""), issue["who13_model"],
                    issue["raw_code"], issue["source"], issue["official"],
                )
            return
        # Always clear on the no-conflict path: a fresh handler (after a reload) starts
        # with no conflict in memory while the previous instance's warning may still
        # sit in the issue registry. Deleting an absent issue is a no-op.
        async_delete_identity_issue(self.hass, entry_id)

    def _sync_device_registry_model(self, model: str) -> None:
        """Keep the device registry in step with the resolved identity."""
        if not model or not self.device_registry_id:
            return
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get(self.device_registry_id)
        if device is None:
            return
        updates: dict[str, Any] = {}
        if getattr(device, "model", None) != model:
            updates["model"] = model
        model_id = self._who1013["code"]
        if model_id is not None and getattr(device, "model_id", None) != model_id:
            updates["model_id"] = model_id
        if updates:
            dev_reg.async_update_device(self.device_registry_id, **updates)

    async def sending_loop(self, worker_id: int) -> None:
        """Run sending loop for worker."""
        await self._command_pool.sending_loop(worker_id)

    async def initial_discovery(self) -> None:
        """Queue the startup sweep that discovers devices missing from the config."""
        for who, frame in ((2, "*#2*0##"), (4, "*#4*0##"), (16, "*#16*0*5##")):
            if getattr(self, "is_follower", False) is True and who not in getattr(self, "delegated_whos", set()):
                LOGGER.debug(
                    "%s Skipping WHO=%s discovery: follower gateway on shared bus.",
                    self.log_id,
                    who,
                )
                continue
            if who in getattr(self, "delegated_away_whos", set()):
                LOGGER.debug(
                    "%s Skipping WHO=%s discovery: delegated to a secondary gateway.",
                    self.log_id,
                    who,
                )
                continue
            if not self._profile_supports_who(who):
                LOGGER.debug(
                    "%s Skipping WHO=%s discovery: not supported by %s profile.",
                    self.log_id,
                    who,
                    self.gateway.model_name,
                )
                continue
            cmd = OWNCommand.parse(frame)
            if cmd is not None:
                await self.send_status_request(cmd)

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
        if self.is_standby:
            primary = self._get_primary_gateway()
            if primary is not None and not primary._available:
                primary._evaluate_failover()
                async_dispatcher_send(self.hass, primary.availability_signal)


        self._event_runner.close()
        self._command_pool.close()
        return True

    def _failover_target(self, message: OWNCommand) -> "MyHOMEGatewayHandler" | None:
        """The connected warm standby to send through while this primary is disconnected."""
        msg_who = getattr(message, "who", getattr(message, "_who", None))
        if msg_who in (13, 1013):
            return None

        if self.is_connected:
            return None
        standby = self._get_standby_gateway()
        if standby is None or not standby.is_connected:
            return None

        if msg_who is not None and not standby._profile_supports_who(int(msg_who)):
            return None

        LOGGER.debug(
            "%s Primary gateway is disconnected; sending `%s` through standby gateway %s.",
            self.log_id,
            message,
            standby.log_id,
        )
        self._evaluate_failover()
        return standby

    async def send(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a command; the returned future resolves to the monotonic write time."""
        standby = self._failover_target(message)
        if standby is not None:
            return await standby.send(message)
        return await self._command_pool.send(message)

    async def send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a status request; the returned future resolves to the monotonic write time."""
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
