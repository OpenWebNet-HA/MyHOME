"""Code to handle a MyHome Gateway."""
from __future__ import annotations

import asyncio
import collections
import inspect
import re
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
    BUS_ROUTING,
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
# How long a zone request waits for others once the startup sweep is over: zones discovered
# from incoming frames arrive over 1-2 s, and each would otherwise start its own all-zone
# sweep (*#4*#0*20##) on an F461.
ZONE_REQUEST_COALESCE = 0.1
DISCOVERY_REQUESTS: tuple[tuple[int, str], ...] = (
    (1, "*#1*0##"),
    (2, "*#2*0##"),
    (4, "*#4*0##"),
    (5, "*#5*0##"),
    (16, "*#16*0*5##"),
    (18, "*#18*51*51##"),
    (18, "*#18*51*113##"),
)
# Startup requests the firmware drops instead of forwarding them to the bus (own-firmware-oracle,
# results/mcp_index.json at 53f9514; "silent" verdicts). Read from the OWNd profile first
# (``supports_general_heating_status``, ``supports_who(16)``); these tables are the fallback
# until the flags exist there. Gateways not listed keep sending the request.
GENERAL_ZONE_REQUEST_REFUSED = frozenset(
    {"F450", "F454", "F459", "F460", "F461", "H4684", "MH200", "MH202", "MYHOMESERVER1"}
)  # *#4*0## (trace-corpus.tsv); F453, F453AV and MH200N forward it
SOUND_STATUS_REQUEST_REFUSED = frozenset(
    {"F450", "F459", "F460", "F461", "MH202", "MYHOMESERVER1"}
)  # *#16*0*5## (sound-who16-source.tsv)
# Zones of these SCS-only gateways report nothing after the startup sweep, so each zone asks
# for its temperature (*#4*Z*0##): the ones refusing *#4*0## that also forward *#4*Z## to the
# bus unanswered (#674), and the MH201, whose standalone thermostats stayed unknown after every
# restart (#185; the MH201 is not in the oracle). F460, F461 and MyHomeServer1 refuse *#4*0##
# too but answer *#4*Z## themselves (F460 and F461 expand it into the four dimension requests,
# $00499013000 in trace-corpus.tsv; MyHomeServer1 replies from its cache, #649), so their zones
# keep the zone status poll and are not listed here.
ZONE_TEMPERATURE_POLL = frozenset({"F450", "F454", "F459", "H4684", "MH200", "MH201", "MH202"})
# A zone behind an F422 bus interface (WHERE Z#4#II) cannot be polled on these gateways: the
# firmware drops *#4*Z#4#II*0## (and *#4*Z#4#II##) before the bus, nack/silent on ten of the
# twelve emulated images; only H4684 and MH200 forward them (own-firmware-oracle PR #60,
# thermo-zone-requests suite, not published yet). Read from the OWNd profile first
# (``supports_interface_zone_requests``); this table is the fallback until the flag exists there.
INTERFACE_ZONE_REQUEST_REFUSED = frozenset(
    {"F450", "F453", "F453AV", "F454", "F459", "F460", "F461", "MH200N", "MH202", "MYHOMESERVER1"}
)

