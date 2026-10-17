"""SSDP discovery, reconfigure, reauth, connection test, and authentication steps for MyHome config flow (#732)."""
from __future__ import annotations

import ipaddress
import re
import typing

import voluptuous as vol
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_ID,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from voluptuous import All, Coerce, In, Range, Required

from .const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_OWN_PASSWORD,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    CONF_WORKER_COUNT,
    DOMAIN,
    LOGGER,
)
from .gateway import command_session_default
from .typing_compat import flow_schema

TEST_CONNECTION_ABORT_REASONS = frozenset(
    {
        "cannot_connect",
        "connection_closed",
        "connection_error",
        "connection_refused",
        "negotiation_error",
        "negotiation_failed",
        "negotiation_refused",
        "negotiation_timeout",
    }
)
TEST_CONNECTION_RETRY_DELAY: float = 1.5


class MACAddress:
    """Validate and format a 12-hex-digit MAC address."""

    def __init__(self, mac: str):
        mac = re.sub("[.:-]", "", mac).upper()
        mac = "".join(mac.split())
        if len(mac) != 12 or not mac.isalnum() or re.search("[G-Z]", mac) is not None:
            raise ValueError("Invalid MAC address")
        self.mac = mac

    def __repr__(self) -> str:
        return ":".join(["%s" % (self.mac[i : i + 2]) for i in range(0, 12, 2)])

    def __str__(self) -> str:
        return ":".join(["%s" % (self.mac[i : i + 2]) for i in range(0, 12, 2)])


def _get_serial_ports() -> list:  # type: ignore
    """Enumerate serial ports safely without hard dependency on pyserial."""
    try:
        import serial.tools.list_ports  # type: ignore

        return list(serial.tools.list_ports.comports())
    except Exception:
        return []


