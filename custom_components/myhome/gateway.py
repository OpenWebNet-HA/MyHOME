"""Code to handle a MyHome Gateway."""
import asyncio
from typing import Any, Dict, List

from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
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
    LOGGER,
)

EVENT_READY_TIMEOUT = 120
COMMAND_SESSION_IDLE_TIMEOUT = 15.0
AVAILABILITY_GRACE = 60


class MyHOMEGatewayHandler:
    """Manages a single MyHOME Gateway."""

    def __init__(self, hass, config_entry, generate_events=False):
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
        self._unavailable_timer = None
        self._event_session_ready = asyncio.Event()
        self._sender_stop = asyncio.Event()
        self.listening_worker: asyncio.tasks.Task = None
        self.sending_workers: List[asyncio.tasks.Task] = []
        queue_max_size = (
            self.gateway.profile.max_queue_size
            if hasattr(self.gateway, "profile") and self.gateway.profile
            else 250
        )
        self.send_buffer = asyncio.Queue(maxsize=queue_max_size)
        self.bus_monitor = BusMonitor()
        self.device_registry_id: str | None = None
        self._cen_devices: set[tuple[int, Any]] = set()

    def _ensure_cen_device(self, who: int, object_id: int | str) -> None:
        """Ensure CEN/CEN+ scenario unit is registered in device registry."""
        device_key = (who, object_id)
        obj_str = str(object_id)
        if device_key in self._cen_devices or (who, obj_str) in self._cen_devices:
            return

        self._cen_devices.add(device_key)
        self._cen_devices.add((who, obj_str))
        try:
            self._cen_devices.add((who, int(object_id)))
        except (ValueError, TypeError):
            pass

        if not self.config_entry or not hasattr(self.config_entry, "entry_id") or not isinstance(self.config_entry.entry_id, str):
            return

        try:
            device_registry = dr.async_get(self.hass)
            type_name = "CEN+" if who == 25 else "CEN"
            device_registry.async_get_or_create(
                config_entry_id=self.config_entry.entry_id,
                identifiers={(DOMAIN, f"{self.mac}-{who}-{obj_str}")},
                name=f"{type_name} Unit {obj_str}",
                manufacturer="BTicino",
                model=f"{type_name} Scenario Control",
                via_device=(DOMAIN, self.mac),
            )
        except Exception as err:
            LOGGER.debug("Could not auto-register %s device %s: %s", who, object_id, err)


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
        return self.gateway.log_id

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
        return self.gateway.model_name

    @property
    def firmware(self) -> str:
        fw = self.gateway.firmware
        if isinstance(fw, (list, tuple)):
            return ".".join(str(x) for x in fw) if fw else None
        return str(fw) if fw else None

    @property
    def profile(self):
        return self.gateway.profile

    @property
    def available(self) -> bool:
        """Return the grace-filtered gateway availability."""
        return self._available

    @property
    def availability_signal(self) -> str:
        """Return the dispatcher signal for availability changes."""
        return f"{DOMAIN}_{self.mac}_availability"

    async def test(self) -> Dict:
        return await OWNSession(gateway=self.gateway, logger=LOGGER).test_connection()

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
    def _mark_unavailable(self, _now) -> None:
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

    async def listening_loop(self):
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

        # Active Discovery (WHO=1 general status request *#1*0## is invalid in OpenWebNet and omitted)
        await self.send_status_request(OWNCommand.parse("*#2*0##")) # Automation / Covers
        await self.send_status_request(OWNCommand.parse("*#4*0##")) # Heating / Climate
        await self.send_status_request(OWNCommand.parse("*#16*0##")) # Audio

        while not self._terminate_listener:
            message = await _event_session.get_next()
            if message is not None:
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

    async def _process_message(self, message: Any) -> None:
        """Process a received message and dispatch to Home Assistant."""
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
                        await asyncio.sleep(0.1)
                        await self.send_status_request(OWNLightingCommand.status(message.area))
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
            where = message.where[1:] if message.where.startswith("#") else message.where
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
                "object": int(message.object),
                "pushbutton": int(message.push_button),
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

    async def sending_loop(self, worker_id: int):
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
        res = await _command_session.connect()
        if isinstance(res, dict) and not res.get("Success", True):
            if res.get("Message") in ("password_error", "password_required", "negotiation_refused", "connection_refused"):
                LOGGER.error(
                    "%s Command session authentication or connection refused (%s). Terminating sending worker %s to prevent gateway lockout.",
                    self.log_id,
                    res.get("Message"),
                    worker_id,
                )
                return

        while not self._terminate_sender:
            try:
                task = await asyncio.wait_for(
                    self.send_buffer.get(),
                    timeout=COMMAND_SESSION_IDLE_TIMEOUT,
                )
            except TimeoutError:
                if _command_session and _command_session.is_connected:
                    LOGGER.debug(
                        "%s Command session idle for %ss; closing socket to release gateway resource.",
                        self.log_id,
                        COMMAND_SESSION_IDLE_TIMEOUT,
                    )
                    await _command_session.close()
                continue

            if task is None:
                self.send_buffer.task_done()
                break


            LOGGER.debug(
                "%s Message `%s` was successfully unqueued by worker %s.",
                self.log_id,
                task["message"],
                worker_id,
            )
            self.bus_monitor.record_frame(
                direction="tx",
                raw=str(task["message"]),
                parsed=task["message"] if isinstance(task["message"], OWNMessage) else None,
            )
            collected = await _command_session.send(message=task["message"], is_status_request=task["is_status_request"])
            if collected and isinstance(collected, list):
                for resp in collected:
                    self.bus_monitor.record_frame(
                        direction="rx",
                        raw=str(resp),
                        parsed=resp if isinstance(resp, OWNMessage) else None,
                    )
                    if isinstance(resp, OWNMessage):
                        async_dispatcher_send(self.hass, f"myhome_message_{self.mac}", resp)
            self.send_buffer.task_done()

            if hasattr(self.gateway, "profile") and self.gateway.profile.command_queue_delay > 0:
                await asyncio.sleep(self.gateway.profile.command_queue_delay)

        await _command_session.close()

        LOGGER.debug(
            "%s Destroying sending worker %s",
            self.log_id,
            worker_id,
        )

    async def close_listener(self) -> bool:
        LOGGER.info("%s Closing event listener", self.log_id)
        self._terminate_sender = True
        self._terminate_listener = True
        if self._unavailable_timer is not None:
            self._unavailable_timer()
            self._unavailable_timer = None
        self.is_connected = False
        self._available = False
        self._event_session_ready.set()
        self._sender_stop.set()


        # Unblock any sending workers waiting on send_buffer
        for _ in range(max(1, len(self.sending_workers))):
            try:
                self.send_buffer.put_nowait(None)
            except (asyncio.QueueFull, Exception):
                pass

        return True

    async def send(self, message: OWNCommand):
        await self.send_buffer.put({"message": message, "is_status_request": False})
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )

    async def send_status_request(self, message: OWNCommand):
        await self.send_buffer.put({"message": message, "is_status_request": True})
        LOGGER.debug(
            "%s Message `%s` was successfully queued.",
            self.log_id,
            message,
        )