# Gateways whose firmware drops the per-zone actuator request (``*#4*Z*20##``) instead of
# forwarding it to the bus: own-firmware-oracle ``results/<P>/<V>/oracle/full/thermo-actuator.tsv``
# (``*#4*1*20##`` "silent" on these six, "out" on F450, F454, F459, F460, F461, MyHomeServer1).
# Read from the OWNd profile first (``supports_heating_actuator_status``); this table is the
# fallback until the flag exists there.
ACTUATOR_REQUEST_REFUSED = frozenset({"F453", "F453AV", "H4684", "MH200", "MH200N", "MH202"})
# One request for every zone, by dimension. The live F461 2.0.11 answered ``*#4*#0*20##`` with
# the actuators of each zone (``*#4*2#1*20*0##``, ``*#4*2#2*20*5##`` after 2.1 s).
# The MyHomeServer1 3.87.13 (myhomeserver1_live_2026-10-24, run2_reads_thermo_sound_alarm_lights)
# answered it in 0.39 s with 43 actuator frames for its 34 zones, where asking each zone
# through the paced sender takes about 32 s; the emulated 028206 forwards it ("out").
ALL_ZONE_REQUESTS: dict[str, dict[int, str]] = {
    "F461": {20: "*#4*#0*20##"},
    "MYHOMESERVER1": {20: "*#4*#0*20##"},
}
# Gateways whose emulated firmware did not forward the zone humidity request (``*#4*Z*60##``:
# nack and nothing on the bus) in the own-firmware-oracle thermo-zone-requests suite
# (findings/thermo-zone-requests.md on main, ``firmware_observed``, emulated firmware); F459,
# F460, F461 and MyHomeServer1 forwarded it, and the live F461 / MyHomeServer1 answer it.
# Read from the OWNd profile first (``supports_heating_humidity_request``: ``True`` / ``False``
# wins, ``None`` = unverified); this table is the fallback until the flag exists there.
HUMIDITY_REQUEST_REFUSED = frozenset({"F450", "F453", "F453AV", "F454", "H4684", "MH200", "MH200N", "MH202"})
# Every gateway model the request tables can be keyed on: the twelve images the oracle emulates
# (results/<P>/<V>, refusing and forwarding alike) and the other models OWNd profiles, so a
# forwarding model named with a brand prefix ("BTicino F461") is keyed as itself (``F461``)
# and not left as the whole string. The same key on every table of #792 / #793 / #794.
KNOWN_GATEWAY_MODELS = frozenset(
    {
        "F450", "F452", "F452V", "F453", "F453AV", "F454", "F455", "F459", "F460", "F461",
        "H4684", "H4890", "MH200", "MH200N", "MH201", "MH202", "MYHOMESERVER1",
    }
)
# The models the tables know, longest first so ``MH200N`` is not read as ``MH200``.
_ZONE_REQUEST_MODELS = sorted(
    KNOWN_GATEWAY_MODELS | ACTUATOR_REQUEST_REFUSED | HUMIDITY_REQUEST_REFUSED | set(ALL_ZONE_REQUESTS)
    | GENERAL_ZONE_REQUEST_REFUSED | SOUND_STATUS_REQUEST_REFUSED | ZONE_TEMPERATURE_POLL,
    key=len,
    reverse=True,
)
# SSDP announces the H4684 as "HL4684" (manifest.json), which OWNd does not alias to its H4684
# profile yet (it knows h4684, l4684, n4684, nt4684, lgrh4684; #794 maps the same spelling), and
# "HC4684" is the other prefixed spelling of the same product: keyed on the oracle's product name.
_MODEL_SPELLINGS = {"HL4684": "H4684", "HC4684": "H4684"}


def _alnum(name: object) -> str:
    """A model string reduced to its upper-case letters and digits (``"BTicino F461"`` -> ``BTICINOF461``)."""
    return "".join(character for character in str(name).upper() if character.isalnum())


def _resolved_model(word: str) -> str | None:
    """The model OWNd resolves one word of a model string to, or ``None`` for a word it does not know.

    ``L4684`` -> ``H4684``, ``003598`` -> ``F454``, ``03565`` -> ``MH200N`` (OWNd's aliases and
    catalogue item numbers); the spellings OWNd has no alias for are mapped first.
    """
    key = _MODEL_SPELLINGS.get(_alnum(word), word)
    profile = get_gateway_profile(key)
    if isinstance(profile, GenericGatewayProfile):
        return None
    return _alnum(profile.model_name)


