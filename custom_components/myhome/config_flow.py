"""Config flow to configure MyHome."""
import asyncio
import ipaddress
import re
import typing
from typing import Dict, Optional

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_ID,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from OWNd.connection import OWNGateway, OWNSession
from OWNd.discovery import find_gateways, get_gateway
from voluptuous import (
    All,
    Coerce,
    In,
    Range,
    Required,
    Schema,
)

from .const import (
    CONF_ADDRESS,
    CONF_BROADCAST_RESYNC,
    CONF_BUS_TOPOLOGY,
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    CONF_DELEGATED_WHOS,
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_GATEWAY_ROLE,
    CONF_GENERATE_EVENTS,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_OWN_PASSWORD,
    CONF_PRIMARY_GATEWAY,
    CONF_SOURCE_DEFAULT_FIELD,
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_TRANSITION_MODE,
    CONF_UDN,
    CONF_WORKER_COUNT,
    DEFAULT_TRANSITION_MODE,
    DOMAIN,
    IDENTIFICATION_MANUAL,
    LOGGER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    ROLE_STANDBY,
    SUPPORTED_GATEWAY_MODELS,
    TOPOLOGY_SHARED,
    TOPOLOGY_STANDALONE,
)
from .gateway import MyHOMEGatewayHandler, command_session_limit
from .topology import (
    dependents,
    entry_delegated_whos,
    entry_for_mac,
    entry_mac,
    entry_primary_mac,
    entry_role,
    entry_topology,
)


class MACAddress:
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


