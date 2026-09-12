"""WebSocket API for MyHOME OpenWebNet integration.

Provides real-time bus streaming, historical frame inspection, and diagnostic
injection for the Lovelace bus monitor card.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from OWNd.message import OWNMessage

from .bus_monitor import BusFrame, BusMonitor
from .const import (
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_WORKER_COUNT,
    DOMAIN,
    INTEGRATION_VERSION,
    get_ownd_version,
)

_LOGGER = logging.getLogger(__name__)

WS_TYPE_HISTORY = "myhome/bus_monitor/history"
WS_TYPE_STREAM = "myhome/bus_monitor/stream"
WS_TYPE_SEND = "myhome/bus_monitor/send"
WS_TYPE_CLEAR = "myhome/bus_monitor/clear"
WS_TYPE_INFO = "myhome/bus_monitor/info"

SCHEMA_WS_INFO = {
    vol.Required("type"): WS_TYPE_INFO,
    vol.Optional("mac"): vol.Any(cv.string, None),
}

SCHEMA_WS_HISTORY = {
    vol.Required("type"): WS_TYPE_HISTORY,
    vol.Optional("mac"): vol.Any(cv.string, None),
    vol.Optional("limit", default=100): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
    vol.Optional("who"): vol.Any(cv.string, vol.Coerce(int), None),
    vol.Optional("where"): vol.Any(cv.string, None),
    vol.Optional("direction"): vol.Any(vol.In(["rx", "tx", "ack", "nack", "all"]), None),
}

SCHEMA_WS_STREAM = {
    vol.Required("type"): WS_TYPE_STREAM,
    vol.Optional("mac"): vol.Any(cv.string, None),
    vol.Optional("who"): vol.Any(cv.string, vol.Coerce(int), None),
    vol.Optional("where"): vol.Any(cv.string, None),
    vol.Optional("direction"): vol.Any(vol.In(["rx", "tx", "ack", "nack", "all"]), None),
}

SCHEMA_WS_SEND = {
    vol.Required("type"): WS_TYPE_SEND,
    vol.Required("frame"): cv.string,
    vol.Optional("mac"): vol.Any(cv.string, None),
}

SCHEMA_WS_CLEAR = {
    vol.Required("type"): WS_TYPE_CLEAR,
    vol.Optional("mac"): vol.Any(cv.string, None),
}


def _extract_gateway_info(gw: Optional[Any]) -> dict[str, Any]:
    """Safely extract JSON-serializable gateway runtime and hardware configuration."""
    if gw is None:
        return {}

    raw_gw = getattr(gw, "gateway", None)
    model = ""
    manufacturer = "BTicino"
    firmware = ""
    host = ""
    port: Optional[int] = 20000
    profile = None

    if raw_gw is not None:
        m_name = getattr(raw_gw, "model_name", None)
        if isinstance(m_name, str):
            model = m_name
        elif isinstance(getattr(raw_gw, "model", None), str):
            model = raw_gw.model
        m_manuf = getattr(raw_gw, "manufacturer", None)
        if isinstance(m_manuf, str):
            manufacturer = m_manuf
        m_fw = getattr(raw_gw, "firmware", None)
        if isinstance(m_fw, str):
            firmware = m_fw
        m_host = getattr(raw_gw, "host", None)
        if isinstance(m_host, str):
            host = m_host
        m_port = getattr(raw_gw, "port", None)
        if isinstance(m_port, int):
            port = m_port
        profile = getattr(raw_gw, "profile", None)

    config_entry = getattr(gw, "config_entry", None)
    config_data = getattr(config_entry, "data", {}) if config_entry else {}
    if not isinstance(config_data, dict):
        config_data = {}

    if not model and isinstance(config_data.get(CONF_NAME), str):
        model = config_data[CONF_NAME]
    if not model and isinstance(getattr(gw, "model", None), str):
        model = gw.model
    if not model:
        model = "Generic"

    if not host and isinstance(config_data.get(CONF_HOST), str):
        host = config_data[CONF_HOST]
    if port == 20000 and isinstance(config_data.get(CONF_PORT), int):
        port = config_data[CONF_PORT]
    if not firmware and isinstance(config_data.get(CONF_FIRMWARE), str):
        firmware = config_data[CONF_FIRMWARE]

    transport_type = config_data.get("transport_type") or getattr(gw, "transport_type", None)
    is_serial = isinstance(transport_type, str) and transport_type == "serial"

    raw_serial = (
        config_data.get("serial_port")
        or config_data.get("device")
        or getattr(raw_gw, "serial_port", None)
        or getattr(gw, "serial_port", None)
    )
    serial_port: Optional[str] = raw_serial if isinstance(raw_serial, str) else None

    # Check if host or port was used to store serial device path (e.g. /dev/ttyUSB0 or COM3)
    raw_port = getattr(raw_gw, "port", None)
    cfg_port = config_data.get(CONF_PORT)
    if not serial_port:
        if isinstance(cfg_port, str) and not cfg_port.isdigit():
            serial_port = cfg_port
        elif isinstance(raw_port, str) and not raw_port.isdigit():
            serial_port = raw_port
        elif is_serial and isinstance(host, str) and host:
            serial_port = host

    if is_serial or serial_port:
        host = ""
        port = None

    mac_val = getattr(gw, "mac", None)
    if not isinstance(mac_val, str):
        mac_val = config_data.get(CONF_MAC)
    mac_addr = mac_val if isinstance(mac_val, str) else ""

    mac_prefix = (
        ":".join(mac_addr.split(":")[:3])
        if ":" in mac_addr
        else (mac_addr[:8] if mac_addr else "Unknown")
    )

    queue_pacing = 0.0
    if profile is not None and isinstance(getattr(profile, "command_queue_delay", None), (int, float)):
        queue_pacing = float(profile.command_queue_delay)

    workers = getattr(gw, "sending_workers", None)
    worker_count = len(workers) if isinstance(workers, list) else 0
    if worker_count == 0 and isinstance(config_data.get(CONF_WORKER_COUNT), int):
        worker_count = config_data[CONF_WORKER_COUNT]
    if worker_count == 0:
        worker_count = 1

    send_buffer = getattr(gw, "send_buffer", None)
    queue_depth = 0
    if send_buffer is not None:
        try:
            q_size = send_buffer.qsize()
            if isinstance(q_size, int):
                queue_depth = q_size
        except Exception:
            queue_depth = 0

    is_connected = bool(getattr(gw, "is_connected", False))

    return {
        "model": model,
        "manufacturer": manufacturer,
        "firmware": firmware,
        "host": host,
        "port": port,
        "serial_port": serial_port,
        "mac_prefix": mac_prefix,
        "queue_pacing": queue_pacing,
        "worker_count": worker_count,
        "queue_depth": queue_depth,
        "is_connected": is_connected,
        "integration_version": INTEGRATION_VERSION,
        "ownd_version": get_ownd_version(),
    }


def _get_gateway_and_monitor(
    hass: HomeAssistant, mac: Optional[str] = None
) -> tuple[Optional[Any], Optional[BusMonitor]]:
    """Retrieve the gateway handler and bus monitor for a given MAC or the primary gateway."""
    domain_data = hass.data.get(DOMAIN, {})
    if not isinstance(domain_data, dict):
        return None, None

    # If MAC is provided, look up specifically
    if mac:
        try:
            formatted_mac = dr.format_mac(mac)
        except Exception:
            formatted_mac = mac

        for key, val in domain_data.items():
            if isinstance(val, dict):
                norm_key = key
                if isinstance(key, str) and (":" in key or len(key) == 12):
                    try:
                        norm_key = dr.format_mac(key)
                    except Exception:
                        norm_key = key
                if key == mac or key == formatted_mac or norm_key == formatted_mac:
                    gw = val.get(CONF_ENTITY)
                    bm = val.get("bus_monitor") or getattr(gw, "bus_monitor", None)
                    if gw or bm:
                        return gw, bm

    # Fallback to the first available gateway entry
    for key, val in domain_data.items():
        if isinstance(val, dict) and (CONF_ENTITY in val or "bus_monitor" in val):
            gw = val.get(CONF_ENTITY)
            bm = val.get("bus_monitor") or getattr(gw, "bus_monitor", None)
            if gw or bm:
                return gw, bm

    return None, None


def _matches_filter(
    frame: dict[str, Any] | BusFrame,
    who: Optional[Any] = None,
    where: Optional[str] = None,
    direction: Optional[str] = None,
) -> bool:
    """Check if a frame matches filter criteria."""
    raw_who = getattr(frame, "who", None) if isinstance(frame, BusFrame) else frame.get("who")
    raw_where = getattr(frame, "where", None) if isinstance(frame, BusFrame) else frame.get("where")
    f_dir = (getattr(frame, "direction", "") if isinstance(frame, BusFrame) else frame.get("direction", "")).lower()
    is_ack = getattr(frame, "is_ack", False) if isinstance(frame, BusFrame) else frame.get("is_ack", False)
    is_nack = getattr(frame, "is_nack", False) if isinstance(frame, BusFrame) else frame.get("is_nack", False)

    if direction and direction != "all":
        d_lower = direction.lower()
        if d_lower in ("rx", "tx") and f_dir != d_lower:
            return False
        if d_lower == "ack" and not is_ack:
            return False
        if d_lower == "nack" and not is_nack:
            return False

    if who is not None and str(who) != "all":
        if raw_who is None or str(who) != str(raw_who):
            return False

    if where is not None and str(where) != "":
        if raw_where is None or str(where) != str(raw_where):
            return False

    return True


@websocket_api.websocket_command(SCHEMA_WS_HISTORY)
@websocket_api.async_response
async def ws_bus_monitor_history(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return historical bus frames from the circular ring buffer."""
    gw, monitor = _get_gateway_and_monitor(hass, msg.get("mac"))
    if monitor is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            "No active MyHOME gateway or bus monitor found",
        )
        return

    limit = msg.get("limit", 100)
    who = msg.get("who")
    where = msg.get("where")
    direction = msg.get("direction", "all")

    raw_frames = monitor.get_recent_frames(limit=monitor.maxlen)
    filtered = [
        f for f in raw_frames if _matches_filter(f, who=who, where=where, direction=direction)
    ]

    # Return newest frames up to requested limit
    if len(filtered) > limit:
        filtered = filtered[-limit:]

    connection.send_result(
        msg["id"],
        {
            "frames": filtered,
            "stats": monitor.get_stats(),
            "gateway": _extract_gateway_info(gw),
        },
    )


