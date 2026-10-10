"""Config flow to configure MyHome."""
import asyncio
import ipaddress
import re
import typing
from types import SimpleNamespace
from typing import Dict, Optional

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from OWNd.connection import OWNGateway, OWNSession
from OWNd.discovery import find_gateways, get_gateway
from voluptuous import (
    All,
    Coerce,
    In,
    Range,
    Required,
)

from .const import (
    CONF_ADDRESS,
    CONF_AUTO_JOIN_STREAMING,
    CONF_BROADCAST_RESYNC,
    CONF_BUS_TOPOLOGY,
    CONF_DECODER_COMPANION,
    CONF_DECODER_ENTITY,
    CONF_DECODER_PRE_GAIN,
    CONF_DECODER_SLOTS,
    CONF_DECODER_SOURCE,
    CONF_DELEGATED_WHOS,
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_GATEWAY_ROLE,
    CONF_GENERATE_EVENTS,
    CONF_IGNORED_ADDRESSES,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_OWN_PASSWORD,
    CONF_PRIMARY_GATEWAY,
    CONF_SOURCE_DEFAULT_FIELD,
    CONF_SOURCE_DEFAULTS,
    CONF_SOURCE_NAME,
    CONF_SOURCE_SLOTS,
    CONF_SOURCE_TUNER,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_TRANSITION_MODE,
    CONF_UDN,
    CONF_WORKER_COUNT,
    DEFAULT_AUTO_JOIN_STREAMING,
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
from .decoder_companion import async_get_excluded_decoders
from .gateway import MyHOMEGatewayHandler, command_session_default, command_session_limit
from .ignored import validate_ignored_addresses
from .topology import (
    entry_for_mac,
    entry_is_follower,
    entry_mac,
    recommend_follower,
    validate_shared_bus_topology,
)
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
        # (title, data, options) of an entry held back by the bus topology step
        self._pending_entry: tuple[str, dict[str, typing.Any], dict[str, typing.Any]] | None = None

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
            data_schema=flow_schema(
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
            schema = flow_schema(
                {
                    Required("port"): In(available_ports),
                    Required("baudrate", default=19200): In([9600, 19200, 38400, 57600, 115200]),
                    vol.Optional(CONF_FRIENDLY_NAME, default="Legrand 3578 Gateway"): cv.string,
                }
            )
        else:
            schema = flow_schema(
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
            data_schema=flow_schema(
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

    async def async_step_test_connection(self, user_input: typing.Any = None, errors: typing.Any = None) -> typing.Any:  # pylint: disable=unused-argument  # type: ignore
        """Testing connection to the OWN Gateway.

        Given a configured gateway, will attempt to connect and negociate a
        dummy event session to validate all parameters.
        """
        if errors is None:
            errors = {}

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

        async def _run_test_connection() -> dict[str, typing.Any]:
            try:
                session = OWNSession(gateway=gateway, logger=LOGGER)
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

        # Retry once after a brief pause if the connection was dropped mid-negotiation
        # (e.g. gateway busy, out of session slots, or stale socket recycling).
        # We only retry connection_closed because TCP already succeeded and OWNd does
        # not retry negotiation drops internally. We do NOT retry connection_error or
        # cannot_connect to avoid stacking delays on dead/unreachable hosts.
        if not test_result.get("Success") and test_result.get("Message") == "connection_closed":
            LOGGER.warning(
                "Gateway %s (%s:%s) test connection closed by gateway; retrying once after %ss pause",
                gateway.model_name,
                gateway.address,
                gateway.port,
                TEST_CONNECTION_RETRY_DELAY,
            )
            await asyncio.sleep(TEST_CONNECTION_RETRY_DELAY)
            test_result = await _run_test_connection()

        if test_result.get("Success"):
            if self._existing_entry:
                new_data = dict(self._existing_entry.data)
                new_data[CONF_PASSWORD] = gateway.password
                return self.async_update_reload_and_abort(
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

            if self._bus_primaries():
                self._pending_entry = (f"{gateway.model_name} Gateway", _new_entry_data, _new_entry_options)
                return await self.async_step_bus_topology()

            return self.async_create_entry(
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
                abort_reason = msg if msg in TEST_CONNECTION_ABORT_REASONS else "cannot_connect"
                return self.async_abort(reason=abort_reason)

    def _bus_primaries(self) -> list[ConfigEntry]:
        """Configured gateways a new one could share an SCS bus with.

        IP gateways that are not followers themselves; a USB / serial gateway
        is not on an SCS bus.
        """
        return [
            entry
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.source != SOURCE_IGNORE
            and entry.data.get("transport_type") != "serial"
            and not entry_is_follower(entry)
        ]

    async def async_step_bus_topology(self, user_input: dict[str, typing.Any] | None = None) -> ConfigFlowResult:
        """Ask whether the new gateway shares its SCS bus with a configured one (#524).

        Asked before the entry exists: a gateway set up as a standalone primary
        sweeps and discovers the whole bus at once, duplicating every device
        the other gateway already has. Joining as that gateway's secondary or
        standby from the start avoids it.
        """
        assert self._pending_entry is not None
        title, data, options = self._pending_entry
        primaries = self._bus_primaries()
        pending = SimpleNamespace(entry_id=None, data=data, options={}, unique_id=data[CONF_MAC], title=title)
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get(CONF_BUS_TOPOLOGY) != TOPOLOGY_SHARED:
                return self.async_create_entry(
                    title=title, data=data, options={**options, CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE}
                )

            primary_mac = dr.format_mac(str(user_input.get(CONF_PRIMARY_GATEWAY) or ""))
            primary = entry_for_mac(self.hass, primary_mac) if primary_mac else None
            role = user_input.get(CONF_GATEWAY_ROLE, ROLE_SECONDARY)
            follower_options = {
                **options,
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: role,
                CONF_PRIMARY_GATEWAY: primary_mac,
            }
            if role == ROLE_SECONDARY:
                follower_options[CONF_DELEGATED_WHOS] = sorted(
                    int(w) for w in user_input.get(CONF_DELEGATED_WHOS, []) if str(w).isdigit()
                )
            # A standalone gateway becomes the shared bus's primary.
            primary_options = dict(primary.options) if primary is not None else {}
            primary_options.update({CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY})
            primary_options.pop(CONF_PRIMARY_GATEWAY, None)
            primary_options.pop(CONF_DELEGATED_WHOS, None)

            errors = validate_shared_bus_topology(
                self.hass, pending, follower_options, target_primary_options=primary_options
            )
            if not errors and primary is not None:
                if primary_options != dict(primary.options):
                    self.hass.config_entries.async_update_entry(primary, options=primary_options)
                from .repairs import async_delete_shared_bus_issue

                async_delete_shared_bus_issue(self.hass, data[CONF_MAC], primary_mac)
                return self.async_create_entry(title=title, data=data, options=follower_options)

        if not primaries:  # the other gateway was removed while the form was open
            return self.async_create_entry(title=title, data=data, options=options)

        gw_options = [
            selector.SelectOptionDict(value=str(entry_mac(e)), label=f"{e.title} ({e.data.get(CONF_HOST)})")
            for e in primaries
        ]
        suggested_primary = (user_input or {}).get(CONF_PRIMARY_GATEWAY) or gw_options[0]["value"]
        role, delegated = ROLE_STANDBY, set[int]()
        if (suggested_entry := entry_for_mac(self.hass, dr.format_mac(str(suggested_primary)))) is not None:
            role, delegated = recommend_follower(suggested_entry, pending)

        return self.async_show_form(
            step_id="bus_topology",
            data_schema=flow_schema(
                {
                    Required(
                        CONF_BUS_TOPOLOGY, default=(user_input or {}).get(CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[TOPOLOGY_STANDALONE, TOPOLOGY_SHARED],
                            mode=selector.SelectSelectorMode.LIST,
                            translation_key=CONF_BUS_TOPOLOGY,
                        )
                    ),
                    Required(CONF_PRIMARY_GATEWAY, default=suggested_primary): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=gw_options, mode=selector.SelectSelectorMode.DROPDOWN)
                    ),
                    Required(
                        CONF_GATEWAY_ROLE, default=(user_input or {}).get(CONF_GATEWAY_ROLE, role)
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[ROLE_SECONDARY, ROLE_STANDBY],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_GATEWAY_ROLE,
                        )
                    ),
                    vol.Optional(
                        CONF_DELEGATED_WHOS,
                        description={
                            "suggested_value": (user_input or {}).get(
                                CONF_DELEGATED_WHOS, [str(w) for w in sorted(delegated)]
                            )
                        },
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["1", "2", "4", "5", "9", "15", "16", "18", "22", "25"],
                            multiple=True,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_DELEGATED_WHOS,
                        )
                    ),
                }
            ),
            description_placeholders={CONF_NAME: str(data.get(CONF_NAME) or "gateway")},
            errors=errors,
        )

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
            data_schema=flow_schema(
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
            data_schema=flow_schema(
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
        if not gateway.unique_id or not gateway.serial:
            return self.async_abort(reason="no_serial")
        await self.async_set_unique_id(dr.format_mac(gateway.unique_id))
        LOGGER.info("Found gateway: %s", gateway.address)
        # What the gateway reports about itself follows a rediscovery. The port is not
        # among it (20000 is only assumed above, never discovered) and neither is the
        # model name (the user may have chosen it): writing those back would undo a
        # reconfigure on every restart.
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




class MyhomeOptionsFlowHandler(OptionsFlowWithReload):
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
            self.options[CONF_WORKER_COUNT] = command_session_default(self.data.get(CONF_NAME))  # type: ignore
        if CONF_GENERATE_EVENTS not in self.options:  # type: ignore
            self.options[CONF_GENERATE_EVENTS] = False  # type: ignore
        if CONF_BROADCAST_RESYNC not in self.options:  # type: ignore
            self.options[CONF_BROADCAST_RESYNC] = True  # type: ignore
        if CONF_TRANSITION_MODE not in self.options:  # type: ignore
            self.options[CONF_TRANSITION_MODE] = DEFAULT_TRANSITION_MODE  # type: ignore
        if CONF_AUTO_JOIN_STREAMING not in self.options:  # type: ignore
            self.options[CONF_AUTO_JOIN_STREAMING] = DEFAULT_AUTO_JOIN_STREAMING  # type: ignore
        if CONF_IGNORED_ADDRESSES not in self.options:  # type: ignore
            self.options[CONF_IGNORED_ADDRESSES] = []  # type: ignore
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
        top_errors = validate_shared_bus_topology(
            self.hass,
            self.config_entry,
            user_input,
            model_override=user_input.get(CONF_NAME),
        )
        if top_errors:
            errors.update(top_errors)
            return

        in_topo = user_input.get(CONF_BUS_TOPOLOGY, self.options.get(CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE))  # type: ignore
        in_role = user_input.get(CONF_GATEWAY_ROLE, self.options.get(CONF_GATEWAY_ROLE, ROLE_PRIMARY))  # type: ignore
        in_pri = user_input.get(CONF_PRIMARY_GATEWAY, self.options.get(CONF_PRIMARY_GATEWAY))  # type: ignore
        shared = in_topo == TOPOLOGY_SHARED
        follower = shared and in_role in (ROLE_SECONDARY, ROLE_STANDBY)
        my_mac = entry_mac(self.config_entry)
        norm_pri = dr.format_mac(str(in_pri)) if in_pri else None

        self.options[CONF_BUS_TOPOLOGY] = TOPOLOGY_SHARED if shared else TOPOLOGY_STANDALONE  # type: ignore
        self.options[CONF_GATEWAY_ROLE] = in_role if shared else ROLE_PRIMARY  # type: ignore
        if follower:
            self.options[CONF_PRIMARY_GATEWAY] = norm_pri  # type: ignore
        else:
            self.options.pop(CONF_PRIMARY_GATEWAY, None)  # type: ignore
        if follower and in_role == ROLE_SECONDARY:
            delegated = [int(w) for w in user_input.get(CONF_DELEGATED_WHOS, []) if str(w).isdigit()]
            self.options[CONF_DELEGATED_WHOS] = delegated  # type: ignore
        else:
            self.options.pop(CONF_DELEGATED_WHOS, None)  # type: ignore

        if follower and my_mac and norm_pri:
            from .repairs import async_delete_shared_bus_issue
            async_delete_shared_bus_issue(self.hass, my_mac, str(norm_pri))

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
            registry = er.async_get(self.hass)

            seen_sources: dict[int, str] = {}
            for i in range(1, CONF_DECODER_SLOTS + 1):
                entity_key = CONF_DECODER_ENTITY.format(i)
                source_key = CONF_DECODER_SOURCE.format(i)
                companion_key = CONF_DECODER_COMPANION.format(i)
                entity_val = str(user_input.get(entity_key) or "").strip()
                companion_val = str(user_input.get(companion_key) or "").strip()

                if entity_val:
                    if not entity_val.startswith("media_player."):
                        errors[entity_key] = "not_a_media_player"
                    else:
                        entry = registry.async_get(entity_val)
                        if entry and entry.platform == "mass":
                            # Prevent infinite loops by rejecting MA clones
                            errors[entity_key] = "mass_entity_not_allowed"
                        elif entry and entry.platform == "myhome":
                            errors[entity_key] = "myhome_entity_not_allowed"

                    src_val = int(user_input.get(source_key, i) or i)
                    if src_val in seen_sources:
                        errors[source_key] = "duplicate_decoder_source"
                    else:
                        seen_sources[src_val] = source_key

                if companion_val:
                    if not entity_val:
                        errors[companion_key] = "companion_without_decoder"
                    elif not companion_val.startswith("media_player."):
                        errors[companion_key] = "not_a_media_player"
                    else:
                        comp_entry = registry.async_get(companion_val)
                        if companion_val == entity_val:
                            errors[companion_key] = "companion_same_as_decoder"
                        elif comp_entry and comp_entry.platform == "mass":
                            errors[companion_key] = "mass_entity_not_allowed"
                        elif comp_entry and comp_entry.platform == "myhome":
                            errors[companion_key] = "myhome_entity_not_allowed"

            limit_model = user_input.get(CONF_NAME, self.data.get(CONF_NAME))  # type: ignore
            session_limit = command_session_limit(limit_model)
            if session_limit is not None and int(user_input[CONF_WORKER_COUNT]) > session_limit:
                errors[CONF_WORKER_COUNT] = "worker_count_above_gateway_limit"

            # Home Assistant frontend (ha-selector-text / ha-form) omits non-required
            # fields from user_input when cleared. Defaulting to "" ensures self.options
            # is cleared to [] rather than preserving stale addresses (#612).
            ignored_raw = user_input.get(CONF_IGNORED_ADDRESSES, "")
            parsed_ignored, is_valid = validate_ignored_addresses(ignored_raw)
            if not is_valid:
                errors[CONF_IGNORED_ADDRESSES] = "invalid_ignored_address"
            else:
                self.options[CONF_IGNORED_ADDRESSES] = parsed_ignored  # type: ignore

            if not errors:
                self.options.update({CONF_WORKER_COUNT: user_input[CONF_WORKER_COUNT]})  # type: ignore
                self.options.update({CONF_GENERATE_EVENTS: user_input[CONF_GENERATE_EVENTS]})  # type: ignore
                self.options.update({CONF_BROADCAST_RESYNC: user_input.get(CONF_BROADCAST_RESYNC, True)})  # type: ignore
                self.options[CONF_TRANSITION_MODE] = user_input.get(CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE)  # type: ignore
                self.options[CONF_AUTO_JOIN_STREAMING] = user_input.get(  # type: ignore
                    CONF_AUTO_JOIN_STREAMING, DEFAULT_AUTO_JOIN_STREAMING
                )

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
                    tuner_key = CONF_SOURCE_TUNER.format(i)
                    self.options[tuner_key] = bool(user_input.get(tuner_key, False))  # type: ignore

                for i in range(1, CONF_DECODER_SLOTS + 1):
                    entity_key = CONF_DECODER_ENTITY.format(i)
                    source_key = CONF_DECODER_SOURCE.format(i)
                    gain_key = CONF_DECODER_PRE_GAIN.format(i)
                    entity_val = str(user_input.get(entity_key) or "").strip()
                    self.options[entity_key] = entity_val  # type: ignore
                    companion_key = CONF_DECODER_COMPANION.format(i)
                    companion = str(user_input.get(companion_key) or "").strip() if entity_val else ""
                    if companion or companion_key in self.options:  # type: ignore[operator]
                        self.options[companion_key] = companion  # type: ignore
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
                        # OptionsFlowWithReload only schedules a reload when entry.options
                        # change. When only connection data changed (host, password, model)
                        # and options remain identical, schedule reload explicitly so the
                        # integration restarts with the new connection parameters.
                        if self.config_entry.options == self.options:
                            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

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
            ): vol.Maybe(str),
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
            vol.Optional(
                CONF_AUTO_JOIN_STREAMING,
                description={
                    "suggested_value": self.options.get(  # type: ignore
                        CONF_AUTO_JOIN_STREAMING, DEFAULT_AUTO_JOIN_STREAMING
                    )
                },
                default=DEFAULT_AUTO_JOIN_STREAMING,
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_IGNORED_ADDRESSES,
                description={
                    "suggested_value": "\n".join(self.options.get(CONF_IGNORED_ADDRESSES, []))  # type: ignore
                    if isinstance(self.options.get(CONF_IGNORED_ADDRESSES), list)  # type: ignore
                    else str(self.options.get(CONF_IGNORED_ADDRESSES) or "")  # type: ignore
                },
            ): vol.Maybe(selector.TextSelector(selector.TextSelectorConfig(multiline=True))),
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
            # A tuner accepts frequency, station and RDS messages that a line
            # interface does not, and nothing on the bus tells them apart until
            # the device speaks, so the user declares it.
            tuner_key = CONF_SOURCE_TUNER.format(i)
            schema_dict[vol.Required(
                tuner_key,
                default=bool(self.options.get(tuner_key, False)),  # type: ignore
            )] = selector.BooleanSelector()

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

        # Exclude internal MyHOME zones and Music Assistant clones from decoder choices
        _decoder_selector_cfg = selector.EntitySelectorConfig(
            domain=["media_player"],
            exclude_entities=async_get_excluded_decoders(self.hass),
        )

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
                )] = selector.EntitySelector(_decoder_selector_cfg)
            else:
                schema_dict[vol.Optional(entity_key)] = selector.EntitySelector(_decoder_selector_cfg)

            companion_key = CONF_DECODER_COMPANION.format(i)
            _companion_val = self.options.get(companion_key, "")  # type: ignore
            schema_dict[vol.Optional(
                companion_key,
                description={"suggested_value": _companion_val} if _companion_val else None,
            )] = selector.EntitySelector(_decoder_selector_cfg)

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
                    min=0, max=100, step=1,
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
                description={"suggested_value": self.options.get(CONF_BUS_TOPOLOGY, TOPOLOGY_STANDALONE)},  # type: ignore[attr-defined]
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[TOPOLOGY_STANDALONE, TOPOLOGY_SHARED],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_BUS_TOPOLOGY,
                )
            )
            suggested_role = self.options.get(CONF_GATEWAY_ROLE)  # type: ignore[attr-defined]
            suggested_whos = [str(w) for w in self.options.get(CONF_DELEGATED_WHOS, [])]  # type: ignore[attr-defined]
            selected_pri = self.options.get(CONF_PRIMARY_GATEWAY)  # type: ignore[attr-defined]
            if not selected_pri and gw_options:
                selected_pri = gw_options[0]["value"]
            if selected_pri and (suggested_role is None or not suggested_whos):
                from .topology import entry_for_mac, entry_mac, infer_shared_bus_topology

                pri_entry = entry_for_mac(self.hass, selected_pri)
                if pri_entry and self.config_entry:
                    rec = infer_shared_bus_topology(pri_entry, self.config_entry)
                    my_mac = entry_mac(self.config_entry)
                    if suggested_role is None:
                        suggested_role = rec.role if rec.secondary_mac == my_mac else ROLE_PRIMARY
                    if not suggested_whos and rec.secondary_mac == my_mac and rec.role == ROLE_SECONDARY:
                        suggested_whos = [str(w) for w in sorted(rec.delegated_whos)]
                    LOGGER.debug(
                        "Inferred shared-bus smart defaults for %s: role=%s, delegated_whos=%s (selected primary %s)",
                        my_mac,
                        suggested_role,
                        suggested_whos,
                        selected_pri,
                    )

            if suggested_role is None:
                suggested_role = ROLE_PRIMARY

            schema_dict[vol.Optional(
                CONF_GATEWAY_ROLE,
                description={"suggested_value": suggested_role},
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[ROLE_PRIMARY, ROLE_SECONDARY, ROLE_STANDBY],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_GATEWAY_ROLE,
                )
            )
            schema_dict[vol.Optional(
                CONF_PRIMARY_GATEWAY,
                description={"suggested_value": self.options.get(CONF_PRIMARY_GATEWAY)},  # type: ignore[attr-defined]
            )] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=gw_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Optional(
                CONF_DELEGATED_WHOS,
                description={"suggested_value": suggested_whos},
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
            data_schema=flow_schema(schema_dict),
            errors=errors,
            description_placeholders={
                "session_limit": str(command_session_limit(limit_model or current_model) or ""),
                "session_default": str(command_session_default(limit_model or current_model)),
                "model": str(limit_model or current_model),
            },
        )
