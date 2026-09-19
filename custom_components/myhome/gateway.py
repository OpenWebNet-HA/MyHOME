"""Code to handle a MyHome Gateway."""
import asyncio
import collections
import contextlib
import logging
import time
from typing import Any, List, cast

import OWNd.message as _ownd_msg
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
from OWNd.message import (
    OWNAlarmEvent,
    OWNAutomationEvent,
    OWNAuxEvent,
    OWNCENEvent,
    OWNCENPlusEvent,
    OWNCommand,
    OWNDryContactEvent,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
    OWNMessage,
)
from OWNd.profiles import get_gateway_profile

from .bus_monitor import BusMonitor
from .const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_LONG_PRESS,
    CONF_LONG_RELEASE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_ROTARY_CCW_FAST,
    CONF_ROTARY_CCW_SLOW,
    CONF_ROTARY_CW_FAST,
    CONF_ROTARY_CW_SLOW,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
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
    RESYNC_DEBOUNCE_S,
    RESYNC_LEADING_WINDOW_S,
    WHO13_AMBIGUOUS_DEVICE_TYPES,
    WHO13_OBSERVED_DEVICE_TYPES,
    WHO13_OFFICIAL_DEVICE_TYPES,
    area_of_where,
    is_who13_code_compatible,
)
from .discovery import Address, parse_unique_id
from .repairs import (
    async_create_identity_corrected_issue,
    async_create_identity_issue,
    async_create_unconfigured_timezone_issue,
    async_create_unknown_model_issue,
    async_delete_identity_issue,
    async_delete_unconfigured_timezone_issue,
    async_delete_unknown_model_issue,
)

_orig_gw_tz = _ownd_msg._gateway_timezone


def _compat_gateway_timezone(values: list[str]) -> str:
    """Compatibility wrapper for OWNd < 2.0.0b7: accept 999 as unconfigured timezone."""
    if len(values) > 3 and values[3] == "999":
        return ""
    return str(_orig_gw_tz(values))


_ownd_msg._gateway_timezone = _compat_gateway_timezone


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

EVENT_READY_TIMEOUT = 120


def _resolve_written(task: dict[str, Any], when: float) -> None:
    """Complete a queued frame's delivery future with the write timestamp."""
    written = task.get("written")
    if isinstance(written, asyncio.Future) and not written.done():
        written.set_result(when)


def _session_is_open(session: Any) -> bool:
    """Whether the command session has an open socket.

    Not ``is_connected``: OWNd's ``close()`` only drops the streams and leaves
    that flag as ``connect()`` last set it, so after the idle close it still
    reads ``True``. The streams are what ``send()`` would reopen.
    """
    return getattr(session, "_stream_reader", None) is not None and getattr(session, "_stream_writer", None) is not None


def _cancel_written(task: dict[str, Any]) -> None:
    """Cancel a queued frame's delivery future (the frame will never be written)."""
    written = task.get("written")
    if isinstance(written, asyncio.Future) and not written.done():
        written.cancel()