# A WHO 4 zone address: ``Z``, ``#Z``, ``Z#N`` (actuator), each optionally behind an
# interface of exactly two digits (``Z#4#01``): the grammar of OWNd#140's builder
# (``OWNHeatingCommand.actuators_status``).
_ZONE_WHERE = re.compile(rf"^#?[0-9]+(?:#[0-9]+)?(?:{re.escape(BUS_ROUTING)}[0-9]{{2}})?$")


def _validate_zone_where(where: str) -> str:
    """The WHO 4 zone address as it goes into ``*#4*WHERE*DIM##``, or ``ValueError``.

    A one-digit interface is written with two (``3#4#1`` -> ``3#4#01``), as the frames on
    the wire and the configuration validator spell it. Anything else would change the
    frame's grammar: ``2*22`` would turn a dimension 20 request into ``*#4*2*22*20##``, a
    dimension 22 write; ``2##`` ends the frame early. The builder of OWNd#140
    (``OWNHeatingCommand.actuators_status``) accepts and rejects the same addresses.
    """
    zone, routing, interface = (where or "").partition(BUS_ROUTING)
    if routing and len(interface) == 1 and interface.isdigit():
        where = f"{zone}{routing}{interface.zfill(2)}"
    if not _ZONE_WHERE.match(where or ""):
        raise ValueError(f"Invalid zone address: {where!r}")
    return where


