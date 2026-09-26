"""Services for the MyHOME integration."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, cast

from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    DOMAIN,
    SERVICE_STOP_COVER_CALIBRATION,
)
from .data import get_runtime_data

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

_LOGGER = logging.getLogger(__name__)

SERVICE_SYNC_TIME = "sync_time"
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_SWEEP_BUS = "sweep_bus"


def _loaded_gateways(hass: HomeAssistant) -> dict[str, MyHOMEGatewayHandler]:
    """Return {mac: gateway handler} for every config entry that is set up."""
    gateways: dict[str, MyHOMEGatewayHandler] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = get_runtime_data(entry)
        if runtime is not None:
            gateways[str(entry.data.get(CONF_MAC) or runtime.mac)] = runtime.gateway
    return gateways


def _get_gateway_handler(hass: HomeAssistant, gateway_identifier: str | None) -> MyHOMEGatewayHandler | None:
    """Retrieve the MyHOMEGatewayHandler for a given gateway MAC or default."""
    gateways = _loaded_gateways(hass)
    if not gateways:
        return None

    if gateway_identifier is None:
        return next(iter(gateways.values()))

    if gateway_identifier in gateways:
        return gateways[gateway_identifier]

    mac = dr.format_mac(gateway_identifier)
    if mac is not None:
        for known_mac, handler in gateways.items():
            if known_mac.lower() == mac.lower():
                return handler

    return None


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register MyHOME domain services."""
    if hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME):
        return

    async def handle_sync_time(call: ServiceCall) -> None:
        """Handle time synchronization service call."""
        gateway = call.data.get(ATTR_GATEWAY, None)
        if gateway is None:
            if not _loaded_gateways(hass):
                _LOGGER.error("No MyHOME gateways found, cannot sync time.")
                return
        else:
            gateway = dr.format_mac(gateway)

        timezone = hass.config.as_dict().get("time_zone", "UTC")
        handler = _get_gateway_handler(hass, gateway)
        if handler is not None:
            from OWNd.message import OWNGatewayCommand
            await handler.send(OWNGatewayCommand.set_datetime_to_now(timezone))
            return

        _LOGGER.error(
            "Gateway `%s` not found, could not send time synchronisation message.",
            gateway,
        )
        return

    async def handle_send_message(call: ServiceCall) -> None:
        """Handle sending an arbitrary OpenWebNet message."""
        gateway = call.data.get(ATTR_GATEWAY, None)
        message = call.data.get(ATTR_MESSAGE, None)
        if gateway is None:
            if not _loaded_gateways(hass):
                _LOGGER.error("No MyHOME gateways found, cannot send message `%s`.", message)
                return
        else:
            gateway = dr.format_mac(gateway)

        _LOGGER.debug("Handling message `%s` to be sent to `%s`", message, gateway)
        handler = _get_gateway_handler(hass, gateway)
        if handler is not None:
            if message is not None:
                from OWNd.message import OWNCommand
                own_message = OWNCommand.parse(message)
                if own_message is not None and own_message.is_valid:
                    _LOGGER.debug(
                        "%s Sending valid OpenWebNet Message: `%s`",
                        handler.log_id,
                        own_message,
                    )
                    await handler.send(own_message)
                    return
                _LOGGER.error(
                    "Could not parse message `%s`, not sending it.", message
                )
                return
            _LOGGER.error("No message specified to send.")
            return

        _LOGGER.error(
            "Gateway `%s` not found, could not send message `%s`.", gateway, message
        )
        return

    async def handle_sweep_bus(call: ServiceCall) -> None:
        """Trigger an active status query sweep across bus subsystems to populate the bus monitor."""
        from OWNd.message import OWNCommand, OWNMessage

        gateway = call.data.get(ATTR_GATEWAY, None)
        gateways = _loaded_gateways(hass)
        target_gateways: dict[str, MyHOMEGatewayHandler] = {}
        if gateway is not None:
            mac = dr.format_mac(gateway)
            handler = _get_gateway_handler(hass, mac) if mac else None
            if handler is not None:
                target_gateways[mac] = handler
            else:
                _LOGGER.error("Gateway `%s` not found for sweep_bus.", gateway)
                return
        else:
            target_gateways = gateways

        if not target_gateways:
            _LOGGER.warning("No active MyHOME gateways found to sweep.")
            return

        sweep_queries = [
            "*#13**0##",   # Gateway real-time clock
            "*#13**15##",  # Gateway device model
            "*#13**16##",  # Gateway firmware version
            "*#2*0##",     # All cover actuators
            "*#4*0##",     # Thermoregulation master status
            "*#5*0##",     # Burglar alarm central unit status
            "*#16*0*5##",  # Sound system status (lists all amplifiers & sources)
        ]

        for gw_mac, handler in target_gateways.items():
            _LOGGER.info("Executing diagnostic bus sweep on gateway %s", gw_mac)
            for query in sweep_queries:
                msg = OWNMessage.parse(query)
                if msg is not None:
                    await handler.send(cast(OWNCommand, msg))
                await asyncio.sleep(0.05)

        return

    async def handle_stop_cover_calibration(call: ServiceCall) -> None:
        """Handle stopping active and queued cover calibrations."""
        from .cover import async_stop_cover_calibration
        gateway = call.data.get(ATTR_GATEWAY, None)
        await async_stop_cover_calibration(hass, gateway_mac=gateway)

    hass.services.async_register(DOMAIN, SERVICE_SYNC_TIME, handle_sync_time)
    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, handle_send_message)
    hass.services.async_register(DOMAIN, SERVICE_SWEEP_BUS, handle_sweep_bus)
    hass.services.async_register(DOMAIN, SERVICE_STOP_COVER_CALIBRATION, handle_stop_cover_calibration)