COMMAND_SESSION_IDLE_TIMEOUT = 15.0
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
        self._terminate_listener = False
        self._terminate_sender = False
        self.is_connected = False
        self._available = False
        self._unavailable_timer: CALLBACK_TYPE | None = None
        self._event_session_ready = asyncio.Event()
        self._sender_stop = asyncio.Event()
        self.listening_worker: asyncio.Task[None] | None = None
        self.sending_workers: List[asyncio.Task[None]] = []
        queue_max_size = (
            self.gateway.profile.max_queue_size
            if hasattr(self.gateway, "profile") and self.gateway.profile
            else 250
        )
        self.send_buffer: asyncio.Queue[Any] = asyncio.Queue(maxsize=queue_max_size)
        self.bus_monitor = BusMonitor()
        self.device_registry_id = None
        self._cen_devices: set[tuple[int, Any]] = set()
        # Everything we know about how this gateway was identified; exported in
        # diagnostics, the WebSocket info payload and every trace (see identification()).
        self._who13: dict[str, Any] = {
            "code": None, "model": None, "model_official": None, "model_observed": None,
            "firmware": None, "kernel": None, "distribution": None,
        }
        self._identity_conflict: str | None = None
        self.broadcast_resync = broadcast_resync
        self._resync_timers: dict[str, CALLBACK_TYPE] = {}
        self._resync_group_echoes: dict[str, int] = {}
        self._recent_ptp: collections.deque[tuple[float, str, str | None]] = collections.deque()

    def _ensure_cen_device(self, who: int, object_id: int | str) -> None:
        """Ensure CEN/CEN+ scenario unit is registered in device registry."""
        device_key = (who, object_id)
        obj_str = str(object_id)
        if device_key in self._cen_devices or (who, obj_str) in self._cen_devices:
            return

        if not self.config_entry or not hasattr(self.config_entry, "entry_id") or not isinstance(self.config_entry.entry_id, str):
            return
        if self.device_registry_id is None:
            LOGGER.debug(
                "%s Deferring %s device %s until the gateway device is registered.",
                self.log_id,
                "CEN+" if who == 25 else "CEN",
                obj_str,
            )
            return

        try:
            device_registry = dr.async_get(self.hass)
            type_name = "CEN+" if who == 25 else "CEN"
            via_kwargs: dict[str, Any] = {}
            if self.device_registry_id:
                via_kwargs["via_device_id"] = self.device_registry_id
            device_registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                identifiers={(DOMAIN, f"{self.mac}-{who}-{obj_str}")},
                name=f"{type_name} Unit {obj_str}",
                manufacturer="BTicino",
                model=f"{type_name} Scenario Control",
                **via_kwargs,
            )
            self._cen_devices.add(device_key)
            self._cen_devices.add((who, obj_str))
            try:
                self._cen_devices.add((who, int(object_id)))
            except (ValueError, TypeError):
                pass
        except Exception as err:
            LOGGER.debug("Could not auto-register %s device %s: %s", who, object_id, err)


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
            "profile": type(self.profile).__name__ if self.profile is not None else None,
            "conflict": self._identity_conflict,
        }

    @property
    def mac(self) -> str:
        serial = self.gateway.serial
        if serial:
            formatted = dr.format_mac(serial)
            if formatted:
                return formatted
        return serial or ""

    @property
    def unique_id(self) -> str:
        return self.mac

    @property
    def log_id(self) -> str:
        return str(self.gateway.log_id)

    @property
    def manufacturer(self) -> str:
        mfg = self.gateway.manufacturer
        if isinstance(mfg, (list, tuple)):
            return str(mfg[0]) if mfg else "BTicino S.p.A."
        return str(mfg) if mfg else "BTicino S.p.A."

    @property
    def name(self) -> str:
        return f"{self.gateway.model_name} Gateway"

    @property
    def model(self) -> str:
        return str(self.gateway.model_name)

    @property
    def firmware(self) -> str | None:
        return self.gateway.firmware

    @property
    def profile(self) -> Any:
        return self.gateway.profile

    @property
    def command_session_idle_timeout(self) -> float:
        """Idle timeout before releasing the command session socket.

        Uses the gateway profile's custom timeout if configured; otherwise falls
        back to COMMAND_SESSION_IDLE_TIMEOUT.
        """
        profile = getattr(self.gateway, "profile", None)
        profile_timeout = getattr(profile, "command_session_idle_timeout", None) if profile else None
        return float(profile_timeout) if profile_timeout is not None else COMMAND_SESSION_IDLE_TIMEOUT

    @property
    def available(self) -> bool:
        """Return the grace-filtered gateway availability."""
        return self._available

    @property
    def availability_signal(self) -> str:
        """Return the dispatcher signal for availability changes."""
        return f"{DOMAIN}_{self.mac}_availability"

    async def test(self) -> dict[str, Any]:
        result: dict[str, Any] = await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()
        return result

    @callback
    def _on_event_connection_state_change(self, connected: bool) -> None:
        """Gate commands and publish sustained event-session availability."""
        self.is_connected = connected
        if connected:
            self._event_session_ready.set()
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
        self._unavailable_timer = None
        if self.is_connected or not self._available:
            return
        self._available = False
        LOGGER.warning(
            "%s Gateway unavailable (outage exceeded %ss).",
            self.log_id,
            AVAILABILITY_GRACE,
        )
        self._notify_availability()

    @callback
    def _notify_availability(self) -> None:
        """Notify all entities bound to this gateway."""
        async_dispatcher_send(self.hass, self.availability_signal)

    async def listening_loop(self) -> None:
        self._terminate_listener = False
        self._event_session_ready.clear()

        LOGGER.debug("%s Creating listening worker.", self.log_id)

        _event_session = OWNEventSession(
            gateway=self.gateway,
            logger=LOGGER,
            on_state_change=self._on_event_connection_state_change,
        )
        res = await _event_session.connect()
        if (
            isinstance(res, dict)
            and res.get("Success", False)
            and getattr(_event_session, "is_connected", True)
        ):
            self._on_event_connection_state_change(True)
            LOGGER.debug(
                "%s Event session ready, command sessions can now start.",
                self.log_id,
            )
        elif isinstance(res, dict) and not res.get("Success", True):
            if res.get("Message") in ("password_error", "password_required", "negotiation_refused", "connection_refused"):
                LOGGER.error(
                    "%s Event session authentication or connection refused (%s). Terminating event listener to prevent gateway lockout.",
                    self.log_id,
                    res.get("Message"),
                )
                self._on_event_connection_state_change(False)
                return
        else:
            LOGGER.warning(
                "%s Initial event session was not established; reconnecting "
                "without allowing command sessions to start.",
                self.log_id,
            )

        while not self._terminate_listener:
            message = await _event_session.get_next()
            if message is None:
                # OWNd yields None while the event socket is being re-established
                # (e.g. after a gateway-side idle close); nothing to dispatch.
                LOGGER.debug("%s Event session yielded no message (reconnecting).", self.log_id)
                continue
            self.bus_monitor.record_frame(
                direction="rx",
                raw=str(message),
                parsed=message if isinstance(message, OWNMessage) else None,
            )
            LOGGER.debug("%s Message received: `%s`", self.log_id, message)
            await self._process_message(message)

        await _event_session.close()
        self._on_event_connection_state_change(False)

        LOGGER.debug("%s Destroying listening worker.", self.log_id)

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
        if message is None:
            # A routine EOF during reconnect is not a bus event or a warning.
            LOGGER.debug("%s Data received is not a message: `None`", self.log_id)
            return

        if self.generate_events:
            if isinstance(message, OWNMessage):
                _event_content = {"gateway": str(self.gateway.host)}
                _event_content.update(message.event_content)
                self.hass.bus.async_fire("myhome_message_event", _event_content)
            else:
                self.hass.bus.async_fire("myhome_message_event", {"gateway": str(self.gateway.host), "message": str(message)})

        if isinstance(message, OWNMessage):
            async_dispatcher_send(self.hass, f"myhome_message_{self.mac}", message)

        if not isinstance(message, OWNMessage):
            LOGGER.warning(
                "%s Data received is not a message: `%s`",
                self.log_id,
                message,
            )
        elif (
            isinstance(message, OWNLightingEvent)
            or isinstance(message, OWNAutomationEvent)
            or isinstance(message, OWNDryContactEvent)
            or isinstance(message, OWNAuxEvent)
            or isinstance(message, OWNHeatingEvent)
        ):
            if not message.is_translation:
                if isinstance(message, OWNLightingEvent) and not getattr(message, "is_group", False) and not getattr(message, "is_area", False) and not getattr(message, "is_general", False):
                    now = time.monotonic()
                    while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
                        self._recent_ptp.popleft()
                    area = area_of_where(message.where)
                    self._recent_ptp.append((now, str(message.where), area))

                    if area and area in self._resync_timers:
                        LOGGER.debug("%s area %s echoed point status, cancelling sweep", self.log_id, area)
                        self._resync_timers.pop(area)()
                    for g in [k for k in self._resync_timers if k.startswith("#")]:
                        self._resync_group_echoes[g] = self._resync_group_echoes.get(g, 0) + 1
                        if self._resync_group_echoes[g] >= 2:
                            LOGGER.debug("%s group %s saw member echoes, cancelling sweep", self.log_id, g)
                            self._resync_timers.pop(g)()
                            self._resync_group_echoes.pop(g, None)

                if isinstance(message, OWNLightingEvent):
                    if message.is_general:
                        event = "on" if message.is_on else "off"
                        self.hass.bus.async_fire(
                            "myhome_general_light_event",
                            {"message": str(message), "event": event},
                        )
                    elif message.is_area:
                        event = "on" if message.is_on else "off"
                        self.hass.bus.async_fire(
                            "myhome_area_light_event",
                            {
                                "message": str(message),
                                "area": message.area,
                                "event": event,
                            },
                        )
                    elif message.is_group:
                        event = "on" if message.is_on else "off"
                        self.hass.bus.async_fire(
                            "myhome_group_light_event",
                            {
                                "message": str(message),
                                "group": message.group,
                                "event": event,
                            },
                        )
                    if getattr(message, "is_general", False) or getattr(message, "is_area", False) or getattr(message, "is_group", False):
                        self._schedule_resync(message)
                elif isinstance(message, OWNAutomationEvent):
                    if message.is_general:
                        if message.is_opening and not message.is_closing:
                            event = "open"
                        elif message.is_closing and not message.is_opening:
                            event = "close"
                        else:
                            event = "stop"
                        self.hass.bus.async_fire(
                            "myhome_general_automation_event",
                            {"message": str(message), "event": event},
                        )
                    elif message.is_area:
                        if message.is_opening and not message.is_closing:
                            event = "open"
                        elif message.is_closing and not message.is_opening:
                            event = "close"
                        else:
                            event = "stop"
                        self.hass.bus.async_fire(
                            "myhome_area_automation_event",
                            {
                                "message": str(message),
                                "area": message.area,
                                "event": event,
                            },
                        )
                    elif message.is_group:
                        if message.is_opening and not message.is_closing:
                            event = "open"
                        elif message.is_closing and not message.is_opening:
                            event = "close"
                        else:
                            event = "stop"
                        self.hass.bus.async_fire(
                            "myhome_group_automation_event",
                            {
                                "message": str(message),
                                "group": message.group,
                                "event": event,
                            },
                        )
            else:
                LOGGER.debug(
                    "%s Ignoring translation message `%s`",
                    self.log_id,
                    message,
                )
        elif isinstance(message, OWNHeatingCommand) and message.dimension is not None and message.dimension == 14:
            where_str = cast(str, message.where)
            where = where_str[1:] if where_str.startswith("#") else where_str
            LOGGER.debug(
                "%s Received heating command, sending query to zone %s",
                self.log_id,
                where,
            )
            await self.send_status_request(OWNHeatingCommand.status(where))
        elif isinstance(message, OWNCENPlusEvent):
            event = None
            if message.is_short_pressed:
                event = CONF_SHORT_PRESS
            elif message.is_held or message.is_still_held:
                event = CONF_LONG_PRESS
            elif message.is_released:
                event = CONF_LONG_RELEASE
            elif getattr(message, "is_slowly_turned_cw", False) is True:
                event = CONF_ROTARY_CW_SLOW
            elif getattr(message, "is_quickly_turned_cw", False) is True:
                event = CONF_ROTARY_CW_FAST
            elif getattr(message, "is_slowly_turned_ccw", False) is True:
                event = CONF_ROTARY_CCW_SLOW
            elif getattr(message, "is_quickly_turned_ccw", False) is True:
                event = CONF_ROTARY_CCW_FAST
            else:
                event = None
            raw_obj = str(message.object)
            self._ensure_cen_device(25, raw_obj)
            cenplus_payload = {
                "object": int(message.object),
                "pushbutton": int(message.push_button),
                "event": event,
                "where": raw_obj,
                "gateway_mac": self.mac,
            }
            if self.config_entry and hasattr(self.config_entry, "entry_id") and isinstance(self.config_entry.entry_id, str):
                cenplus_payload["entry_id"] = self.config_entry.entry_id
            self.hass.bus.async_fire("myhome_cenplus_event", cenplus_payload)
            async_dispatcher_send(self.hass, f"myhome_cenplus_event_{self.mac}", cenplus_payload)
            LOGGER.debug(
                "%s %s",
                self.log_id,
                message.human_readable_log,
            )
        elif isinstance(message, OWNCENEvent):
            event = None
            if message.is_pressed:
                event = CONF_SHORT_PRESS
            elif message.is_released_after_short_press:
                event = CONF_SHORT_RELEASE
            elif message.is_held:
                event = CONF_LONG_PRESS
            elif message.is_released_after_long_press:
                event = CONF_LONG_RELEASE
            else:
                event = None
            raw_obj = str(message.object)
            self._ensure_cen_device(15, raw_obj)
            cen_payload = {
                "object": int(cast(str, message.object)),
                "pushbutton": int(cast(int, message.push_button)),
                "event": event,
                "where": raw_obj,
                "gateway_mac": self.mac,
            }
            if self.config_entry and hasattr(self.config_entry, "entry_id") and isinstance(self.config_entry.entry_id, str):
                cen_payload["entry_id"] = self.config_entry.entry_id
            self.hass.bus.async_fire("myhome_cen_event", cen_payload)
            async_dispatcher_send(self.hass, f"myhome_cen_event_{self.mac}", cen_payload)
            LOGGER.debug(
                "%s %s",
                self.log_id,
                message.human_readable_log,
            )
        elif isinstance(message, OWNAlarmEvent):
            self.hass.bus.async_fire(
                "myhome_alarm_event",
                {
                    "where": str(message.where),
                    "state": message.state_name,
                    "state_code": message.state_code,
                    "is_alarm": message.is_alarm,
                    "message": str(message),
                },
            )
            LOGGER.debug(
                "%s %s",
                self.log_id,
                message.human_readable_log,
            )
        elif isinstance(message, OWNGatewayEvent) or isinstance(message, OWNGatewayCommand):
            LOGGER.debug(
                "%s %s",
                self.log_id,
                message.human_readable_log,
            )
            if isinstance(message, OWNGatewayEvent):
                self._handle_gateway_diagnostics(message)
        elif (
            getattr(message, "who", None) == 18
            or isinstance(message, (OWNEnergyEvent, OWNEnergyCommand))
        ):
            LOGGER.debug(
                "%s Energy telemetry message: `%s`",
                self.log_id,
                message,
            )
        else:
            LOGGER.debug(
                "%s Unsupported message type: `%s`",
                self.log_id,
                message,
            )

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
        """Apply the identification precedence to a WHO=13 dimension-15 reply.

        The gateway's own SSDP announcement and the user's explicit choice are
        authoritative; the 2006 code table can only corroborate them. It labels
        an entry only when no model is configured at all.
        """
        official = WHO13_OFFICIAL_DEVICE_TYPES.get(raw_code)
        observed = WHO13_OBSERVED_DEVICE_TYPES.get(raw_code)
        who13_model = official or observed
        self._who13["code"] = raw_code
        self._who13["model"] = who13_model
        self._who13["model_official"] = official
        self._who13["model_observed"] = observed
        source = self.identification_source
        configured = str(self.gateway.model_name or "")
        entry_id = getattr(self.config_entry, "entry_id", None)
        entry_id = entry_id if isinstance(entry_id, str) else None

        if not who13_model:
            LOGGER.info(
                "%s WHO=13 reports device type %s, unknown to the 2006 OpenWebNet table and to field evidence; "
                "keeping model `%s`. Please attach a trace to an issue so the code can be documented.",
                self.log_id, raw_code, configured,
            )
            if entry_id:
                async_create_unknown_model_issue(self.hass, entry_id, raw_code)
            self._set_conflict(None, entry_id)
            self._sync_device_registry_model(configured)
            return

        if entry_id:
            async_delete_unknown_model_issue(self.hass, entry_id)

        is_ambiguous = raw_code in WHO13_AMBIGUOUS_DEVICE_TYPES
        compatibility = is_who13_code_compatible(raw_code, configured)

        if source in (IDENTIFICATION_SSDP, IDENTIFICATION_SERIAL) or (source == IDENTIFICATION_MANUAL and (not official or compatibility is not False)):
            # The announced (or serial-fixed) model wins outright; a manual model is kept
            # if compatible with the reply or only questioned by unverified field evidence.
            conflict = None
            if compatibility is False:
                basis = "the OpenWebNet specification" if official else "field evidence"
                conflict = (
                    f"configured as {configured} ({source}) but WHO=13 device type {raw_code} "
                    f"identifies {who13_model} per {basis}"
                )
                LOGGER.warning("%s Gateway identity mismatch: %s.", self.log_id, conflict)
            elif compatibility is None:
                LOGGER.info(
                    "%s WHO=13 reports device type %s (%s per field evidence); "
                    "compatibility with `%s` is unverified, keeping configured model.",
                    self.log_id, raw_code, who13_model, configured,
                )
            self._set_conflict(conflict, entry_id, who13_model=who13_model, raw_code=raw_code, source=source, official=bool(official))
            self._sync_device_registry_model(configured)
            return

        if is_ambiguous:
            # An ambiguous code (e.g. 200 seen on both F454 and MyHOMEServer1) cannot
            # uniquely label an unconfigured gateway.
            LOGGER.info(
                "%s WHO=13 reports device type %s (seen on multiple modern gateways: %s); "
                "keeping model `%s` without auto-labelling.",
                self.log_id, raw_code, who13_model, configured,
            )
            self._set_conflict(None, entry_id)
            self._sync_device_registry_model(configured)
            return

        # Either no trustworthy model (unknown / earlier WHO=13 label) or a manual choice
        # contradicted by an official code: apply the WHO=13 model.
        corrected_from = configured if source == IDENTIFICATION_MANUAL else None
        if who13_model.lower() != configured.lower():
            LOGGER.warning(
                "%s Gateway model `%s` set from WHO=13 device type %s (was `%s`, source %s).",
                self.log_id, who13_model, raw_code, configured, source,
            )
            self.gateway.model_name = who13_model
            self.gateway.model = who13_model
            self.gateway.profile = get_gateway_profile(who13_model)
            self.gateway._log_id = f"[{who13_model} gateway - {self.gateway.host}]"
            if self.config_entry is not None:
                new_data = dict(self.config_entry.data)
                if new_data.get(CONF_NAME) != who13_model:
                    new_data[CONF_NAME] = who13_model
                    new_data["model_source"] = IDENTIFICATION_WHO13
                    update_kwargs: dict[str, Any] = {"data": new_data}
                    if str(getattr(self.config_entry, "title", "")).endswith("Gateway"):
                        update_kwargs["title"] = f"{who13_model} Gateway"
                    self.hass.config_entries.async_update_entry(self.config_entry, **update_kwargs)
            if corrected_from and entry_id:
                async_create_identity_corrected_issue(self.hass, entry_id, corrected_from, who13_model, raw_code)
        self._set_conflict(None, entry_id)
        self._sync_device_registry_model(who13_model)

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
        """Keep the device registry model in step (repairs entries mislabelled by earlier releases)."""
        if not model or not self.device_registry_id:
            return
        dev_reg = dr.async_get(self.hass)
        device = dev_reg.async_get(self.device_registry_id)
        if device is not None and getattr(device, "model", None) != model:
            dev_reg.async_update_device(self.device_registry_id, model=model)

    async def sending_loop(self, worker_id: int) -> None:
        self._terminate_sender = False

        LOGGER.debug(
            "%s Creating sending worker %s",
            self.log_id,
            worker_id,
        )

        LOGGER.debug(
            "%s Worker %s waiting for event session to be ready...",
            self.log_id,
            worker_id,
        )
        while not self._terminate_sender and not self._event_session_ready.is_set():
            try:
                async with asyncio.timeout(EVENT_READY_TIMEOUT):
                    await self._event_session_ready.wait()
            except TimeoutError:
                LOGGER.warning(
                    "%s Worker %s: event session was not ready after %ss; "
                    "continuing to wait without consuming queued commands.",
                    self.log_id,
                    worker_id,
                    EVENT_READY_TIMEOUT,
                )

        if self._terminate_sender:
            return

        LOGGER.debug(
            "%s Worker %s: event session is ready, proceeding with command session.",
            self.log_id,
            worker_id,
        )

        _command_session = OWNCommandSession(gateway=self.gateway, logger=LOGGER)
        try:
            try:
                res = await _command_session.connect()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception(
                    "%s Worker %s: initial command session connection raised; "
                    "queued commands will retry on send.",
                    self.log_id,
                    worker_id,
                )
                res = None

            if self._connect_refused(res, worker_id):
                return

            while not self._terminate_sender:
                idle_timeout = self.command_session_idle_timeout
                try:
                    task = await asyncio.wait_for(
                        self.send_buffer.get(),
                        timeout=idle_timeout,
                    )
                except TimeoutError:
                    # The gateway drops an idle command session on its own timeline
                    # (observed ~30s on MyHomeServer1/MH200N); close ours first so
                    # the next send() reconnects instead of writing into a socket
                    # the gateway has already torn down (issue #378).
                    if _session_is_open(_command_session):
                        LOGGER.debug(
                            "%s Command session idle for %ss; closing socket to release gateway resource.",
                            self.log_id,
                            idle_timeout,
                        )
                        await _command_session.close()
                    continue

                try:
                    if task is None:
                        break

                    LOGGER.debug(
                        "%s Message `%s` was successfully unqueued by worker %s.",
                        self.log_id,
                        task["message"],
                        worker_id,
                    )
                    task_start = time.time()
                    self.bus_monitor.record_frame(
                        direction="tx",
                        raw=str(task["message"]),
                        parsed=(
                            task["message"]
                            if isinstance(task["message"], OWNMessage)
                            else None
                        ),
                    )
                    # The delivery future carries the time the frame reached the bus.
                    # Reconnect explicitly *before* taking the timestamp; OWNd's
                    # send() would otherwise do it after our stamp. The future is resolved
                    # only once send() reports the frame written and acknowledged, and
                    # cancelled when it was not: a frame that never reached the bus must
                    # not start a timed run.
                    if not _session_is_open(_command_session):
                        res = await _command_session.connect()
                        if self._connect_refused(res, worker_id):
                            # As at start-up: no further negotiation with a gateway that
                            # refused us. The frame was not written and never will be.
                            _cancel_written(task)
                            return
                        if not _session_is_open(_command_session):
                            # connect() gave up after its retries; send() would only run
                            # the same cycle again. Drop this frame and try the next.
                            LOGGER.warning(
                                "%s Command session unavailable; message `%s` not sent.",
                                self.log_id,
                                task["message"],
                            )
                            _cancel_written(task)
                            continue
                    written_at = time.monotonic()
                    # OWNd's send() decides the retry policy itself: a status
                    # request may be retried after a transport reset, a written
                    # command is never replayed. Keep this call to its public
                    # signature - the test suite pins it against the real class.
                    collected = await _command_session.send(
                        message=task["message"],
                        is_status_request=task["is_status_request"],
                    )
                    if collected is None:
                        _cancel_written(task)
                    else:
                        _resolve_written(task, written_at)
                    if collected and isinstance(collected, list):
                        for resp in collected:
                            raw_resp = str(resp)
                            if self.bus_monitor.has_frame_since(
                                task_start, direction="rx", raw=raw_resp
                            ):
                                continue
                            frame = self.bus_monitor.record_frame(
                                direction="rx",
                                raw=raw_resp,
                                parsed=resp if isinstance(resp, OWNMessage) else None,
                            )
                            if not getattr(
                                frame, "is_duplicate", False
                            ) and isinstance(resp, OWNMessage):
                                async_dispatcher_send(
                                    self.hass, f"myhome_message_{self.mac}", resp
                                )
                except asyncio.CancelledError:
                    _cancel_written(task)
                    raise
                except Exception:
                    _cancel_written(task)
                    LOGGER.exception(
                        "%s Worker %s: unexpected error while sending `%s`; "
                        "delivery is unconfirmed.",
                        self.log_id,
                        worker_id,
                        task.get("message") if isinstance(task, dict) else task,
                    )
                finally:
                    self.send_buffer.task_done()

                if (
                    hasattr(self.gateway, "profile")
                    and self.gateway.profile.command_queue_delay > 0
                ):
                    await asyncio.sleep(self.gateway.profile.command_queue_delay)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.shield(_command_session.close())
            LOGGER.debug("%s Destroying sending worker %s", self.log_id, worker_id)

    def _connect_refused(self, result: Any, worker_id: int) -> bool:
        """A command-session ``connect()`` result the worker must not retry on.

        A refused negotiation (wrong password, refused connection) is final;
        negotiating again on every queued frame is what locks a gateway out.
        """
        if isinstance(result, dict) and not result.get("Success", True):
            if result.get("Message") in ("password_error", "password_required", "negotiation_refused", "connection_refused"):
                LOGGER.error(
                    "%s Command session authentication or connection refused (%s). Terminating sending worker %s to prevent gateway lockout.",
                    self.log_id,
                    result.get("Message"),
                    worker_id,
                )
                return True
        return False

    async def initial_discovery(self) -> None:
        """Queue the startup sweep that discovers devices missing from the config.

        Replies are dispatched to the platform message listeners, so this must
        only run once every platform has subscribed: a fast gateway can answer
        before then and the reply would be silently dropped.
        """
        # Active Discovery (WHO=1 general status request *#1*0## is invalid in OpenWebNet and omitted).
        # Only query subsystems the gateway profile advertises: an MH200N NACKs *#16*0##
        # (no audio) and logs a retry error on every boot otherwise.
        for who, frame in ((2, "*#2*0##"), (4, "*#4*0##"), (16, "*#16*0##")):
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
        LOGGER.info("%s Closing event listener", self.log_id)
        self._terminate_sender = True
        self._terminate_listener = True
        if self._unavailable_timer is not None:
            self._unavailable_timer()
        for t in self._resync_timers.values():
            t()
        self._resync_timers.clear()
        self._resync_group_echoes.clear()
        self._recent_ptp.clear()
        self._unavailable_timer = None
        self.is_connected = False
        self._available = False
        self._event_session_ready.set()
        self._sender_stop.set()


        # Nothing queued will be written any more: tell the callers waiting on delivery
        while True:
            try:
                task = self.send_buffer.get_nowait()
            except asyncio.QueueEmpty:
                break
            if task is not None:
                _cancel_written(task)
            self.send_buffer.task_done()

        # Unblock any sending workers waiting on send_buffer
        for _ in range(max(1, len(self.sending_workers))):
            try:
                self.send_buffer.put_nowait(None)
            except (asyncio.QueueFull, Exception):
                pass

        return True

    async def send(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a command; the returned future resolves to the monotonic write time."""
        return await self._enqueue(message, is_status_request=False)

    async def send_status_request(self, message: OWNCommand) -> asyncio.Future[float]:
        """Queue a status request; the returned future resolves to the monotonic write time."""
        return await self._enqueue(message, is_status_request=True)

    async def _enqueue(self, message: OWNCommand, *, is_status_request: bool) -> asyncio.Future[float]:
        """Put a frame on the send queue and hand back its delivery future.

        The future completes with ``time.monotonic()`` taken by the sending
        worker immediately before the frame is written to an already open
        command session - queue wait and reconnect included - once the
        gateway has acknowledged it, so callers that model physical motion
        (timed covers) can start their clock at the real write instead of
        at enqueue. It is cancelled when the frame was not delivered (send
        failed, NACK) or if the gateway shuts down
        before the frame leaves.
        """
        written: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        await self.send_buffer.put(
            {"message": message, "is_status_request": is_status_request, "written": written}
        )
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )
        return written

    def _known_light_areas(self) -> list[str]:
        areas = set()
        if not self.config_entry or not hasattr(self.config_entry, "entry_id") or not isinstance(self.config_entry.entry_id, str):
            return []

        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(registry, self.config_entry.entry_id)
        for entry in entries:
            if entry.domain in ("light", "switch"):
                # entry.unique_id is like "00:03:50:00:12:34-1-12"
                _, key = parse_unique_id(entry.unique_id, self.gateway.mac)
                if not key:
                    continue
                address = Address.from_device_id(key)
                area = area_of_where(address.where)
                if area:
                    areas.add(area)
        return sorted(list(areas))

    def _schedule_resync(self, message: Any) -> None:
        if not self.broadcast_resync:
            return

        now = time.monotonic()
        while self._recent_ptp and self._recent_ptp[0][0] < now - RESYNC_LEADING_WINDOW_S:
            self._recent_ptp.popleft()

        targets = []
        if getattr(message, "is_group", False):
            # Check leading echoes: gateways like MyHomeServer1 emit member echoes ~0.9s before
            # the group frame. If several (>= 2) PTP frames arrived in the leading window, skip sweep.
            recent_count = sum(1 for t, _, _ in self._recent_ptp if t >= now - RESYNC_LEADING_WINDOW_S)
            if recent_count >= 2:
                LOGGER.debug("%s group #%s had %d leading member echoes, skipping sweep", self.log_id, message.group, recent_count)
                return
            targets.append(f"#{message.group}")
        elif getattr(message, "is_area", False):
            raw_where = str(message.where)
            # Check leading echoes for this area
            if any(a == raw_where for _, _, a in self._recent_ptp):
                LOGGER.debug("%s area %s had leading member echoes, skipping sweep", self.log_id, raw_where)
                return
            # Use the frame's raw WHERE ("00"/"1".."9"/"100"), not `message.area`
            # (an int, e.g. 0 for area "00" or 10 for area "100"): re-deriving the
            # status request from the int would either emit the banned `*#1*0##`
            # (general) or target the wrong point-to-point address (`*#1*10##`).
            targets.append(raw_where)
        elif getattr(message, "is_general", False):
            for a in self._known_light_areas():
                # If this area had leading echoes in the leading window, skip it
                if any(entry_a == a for _, _, entry_a in self._recent_ptp):
                    LOGGER.debug("%s general sweep skipping area %s (had leading echoes)", self.log_id, a)
                    continue
                targets.append(f"{a}")

        for where in targets:
            if where in self._resync_timers:
                self._resync_timers.pop(where)()
            if where.startswith("#"):
                self._resync_group_echoes[where] = 0

            @callback
            def _cb(now_cb: Any, w: str = where) -> None:
                self.hass.async_create_task(self._resync_broadcast(w))

            self._resync_timers[where] = async_call_later(self.hass, RESYNC_DEBOUNCE_S, _cb)

    async def _resync_broadcast(self, where: str) -> None:
        self._resync_timers.pop(where, None)
        self._resync_group_echoes.pop(where, None)
        if self._terminate_listener:
            return

        await self.send_status_request(OWNLightingCommand.status(where))
