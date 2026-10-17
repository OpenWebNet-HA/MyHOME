"""Discovery, manual setup, connection test, and authentication steps for MyHome config flow (#732)."""
from __future__ import annotations

import asyncio
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
from voluptuous import Coerce, In, Required

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
    SUPPORTED_GATEWAY_MODELS,
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
    """User, serial, custom IP, connection test, port, password, and reauth config flow steps."""

    hass: typing.Any
    gateway_handler: typing.Any
    discovered_gateways: dict[str, dict[str, typing.Any]] | None
    _existing_entry: typing.Any
    _pending_entry: tuple[str, dict[str, typing.Any], dict[str, typing.Any]] | None
    _custom_address: str
    _custom_port: int

    async def async_step_user(self, user_input=None):  # type: ignore
        """Handle a flow initialized by the user."""
        from . import config_flow as _cf

        # Check if user chooses manual entry or serial entry
        if user_input is not None and user_input["serial"] == "00:00:00:00:00:00":
            return await self.async_step_custom()  # type: ignore

        if user_input is not None and user_input["serial"] == "serial_gateway":
            return await self.async_step_serial()  # type: ignore

        if (
            user_input is not None
            and self.discovered_gateways is not None
            and user_input["serial"] in self.discovered_gateways
        ):
            self.gateway_handler = await _cf.OWNGateway.build_from_discovery_info(
                self.discovered_gateways[user_input["serial"]]
            )
            assert self.gateway_handler is not None
            await self.async_set_unique_id(  # type: ignore[attr-defined]
                dr.format_mac(self.gateway_handler.serial),
                raise_on_progress=False,
            )
            self._abort_if_unique_id_configured()  # type: ignore[attr-defined]
            # We pass user input to link so it will attempt to link right away
            return await self.async_step_test_connection()

        try:
            async with asyncio.timeout(5):
                local_gateways = await _cf.find_gateways()
        except asyncio.TimeoutError:
            return self.async_abort(reason="discovery_timeout")  # type: ignore[attr-defined]

        self.discovered_gateways = {
            gateway["serialNumber"]: gateway for gateway in local_gateways
        }

        return self.async_show_form(  # type: ignore[attr-defined]
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
        from . import config_flow as _cf

        if errors is None:
            errors = {}

        if user_input is not None:
            port = user_input.get("port", "").strip()
            if not port:
                errors["port"] = "invalid_port"
            else:
                import hashlib

                port_hash = hashlib.md5(port.encode()).hexdigest()[:8]
                serial_mac = dr.format_mac(
                    f"35:78:{port_hash[:2]}:{port_hash[2:4]}:{port_hash[4:6]}:{port_hash[6:8]}"
                )

                await self.async_set_unique_id(serial_mac, raise_on_progress=False)  # type: ignore[attr-defined]
                self._abort_if_unique_id_configured()  # type: ignore[attr-defined]

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
                return self.async_create_entry(  # type: ignore[attr-defined]
                    title=data[CONF_FRIENDLY_NAME],
                    data=data,
                )

        available_ports = {}
        try:
            ports = await self.hass.async_add_executor_job(_cf._get_serial_ports)
            for p in ports:
                desc = getattr(p, "description", "")
                device = getattr(p, "device", str(p))
                available_ports[device] = (
                    f"{device} ({desc})" if desc and desc != device else device
                )
        except Exception:
            pass

        if available_ports:
            schema = flow_schema(
                {
                    Required("port"): In(available_ports),
                    Required("baudrate", default=19200): In(
                        [9600, 19200, 38400, 57600, 115200]
                    ),
                    vol.Optional(
                        CONF_FRIENDLY_NAME, default="Legrand 3578 Gateway"
                    ): cv.string,
                }
            )
        else:
            schema = flow_schema(
                {
                    Required("port"): cv.string,
                    Required("baudrate", default=19200): In(
                        [9600, 19200, 38400, 57600, 115200]
                    ),
                    vol.Optional(
                        CONF_FRIENDLY_NAME, default="Legrand 3578 Gateway"
                    ): cv.string,
                }
            )

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="serial",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_custom(self, user_input=None, errors=None):  # type: ignore
        """Handle manual gateway setup — auto-discovers MAC from IP when possible."""
        from . import config_flow as _cf

        if errors is None:
            errors = {}

        if user_input is not None:
            try:
                user_input["address"] = str(
                    ipaddress.IPv4Address(user_input["address"])
                )
            except ipaddress.AddressValueError:
                errors["address"] = "invalid_ip"

            if not errors:
                try:
                    async with asyncio.timeout(5):
                        discovered = await _cf.get_gateway(user_input["address"])
                except (asyncio.TimeoutError, Exception):  # pylint: disable=broad-except
                    discovered = None

                if discovered is not None:
                    discovered["password"] = None
                    discovered["port"] = discovered.get("port") or user_input.get(
                        "port", 20000
                    )
                    self.gateway_handler = _cf.OWNGateway(discovered)
                    await self.async_set_unique_id(  # type: ignore[attr-defined]
                        dr.format_mac(self.gateway_handler.serial),
                        raise_on_progress=False,
                    )
                    self._abort_if_unique_id_configured()  # type: ignore[attr-defined]
                    LOGGER.info(
                        "Auto-discovered gateway at %s — serial %s, model %s",
                        user_input["address"],
                        self.gateway_handler.serial,
                        self.gateway_handler.model_name,
                    )
                    return await self.async_step_test_connection()
                else:
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

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="custom",
            data_schema=flow_schema(
                {
                    Required(
                        "address", description={"suggested_value": address_suggestion}
                    ): str,
                    Required(
                        "port", description={"suggested_value": port_suggestion}
                    ): int,
                }
            ),
            errors=errors,
        )

    async def async_step_custom_manual(self, user_input=None, errors=None):  # type: ignore
        """Fallback manual entry when UPnP auto-discovery fails."""
        from . import config_flow as _cf

        if errors is None:
            errors = {}

        if user_input is not None:
            user_input["address"] = getattr(
                self, "_custom_address", user_input.get("address", "")
            )
            user_input["port"] = getattr(
                self, "_custom_port", user_input.get("port", 20000)
            )

            try:
                user_input["address"] = str(
                    ipaddress.IPv4Address(user_input["address"])
                )
            except ipaddress.AddressValueError:
                errors["address"] = "invalid_ip"

            try:
                user_input["serialNumber"] = dr.format_mac(
                    f'{_cf.MACAddress(user_input["serialNumber"])}'
                )
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
                self.gateway_handler = _cf.OWNGateway(user_input)
                await self.async_set_unique_id(  # type: ignore[attr-defined]
                    user_input["serialNumber"], raise_on_progress=False
                )
                self._abort_if_unique_id_configured()  # type: ignore[attr-defined]
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

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="custom_manual",
            data_schema=flow_schema(
                {
                    Required(
                        "serialNumber",
                        description={"suggested_value": serial_number_suggestion},
                    ): str,
                    Required(
                        "modelName",
                        default=model_name_suggestion,
                    ): vol.Any(In(model_options), cv.string),
                }
            ),
            description_placeholders={
                CONF_HOST: address_val,
                CONF_PORT: str(port_val),
            },
            errors=errors,
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