class ConfigFlowDiscoveryMixin:
    """SSDP discovery, reconfigure, reauth, connection test, port, and password steps."""

    hass: typing.Any
    gateway_handler: typing.Any
    discovered_gateways: dict[str, dict[str, typing.Any]] | None
    _existing_entry: typing.Any
    _pending_entry: tuple[str, dict[str, typing.Any], dict[str, typing.Any]] | None
    _custom_address: str
    _custom_port: int

    async def async_step_ssdp(self, discovery_info):  # type: ignore
        """Handle a discovered OpenWebNet gateway.

        This flow is triggered by the SSDP component. It will check if the
        gateway is already configured and if not, it will ask for the connection port
        if it has not been discovered on its own, and test the connection.
        """
        from . import config_flow as _cf

        _discovery_info = discovery_info.upnp
        _discovery_info["ssdp_st"] = discovery_info.ssdp_st
        _discovery_info["ssdp_location"] = discovery_info.ssdp_location
        _discovery_info["address"] = discovery_info.ssdp_headers["_host"]
        _discovery_info["port"] = 20000

        gateway = await _cf.OWNGateway.build_from_discovery_info(_discovery_info)
        if gateway is None:
            return self.async_abort(reason="unknown")  # type: ignore[attr-defined]
        if not gateway.unique_id or not gateway.serial:
            return self.async_abort(reason="no_serial")  # type: ignore[attr-defined]
        await self.async_set_unique_id(dr.format_mac(gateway.unique_id))  # type: ignore[attr-defined]
        LOGGER.info("Found gateway: %s", gateway.address)
        updatable = {
            CONF_HOST: gateway.address,
            CONF_FRIENDLY_NAME: gateway.friendly_name,
            CONF_UDN: gateway.udn,
            CONF_FIRMWARE: gateway.firmware,
        }

        self._abort_if_unique_id_configured(updates=updatable)  # type: ignore[attr-defined]

        self.gateway_handler = gateway
        self.context.update(  # type: ignore[attr-defined]
            {
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
            if self.gateway_handler.port is None:
                return await self.async_step_port()  # type: ignore
            return await self.async_step_test_connection()

        self._set_confirm_only()  # type: ignore[attr-defined]
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="discovery_confirm",
            description_placeholders={
                CONF_HOST: self.gateway_handler.address,
                CONF_NAME: self.gateway_handler.model_name or "MyHOME Gateway",
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
                    self.context.get("entry_id")  # type: ignore[attr-defined]
                )
            )
        except Exception:
            entry = None

        if entry is None:
            return self.async_abort(reason="unknown")  # type: ignore[attr-defined]

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
                    return self.async_update_reload_and_abort(  # type: ignore[attr-defined]
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
                    return self.async_update_reload_and_abort(  # type: ignore[attr-defined]
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

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                CONF_NAME: entry.data.get(CONF_NAME, "Gateway"),
            },
        )

    async def async_step_reauth(self, config: dict = None):  # type: ignore
        """Perform reauth upon an authentication error."""
        from . import config_flow as _cf

        entry = self.hass.config_entries.async_get_entry(
            self.context.get("entry_id")  # type: ignore[attr-defined]
        )
        if entry is None and config and CONF_MAC in config:
            entry = self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, config[CONF_MAC]
            )
        self._existing_entry = entry

        mac = entry.unique_id if entry else (config.get(CONF_MAC) if config else None)
        if mac:
            await self.async_set_unique_id(mac)  # type: ignore[attr-defined]

        if self._existing_entry:
            self.gateway_handler = _cf.MyHOMEGatewayHandler(
                hass=self.hass, config_entry=self._existing_entry
            ).gateway
        elif config:
            self.gateway_handler = _cf.OWNGateway(config)

        model_val = getattr(self.gateway_handler, "model_name", None) or getattr(
            self.gateway_handler, "model", "Gateway"
        )
        self.context.update(  # type: ignore[attr-defined]
            {
                CONF_HOST: self.gateway_handler.host,
                CONF_NAME: model_val,
                CONF_MAC: self.gateway_handler.serial,
                "title_placeholders": {
                    CONF_HOST: self.gateway_handler.host,
                    CONF_NAME: model_val,
                    CONF_MAC: self.gateway_handler.serial,
                },
            }
        )

        return await self.async_step_password(
            errors={CONF_OWN_PASSWORD: "password_error"}
        )  # type: ignore

    async def async_step_test_connection(  # pylint: disable=unused-argument
        self, user_input: typing.Any = None, errors: typing.Any = None
    ) -> typing.Any:
        """Testing connection to the OWN Gateway."""
        from . import config_flow as _cf

        if errors is None:
            errors = {}

        gateway = self.gateway_handler
        assert gateway is not None

        self.context.update(  # type: ignore[attr-defined]
            {
                CONF_HOST: gateway.host,
                CONF_NAME: gateway.model_name,
                CONF_MAC: gateway.serial,
                "title_placeholders": {
                    CONF_HOST: str(gateway.host or ""),
                    CONF_NAME: str(gateway.model_name or ""),
                    CONF_MAC: str(gateway.serial or ""),
                },
            }
        )

        async def _run_test_connection() -> dict[str, typing.Any]:
            try:
                session = _cf.OWNSession(gateway=gateway, logger=LOGGER)
                res: typing.Any = await session.test_connection()
                if isinstance(res, dict):
                    return res
                return {"Success": False, "Message": "cannot_connect"}
            except (OSError, TimeoutError, ConnectionError) as exc:
                LOGGER.warning(
                    "Gateway %s (%s:%s) test connection encountered a communication error: %s",
                    gateway.model_name,
                    gateway.address,
                    gateway.port,
                    exc,
                )
                return {"Success": False, "Message": "connection_error"}
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.exception(
                    "Gateway %s (%s:%s) test connection encountered an unexpected error: %s",
                    gateway.model_name,
                    gateway.address,
                    gateway.port,
                    exc,
                )
                return {"Success": False, "Message": "cannot_connect"}

        test_result = await _run_test_connection()

        if (
            not test_result.get("Success")
            and test_result.get("Message") == "connection_closed"
        ):
            LOGGER.warning(
                "Gateway %s (%s:%s) test connection closed by gateway; retrying once after %ss pause",
                gateway.model_name,
                gateway.address,
                gateway.port,
                _cf.TEST_CONNECTION_RETRY_DELAY,
            )
            await _cf.asyncio.sleep(_cf.TEST_CONNECTION_RETRY_DELAY)
            test_result = await _run_test_connection()

        if test_result.get("Success"):
            if self._existing_entry:
                new_data = dict(self._existing_entry.data)
                new_data[CONF_PASSWORD] = gateway.password
                return self.async_update_reload_and_abort(  # type: ignore[attr-defined]
                    self._existing_entry,
                    data=new_data,
                    reason="reauth_successful",
                )

            _new_entry_data = {
                CONF_ID: dr.format_mac(gateway.serial),
                CONF_HOST: gateway.address,
                CONF_PORT: gateway.port,
                CONF_PASSWORD: gateway.password,
                CONF_SSDP_LOCATION: gateway.ssdp_location,
                CONF_SSDP_ST: gateway.ssdp_st,
                CONF_DEVICE_TYPE: gateway.device_type,
                CONF_FRIENDLY_NAME: gateway.friendly_name,
                CONF_MANUFACTURER: gateway.manufacturer,
                CONF_MANUFACTURER_URL: gateway.manufacturer_url,
                CONF_NAME: gateway.model_name,
                CONF_FIRMWARE: gateway.model_number,
                CONF_MAC: dr.format_mac(gateway.serial),
                CONF_UDN: gateway.udn,
            }
            _new_entry_options = {
                CONF_WORKER_COUNT: command_session_default(gateway.model_name),
            }

            if self._bus_primaries():  # type: ignore[attr-defined]
                self._pending_entry = (
                    f"{gateway.model_name} Gateway",
                    _new_entry_data,
                    _new_entry_options,
                )
                return await self.async_step_bus_topology()  # type: ignore[attr-defined]

            return self.async_create_entry(  # type: ignore[attr-defined]
                title=f"{gateway.model_name} Gateway",
                data=_new_entry_data,
                options=_new_entry_options,
            )
        else:
            msg = test_result.get("Message")
            LOGGER.warning(
                "Gateway %s (%s:%s) test connection failed: %s",
                gateway.model_name,
                gateway.address,
                gateway.port,
                msg,
            )
            if msg == "password_required":
                return await self.async_step_password()  # type: ignore
            elif msg in ("password_error", "password_retry"):
                errors["password"] = msg
                return await self.async_step_password(errors=errors)  # type: ignore
            else:
                abort_reason = (
                    msg
                    if msg in _cf.TEST_CONNECTION_ABORT_REASONS
                    else "cannot_connect"
                )
                return self.async_abort(reason=abort_reason)  # type: ignore[attr-defined]

    async def async_step_port(self, user_input=None, errors=None):  # type: ignore
        """Port information for the gateway is missing."""
        if errors is None:
            errors = {}

        if user_input is not None:
            if 1 <= int(user_input[CONF_PORT]) <= 65535:
                self.gateway_handler.port = int(user_input[CONF_PORT])
                return await self.async_step_test_connection()
            errors["port"] = "invalid_port"

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="port",
            data_schema=flow_schema(
                {
                    Required(CONF_PORT, description={"suggested_value": 20000}): int,
                }
            ),
            description_placeholders={
                CONF_HOST: self.context[CONF_HOST],  # type: ignore[attr-defined]
                CONF_NAME: self.context[CONF_NAME],  # type: ignore[attr-defined]
                CONF_MAC: self.context[CONF_MAC],  # type: ignore[attr-defined]
            },
            errors=errors,
        )

    async def async_step_password(self, user_input=None, errors=None):  # type: ignore
        """Password is required to connect the gateway."""
        if errors is None:
            errors = {}

        if user_input is not None:
            self.gateway_handler.password = str(user_input[CONF_OWN_PASSWORD])
            return await self.async_step_test_connection()
        else:
            if self.gateway_handler.password is not None:
                _suggested_password = self.gateway_handler.password
            else:
                _suggested_password = 12345

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="password",
            data_schema=flow_schema(
                {
                    Required(
                        CONF_OWN_PASSWORD,
                        description={"suggested_value": _suggested_password},
                    ): Coerce(str),
                }
            ),
            description_placeholders={
                CONF_HOST: self.context[CONF_HOST],  # type: ignore[attr-defined]
                CONF_NAME: self.context[CONF_NAME],  # type: ignore[attr-defined]
                CONF_MAC: self.context[CONF_MAC],  # type: ignore[attr-defined]
            },
            errors=errors,
        )
