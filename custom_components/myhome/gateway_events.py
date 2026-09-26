"""Gateway event dispatcher for MyHOME.

Parses incoming OpenWebNet frames, dispatches Home Assistant bus events,
and registers CEN/CEN+ scenario pushbuttons in the device registry.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAlarmEvent,
    OWNAutomationEvent,
    OWNAuxEvent,
    OWNCENEvent,
    OWNCENPlusEvent,
    OWNDryContactEvent,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingEvent,
    OWNMessage,
)

from .const import (
    CONF_LONG_PRESS,
    CONF_LONG_PRESS_REPEAT,
    CONF_LONG_RELEASE,
    CONF_ROTARY_CCW_FAST,
    CONF_ROTARY_CCW_SLOW,
    CONF_ROTARY_CW_FAST,
    CONF_ROTARY_CW_SLOW,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler


class GatewayEventDispatcher:
    """Dispatches bus and integration events from gateway monitor frames."""

    def __init__(
        self,
        handler: MyHOMEGatewayHandler,
        cen_devices: set[tuple[int, Any]] | None = None,
    ) -> None:
        """Initialize the event dispatcher."""
        self.handler = handler
        self._cen_devices: set[tuple[int, Any]] = (
            cen_devices if cen_devices is not None else set()
        )

    @property
    def _logger(self) -> Any:
        from . import gateway as gw_module

        return getattr(gw_module, "LOGGER", LOGGER)

    @property
    def hass(self) -> HomeAssistant:
        """Return HomeAssistant instance."""
        return self.handler.hass

    @property
    def cen_devices(self) -> set[tuple[int, Any]]:
        """Return the registered CEN device set."""
        return self._cen_devices

    def ensure_cen_device(self, who: int, object_id: int | str) -> None:
        """Ensure CEN/CEN+ scenario unit is registered in device registry."""
        device_key = (who, object_id)
        obj_str = str(object_id)
        if device_key in self._cen_devices or (who, obj_str) in self._cen_devices:
            return

        config_entry = getattr(self.handler, "config_entry", None)
        if not config_entry or not hasattr(config_entry, "entry_id") or not isinstance(config_entry.entry_id, str):
            return
        if self.handler.device_registry_id is None:
            self._logger.debug(
                "%s Deferring %s device %s until the gateway device is registered.",
                self.handler.log_id,
                "CEN+" if who == 25 else "CEN",
                obj_str,
            )
            return

        try:
            device_registry = dr.async_get(self.hass)
            type_name = "CEN+" if who == 25 else "CEN"
            via_kwargs: dict[str, Any] = {}
            if self.handler.device_registry_id:
                via_kwargs["via_device_id"] = self.handler.device_registry_id
            device_registry.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers={(DOMAIN, f"{self.handler.mac}-{who}-{obj_str}")},
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
            self._logger.debug("Could not auto-register %s device %s: %s", who, object_id, err)

    _ensure_cen_device = ensure_cen_device

    async def process_message(self, message: Any) -> None:
        """Process a received message and dispatch to Home Assistant."""
        from . import gateway as gw_module

        dispatcher_send = getattr(gw_module, "async_dispatcher_send", async_dispatcher_send)

        if message is None:
            self._logger.debug("%s Data received is not a message: `None`", self.handler.log_id)
            return

        if getattr(self.handler, "generate_events", False):
            if isinstance(message, OWNMessage):
                event_content = {"gateway": str(self.handler.gateway.host)}
                event_content.update(message.event_content)
                self.hass.bus.async_fire("myhome_message_event", event_content)
            else:
                self.hass.bus.async_fire(
                    "myhome_message_event",
                    {"gateway": str(self.handler.gateway.host), "message": str(message)},
                )

        if isinstance(message, OWNMessage):
            dispatcher_send(self.hass, f"myhome_message_{self.handler.mac}", message)

        if not isinstance(message, OWNMessage):
            self._logger.warning(
                "%s Data received is not a message: `%s`",
                self.handler.log_id,
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
                if (
                    isinstance(message, OWNLightingEvent)
                    and not getattr(message, "is_group", False)
                    and not getattr(message, "is_area", False)
                    and not getattr(message, "is_general", False)
                ):
                    self.handler._resync_manager.handle_ptp_echo(message)

                if isinstance(message, OWNLightingEvent):
                    if message.is_on is not None:
                        event = "on" if message.is_on else "off"
                        if message.is_general:
                            self.hass.bus.async_fire(
                                "myhome_general_light_event",
                                {"message": str(message), "event": event},
                            )
                        elif message.is_area:
                            self.hass.bus.async_fire(
                                "myhome_area_light_event",
                                {
                                    "message": str(message),
                                    "area": message.area,
                                    "event": event,
                                },
                            )
                        elif message.is_group:
                            self.hass.bus.async_fire(
                                "myhome_group_light_event",
                                {
                                    "message": str(message),
                                    "group": message.group,
                                    "event": event,
                                },
                            )
                    if (
                        getattr(message, "is_general", False)
                        or getattr(message, "is_area", False)
                        or getattr(message, "is_group", False)
                    ):
                        self.handler._schedule_resync(message)
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
                self._logger.debug(
                    "%s Ignoring translation message `%s`",
                    self.handler.log_id,
                    message,
                )
        elif isinstance(message, OWNHeatingCommand) and message.dimension is not None and message.dimension == 14:
            where_str = cast(str, message.where)
            where = where_str[1:] if where_str.startswith("#") else where_str
            self._logger.debug(
                "%s Received heating command, sending query to zone %s",
                self.handler.log_id,
                where,
            )
            from OWNd.message import OWNHeatingCommand as OWNHCmd

            await self.handler.send_status_request(OWNHCmd.status(where))
        elif isinstance(message, OWNCENPlusEvent):
            event = None
            if message.is_short_pressed:
                event = CONF_SHORT_PRESS
            elif message.is_held:
                event = CONF_LONG_PRESS
            elif message.is_still_held:
                event = CONF_LONG_PRESS_REPEAT
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
            self.handler._ensure_cen_device(25, raw_obj)
            cenplus_payload: dict[str, Any] = {
                "object": int(message.object),
                "pushbutton": int(message.push_button),
                "event": event,
                "where": raw_obj,
                "gateway_mac": self.handler.mac,
            }
            config_entry = getattr(self.handler, "config_entry", None)
            if config_entry and hasattr(config_entry, "entry_id") and isinstance(config_entry.entry_id, str):
                cenplus_payload["entry_id"] = config_entry.entry_id
            self.hass.bus.async_fire("myhome_cenplus_event", cenplus_payload)
            dispatcher_send(self.hass, f"myhome_cenplus_event_{self.handler.mac}", cenplus_payload)
            self._logger.debug(
                "%s %s",
                self.handler.log_id,
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
            self.handler._ensure_cen_device(15, raw_obj)
            cen_payload: dict[str, Any] = {
                "object": int(cast(str, message.object)),
                "pushbutton": int(cast(int, message.push_button)),
                "event": event,
                "where": raw_obj,
                "gateway_mac": self.handler.mac,
            }
            config_entry = getattr(self.handler, "config_entry", None)
            if config_entry and hasattr(config_entry, "entry_id") and isinstance(config_entry.entry_id, str):
                cen_payload["entry_id"] = config_entry.entry_id
            self.hass.bus.async_fire("myhome_cen_event", cen_payload)
            dispatcher_send(self.hass, f"myhome_cen_event_{self.handler.mac}", cen_payload)
            self._logger.debug(
                "%s %s",
                self.handler.log_id,
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
            self._logger.debug(
                "%s %s",
                self.handler.log_id,
                message.human_readable_log,
            )
        elif isinstance(message, OWNGatewayEvent) or isinstance(message, OWNGatewayCommand):
            self._logger.debug(
                "%s %s",
                self.handler.log_id,
                message.human_readable_log,
            )
            if isinstance(message, OWNGatewayEvent):
                self.handler._handle_gateway_diagnostics(message)
        elif getattr(message, "who", None) == 1013:
            if getattr(message, "dimension", getattr(message, "_dimension", None)) == 1:
                self.handler._handle_gateway_identity_diagnostics(message)
            else:
                self._logger.debug(
                    "%s Unhandled WHO=1013 diagnostic message: `%s`",
                    self.handler.log_id,
                    message,
                )
        elif (
            getattr(message, "who", None) == 18
            or isinstance(message, (OWNEnergyEvent, OWNEnergyCommand))
        ):
            self._logger.debug(
                "%s Energy telemetry message: `%s`",
                self.handler.log_id,
                message,
            )
        else:
            self._logger.debug(
                "%s Unsupported message type: `%s`",
                self.handler.log_id,
                message,
            )

    _process_message = process_message
