"""Config flow to configure MyHome."""
from __future__ import annotations

import asyncio
import errno
import ipaddress
import os
import re
import typing
from typing import Dict, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigSubentryFlow
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from OWNd.connection import OWNGateway, OWNSession
from OWNd.discovery import find_gateways, get_gateway
from OWNd.profiles import Legrand3578Profile
from OWNd.transport.serial import AsyncSerialTransport
from serialx import SerialException
from voluptuous import In, Required

from .config_flow_discovery import (
    TEST_CONNECTION_ABORT_REASONS,
    TEST_CONNECTION_RETRY_DELAY,
    ConfigFlowDiscoveryMixin,
    GatewayIdentity,
    MACAddress,
    identify_gateway,
)
from .config_flow_expose import ExposeSubentryFlowHandler
from .config_flow_options import MyhomeOptionsFlowHandler
from .config_flow_thermo_switch import ThermoSwitchSubentryFlowHandler
from .config_flow_topology import ConfigFlowTopologyMixin
from .const import (
    CONF_BAUDRATE,
    CONF_BROADCAST_RESYNC,
    CONF_FIRMWARE,
    CONF_GENERATE_EVENTS,
    CONF_MANUFACTURER,
    CONF_SERIAL_PORT,
    CONF_WORKER_COUNT,
    DEFAULT_BAUDRATE,
    DOMAIN,
    IDENTIFICATION_SERIAL,
    LOGGER,
    SUBENTRY_TYPE_EXPOSE,
    SUBENTRY_TYPE_THERMO_SWITCH,
    SUPPORTED_GATEWAY_MODELS,
    SUPPORTED_SERIAL_MODELS,
)
from .gateway import MyHOMEGatewayHandler, command_session_default
from .typing_compat import flow_schema

_EUI64_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2}$")
_UNUSABLE_EUI64S: frozenset[str] = frozenset(
    {"00:00:00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff:ff:ff"}
)


def _validated_eui64(ieee: str | None) -> str | None:
    """Validate and normalize an 8-octet IEEE EUI-64 string."""
    if not isinstance(ieee, str):
        return None
    candidate = ieee.strip().lower()
    if not _EUI64_RE.fullmatch(candidate) or candidate in _UNUSABLE_EUI64S:
        return None
    return candidate


def _fallback_get_serial_by_id(dev_path: str) -> str:
    """Resolve a ``/dev/...`` node to its stable ``/dev/serial/by-id/...`` symlink."""
    by_id = "/dev/serial/by-id"
    if not os.path.isdir(by_id):
        return dev_path
    target_real = os.path.realpath(dev_path)
    for entry in os.scandir(by_id):
        if entry.is_symlink() and os.path.realpath(entry.path) == target_real:
            return entry.path
    return dev_path


def _resolve_serial_by_id_sync(port: str) -> str:
    if "://" in port:
        return port
    try:
        from homeassistant.components import usb

        return str(usb.get_serial_by_id(port))
    except ImportError:
        return _fallback_get_serial_by_id(port)


async def _async_resolve_serial_by_id(hass: HomeAssistant, port: str) -> str:
    """Resolve local serial device paths to ``/dev/serial/by-id/...`` off the event loop."""
    if "://" in port:
        return port
    return await hass.async_add_executor_job(_resolve_serial_by_id_sync, port)


async def _async_ports_match(
    hass: HomeAssistant, current_port: str, new_port: str
) -> bool:
    """Return True if ``current_port`` and ``new_port`` resolve to the same endpoint."""
    if current_port == new_port:
        return True
    if "://" in current_port or "://" in new_port:
        return False

    def _realpath_pair(first: str, second: str) -> tuple[str, str]:
        return os.path.realpath(first), os.path.realpath(second)

    cur_real, new_real = await hass.async_add_executor_job(
        _realpath_pair, current_port, new_port
    )
    return cur_real == new_real


def _fallback_list_serial_ports_sync() -> list[selector.SelectOptionDict]:
    import serialx

    options: list[selector.SelectOptionDict] = []
    for port_info in serialx.list_serial_ports():
        dev = str(port_info.device)
        by_id = _fallback_get_serial_by_id(dev) if dev.startswith("/dev/") else dev
        desc = getattr(port_info, "description", None)
        label = f"{desc} ({by_id})" if desc else by_id
        options.append(selector.SelectOptionDict(value=by_id, label=label))
    return options


async def _async_list_serial_options(
    hass: HomeAssistant,
) -> list[selector.SelectOptionDict]:
    """List serial ports using HA's ``usb`` integration when loaded, else ``serialx``."""
    if "usb" in hass.config.components:
        try:
            from homeassistant.components import usb

            ports = await usb.async_scan_serial_ports(hass)
            options: list[selector.SelectOptionDict] = []
            for port_info in ports:
                dev = str(port_info.device)
                resolved = (
                    await hass.async_add_executor_job(usb.get_serial_by_id, dev)
                    if dev.startswith("/dev/")
                    else dev
                )
                label = usb.human_readable_device_name(
                    resolved,
                    getattr(port_info, "serial_number", None),
                    getattr(port_info, "manufacturer", None),
                    getattr(port_info, "description", None),
                    getattr(port_info, "vid", None),
                    getattr(port_info, "pid", None),
                )
                options.append(selector.SelectOptionDict(value=resolved, label=label))
            return options
        except ImportError:
            pass
    return await hass.async_add_executor_job(_fallback_list_serial_ports_sync)


