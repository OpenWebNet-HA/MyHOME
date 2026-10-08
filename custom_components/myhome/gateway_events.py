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
        if getattr(self.handler, "is_standby", False) or not self._is_active_for_who(who):
            # Standby gateways never register devices on their own config entry,
            # and followers/primaries only register for their active subsystems.
            return

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

            wire_ident = (DOMAIN, f"{self.handler.mac}-{who}-{obj_str}")
            identifiers = {wire_ident}

            # Only pure numeric addresses with leading zeros (e.g. "0512") have a normalized decimal form.
            # Routed addresses containing '#' (e.g. "36#4#01") must never strip routing or collide with base units.
            norm_str: str | None = None
            if obj_str.isdigit() and len(obj_str) > 1 and obj_str.startswith("0"):
                norm_str = str(int(obj_str))

            existing_wire_dev = device_registry.async_get_device(identifiers={wire_ident})
            existing_norm_dev = (
                device_registry.async_get_device(identifiers={(DOMAIN, f"{self.handler.mac}-{who}-{norm_str}")})
                if norm_str is not None
                else None
            )

            # Prevent passing multiple identifiers if two separate devices already exist in the registry,
            # which would cause async_get_or_create to raise ValueError.
            if existing_norm_dev is not None and existing_wire_dev is None:
                # Alias the wire identifier onto the existing normalized device
                identifiers.add((DOMAIN, f"{self.handler.mac}-{who}-{norm_str}"))

            # Preserve existing user-facing device name if the device already exists
            existing_dev = existing_wire_dev or existing_norm_dev
            dev_name = (
                existing_dev.name
                if existing_dev and isinstance(existing_dev.name, str) and existing_dev.name
                else f"{type_name} Unit {obj_str}"
            )

            device_registry.async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers=identifiers,
                name=dev_name,
                manufacturer="BTicino",
                model=f"{type_name} Scenario Control",
                **via_kwargs,
            )
            self._cen_devices.add(device_key)
            self._cen_devices.add((who, obj_str))
            if norm_str is not None and (existing_norm_dev is not None or existing_wire_dev is None):
                self._cen_devices.add((who, norm_str))
            try:
                self._cen_devices.add((who, int(object_id)))
            except (ValueError, TypeError):
                pass
        except Exception as err:
            self._logger.debug("Could not auto-register %s device %s: %s", who, object_id, err)

    _ensure_cen_device = ensure_cen_device

    def _is_active_for_who(self, who: int | None) -> bool:
        """Return True if this gateway is the active owner for this WHO subsystem."""
        if getattr(self.handler, "is_standby", False):
            # Standby is only active for bus events if failover is active (primary is offline)
            primary_gw = self.handler._get_primary_gateway()
            if primary_gw is not None and not primary_gw.is_connected:
                return who is not None and self.handler._profile_supports_who(who)
            return False

        if getattr(self.handler, "is_secondary", False):
            return who is not None and who in getattr(self.handler, "delegated_whos", set())

        # Primary or standalone
        return who is None or who not in getattr(self.handler, "delegated_away_whos", set())

    def _observe_health(self, message: OWNMessage, who: int) -> None:
        """Feed a lighting frame to the tracker that owns this address's issues.

        A warm standby hands the frame to its primary's tracker: the primary's entry
        owns the issue, so it still clears when the primary returns and stops
        listening to the standby. A failure here must not cost the frame its handling.
        """
        try:
            if not self._is_active_for_who(who - 1000 if who > 1000 else who):
                return
            health = self.handler.health_owner()
            if health is not None:
                health.observe(message)
        except Exception:
            self._logger.exception("%s Device health could not process `%s`", self.handler.log_id, message)

    async def process_message(self, message: Any) -> None:
        """Process a received message and dispatch to Home Assistant."""
        from . import gateway as gw_module

        dispatcher_send = getattr(gw_module, "async_dispatcher_send", async_dispatcher_send)

        if message is None:
            self._logger.debug("%s Data received is not a message: `None`", self.handler.log_id)
            return

        note_frame = getattr(self.handler, "note_event_frame", None)
        if callable(note_frame):
            note_frame()

        msg_who = getattr(message, "who", getattr(message, "_who", None))
        who_int = int(msg_who) if msg_who is not None and str(msg_who).isdigit() else None

        if getattr(self.handler, "generate_events", False) and self._is_active_for_who(who_int):
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
            self.handler._correlate_shared_bus_traffic(message)
            self.handler._bridge_to_primary(message)
            # Diagnostic WHOs (1001 for lighting) belong to their functional subsystem:
            # on a shared bus only that subsystem's owner raises the device's issues.
            if who_int in (1, 1001):
                self._observe_health(message, who_int)

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
                if isinstance(message, OWNLightingEvent) and self._is_active_for_who(1):
                    if (
                        not getattr(message, "is_group", False)
                        and not getattr(message, "is_area", False)
                        and not getattr(message, "is_general", False)
                        and message.is_on is not None
                        and message.dimension is None
                    ):
                        # Only an actuator's own on/off status is a member echo: motion
                        # frames and illuminance / PIR dimension pushes must not cancel
                        # or count towards a resync sweep.
                        self.handler._resync_manager.handle_ptp_echo(message)

                    dim = getattr(message, "dimension", None)
                    is_not_dimension = dim is None or type(dim).__name__ == "MagicMock"

                    if message.is_on is not None and is_not_dimension:
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
                elif isinstance(message, OWNAutomationEvent) and self._is_active_for_who(2):
                    if message.is_opening and not message.is_closing:
                        event = "open"
                    elif message.is_closing and not message.is_opening:
                        event = "close"
                    else:
                        event = "stop"

                    where_raw = getattr(message, "where", None)
                    where_val = str(where_raw) if (where_raw is not None and not str(where_raw).startswith("<MagicMock")) else "0"

                    auto_payload: dict[str, Any] = {
                        "message": str(message),
                        "event": event,
                        "where": where_val,
                        "gateway_mac": self.handler.mac,
                    }
                    config_entry = getattr(self.handler, "config_entry", None)
                    if config_entry and hasattr(config_entry, "entry_id") and isinstance(config_entry.entry_id, str):
                        auto_payload["entry_id"] = config_entry.entry_id

                    if message.is_general:
                        self.hass.bus.async_fire(
                            "myhome_general_automation_event",
                            auto_payload,
                        )
                        dispatcher_send(
                            self.hass,
                            f"myhome_general_automation_event_{self.handler.mac}",
                            auto_payload,
                        )
                    elif message.is_area:
                        area_payload = dict(auto_payload)
                        area_payload["area"] = getattr(message, "area", None)
                        self.hass.bus.async_fire(
                            "myhome_area_automation_event",
                            area_payload,
                        )
                        dispatcher_send(
                            self.hass,
                            f"myhome_area_automation_event_{self.handler.mac}",
                            area_payload,
                        )
                    elif message.is_group:
                        group_payload = dict(auto_payload)
                        group_payload["group"] = getattr(message, "group", None)
                        self.hass.bus.async_fire(
                            "myhome_group_automation_event",
                            group_payload,
                        )
                        dispatcher_send(
                            self.hass,
                            f"myhome_group_automation_event_{self.handler.mac}",
                            group_payload,
                        )
            else:
                self._logger.debug(
                    "%s Ignoring translation message `%s`",
                    self.handler.log_id,
                    message,
                )
        elif isinstance(message, OWNHeatingCommand) and message.dimension is not None and message.dimension == 14 and self._is_active_for_who(4):
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
            clean_obj = raw_obj.split("#")[0]
            try:
                obj_val: int | str = int(clean_obj)
            except (ValueError, TypeError):  # pragma: no cover - defensive
                obj_val = raw_obj
            try:
                pb_val = int(message.push_button)
            except (ValueError, TypeError):  # pragma: no cover - defensive
                pb_val = message.push_button

            target_mac: str | None = self.handler.mac
            config_entry = getattr(self.handler, "config_entry", None)
            target_entry_id = getattr(config_entry, "entry_id", None) if config_entry else None

            if getattr(self.handler, "is_standby", False):
                primary_gw = self.handler._get_primary_gateway()
                if primary_gw is not None and not primary_gw.is_connected and self.handler._profile_supports_who(25):
                    target_mac = primary_gw.mac
                    pri_entry = getattr(primary_gw, "config_entry", None)
                    target_entry_id = getattr(pri_entry, "entry_id", None) if pri_entry else None
                else:
                    target_mac = None

            if target_mac is not None and self._is_active_for_who(25):
                self.handler._ensure_cen_device(25, raw_obj)
                cenplus_payload: dict[str, Any] = {
                    "object": obj_val,
                    "pushbutton": pb_val,
                    "event": event,
                    "where": raw_obj,
                    "gateway_mac": target_mac,
                }
                if target_entry_id and isinstance(target_entry_id, str):
                    cenplus_payload["entry_id"] = target_entry_id
                self.hass.bus.async_fire("myhome_cenplus_event", cenplus_payload)
                dispatcher_send(self.hass, f"myhome_cenplus_event_{target_mac}", cenplus_payload)
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
            clean_obj = raw_obj.split("#")[0]
            try:
                obj_val = int(clean_obj)
            except (ValueError, TypeError):  # pragma: no cover - defensive
                obj_val = raw_obj
            try:
                pb_val = int(cast(int, message.push_button))
            except (ValueError, TypeError):  # pragma: no cover - defensive
                pb_val = message.push_button

            target_mac = self.handler.mac
            config_entry = getattr(self.handler, "config_entry", None)
            target_entry_id = getattr(config_entry, "entry_id", None) if config_entry else None

            if getattr(self.handler, "is_standby", False):
                primary_gw = self.handler._get_primary_gateway()
                if primary_gw is not None and not primary_gw.is_connected and self.handler._profile_supports_who(15):
                    target_mac = primary_gw.mac
                    pri_entry = getattr(primary_gw, "config_entry", None)
                    target_entry_id = getattr(pri_entry, "entry_id", None) if pri_entry else None
                else:
                    target_mac = None

            if target_mac is not None and self._is_active_for_who(15):
                self.handler._ensure_cen_device(15, raw_obj)
                cen_payload: dict[str, Any] = {
                    "object": obj_val,
                    "pushbutton": pb_val,
                    "event": event,
                    "where": raw_obj,
                    "gateway_mac": target_mac,
                }
                if target_entry_id and isinstance(target_entry_id, str):
                    cen_payload["entry_id"] = target_entry_id
                self.hass.bus.async_fire("myhome_cen_event", cen_payload)
                dispatcher_send(self.hass, f"myhome_cen_event_{target_mac}", cen_payload)
            self._logger.debug(
                "%s %s",
                self.handler.log_id,
                message.human_readable_log,
            )
        elif isinstance(message, OWNAlarmEvent) and self._is_active_for_who(5):
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
