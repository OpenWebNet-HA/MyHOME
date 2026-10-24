"""Config flow to configure MyHome."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import typing
from typing import Dict, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigSubentryFlow
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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from OWNd.connection import OWNGateway, OWNSession
from OWNd.discovery import find_gateways, get_gateway
from voluptuous import In, Required

from .config_flow_discovery import (
    TEST_CONNECTION_ABORT_REASONS,
    TEST_CONNECTION_RETRY_DELAY,
    ConfigFlowDiscoveryMixin,
    MACAddress,
    _get_serial_ports,
    identify_gateway,
)
from .config_flow_expose import ExposeSubentryFlowHandler
from .config_flow_options import MyhomeOptionsFlowHandler
from .config_flow_topology import ConfigFlowTopologyMixin
from .const import (
    CONF_DEVICE_TYPE,
    CONF_MANUFACTURER,
    DOMAIN,
    LOGGER,
    SUBENTRY_TYPE_EXPOSE,
    SUPPORTED_GATEWAY_MODELS,
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

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return subentries supported by this integration."""
        return {SUBENTRY_TYPE_EXPOSE: ExposeSubentryFlowHandler}

    def __init__(self):  # type: ignore
        """Initialize the MyHome flow."""
        self.gateway_handler: Optional[OWNGateway] = None
        self.discovered_gateways: Optional[Dict[str, dict[str, typing.Any]]] = None
        self._existing_entry: ConfigEntry | None = None
        # Set by the manual step: the MAC and model were typed, not discovered.
        self._identify_after_auth = False
        # (title, data, options) of an entry held back by the bus topology step
        self._pending_entry: (
            tuple[str, dict[str, typing.Any], dict[str, typing.Any]] | None
        ) = None

    async def async_step_user(self, user_input=None):  # type: ignore
        """Handle a flow initialized by the user."""
        if user_input is not None and user_input["serial"] == "00:00:00:00:00:00":
            return await self.async_step_custom()  # type: ignore

        if user_input is not None and user_input["serial"] == "serial_gateway":
            return await self.async_step_serial()  # type: ignore

        if (
            user_input is not None
            and self.discovered_gateways is not None
            and user_input["serial"] in self.discovered_gateways
        ):
            self.gateway_handler = await OWNGateway.build_from_discovery_info(
                self.discovered_gateways[user_input["serial"]]
            )
            assert self.gateway_handler is not None
            await self.async_set_unique_id(
                dr.format_mac(self.gateway_handler.serial),
                raise_on_progress=False,
            )
            self._abort_if_unique_id_configured()
            return await self.async_step_test_connection()

        try:
            async with asyncio.timeout(5):
                local_gateways = await find_gateways(
                    session=async_get_clientsession(self.hass)
                )
        except asyncio.TimeoutError:
            return self.async_abort(reason="discovery_timeout")

        self.discovered_gateways = {
            gateway["serialNumber"]: gateway for gateway in local_gateways
        }

        return self.async_show_form(
            step_id="user",
            data_schema=flow_schema(
                {
                    Required("serial"): In(
                        {
                            **{
                                gateway["serialNumber"]: f"{gateway['modelName']} Gateway ({gateway['address']})"
                                for gateway in local_gateways
                            },
                            "00:00:00:00:00:00": "Custom (IP Gateway)",
                            "serial_gateway": "USB / Serial Gateway (Legrand 3578 / OpenZigBee)",
                        }
                    )
                }
            ),
        )

    async def async_step_serial(self, user_input=None, errors=None):  # type: ignore
        """Handle USB / Serial gateway setup (Legrand 3578 / OpenZigBee)."""
        if errors is None:
            errors = {}

        if user_input is not None:
            port = user_input.get("port", "").strip()
            if not port:
                errors["port"] = "invalid_port"
            else:
                port_hash = hashlib.md5(port.encode()).hexdigest()[:8]
                serial_mac = dr.format_mac(
                    f"35:78:{port_hash[:2]}:{port_hash[2:4]}:{port_hash[4:6]}:{port_hash[6:8]}"
                )

                await self.async_set_unique_id(serial_mac, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                data = {
                    CONF_HOST: port,
                    CONF_PORT: user_input.get("baudrate", 19200),
                    CONF_PASSWORD: None,
                    CONF_MAC: serial_mac,
                    CONF_FRIENDLY_NAME: user_input.get(CONF_FRIENDLY_NAME)
                    or f"Legrand 3578 ({port})",
                    CONF_DEVICE_TYPE: "serial",
                    CONF_MANUFACTURER: "Legrand",
                    CONF_NAME: "Legrand 3578 USB Gateway",
                    "transport_type": "serial",
                    "baudrate": user_input.get("baudrate", 19200),
                }
                return self.async_create_entry(
                    title=data[CONF_FRIENDLY_NAME],
                    data=data,
                )

        available_ports = {}
        try:
            ports = await self.hass.async_add_executor_job(_get_serial_ports)
            for p in ports:
                desc = getattr(p, "description", "")
                device = getattr(p, "device", str(p))
                available_ports[device] = (
                    f"{device} ({desc})" if desc and desc != device else device
                )
        except Exception:
            pass

        port_validator = In(available_ports) if available_ports else cv.string
        schema = flow_schema(
            {
                Required("port"): port_validator,
                Required("baudrate", default=19200): In(
                    [9600, 19200, 38400, 57600, 115200]
                ),
                vol.Optional(
                    CONF_FRIENDLY_NAME, default="Legrand 3578 Gateway"
                ): cv.string,
            }
        )

        return self.async_show_form(
            step_id="serial",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_custom(self, user_input=None, errors=None):  # type: ignore
        """Handle manual gateway setup — auto-discovers MAC from IP when possible."""
        if errors is None:
            errors = {}

        if user_input is not None:
            try:
                user_input["address"] = str(ipaddress.IPv4Address(user_input["address"]))
            except ipaddress.AddressValueError:
                errors["address"] = "invalid_ip"

            if not errors:
                try:
                    async with asyncio.timeout(5):
                        discovered = await get_gateway(
                            user_input["address"],
                            session=async_get_clientsession(self.hass),
                        )
                except Exception as err:  # pylint: disable=broad-except
                    # UPnP lookup is optional: any failure (timeout, HTTP,
                    # malformed XML) falls back to manual MAC entry.
                    LOGGER.debug(
                        "UPnP lookup of %s failed, asking for the MAC: %r",
                        user_input["address"],
                        err,
                    )
                    discovered = None

                if discovered is not None:
                    discovered["password"] = None
                    discovered["port"] = discovered.get("port") or user_input.get("port", 20000)
                    self.gateway_handler = OWNGateway(discovered)
                    await self.async_set_unique_id(
                        dr.format_mac(self.gateway_handler.serial),
                        raise_on_progress=False,
                    )
                    self._abort_if_unique_id_configured()
                    LOGGER.info(
                        "Auto-discovered gateway at %s — serial %s, model %s",
                        user_input["address"],
                        self.gateway_handler.serial,
                        self.gateway_handler.model_name,
                    )
                    return await self.async_step_test_connection()
                LOGGER.warning(
                    "Could not auto-discover gateway at %s, falling back to manual entry",
                    user_input["address"],
                )
                self._custom_address = user_input["address"]
                self._custom_port = user_input.get("port", 20000)
                return await self.async_step_custom_manual()  # type: ignore

        address_suggestion = (
            user_input["address"]
            if user_input is not None and user_input.get("address")
            else "192.168.1.100"
        )
        port_suggestion = (
            user_input["port"]
            if user_input is not None and user_input.get("port")
            else 20000
        )

        return self.async_show_form(
            step_id="custom",
            data_schema=flow_schema(
                {
                    Required("address", description={"suggested_value": address_suggestion}): str,
                    Required("port", description={"suggested_value": port_suggestion}): int,
                }
            ),
            errors=errors,
        )

    async def async_step_custom_manual(self, user_input=None, errors=None):  # type: ignore
        """Fallback manual entry when UPnP auto-discovery fails."""
        if errors is None:
            errors = {}

        if user_input is not None:
            user_input["address"] = getattr(self, "_custom_address", user_input.get("address", ""))
            user_input["port"] = getattr(self, "_custom_port", user_input.get("port", 20000))

            try:
                user_input["address"] = str(ipaddress.IPv4Address(user_input["address"]))
            except ipaddress.AddressValueError:
                errors["address"] = "invalid_ip"

            try:
                user_input["serialNumber"] = dr.format_mac(f'{MACAddress(user_input["serialNumber"])}')
            except ValueError:
                errors["serialNumber"] = "invalid_mac"

            if not errors:
                user_input["ssdp_location"] = None
                user_input["ssdp_st"] = None
                user_input["deviceType"] = None
                user_input["friendlyName"] = None
                user_input["manufacturer"] = "BTicino S.p.A."
                user_input["manufacturerURL"] = "http://www.bticino.it"
                user_input["modelNumber"] = None
                user_input["UDN"] = None
                self.gateway_handler = OWNGateway(user_input)
                # Typed by hand: once authenticated, ask the gateway for its MAC and model.
                # The unique_id is set from the MAC the gateway then reports (or the typed
                # one), so a second gateway entered with the form's placeholder MAC is not
                # refused before it could say who it is.
                self._identify_after_auth = True
                return await self.async_step_test_connection()

        address_val = getattr(self, "_custom_address", "192.168.1.100")
        port_val = getattr(self, "_custom_port", 20000)
        serial_number_suggestion = (
            user_input["serialNumber"]
            if user_input is not None and user_input.get("serialNumber")
            else "00:03:50:00:00:00"
        )
        model_name_suggestion = (
            user_input["modelName"]
            if user_input is not None and user_input.get("modelName")
            else "MyHomeServer1"
        )
        model_options = [m for m in SUPPORTED_GATEWAY_MODELS]
        if model_name_suggestion not in model_options:
            model_options.insert(0, model_name_suggestion)

        return self.async_show_form(
            step_id="custom_manual",
            data_schema=flow_schema(
                {
                    Required("serialNumber", description={"suggested_value": serial_number_suggestion}): str,
                    Required("modelName", default=model_name_suggestion): vol.Any(In(model_options), cv.string),
                }
            ),
            description_placeholders={CONF_HOST: address_val, CONF_PORT: str(port_val)},
            errors=errors,
        )


MyhomeConfigFlow = MyhomeFlowHandler

__all__ = [
    "ConfigFlowDiscoveryMixin",
    "MACAddress",
    "MyhomeConfigFlow",
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
    "identify_gateway",
]