def _zone_request_frame(where: str, dimension: int) -> str:
    """The dimension request of one zone (``*#4*WHERE*DIM##``), validated like a builder."""
    return f"*#4*{_validate_zone_where(where)}*{dimension}##"


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
        # Heating zones whose actuators are asked after the startup sweep and every reconnect,
        # and the (zone, dimension) requests waiting for the next paced run.
        self._actuator_zones: dict[str, int] = {}
        self._zone_requests: set[tuple[str, int]] = set()
        self._zone_request_task: asyncio.Task[None] | None = None

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
                "F450",
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
    def _model_key(self) -> str:
        """The gateway model the request tables are keyed on (``F461``, ``MYHOMESERVER1``).

        A known OWNd profile names the model; a ``GenericGatewayProfile`` (also what
        ``get_gateway_profile("BTicino F461")`` returns) falls back to the configured name.
        There a brand-prefixed alias or catalogue number is resolved word by word through
        OWNd (``"BTicino L4684"`` -> ``H4684``, ``"Legrand 003598"`` -> ``F454``), the SSDP
        spelling ``HL4684`` is read as ``H4684``, and otherwise a known model is looked up as
        a substring, longest first (``supports_zone_status`` style), so ``MH200N`` stays
        distinct from ``MH200``. The words are the alphanumeric runs of the string, so
        ``"BTicino-L4684"`` and ``"F454 (003598)"`` resolve too.
        """
        profile = getattr(self.gateway, "profile", None)
        if profile is not None and not isinstance(profile, GenericGatewayProfile):
            name = getattr(profile, "model_name", None) or self.model
        else:
            name = self.model
        for word in re.findall(r"[A-Za-z0-9]+", str(name)):
            resolved = _resolved_model(word)
            if resolved is not None:
                return resolved
        normalized = _MODEL_SPELLINGS.get(_alnum(name), _alnum(name))
        return next((model for model in _ZONE_REQUEST_MODELS if model in normalized), normalized)

    def _who4_target(self) -> MyHOMEGatewayHandler:
        """The gateway a WHO 4 status request goes through, the way ``send_status_request`` routes it.

        On a shared bus WHO 4 may be delegated to a secondary gateway, or served by the warm
        standby while this primary is down: that gateway's refusal and all-zone tables apply.
        """
        probe = OWNCommand.parse("*#4*#0*20##")
        if probe is None:  # pragma: no cover - a fixed, well-formed frame
            return self
        return self._delegated_target(probe) or self._failover_target(probe) or self

    @property
    def actuator_request_refused(self) -> bool:
        """Whether this gateway drops the zone actuator request (``*#4*Z*20##``).

        The OWNd profile flag wins when it exists; otherwise the oracle-derived table.
        """
        supported = getattr(getattr(self.gateway, "profile", None), "supports_heating_actuator_status", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in ACTUATOR_REQUEST_REFUSED

    def all_zone_request(self, dimension: int) -> str | None:
        """The single request this gateway answers with every zone's ``dimension``, if any."""
        return ALL_ZONE_REQUESTS.get(self._model_key, {}).get(dimension)

    @callback
    def watch_zone_actuators(self, where: str) -> CALLBACK_TYPE:
        """Ask for a heating zone's actuators (dimension 20) now and after every reconnect.

        Returns the callback that stops it. The zone is always recorded: whether the gateway
        refuses the request is decided when it is sent, once the in-band identity (WHO 13 /
        WHO 1013) has settled, so a model corrected after the entities were added is honoured.
        """
        where = _validate_zone_where(where)  # before the zone is counted: no phantom watch
        self._actuator_zones[where] = self._actuator_zones.get(where, 0) + 1
        self.request_zone_dimension(where, 20)

        @callback
        def _unwatch() -> None:
            count = self._actuator_zones.get(where, 0) - 1
            if count > 0:
                self._actuator_zones[where] = count
            else:
                self._actuator_zones.pop(where, None)

        return _unwatch

    @callback
    def request_zone_dimension(self, where: str, dimension: int) -> None:
        """Ask a heating zone for one dimension once the startup sweep is over.

        Zones asking together share one paced run; where the gateway answers one request
        for every zone (``all_zone_request``), that frame replaces the local zones' own.
        """
        if dimension == 22:
            # *#4*Z*22## kills the F461 thermostat handler and reboots the gateway.
            raise ValueError("WHO 4 dimension 22 is never requested")
        self._zone_requests.add((_validate_zone_where(where), dimension))
        if self._zone_request_task is None or self._zone_request_task.done():
            self._zone_request_task = self.hass.async_create_background_task(
                self._send_zone_requests(), name=f"myhome_{self.mac}_zone_requests"
            )

    async def _send_zone_requests(self) -> None:
        """Send the waiting zone requests through the paced sender, until none is left."""
        coalesce = self._initial_discovery_done.is_set()
        await self.wait_for_initial_discovery()
        while self._zone_requests:
            # Zones added in the same pass join this run; after the sweep, zones discovered
            # from the bus over the next tenth of a second do too instead of each sweeping on
            # its own, and so do zones that arrived while the previous batch was being sent.
            await asyncio.sleep(ZONE_REQUEST_COALESCE if coalesce else 0)
            coalesce = True
            pending, self._zone_requests = self._zone_requests, set()
            frames: list[str] = []
            target = self._who4_target()
            for dimension in sorted({dim for _where, dim in pending}):
                wheres = sorted(where for where, dim in pending if dim == dimension)
                if dimension == 20 and target.actuator_request_refused:
                    LOGGER.debug(
                        "%s Zones %s: %s refuses the actuator request; not asking", self.log_id, wheres, target.model
                    )
                    continue
                all_zones = target.all_zone_request(dimension)
                if all_zones is not None and any(BUS_ROUTING not in where for where in wheres):
                    frames.append(all_zones)
                    wheres = [where for where in wheres if BUS_ROUTING in where]  # behind an interface
                for where in wheres:
                    # One zone the builder rejects must not drop the others of the batch.
                    try:
                        frames.append(_zone_request_frame(where, dimension))
                    except ValueError as err:
                        LOGGER.warning("%s Zone %s: dimension %s not requested: %s", self.log_id, where, dimension, err)
            if not frames:
                continue  # every item skipped (refusing gateway): no empty sweep
            try:
                await self.send_paced(frames)
            except Exception:  # a transport error must not end the task silently (#593)
                LOGGER.exception("%s Zone requests %s could not be sent", self.log_id, frames)

    @property
    def humidity_request_refused(self) -> bool:
        """Whether this gateway drops humidity requests (``*#4*Z*60##``) instead of forwarding them.

        The request goes where ``send_status_request`` sends it: a secondary gateway WHO 4 is
        delegated to, or the warm standby while this primary is down, decides with its own
        profile and table. The OWNd profile flag wins when it exists; otherwise the
        oracle-derived table.
        """
        target = self._who4_target()
        if target is not self:
            return target.humidity_request_refused
        supported = getattr(getattr(self.gateway, "profile", None), "supports_heating_humidity_request", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in HUMIDITY_REQUEST_REFUSED

    @property
    def general_zone_request_refused(self) -> bool:
        """Whether this gateway drops the general heating status request (``*#4*0##``)."""
        supported = getattr(getattr(self.gateway, "profile", None), "supports_general_heating_status", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in GENERAL_ZONE_REQUEST_REFUSED

    @property
    def sound_status_request_refused(self) -> bool:
        """Whether this gateway drops the sound status request (``*#16*0*5##``)."""
        if not self._profile_supports_who(16):
            return True
        return self._model_key in SOUND_STATUS_REQUEST_REFUSED

    @property
    def zone_temperature_poll_needed(self) -> bool:
        """Whether each zone must ask for its temperature (``*#4*Z*0##``) itself.

        Only on SCS-only gateways (no ``*#4*Z##``) whose startup sweep brings no zone
        temperature; elsewhere the zone status poll or the sweep reports it.
        """
        if self.supports_zone_status:
            return False
        supported = getattr(getattr(self.gateway, "profile", None), "supports_general_heating_status", None)
        if isinstance(supported, bool):
            # The OWNd profile wins when it states the flag: a sweep the gateway answers makes
            # the per-zone poll pointless, whatever the fallback table says.
            return not supported
        return self._model_key in ZONE_TEMPERATURE_POLL

    @property
    def interface_zone_request_refused(self) -> bool:
        """Whether this gateway drops the status requests of a zone behind an F422 (``*#4*Z#4#II*0##``)."""
        supported = getattr(getattr(self.gateway, "profile", None), "supports_interface_zone_requests", None)
        if isinstance(supported, bool):
            return not supported
        return self._model_key in INTERFACE_ZONE_REQUEST_REFUSED

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
                # Actuators that switched while the session was down sent their frame to nobody.
                for where in list(self._actuator_zones):
                    self.request_zone_dimension(where, 20)
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

    def _refused_by_gateway(self, frame: str) -> bool:
        """Whether this gateway's firmware drops the startup request instead of forwarding it."""
        if frame == "*#4*0##":
            return self.general_zone_request_refused
        if frame == "*#16*0*5##":
            return self.sound_status_request_refused
        return False

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
            if self._refused_by_gateway(frame):
                LOGGER.debug("%s Skipping `%s`: %s firmware refuses it.", self.log_id, frame, self.gateway.model_name)
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
        if self._zone_request_task is not None:
            self._zone_request_task.cancel()
            self._zone_request_task = None
        self._zone_requests.clear()
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

    def claim_in_flight_dimension_where(self, who: int, dimension: int) -> str | None:
        """Claim the sole point-to-point WHERE with an in-flight dimension request (#777)."""
        if who not in (13, 1013) and who in self.delegated_away_whos:
            sec = self._get_secondary_for_who(who)
            if sec is not None and sec.is_connected:
                return sec._command_pool.claim_in_flight_dimension_where(who, dimension)
        if who not in (13, 1013) and not self.is_connected:
            standby = self._get_standby_gateway()
            if standby is not None and standby.is_connected and standby._profile_supports_who(int(who)):
                return standby._command_pool.claim_in_flight_dimension_where(who, dimension)
        return self._command_pool.claim_in_flight_dimension_where(who, dimension)

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