class MyhomeFlowHandler(ConfigFlow, domain=DOMAIN):
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

    async def async_step_user(self, user_input=None):  # type: ignore
        """Handle a flow initialized by the user."""

        # Check if user chooses manual entry or serial entry
        if user_input is not None and user_input["serial"] == "00:00:00:00:00:00":
            return await self.async_step_custom()  # type: ignore

        if user_input is not None and user_input["serial"] == "serial_gateway":
            return await self.async_step_serial()  # type: ignore

        if user_input is not None and self.discovered_gateways is not None and user_input["serial"] in self.discovered_gateways:
            self.gateway_handler = await OWNGateway.build_from_discovery_info(self.discovered_gateways[user_input["serial"]])
            assert self.gateway_handler is not None
            await self.async_set_unique_id(
                dr.format_mac(self.gateway_handler.serial),
                raise_on_progress=False,
            )
            self._abort_if_unique_id_configured()
            # We pass user input to link so it will attempt to link right away
            return await self.async_step_test_connection()

        try:
            async with asyncio.timeout(5):
                local_gateways = await find_gateways()
        except asyncio.TimeoutError:
            return self.async_abort(reason="discovery_timeout")

        self.discovered_gateways = {gateway["serialNumber"]: gateway for gateway in local_gateways}

        return self.async_show_form(
            step_id="user",
            data_schema=Schema(
                {
                    Required("serial"): In(
                        {
                            **{gateway["serialNumber"]: f"{gateway['modelName']} Gateway ({gateway['address']})" for gateway in local_gateways},
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
                import hashlib
                port_hash = hashlib.md5(port.encode()).hexdigest()[:8]
                serial_mac = dr.format_mac(f"35:78:{port_hash[:2]}:{port_hash[2:4]}:{port_hash[4:6]}:{port_hash[6:8]}")

                await self.async_set_unique_id(serial_mac, raise_on_progress=False)
                self._abort_if_unique_id_configured()

                data = {
                    CONF_HOST: port,
                    CONF_PORT: user_input.get("baudrate", 19200),
                    CONF_PASSWORD: None,
                    CONF_MAC: serial_mac,
                    CONF_FRIENDLY_NAME: user_input.get(CONF_FRIENDLY_NAME) or f"Legrand 3578 ({port})",
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
                available_ports[device] = f"{device} ({desc})" if desc and desc != device else device
        except Exception:
            pass

        if available_ports:
            schema = Schema(
                {
                    Required("port"): In(available_ports),
                    Required("baudrate", default=19200): In([9600, 19200, 38400, 57600, 115200]),
                    vol.Optional(CONF_FRIENDLY_NAME, default="Legrand 3578 Gateway"): cv.string,
                }
            )
        else:
            schema = Schema(
                {
                    Required("port"): cv.string,
                    Required("baudrate", default=19200): In([9600, 19200, 38400, 57600, 115200]),
                    vol.Optional(CONF_FRIENDLY_NAME, default="Legrand 3578 Gateway"): cv.string,
                }
            )

        return self.async_show_form(
            step_id="serial",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_custom(self, user_input=None, errors=None):  # type: ignore
        """Handle manual gateway setup — auto-discovers MAC from IP when possible.

        Step 1: User provides only IP and port.
        We attempt UPnP discovery to resolve serial, model, and other metadata
        automatically.  If discovery succeeds the user never needs to type the MAC.
        If it fails we fall through to async_step_custom_manual.
        """
        if errors is None:
            errors = {}

        if user_input is not None:
            try:
                user_input["address"] = str(ipaddress.IPv4Address(user_input["address"]))
            except ipaddress.AddressValueError:
                errors["address"] = "invalid_ip"

            if not errors:
                # Try UPnP/SSDP auto-discovery to resolve the MAC automatically
                try:
                    async with asyncio.timeout(5):
                        discovered = await get_gateway(user_input["address"])
                except (asyncio.TimeoutError, Exception):  # pylint: disable=broad-except
                    discovered = None

                if discovered is not None:
                    # Discovery succeeded — populate everything from UPnP
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
                else:
                    # Discovery failed — fall back to full manual form
                    LOGGER.warning(
                        "Could not auto-discover gateway at %s, falling back to manual entry",
                        user_input["address"],
                    )
                    self._custom_address = user_input["address"]
                    self._custom_port = user_input.get("port", 20000)
                    return await self.async_step_custom_manual()  # type: ignore

        address_suggestion = user_input["address"] if user_input is not None and user_input.get("address") else "192.168.1.100"
        port_suggestion = user_input["port"] if user_input is not None and user_input.get("port") else 20000

        return self.async_show_form(
            step_id="custom",
            data_schema=Schema(
                {
                    Required("address", description={"suggested_value": address_suggestion}): str,
                    Required("port", description={"suggested_value": port_suggestion}): int,
                }
            ),
            errors=errors,
        )

    async def async_step_custom_manual(self, user_input=None, errors=None):  # type: ignore
        """Fallback manual entry when UPnP auto-discovery fails.

        Shown only when the gateway could not be discovered by IP.
        The address and port are carried over from the previous step.
        """
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
                await self.async_set_unique_id(user_input["serialNumber"], raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return await self.async_step_test_connection()

        address_val = getattr(self, "_custom_address", "192.168.1.100")
        port_val = getattr(self, "_custom_port", 20000)
        serial_number_suggestion = user_input["serialNumber"] if user_input is not None and user_input.get("serialNumber") else "00:03:50:00:00:00"
        model_name_suggestion = user_input["modelName"] if user_input is not None and user_input.get("modelName") else "MyHomeServer1"
        model_options = [m for m in SUPPORTED_GATEWAY_MODELS]
        if model_name_suggestion not in model_options:
            model_options.insert(0, model_name_suggestion)

        return self.async_show_form(
            step_id="custom_manual",
            data_schema=Schema(
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

        entry = self.hass.config_entries.async_get_entry(self.context.get("entry_id"))  # type: ignore
        if entry is None and config and CONF_MAC in config:
            entry = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, config[CONF_MAC])
        self._existing_entry = entry

        mac = entry.unique_id if entry else (config.get(CONF_MAC) if config else None)
        if mac:
            await self.async_set_unique_id(mac)

        if self._existing_entry:
            self.gateway_handler = MyHOMEGatewayHandler(hass=self.hass, config_entry=self._existing_entry).gateway
        elif config:
            self.gateway_handler = OWNGateway(config)

        model_val = getattr(self.gateway_handler, "model_name", None) or getattr(self.gateway_handler, "model", "Gateway")
        self.context.update(
            {  # type: ignore
                CONF_HOST: self.gateway_handler.host,
                CONF_NAME: model_val,
                CONF_MAC: self.gateway_handler.serial,
                "title_placeholders": {
                    CONF_HOST: self.gateway_handler.host,  # type: ignore
                    CONF_NAME: model_val,  # type: ignore
                    CONF_MAC: self.gateway_handler.serial,  # type: ignore
                },
            }
        )

        return await self.async_step_password(errors={CONF_OWN_PASSWORD: "password_error"})  # type: ignore

    async def async_step_test_connection(self, user_input: typing.Any = None, errors: typing.Any = {}) -> typing.Any:  # pylint: disable=unused-argument,dangerous-default-value  # type: ignore
        """Testing connection to the OWN Gateway.

        Given a configured gateway, will attempt to connect and negociate a
        dummy event session to validate all parameters.
        """
        gateway = self.gateway_handler
        assert gateway is not None

        self.context.update(
            {  # type: ignore
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

        test_session = OWNSession(gateway=gateway, logger=LOGGER)
        test_result = await test_session.test_connection()

        if test_result["Success"]:
            if self._existing_entry:
                new_data = dict(self._existing_entry.data)
                new_data[CONF_PASSWORD] = gateway.password
                self.hass.config_entries.async_update_entry(
                    self._existing_entry,
                    data=new_data,
                )
                await self.hass.config_entries.async_reload(self._existing_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

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
                CONF_WORKER_COUNT: 1,
            }

            return self.async_create_entry(
                title=f"{gateway.model_name} Gateway",
                data=_new_entry_data,
                options=_new_entry_options,
            )
        else:
            if test_result["Message"] == "password_required":
                return await self.async_step_password()  # type: ignore
            elif test_result["Message"] == "password_error" or test_result["Message"] == "password_retry":
                errors["password"] = test_result["Message"]
                return await self.async_step_password(errors=errors)  # type: ignore
            else:
                return self.async_abort(reason=test_result["Message"])

    async def async_step_port(self, user_input=None, errors=None):  # type: ignore
        """Port information for the gateway is missing.

        Asking user to provide the port on which the gateway is listening.
        """
        if errors is None:
            errors = {}

        if user_input is not None:
            # Validate user input
            if 1 <= int(user_input[CONF_PORT]) <= 65535:
                self.gateway_handler.port = int(user_input[CONF_PORT])  # type: ignore
                return await self.async_step_test_connection()
            errors["port"] = "invalid_port"

        return self.async_show_form(
            step_id="port",
            data_schema=Schema(
                {
                    Required(CONF_PORT, description={"suggested_value": 20000}): int,
                }
            ),
            description_placeholders={
                CONF_HOST: self.context[CONF_HOST],  # type: ignore
                CONF_NAME: self.context[CONF_NAME],  # type: ignore
                CONF_MAC: self.context[CONF_MAC],  # type: ignore
            },
            errors=errors,
        )

    async def async_step_password(self, user_input=None, errors=None):  # type: ignore
        """Password is required to connect the gateway.

        Asking user to provide the gateway's password.
        """
        if errors is None:
            errors = {}

        if user_input is not None:
            # Validate user input
            self.gateway_handler.password = str(user_input[CONF_OWN_PASSWORD])  # type: ignore
            return await self.async_step_test_connection()
        else:
            if self.gateway_handler.password is not None:  # type: ignore
                _suggested_password = self.gateway_handler.password  # type: ignore
            else:
                _suggested_password = 12345

        return self.async_show_form(
            step_id="password",
            data_schema=Schema(
                {
                    Required(
                        CONF_OWN_PASSWORD,
                        description={"suggested_value": _suggested_password},
                    ): Coerce(str),
                }
            ),
            description_placeholders={
                CONF_HOST: self.context[CONF_HOST],  # type: ignore
                CONF_NAME: self.context[CONF_NAME],  # type: ignore
                CONF_MAC: self.context[CONF_MAC],  # type: ignore
            },
            errors=errors,
        )

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
        await self.async_set_unique_id(dr.format_mac(gateway.unique_id))
        LOGGER.info("Found gateway: %s", gateway.address)
        updatable = {
            CONF_HOST: gateway.address,
            CONF_NAME: gateway.model_name,
            CONF_FRIENDLY_NAME: gateway.friendly_name,
            CONF_UDN: gateway.udn,
            CONF_FIRMWARE: gateway.firmware,
        }
        if gateway.port is not None:
            updatable[CONF_PORT] = gateway.port

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
                else self.hass.config_entries.async_get_entry(self.context.get("entry_id"))  # type: ignore
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
            schema = Schema(
                {
                    Required("port", default=entry.data.get(CONF_HOST, "")): cv.string,
                    Required("baudrate", default=entry.data.get("baudrate", 19200)): In(
                        [9600, 19200, 38400, 57600, 115200]
                    ),
                }
            )
        else:
            schema = Schema(
                {
                    Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                    Required(CONF_PORT, default=entry.data.get(CONF_PORT, 20000)): All(
                        Coerce(int), Range(min=1, max=65535)
                    ),
                    vol.Optional(
                        CONF_PASSWORD,
                        description={"suggested_value": entry.data.get(CONF_PASSWORD) or ""},
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




class MyhomeOptionsFlowHandler(OptionsFlow):
    """Handle MyHome options (general settings + decoder mapping)."""

    def __init__(self, config_entry: ConfigEntry = None):  # type: ignore
        """Initialize MyHome options flow."""
        self._config_entry = config_entry
        self.options = None
        self.data = None

    @property
    def config_entry(self):  # type: ignore
        """Return the config entry for this options flow."""
        if self._config_entry is not None:
            return self._config_entry
        if hasattr(self, "handler") and self.hass:  # type: ignore
            return self.hass.config_entries.async_get_entry(self.handler)
        return None

    async def async_step_init(self, user_input: typing.Any = None) -> typing.Any:  # pylint: disable=unused-argument  # type: ignore
        """Manage the MyHome options."""
        self.options = dict(self.config_entry.options)  # type: ignore
        self.data = dict(self.config_entry.data)  # type: ignore
        if CONF_WORKER_COUNT not in self.options:  # type: ignore
            self.options[CONF_WORKER_COUNT] = 1  # type: ignore
        if CONF_GENERATE_EVENTS not in self.options:  # type: ignore
            self.options[CONF_GENERATE_EVENTS] = False  # type: ignore
        if CONF_BROADCAST_RESYNC not in self.options:  # type: ignore
            self.options[CONF_BROADCAST_RESYNC] = True  # type: ignore
        if CONF_TRANSITION_MODE not in self.options:  # type: ignore
            self.options[CONF_TRANSITION_MODE] = DEFAULT_TRANSITION_MODE  # type: ignore
        return await self.async_step_user()  # type: ignore

    def _audio_environments(self) -> list[str]:
        """Return the environments that have audio zones, from the registry.

        Amplifier addresses are ``EA`` (environment, amplifier), and the F441M
        routes per environment, so defaults are offered per environment rather
        than per zone: two amplifiers in one room physically cannot sit on
        different inputs.  Environment 0 is left out: its routing address would
        be ``10S``, which is the source device itself, so it cannot be routed.
        So is any zone that is not a two-digit amplifier address.
        """
        from homeassistant.helpers import entity_registry as er

        environments: set[str] = set()
        try:
            registry = er.async_get(self.hass)
            entries = er.async_entries_for_config_entry(
                registry, self.config_entry.entry_id
            )
        except Exception:  # pylint: disable=broad-except
            return []
        for entry in entries:
            if entry.domain != "media_player" or "#16" not in (entry.unique_id or ""):
                continue
            zone = (entry.unique_id or "").rsplit("-", 1)[-1].split("#")[0]
            # Only two-digit amplifiers (01-99) have an environment digit
            if len(zone) == 2 and zone.isdigit():
                environments.add(zone[0])
        environments.discard("0")
        return sorted(environments)

    def _apply_topology(self, user_input: dict[str, typing.Any], errors: dict[str, str]) -> None:
        """Validate the shared-bus settings (#453) and store them in the options."""
        in_topo = user_input.get(CONF_BUS_TOPOLOGY, self.options.get(CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE))  # type: ignore
        in_role = user_input.get(CONF_GATEWAY_ROLE, self.options.get(CONF_GATEWAY_ROLE, ROLE_PRIMARY))  # type: ignore
        in_pri = user_input.get(CONF_PRIMARY_GATEWAY, self.options.get(CONF_PRIMARY_GATEWAY))  # type: ignore
        my_mac = entry_mac(self.config_entry)
        shared = in_topo == TOPOLOGY_SHARED
        follower = shared and in_role in (ROLE_SECONDARY, ROLE_STANDBY)

        if follower:
            norm_pri = dr.format_mac(str(in_pri)) if in_pri else None
            target = entry_for_mac(self.hass, norm_pri) if norm_pri and norm_pri != my_mac else None
            if not norm_pri:
                errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_required"
            elif norm_pri == my_mac:
                errors[CONF_PRIMARY_GATEWAY] = "invalid_primary_gateway"
            elif target is None:
                errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_not_found"
            elif entry_primary_mac(target) == my_mac:
                errors[CONF_PRIMARY_GATEWAY] = "circular_gateway_reference"
            elif entry_topology(target) != TOPOLOGY_SHARED or entry_role(target) != ROLE_PRIMARY:
                # Otherwise the primary does not know it shares its bus and flags its own standby
                errors[CONF_PRIMARY_GATEWAY] = "primary_gateway_not_shared_primary"

            if norm_pri and in_role == ROLE_STANDBY and CONF_PRIMARY_GATEWAY not in errors:
                for other in dependents(self.hass, norm_pri):
                    if getattr(self.config_entry, "entry_id", None) == other.entry_id:
                        continue
                    if entry_role(other) == ROLE_STANDBY:
                        errors[CONF_GATEWAY_ROLE] = "multiple_standbys"
                        break

            if in_role == ROLE_SECONDARY and CONF_PRIMARY_GATEWAY not in errors:
                delegated = [int(w) for w in user_input.get(CONF_DELEGATED_WHOS, [])]

                my_model = self.data.get(CONF_NAME)
                if my_model:
                    from OWNd.profiles import get_gateway_profile
                    profile = get_gateway_profile(my_model)
                    supports = getattr(profile, "supports_who", None)
                    if callable(supports):
                        for w in delegated:
                            if not supports(int(w)):
                                errors[CONF_DELEGATED_WHOS] = "who_not_supported_by_gateway"
                                break

                if norm_pri and CONF_DELEGATED_WHOS not in errors:
                    for other in dependents(self.hass, norm_pri):
                        if getattr(self.config_entry, "entry_id", None) == other.entry_id:
                            continue
                        if entry_role(other) == ROLE_SECONDARY:
                            if set(delegated) & entry_delegated_whos(other):
                                errors[CONF_DELEGATED_WHOS] = "overlapping_delegated_whos"
                                break

        if not (shared and in_role == ROLE_PRIMARY) and my_mac and dependents(self.hass, my_mac):
            errors[CONF_GATEWAY_ROLE] = "gateway_has_dependents"
        if errors:
            return

        self.options[CONF_BUS_TOPOLOGY] = TOPOLOGY_SHARED if shared else TOPOLOGY_STANDALONE  # type: ignore
        self.options[CONF_GATEWAY_ROLE] = in_role if shared else ROLE_PRIMARY  # type: ignore
        if follower:
            self.options[CONF_PRIMARY_GATEWAY] = in_pri  # type: ignore
        else:
            self.options.pop(CONF_PRIMARY_GATEWAY, None)  # type: ignore
        if follower and in_role == ROLE_SECONDARY:
            delegated = [int(w) for w in user_input.get(CONF_DELEGATED_WHOS, [])]
            self.options[CONF_DELEGATED_WHOS] = delegated  # type: ignore
        else:
            self.options.pop(CONF_DELEGATED_WHOS, None)  # type: ignore

        if follower and my_mac:
            from .repairs import async_delete_shared_bus_issue
            async_delete_shared_bus_issue(self.hass, my_mac, str(in_pri))

    async def async_step_user(self, user_input=None, errors=None):  # type: ignore
        """Manage general settings and decoder mapping."""

        errors = errors or {}
        limit_model: str | None = None

        if self.options is None:
            self.options = dict(self.config_entry.options) if self.config_entry else {}  # type: ignore
        if self.data is None:
            self.data = dict(self.config_entry.data) if self.config_entry else {}  # type: ignore

        if user_input is not None:
            # ── Validate decoder entity IDs ───────────────────────────────
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(self.hass)

            seen_sources: dict[int, str] = {}
            for i in range(1, CONF_DECODER_SLOTS + 1):
                entity_key = CONF_DECODER_ENTITY.format(i)
                source_key = CONF_DECODER_SOURCE.format(i)
                entity_val = user_input.get(entity_key, "").strip()
                if entity_val:
                    if not entity_val.startswith("media_player."):
                        errors[entity_key] = "not_a_media_player"
                    else:
                        entry = registry.async_get(entity_val)
                        if entry and entry.platform == "mass":
                            # Prevent infinite loops by rejecting MA clones
                            errors[entity_key] = "mass_entity_not_allowed"

                    src_val = int(user_input.get(source_key, i) or i)
                    if src_val in seen_sources:
                        errors[source_key] = "duplicate_decoder_source"
                    else:
                        seen_sources[src_val] = source_key

            limit_model = user_input.get(CONF_NAME, self.data.get(CONF_NAME))  # type: ignore
            session_limit = command_session_limit(limit_model)
            if session_limit is not None and int(user_input[CONF_WORKER_COUNT]) > session_limit:
                errors[CONF_WORKER_COUNT] = "worker_count_above_gateway_limit"

            if not errors:
                self.options.update({CONF_WORKER_COUNT: user_input[CONF_WORKER_COUNT]})  # type: ignore
                self.options.update({CONF_GENERATE_EVENTS: user_input[CONF_GENERATE_EVENTS]})  # type: ignore
                self.options.update({CONF_BROADCAST_RESYNC: user_input.get(CONF_BROADCAST_RESYNC, True)})  # type: ignore
                self.options[CONF_TRANSITION_MODE] = user_input.get(CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE)  # type: ignore

                # Persist the per-environment default source ("" = leave routing alone)
                _defaults: dict[str, int] = {}
                for env in self._audio_environments():
                    raw = user_input.get(CONF_SOURCE_DEFAULT_FIELD.format(env), "")
                    if raw not in ("", None, "none"):
                        _defaults[env] = int(raw)
                self.options[CONF_SOURCE_DEFAULTS] = _defaults  # type: ignore

                # Persist matrix source names (blank = nothing wired to that input)
                for i in range(1, CONF_SOURCE_SLOTS + 1):
                    name_key = CONF_SOURCE_NAME.format(i)
                    self.options[name_key] = str(user_input.get(name_key, "") or "").strip()  # type: ignore

                # Persist decoder slots
                for i in range(1, CONF_DECODER_SLOTS + 1):
                    entity_key = CONF_DECODER_ENTITY.format(i)
                    source_key = CONF_DECODER_SOURCE.format(i)
                    gain_key = CONF_DECODER_PRE_GAIN.format(i)
                    self.options[entity_key] = user_input.get(entity_key, "")  # type: ignore
                    # Selectors hand back strings/floats; the decoder pool and the
                    # source labels both index on plain ints.
                    self.options[source_key] = int(user_input.get(source_key, i) or i)  # type: ignore
                    self.options[gain_key] = int(float(user_input.get(gain_key, 0) or 0))  # type: ignore

                self._apply_topology(user_input, errors)

                _model_update = False
                if CONF_NAME in user_input and user_input[CONF_NAME] != self.data.get(CONF_NAME):  # type: ignore
                    self.data[CONF_NAME] = user_input[CONF_NAME]  # type: ignore
                    # An explicit choice is authoritative: drop any earlier WHO=13 label so
                    # the next device-type reply cannot overwrite it (see gateway.py).
                    self.data["model_source"] = IDENTIFICATION_MANUAL  # type: ignore
                    _model_update = True

                _data_update = not (
                    self.data.get(CONF_HOST) == user_input.get(CONF_ADDRESS)  # type: ignore
                    and self.data.get(CONF_PASSWORD) == user_input.get(CONF_OWN_PASSWORD)  # type: ignore
                ) or _model_update
                self.data.update({CONF_HOST: user_input.get(CONF_ADDRESS)})  # type: ignore
                self.data.update({CONF_PASSWORD: user_input.get(CONF_OWN_PASSWORD)})  # type: ignore

                try:
                    self.data[CONF_HOST] = str(ipaddress.IPv4Address(self.data[CONF_HOST]))  # type: ignore
                except ipaddress.AddressValueError:
                    errors[CONF_ADDRESS] = "invalid_ip"

                if not errors:
                    if _data_update:
                        update_kwargs = {"data": self.data}
                        if _model_update and self.config_entry.title.endswith("Gateway"):
                            update_kwargs["title"] = f"{user_input[CONF_NAME]} Gateway"  # type: ignore
                        self.hass.config_entries.async_update_entry(self.config_entry, **update_kwargs)  # type: ignore

                    # We must only reload once. The options flow listener triggers a reload
                    # if the options dictionary actually changed. If data changed but options
                    # did not, we must trigger the reload ourselves.
                    options_changed = self.options != self.config_entry.options
                    if _data_update and not options_changed:
                        # Schedule a background reload so we can return the options form close event safely
                        self.hass.async_create_task(self.hass.config_entries.async_reload(self.config_entry.entry_id))

                    return self.async_create_entry(title="", data=self.options)  # type: ignore

        # ── Build form schema ─────────────────────────────────────────────
        model_options = [m for m in SUPPORTED_GATEWAY_MODELS]
        current_model = self.data.get(CONF_NAME, "MyHomeServer1")  # type: ignore
        if current_model not in model_options:
            model_options.insert(0, current_model)
        # An entry that never finished setup since upgrading still stores a count
        # above its gateway's limit; do not offer it back only to reject it.
        suggested_workers = int(self.options.get(CONF_WORKER_COUNT, 1))  # type: ignore
        current_limit = command_session_limit(current_model)
        if current_limit is not None:
            suggested_workers = min(suggested_workers, current_limit)

        schema_dict = {
            Required(
                CONF_ADDRESS,
                description={"suggested_value": self.data.get(CONF_HOST) or ""},  # type: ignore
            ): str,
            vol.Optional(
                CONF_NAME,
                default=current_model,
            ): vol.Any(In(model_options), cv.string),
            vol.Optional(
                CONF_OWN_PASSWORD,
                description={"suggested_value": self.data.get(CONF_PASSWORD) or ""},  # type: ignore
            ): str,
            Required(
                CONF_WORKER_COUNT,
                description={"suggested_value": suggested_workers},
            ): All(Coerce(int), Range(min=1, max=10)),
            Required(
                CONF_GENERATE_EVENTS,
                description={"suggested_value": self.options.get(CONF_GENERATE_EVENTS, False)},  # type: ignore
            ): bool,
            vol.Optional(
                CONF_BROADCAST_RESYNC,
                description={"suggested_value": self.options.get(CONF_BROADCAST_RESYNC, True)},  # type: ignore
                default=True,
            ): bool,
            vol.Optional(
                CONF_TRANSITION_MODE,
                description={
                    "suggested_value": self.options.get(CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE)  # type: ignore
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[  # type: ignore
                        {"value": "software_stepped", "label": "software_stepped (recommended - reliable stepped fades)"},
                        {"value": "native", "label": "native (pass through hardware speed param - only if your dimmers support it)"},
                        {"value": "auto", "label": "auto (alias for software_stepped)"},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        # Matrix source names 1–4 (F441M inputs S1–S4)
        _source_names: dict[int, str] = {}
        for i in range(1, CONF_SOURCE_SLOTS + 1):
            name_key = CONF_SOURCE_NAME.format(i)
            _name = str(self.options.get(name_key, "") or "").strip()  # type: ignore
            if _name:
                _source_names[i] = _name
            schema_dict[vol.Optional(
                name_key,
                description={"suggested_value": _name},
            )] = selector.TextSelector()

        # Default source per environment — only for environments that have zones.
        _stored_defaults = self.options.get(CONF_SOURCE_DEFAULTS) or {}  # type: ignore
        for env in self._audio_environments():
            field = CONF_SOURCE_DEFAULT_FIELD.format(env)
            _current = _stored_defaults.get(env) if isinstance(_stored_defaults, dict) else None
            schema_dict[vol.Required(
                field,
                default=str(_current) if _current else "none",
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="none", label="Leave routing as it is"),
                        *(
                            selector.SelectOptionDict(
                                value=str(i),
                                label=f"S{i} — {_source_names[i]}" if i in _source_names else f"S{i} (unnamed)",
                            )
                            for i in range(1, CONF_SOURCE_SLOTS + 1)
                        ),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        # Decoders are wired to one of those inputs: offer them by name, so the
        # mapping reads "which source is this decoder plugged into" rather than
        # asking the user to remember input numbers.
        _source_options = [
            selector.SelectOptionDict(
                value=str(i),
                label=f"S{i} — {_source_names[i]}" if i in _source_names else f"S{i} (unnamed)",
            )
            for i in range(1, CONF_SOURCE_SLOTS + 1)
        ]

        # Decoder slots 1–4
        for i in range(1, CONF_DECODER_SLOTS + 1):
            entity_key = CONF_DECODER_ENTITY.format(i)
            source_key = CONF_DECODER_SOURCE.format(i)
            gain_key = CONF_DECODER_PRE_GAIN.format(i)

            _entity_val = self.options.get(entity_key, "")  # type: ignore
            if _entity_val:
                schema_dict[vol.Optional(
                    entity_key,
                    description={"suggested_value": _entity_val},
                )] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["media_player"])
                )
            else:
                schema_dict[vol.Optional(entity_key)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["media_player"])
                )

            _source_val = int(self.options.get(source_key, i) or i)  # type: ignore
            schema_dict[vol.Required(
                source_key,
                default=str(min(max(_source_val, 1), CONF_SOURCE_SLOTS)),
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_source_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Required(
                gain_key,
                default=int(self.options.get(gain_key, 0) or 0),  # type: ignore
            )] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=50, step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        other_gateways = [
            e for e in self.hass.config_entries.async_entries(DOMAIN)
            if e.entry_id != getattr(self.config_entry, "entry_id", None)
        ]
        if other_gateways:
            gw_options = [
                selector.SelectOptionDict(value=str(e.data.get(CONF_MAC) or e.unique_id), label=f"{e.title} ({e.data.get(CONF_HOST)})")
                for e in other_gateways
            ]
            schema_dict[vol.Optional(
                CONF_BUS_TOPOLOGY,
                description={"suggested_value": self.options.get(CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE)},  # type: ignore
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[TOPOLOGY_STANDALONE, TOPOLOGY_SHARED],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_BUS_TOPOLOGY,
                )
            )
            schema_dict[vol.Optional(
                CONF_GATEWAY_ROLE,
                description={"suggested_value": self.options.get(CONF_GATEWAY_ROLE, ROLE_PRIMARY)},  # type: ignore
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[ROLE_PRIMARY, ROLE_SECONDARY, ROLE_STANDBY],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_GATEWAY_ROLE,
                )
            )
            schema_dict[vol.Optional(
                CONF_PRIMARY_GATEWAY,
                description={"suggested_value": self.options.get(CONF_PRIMARY_GATEWAY) or gw_options[0]["value"]},  # type: ignore
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=gw_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Optional(
                CONF_DELEGATED_WHOS,
                description={"suggested_value": [str(w) for w in self.options.get(CONF_DELEGATED_WHOS, [])]},  # type: ignore
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=["1", "2", "4", "5", "9", "15", "16", "18", "22", "25"],
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_DELEGATED_WHOS,
                )
            )

        return self.async_show_form(
            step_id="user",
            data_schema=Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "session_limit": str(command_session_limit(limit_model or current_model) or ""),
                "model": str(limit_model or current_model),
            },
        )
