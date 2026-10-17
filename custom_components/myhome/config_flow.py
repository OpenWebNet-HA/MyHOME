"""Config flow to configure MyHome."""
from __future__ import annotations

import asyncio
import ipaddress
import typing
from typing import Dict, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from OWNd.connection import OWNGateway, OWNSession
from OWNd.discovery import find_gateways, get_gateway
from voluptuous import All, Coerce, In, Range, Required

from .config_flow_discovery import (
    TEST_CONNECTION_ABORT_REASONS,
    TEST_CONNECTION_RETRY_DELAY,
    ConfigFlowDiscoveryMixin,
    MACAddress,
    _get_serial_ports,
)
from .config_flow_options import MyhomeOptionsFlowHandler
from .config_flow_topology import ConfigFlowTopologyMixin
from .const import (
    CONF_FIRMWARE,
    CONF_UDN,
    DOMAIN,
    LOGGER,
)
from .gateway import MyHOMEGatewayHandler, command_session_default
from .typing_compat import flow_schema


class MyhomeFlowHandler(
    ConfigFlowDiscoveryMixin, ConfigFlowTopologyMixin, ConfigFlow, domain=DOMAIN
):
    """Handle a MyHome config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):  # type: ignore
        """Get the options flow for this handler."""
        return MyhomeOptionsFlowHandler(config_entry)

    def __init__(self):  # type: ignore
        """Initialize the MyHome flow."""
        self.gateway_handler: Optional[OWNGateway] = None
        self.discovered_gateways: Optional[Dict[str, dict[str, typing.Any]]] = None
        self._existing_entry: ConfigEntry | None = None
        # (title, data, options) of an entry held back by the bus topology step
        self._pending_entry: (
            tuple[str, dict[str, typing.Any], dict[str, typing.Any]] | None
        ) = None

    async def async_step_ssdp(self, discovery_info):  # type: ignore
        """Handle a discovered OpenWebNet gateway.

        This flow is triggered by the SSDP component. It will check if the
        gateway is already configured and if not, it will ask for the connection port
        if it has not been discovered on its own, and test the connection.
        """
        _discovery_info = discovery_info.upnp
        _discovery_info["ssdp_st"] = discovery_info.ssdp_st
        _discovery_info["ssdp_location"] = discovery_info.ssdp_location
        _discovery_info["address"] = discovery_info.ssdp_headers["_host"]
        _discovery_info["port"] = 20000

        gateway = await OWNGateway.build_from_discovery_info(_discovery_info)
        if gateway is None:
            return self.async_abort(reason="unknown")
        if not gateway.unique_id or not gateway.serial:
            return self.async_abort(reason="no_serial")
        await self.async_set_unique_id(dr.format_mac(gateway.unique_id))
        LOGGER.info("Found gateway: %s", gateway.address)
        updatable = {
            CONF_HOST: gateway.address,
            CONF_FRIENDLY_NAME: gateway.friendly_name,
            CONF_UDN: gateway.udn,
            CONF_FIRMWARE: gateway.firmware,
        }

        self._abort_if_unique_id_configured(updates=updatable)

        self.gateway_handler = gateway
        self.context.update(
            {  # type: ignore
                CONF_HOST: gateway.address,
                CONF_NAME: gateway.model_name,
                CONF_MAC: gateway.serial,
                "title_placeholders": {
                    CONF_HOST: str(gateway.address or ""),
                    CONF_NAME: str(gateway.model_name or ""),
                    CONF_MAC: str(gateway.serial or ""),
                },
            }
        )

        return await self.async_step_discovery_confirm()  # type: ignore

    async def async_step_discovery_confirm(self, user_input=None):  # type: ignore
        """Handle user confirmation of discovered gateway."""
        if user_input is not None:
            if self.gateway_handler.port is None:  # type: ignore
                return await self.async_step_port()  # type: ignore
            return await self.async_step_test_connection()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm",
            description_placeholders={
                CONF_HOST: self.gateway_handler.address,  # type: ignore
                CONF_NAME: self.gateway_handler.model_name or "MyHOME Gateway",  # type: ignore
            },
        )

    async def async_step_reconfigure(self, user_input=None):  # type: ignore
        """Handle reconfiguration of the gateway connection."""
        errors = {}
        try:
            entry = (
                self._get_reconfigure_entry()
                if hasattr(self, "_get_reconfigure_entry")
                else self.hass.config_entries.async_get_entry(
                    self.context.get("entry_id")
                )
            )
        except Exception:
            entry = None

        if entry is None:
            return self.async_abort(reason="unknown")

        is_serial = entry.data.get("transport_type") == "serial"

        if user_input is not None:
            if is_serial:
                port = str(user_input.get("port", "")).strip()
                if not port:
                    errors["port"] = "invalid_port"
                else:
                    new_data = {**entry.data}
                    new_data[CONF_HOST] = port
                    new_data["port"] = port
                    new_data["baudrate"] = user_input.get("baudrate", 19200)
                    new_data[CONF_PORT] = new_data["baudrate"]
                    return self.async_update_reload_and_abort(
                        entry,
                        data=new_data,
                        reason="reconfigure_successful",
                    )
            else:
                address = str(user_input.get(CONF_HOST, "")).strip()
                try:
                    address = str(ipaddress.IPv4Address(address))
                except ipaddress.AddressValueError:
                    errors[CONF_HOST] = "invalid_ip"

                port = user_input.get(CONF_PORT, 20000)
                try:
                    port = int(port)  # type: ignore
                    if not (1 <= port <= 65535):
                        errors[CONF_PORT] = "invalid_port"
                except (ValueError, TypeError):
                    errors[CONF_PORT] = "invalid_port"

                if not errors:
                    new_data = {**entry.data}
                    new_data[CONF_HOST] = address
                    new_data[CONF_PORT] = port
                    if CONF_PASSWORD in user_input:
                        new_data[CONF_PASSWORD] = user_input.get(CONF_PASSWORD) or None
                    return self.async_update_reload_and_abort(
                        entry,
                        data=new_data,
                        reason="reconfigure_successful",
                    )

        if is_serial:
            schema = flow_schema(
                {
                    Required("port", default=entry.data.get(CONF_HOST, "")): cv.string,
                    Required("baudrate", default=entry.data.get("baudrate", 19200)): In(
                        [9600, 19200, 38400, 57600, 115200]
                    ),
                }
            )
        else:
            schema = flow_schema(
                {
                    Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                    Required(CONF_PORT, default=entry.data.get(CONF_PORT, 20000)): All(
                        Coerce(int), Range(min=1, max=65535)
                    ),
                    vol.Optional(
                        CONF_PASSWORD,
                        description={
                            "suggested_value": entry.data.get(CONF_PASSWORD) or ""
                        },
                    ): str,
                }
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                CONF_NAME: entry.data.get(CONF_NAME, "Gateway"),
            },
        )


__all__ = [
    "ConfigFlowDiscoveryMixin",
    "MACAddress",
    "MyhomeFlowHandler",
    "MyhomeOptionsFlowHandler",
    "MyHOMEGatewayHandler",
    "OWNGateway",
    "OWNSession",
    "TEST_CONNECTION_ABORT_REASONS",
    "TEST_CONNECTION_RETRY_DELAY",
    "_get_serial_ports",
    "asyncio",
    "command_session_default",
    "find_gateways",
    "get_gateway",
]
