"""Provides device triggers for MyHOME CEN / CEN+ buttons."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CENTRALIZED_SHUTTER_CLOSE,
    CONF_CENTRALIZED_SHUTTER_OPEN,
    CONF_CENTRALIZED_SHUTTER_STOP,
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
)

_LOGGER = logging.getLogger(__name__)


def _noop_unsubscribe() -> None:
    """Unsubscribe callback for a trigger that never subscribed."""

CONF_ADDRESS = "address"
CONF_OBJECT = "object"
CONF_SUBTYPE = "subtype"

TRIGGER_TYPES = {
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    CONF_LONG_PRESS,
    CONF_LONG_PRESS_REPEAT,
    CONF_LONG_RELEASE,
    CONF_ROTARY_CW_SLOW,
    CONF_ROTARY_CW_FAST,
    CONF_ROTARY_CCW_SLOW,
    CONF_ROTARY_CCW_FAST,
}

GATEWAY_TRIGGER_TYPES = {
    CONF_CENTRALIZED_SHUTTER_OPEN,
    CONF_CENTRALIZED_SHUTTER_CLOSE,
    CONF_CENTRALIZED_SHUTTER_STOP,
}

TRIGGER_SUBTYPES = [f"button_{i}" for i in range(0, 32)]

# Triggers a family can never fire, so they are not offered for its devices.
# CEN (WHO 15) has no rotary events and no separate repeat frame (its #3 both
# starts and repeats a hold); CEN+ (WHO 25) has no short-release frame.
_ROTARY_TRIGGER_TYPES = {
    CONF_ROTARY_CW_SLOW,
    CONF_ROTARY_CW_FAST,
    CONF_ROTARY_CCW_SLOW,
    CONF_ROTARY_CCW_FAST,
}
_UNSUPPORTED_TRIGGER_TYPES = {
    "15": _ROTARY_TRIGGER_TYPES | {CONF_LONG_PRESS_REPEAT},
    "25": {CONF_SHORT_RELEASE},
}

TRIGGER_SCHEMA = vol.Any(
    DEVICE_TRIGGER_BASE_SCHEMA.extend(
        {
            vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
            vol.Required(CONF_SUBTYPE): vol.In(TRIGGER_SUBTYPES),
            vol.Optional(CONF_ADDRESS): vol.Any(vol.Coerce(int), str),
            vol.Optional(CONF_OBJECT): vol.Any(vol.Coerce(int), str),
        }
    ),
    DEVICE_TRIGGER_BASE_SCHEMA.extend(
        {
            vol.Required(CONF_TYPE): vol.In(GATEWAY_TRIGGER_TYPES),
            vol.Optional(CONF_SUBTYPE): str,
        }
    ),
)


def _get_gateway_mac_from_device(device: dr.AnyDeviceEntry) -> str | None:
    """Extract gateway MAC address from device entry."""
    # A child device has no network connections; reading them is deprecated.
    connections = () if isinstance(device, dr.ChildDeviceEntry) else device.connections
    for conn_type, conn_val in connections:
        if conn_type == dr.CONNECTION_NETWORK_MAC:
            return str(conn_val)
    for identifier in device.identifiers:
        if identifier[0] != DOMAIN:
            continue
        ident = str(identifier[1])
        parts = ident.split("-")
        if len(parts) >= 3 and parts[-2] in ("15", "25", "cen", "cenplus"):
            return parts[0]
        if len(parts) == 1:
            return ident
    return None


def _get_cen_address_from_device(device: dr.BaseDeviceEntry) -> str | None:
    """Extract scenario address as string from device entry."""
    for identifier in device.identifiers:
        if identifier[0] != DOMAIN:
            continue
        ident = str(identifier[1])
        parts = ident.split("-")
        if len(parts) >= 3 and parts[-2] in ("15", "25", "cen", "cenplus"):
            return parts[-1]
        elif ident.startswith("cen_") or ident.startswith("cenplus_"):
            return ident.split("_", 1)[1]
    return None


def _get_cen_info_from_device(device: dr.BaseDeviceEntry) -> tuple[bool, int | None]:
    """Check if device is a CEN/CEN+ scenario device or gateway, and extract address if available.

    Returns:
        (is_cen_or_gateway, address)
    """
    is_myhome = any(identifier[0] == DOMAIN for identifier in device.identifiers)
    if not is_myhome:
        return False, None

    for identifier in device.identifiers:
        if identifier[0] != DOMAIN:
            continue
        ident = str(identifier[1])
        parts = ident.split("-")
        # Identifiers like "{mac}-15-{where}" or "{mac}-25-{where}"
        if len(parts) >= 3 and parts[-2] in ("15", "25", "cen", "cenplus"):
            try:
                return True, int(parts[-1])
            except ValueError:
                pass
        elif ident.startswith("cen_") or ident.startswith("cenplus_"):
            try:
                return True, int(ident.split("_", 1)[1])
            except ValueError:
                pass

    # Reject standard entities that are not button transmitters
    # (e.g. lights, covers, thermostats, binary sensors with WHO in 1, 2, 4, 5, 9, 18)
    for identifier in device.identifiers:
        if identifier[0] != DOMAIN:
            continue
        ident = str(identifier[1])
        parts = ident.split("-")
        if len(parts) >= 3 and parts[-2] in ("1", "2", "4", "5", "9", "18"):
            return False, None

    # Gateway or unspecified MyHOME device
    return True, None


def _get_cen_family_from_device(device: dr.BaseDeviceEntry) -> str | None:
    """Return "15" for a CEN device, "25" for a CEN+ device, else None."""
    for identifier in device.identifiers:
        if identifier[0] != DOMAIN:
            continue
        ident = str(identifier[1])
        parts = ident.split("-")
        if len(parts) >= 3 and parts[-2] in ("15", "cen"):
            return "15"
        if len(parts) >= 3 and parts[-2] in ("25", "cenplus"):
            return "25"
        if ident.startswith("cen_"):
            return "15"
        if ident.startswith("cenplus_"):
            return "25"
    return None


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """List device triggers for MyHOME CEN/CEN+ devices."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get(device_id)

    if device is None:
        return []

    is_valid, address = _get_cen_info_from_device(device)
    if not is_valid:
        return []

    unsupported = _UNSUPPORTED_TRIGGER_TYPES.get(
        _get_cen_family_from_device(device) or "", set()
    )
    triggers = []
    for trigger_type in TRIGGER_TYPES - unsupported:
        for subtype in TRIGGER_SUBTYPES:
            trigger: dict[str, Any] = {
                CONF_PLATFORM: "device",
                CONF_DEVICE_ID: device_id,
                CONF_DOMAIN: DOMAIN,
                CONF_TYPE: trigger_type,
                CONF_SUBTYPE: subtype,
            }
            if address is not None:
                trigger[CONF_ADDRESS] = address
            triggers.append(trigger)

    if address is None:
        for gw_trigger_type in sorted(GATEWAY_TRIGGER_TYPES):
            triggers.append(
                {
                    CONF_PLATFORM: "device",
                    CONF_DEVICE_ID: device_id,
                    CONF_DOMAIN: DOMAIN,
                    CONF_TYPE: gw_trigger_type,
                }
            )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: Any,
    trigger_info: dict[str, Any],
) -> CALLBACK_TYPE:
    """Attach a trigger to Home Assistant event bus."""
    trigger_data = trigger_info.get("trigger_data")
    if trigger_data is None:
        trigger_data = trigger_info
    trigger_type = config[CONF_TYPE]

    if trigger_type in GATEWAY_TRIGGER_TYPES:
        target_gateway_mac = None
        if CONF_DEVICE_ID in config:
            device_registry = dr.async_get(hass)
            device = device_registry.async_get(config[CONF_DEVICE_ID])
            if device is not None:
                target_gateway_mac = _get_gateway_mac_from_device(device)

        expected_event = {
            CONF_CENTRALIZED_SHUTTER_OPEN: "open",
            CONF_CENTRALIZED_SHUTTER_CLOSE: "close",
            CONF_CENTRALIZED_SHUTTER_STOP: "stop",
        }[trigger_type]

        async def _handle_gateway_event(event: Any) -> None:
            event_data = event.data
            if event_data.get("event") == expected_event:
                if target_gateway_mac is not None:
                    event_mac = event_data.get("gateway_mac")
                    if event_mac is not None and event_mac != target_gateway_mac:
                        return
                await action(
                    {
                        "trigger": {
                            **trigger_data,
                            "platform": "device",
                            "event": event_data,
                        }
                    },
                    event.context,
                )

        return hass.bus.async_listen("myhome_general_automation_event", _handle_gateway_event)

    subtype = config[CONF_SUBTYPE]
    button_num = int(subtype.replace("button_", ""))

    # Determine target scenario address and gateway MAC from config or associated device
    target_address = config.get(CONF_ADDRESS)
    if target_address is None:
        target_address = config.get(CONF_OBJECT)

    target_gateway_mac = None
    # CEN (15) and CEN+ (25) objects are separate address spaces. A trigger on a
    # device of a known family only listens to that family's events; a bare
    # address (no device, or a gateway) keeps matching both.
    family: str | None = None
    if CONF_DEVICE_ID in config:
        device_registry = dr.async_get(hass)
        device = device_registry.async_get(config[CONF_DEVICE_ID])
        if device is None:
            # The family is unknown, so listening to both streams would bring
            # #601 back. Fail closed rather than fire on the wrong family.
            _LOGGER.warning(
                "Device trigger %s: device %s not found; trigger is inactive",
                trigger_data.get("id", trigger_type),
                config[CONF_DEVICE_ID],
            )
            return _noop_unsubscribe
        target_gateway_mac = _get_gateway_mac_from_device(device)
        family = _get_cen_family_from_device(device)
        if target_address is None:
            target_address = _get_cen_address_from_device(device)
            if target_address is None:
                _, dev_addr = _get_cen_info_from_device(device)
                if dev_addr is not None:
                    target_address = dev_addr

    async def _handle_event(event: Any) -> None:
        event_data = event.data
        if (
            event_data.get("event") == trigger_type
            and event_data.get("pushbutton") == button_num
        ):
            # Gateway MAC filtering for multi-gateway plant isolation (P6)
            if target_gateway_mac is not None:
                event_mac = event_data.get("gateway_mac")
                if event_mac is not None and event_mac != target_gateway_mac:
                    return

            # Address filtering with string and numeric tolerance (P2)
            if target_address is not None:
                event_object = event_data.get("object")
                event_where = event_data.get("where")
                event_raw_where = event_data.get("raw_where")
                str_target = str(target_address)
                matches_str = (
                    (event_where is not None and str(event_where) == str_target)
                    or (event_object is not None and str(event_object) == str_target)
                    or (event_raw_where is not None and str(event_raw_where) == str_target)
                    # CEN+ (WHO 25) wire WHERE is 2<object> (e.g. wire WHERE "21" for object 1)
                    or (event_object is not None and str_target == f"2{event_object}")
                )
                if not matches_str:
                    try:
                        int_target = int(target_address)
                        int_object = int(event_object) if event_object is not None else None
                        int_raw_where = int(event_raw_where) if event_raw_where is not None else None
                        if (
                            int_object != int_target
                            and int_raw_where != int_target
                            and (int_object is None or str(int_target) != f"2{int_object}")
                        ):
                            return
                    except (ValueError, TypeError):
                        return

            await action(
                {
                    "trigger": {
                        **trigger_data,
                        "platform": "device",
                        "event": event_data,
                    }
                },
                event.context,
            )

    event_types = {
        "15": ("myhome_cen_event",),
        "25": ("myhome_cenplus_event",),
    }.get(family or "", ("myhome_cen_event", "myhome_cenplus_event"))
    unsubs = [hass.bus.async_listen(event_type, _handle_event) for event_type in event_types]

    def _unsubscribe_all() -> None:
        for unsub in unsubs:
            unsub()

    return _unsubscribe_all