@websocket_api.websocket_command(SCHEMA_WS_STREAM)
@websocket_api.async_response
async def ws_bus_monitor_stream(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Subscribe to real-time bus monitor frames."""
    _, monitor = _get_gateway_and_monitor(hass, msg.get("mac"))
    if monitor is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            "No active MyHOME gateway or bus monitor found",
        )
        return

    who = msg.get("who")
    where = msg.get("where")
    direction = msg.get("direction", "all")

    @callback
    def forward_frame(frame: BusFrame) -> None:
        if _matches_filter(frame, who=who, where=where, direction=direction):
            connection.send_message(
                websocket_api.event_message(msg["id"], frame.to_dict())
            )

    unsub = monitor.subscribe(forward_frame)
    connection.subscriptions[msg["id"]] = unsub
    connection.send_result(msg["id"])


@websocket_api.websocket_command(SCHEMA_WS_SEND)
@websocket_api.async_response
async def ws_bus_monitor_send(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send an OpenWebNet diagnostic frame directly to the gateway."""
    gateway, _ = _get_gateway_and_monitor(hass, msg.get("mac"))
    if gateway is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            "No active MyHOME gateway found to transmit frame",
        )
        return

    frame_str = msg["frame"].strip()
    if not (frame_str.startswith("*") and frame_str.endswith("##")):
        connection.send_error(
            msg["id"],
            websocket_api.ERR_INVALID_FORMAT,
            f"Invalid OpenWebNet frame format: {frame_str}",
        )
        return

    try:
        parsed = OWNMessage.parse(frame_str)
        if parsed is None:
            parsed = OWNMessage(frame_str)
        await gateway.send(parsed)
    except Exception as ex:  # pylint: disable=broad-except
        _LOGGER.error("Failed to transmit frame %s via WebSocket: %s", frame_str, ex)
        connection.send_error(
            msg["id"],
            websocket_api.ERR_UNKNOWN_ERROR,
            f"Failed to transmit frame: {ex}",
        )
        return

    connection.send_result(
        msg["id"],
        {"success": True, "frame": frame_str},
    )


