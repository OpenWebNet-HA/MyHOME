"""Services for the MyHOME integration."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr

from .const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    CONF_ENTITY,
    DOMAIN,
)

if TYPE_CHECKING:
    from .gateway import MyHOMEGatewayHandler

_LOGGER = logging.getLogger(__name__)

SERVICE_SYNC_TIME = "sync_time"
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_SWEEP_BUS = "sweep_bus"


def _get_gateway_handler(hass: HomeAssistant, gateway_identifier: str | None) -> MyHOMEGatewayHandler | None:
    """Retrieve the MyHOMEGatewayHandler for a given gateway MAC or default."""
    if DOMAIN not in hass.data:
        return None

    if gateway_identifier is None:
        _gw_keys = [k for k in hass.data[DOMAIN] if isinstance(k, str) and ":" in k and CONF_ENTITY in hass.data[DOMAIN][k]]
        if not _gw_keys:
            return None
        return hass.data[DOMAIN][_gw_keys[0]][CONF_ENTITY]

    # Check direct match
    if gateway_identifier in hass.data[DOMAIN] and CONF_ENTITY in hass.data[DOMAIN][gateway_identifier]:
        return hass.data[DOMAIN][gateway_identifier][CONF_ENTITY]

    mac = dr.format_mac(gateway_identifier)
    if mac is not None:
        if mac in hass.data[DOMAIN] and CONF_ENTITY in hass.data[DOMAIN][mac]:
            return hass.data[DOMAIN][mac][CONF_ENTITY]
        for k in hass.data[DOMAIN]:
            if isinstance(k, str) and k.lower() == mac.lower() and CONF_ENTITY in hass.data[DOMAIN][k]:
                return hass.data[DOMAIN][k][CONF_ENTITY]

    return None


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register MyHOME domain services."""
    if hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME):
        return

    async def handle_sync_time(call: ServiceCall) -> bool:
        """Handle time synchronization service call."""
        gateway = call.data.get(ATTR_GATEWAY, None)
        if gateway is None:
            _gw_keys = [k for k in hass.data[DOMAIN] if isinstance(k, str) and ":" in k]
            if not _gw_keys:
                _LOGGER.error("No MyHOME gateways found, cannot sync time.")
                return False
            gateway = _gw_keys[0]
        else:
            mac = dr.format_mac(gateway)
            if mac is None:
                _LOGGER.error(
                    "Invalid gateway mac `%s`, could not send time synchronisation message.",
                    gateway,
                )
                return False
            gateway = mac

        timezone = hass.config.as_dict().get("time_zone", "UTC")
        if gateway in hass.data.get(DOMAIN, {}) and CONF_ENTITY in hass.data[DOMAIN][gateway]:
            from OWNd.message import OWNGatewayCommand
            await hass.data[DOMAIN][gateway][CONF_ENTITY].send(
                OWNGatewayCommand.set_datetime_to_now(timezone)
            )
            return True

        _LOGGER.error(
            "Gateway `%s` not found, could not send time synchronisation message.",
            gateway,
        )
        return False

    async def handle_send_message(call: ServiceCall) -> bool:
        """Handle sending an arbitrary OpenWebNet message."""
        gateway = call.data.get(ATTR_GATEWAY, None)
        message = call.data.get(ATTR_MESSAGE, None)
        if gateway is None:
            _gw_keys = [k for k in hass.data[DOMAIN] if isinstance(k, str) and ":" in k]
            if not _gw_keys:
                _LOGGER.error("No MyHOME gateways found, cannot send message `%s`.", message)
                return False
            gateway = _gw_keys[0]
        else:
            mac = dr.format_mac(gateway)
            if mac is None:
                _LOGGER.error(
                    "Invalid gateway mac `%s`, could not send message `%s`.",
                    gateway,
                    message,
                )
                return False
            gateway = mac

        _LOGGER.debug("Handling message `%s` to be sent to `%s`", message, gateway)
        if gateway in hass.data.get(DOMAIN, {}) and CONF_ENTITY in hass.data[DOMAIN][gateway]:
            if message is not None:
                from OWNd.message import OWNCommand
                own_message = OWNCommand.parse(message)
                if own_message is not None and own_message.is_valid:
                    _LOGGER.debug(
                        "%s Sending valid OpenWebNet Message: `%s`",
                        hass.data[DOMAIN][gateway][CONF_ENTITY].log_id,
                        own_message,
                    )
                    await hass.data[DOMAIN][gateway][CONF_ENTITY].send(own_message)
                    return True
                _LOGGER.error(
                    "Could not parse message `%s`, not sending it.", message
                )
                return False
            _LOGGER.error("No message specified to send.")
            return False

        _LOGGER.error(
            "Gateway `%s` not found, could not send message `%s`.", gateway, message
        )
        return False

    async def handle_sweep_bus(call: ServiceCall) -> bool:
        """Trigger an active status query sweep across bus subsystems to populate the bus monitor."""
        from OWNd.message import OWNMessage

        gateway = call.data.get(ATTR_GATEWAY, None)
        target_gateways: list[str] = []
        if gateway is not None:
            mac = dr.format_mac(gateway)
            if mac and mac in hass.data.get(DOMAIN, {}) and CONF_ENTITY in hass.data[DOMAIN][mac]:
                target_gateways.append(mac)
            else:
                _LOGGER.error("Gateway `%s` not found for sweep_bus.", gateway)
                return False
        else:
            target_gateways = [
                k
                for k in hass.data.get(DOMAIN, {})
                if isinstance(k, str) and ":" in k and CONF_ENTITY in hass.data[DOMAIN][k]
            ]

        if not target_gateways:
            _LOGGER.warning("No active MyHOME gateways found to sweep.")
            return False

        sweep_queries = [
            "*#13**0##",   # Gateway real-time clock
            "*#13**15##",  # Gateway device model
            "*#13**16##",  # Gateway firmware version
            "*#2*0##",     # All cover actuators
            "*#4*0##",     # Thermoregulation master status
        ]

        for gw_mac in target_gateways:
            handler = hass.data[DOMAIN][gw_mac][CONF_ENTITY]
            _LOGGER.info("Executing diagnostic bus sweep on gateway %s", gw_mac)
            for query in sweep_queries:
                await handler.send(OWNMessage.parse(query))
                await asyncio.sleep(0.05)

        return True

    hass.services.async_register(DOMAIN, SERVICE_SYNC_TIME, handle_sync_time)
    hass.services.async_register(DOMAIN, SERVICE_SEND_MESSAGE, handle_send_message)
    hass.services.async_register(DOMAIN, SERVICE_SWEEP_BUS, handle_sweep_bus)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unregister MyHOME domain services."""
    for service in (SERVICE_SYNC_TIME, SERVICE_SEND_MESSAGE, SERVICE_SWEEP_BUS):
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)