async def _async_probe_zigbee_serial(
    hass: HomeAssistant,
    serial_port: str,
    baudrate: int = DEFAULT_BAUDRATE,
) -> tuple[str | None, str | None, str | None]:
    """Probe an OpenZigBee USB stick exclusively and return ``(eui64, firmware, error_key)``."""
    resolved_port = await _async_resolve_serial_by_id(hass, serial_port)
    transport = AsyncSerialTransport(
        port=resolved_port,
        baudrate=baudrate,
        gateway=Legrand3578Profile(),
        handshake=True,
        reconnect=False,
    )
    try:
        try:
            ok = await transport.connect()
        except (SerialException, TimeoutError):
            return None, None, "cannot_connect"
        except OSError as err:
            return (
                None,
                None,
                "port_busy" if err.errno == errno.EBUSY else "cannot_connect",
            )

        if not ok:
            reason = getattr(transport.last_error, "reason", None)
            if reason in ("port_busy", "cannot_connect", "no_zigbee_interface"):
                return None, None, reason
            return None, None, "cannot_connect"
        eui64 = _validated_eui64(transport.ieee_address)
        if eui64 is None:
            return None, None, "no_zigbee_interface"
        return eui64, transport.firmware_version, None
    finally:
        await transport.disconnect()


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
        if CONF_SERIAL_PORT in config_entry.data:
            return {}
        return {
            SUBENTRY_TYPE_EXPOSE: ExposeSubentryFlowHandler,
            SUBENTRY_TYPE_THERMO_SWITCH: ThermoSwitchSubentryFlowHandler,
        }

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
        if user_input is not None and user_input["serial"] == "serial":
            return await self.async_step_serial()

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
                            "serial": "USB / Serial Gateway (BTicino 3578 / Legrand 088328)",
                        }
                    )
                }
            ),
        )

    async def async_step_serial(  # type: ignore
        self, user_input: dict[str, typing.Any] | None = None
    ):
        """Handle USB / Serial OpenZigBee gateway setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            raw_port = str(user_input.get(CONF_SERIAL_PORT, "")).strip()
            if not raw_port:
                errors[CONF_SERIAL_PORT] = "cannot_connect"
            else:
                resolved_port = await _async_resolve_serial_by_id(self.hass, raw_port)
                eui64, firmware, err_key = await _async_probe_zigbee_serial(
                    self.hass, resolved_port, DEFAULT_BAUDRATE
                )
                if err_key is not None or eui64 is None:
                    errors[CONF_SERIAL_PORT] = err_key or "cannot_connect"
                else:
                    model_name = (
                        str(user_input.get(CONF_NAME) or SUPPORTED_SERIAL_MODELS[0]).strip()
                        or SUPPORTED_SERIAL_MODELS[0]
                    )
                    await self.async_set_unique_id(eui64, raise_on_progress=False)
                    self._abort_if_unique_id_configured(
                        updates={CONF_SERIAL_PORT: resolved_port}
                    )
                    return self.async_create_entry(
                        title=f"{model_name} ({eui64[-5:]})",
                        data={
                            CONF_SERIAL_PORT: resolved_port,
                            CONF_BAUDRATE: DEFAULT_BAUDRATE,
                            CONF_MAC: eui64,
                            CONF_NAME: model_name,
                            CONF_MANUFACTURER: "Legrand / BTicino",
                            CONF_FIRMWARE: firmware or "",
                            "model_source": IDENTIFICATION_SERIAL,
                        },
                        options={
                            CONF_WORKER_COUNT: 1,
                            CONF_GENERATE_EVENTS: False,
                            CONF_BROADCAST_RESYNC: True,
                        },
                    )

        port_options = await _async_list_serial_options(self.hass)
        suggested_port = (
            str(user_input.get(CONF_SERIAL_PORT, "")).strip()
            if user_input is not None
            else (port_options[0]["value"] if port_options else "")
        )
        port_schema = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=port_options,
                custom_value=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        model_schema = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(SUPPORTED_SERIAL_MODELS),
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        return self.async_show_form(
            step_id="serial",
            data_schema=flow_schema(
                {
                    Required(
                        CONF_SERIAL_PORT,
                        description={"suggested_value": suggested_port},
                    ): port_schema,
                    vol.Optional(
                        CONF_NAME,
                        default=(
                            (user_input or {}).get(CONF_NAME)
                            or SUPPORTED_SERIAL_MODELS[0]
                        ),
                    ): model_schema,
                }
            ),
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
    "asyncio",
    "command_session_default",
    "find_gateways",
    "get_gateway",
    "GatewayIdentity",
    "identify_gateway",
]