@websocket_api.websocket_command(SCHEMA_WS_CLEAR)
@websocket_api.async_response
async def ws_bus_monitor_clear(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Clear the in-memory bus monitor ring buffer."""
    _, monitor = _get_gateway_and_monitor(hass, msg.get("mac"))
    if monitor is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            "No active MyHOME gateway or bus monitor found",
        )
        return

    monitor.clear()
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(SCHEMA_WS_INFO)
@websocket_api.async_response
async def ws_bus_monitor_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return runtime gateway telemetry and buffer stats."""
    gw, monitor = _get_gateway_and_monitor(hass, msg.get("mac"))
    if monitor is None and gw is None:
        connection.send_error(
            msg["id"],
            websocket_api.ERR_NOT_FOUND,
            "No active MyHOME gateway or bus monitor found",
        )
        return

    connection.send_result(
        msg["id"],
        {
            "stats": monitor.get_stats() if monitor else {},
            "gateway": _extract_gateway_info(gw),
        },
    )


@callback
def async_setup_websocket_api(hass: HomeAssistant) -> None:
    """Register all MyHOME WebSocket commands."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    ws_handlers = hass.data.get(websocket_api.DOMAIN, {})
    if domain_data.get("_ws_registered") and WS_TYPE_HISTORY in ws_handlers:
        return

    websocket_api.async_register_command(hass, ws_bus_monitor_history)
    websocket_api.async_register_command(hass, ws_bus_monitor_stream)
    websocket_api.async_register_command(hass, ws_bus_monitor_send)
    websocket_api.async_register_command(hass, ws_bus_monitor_clear)
    websocket_api.async_register_command(hass, ws_bus_monitor_info)

    domain_data["_ws_registered"] = True
    _LOGGER.info("Registered MyHOME WebSocket API commands for Bus Monitor")
