"""Tests for MyHOME WebSocket API commands."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent

from custom_components.myhome.bus_monitor import BusFrame, BusMonitor
from custom_components.myhome.const import (
    CONF_ENTITY,
    DATA_OWND_VERSION,
    DOMAIN,
    INTEGRATION_VERSION,
)
from custom_components.myhome.websocket import (
    _extract_gateway_info,
    _get_gateway_and_monitor,
    _matches_filter,
    async_setup_websocket_api,
    ws_bus_monitor_clear,
    ws_bus_monitor_history,
    ws_bus_monitor_info,
    ws_bus_monitor_send,
    ws_bus_monitor_stream,
)


@pytest.fixture
def mock_ws_connection():
    """Create a mock active WebSocket connection."""
    conn = MagicMock(spec=websocket_api.ActiveConnection)
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()
    conn.send_message = MagicMock()
    conn.subscriptions = {}
    return conn


async def test_websocket_registration_idempotent(hass: HomeAssistant):
    """Test that async_setup_websocket_api can be called multiple times safely."""
    async_setup_websocket_api(hass)
    assert hass.data[DOMAIN]["_ws_registered"] is True
    assert websocket_api.DOMAIN in hass.data
    assert "myhome/bus_monitor/history" in hass.data[websocket_api.DOMAIN]
    assert "myhome/bus_monitor/stream" in hass.data[websocket_api.DOMAIN]
    assert "myhome/bus_monitor/send" in hass.data[websocket_api.DOMAIN]
    assert "myhome/bus_monitor/clear" in hass.data[websocket_api.DOMAIN]
    assert "myhome/bus_monitor/info" in hass.data[websocket_api.DOMAIN]

    # Second call should be a no-op
    with patch("homeassistant.components.websocket_api.async_register_command") as mock_reg:
        async_setup_websocket_api(hass)
        mock_reg.assert_not_called()

    # If handlers dictionary lost the command, it safely re-registers
    del hass.data[websocket_api.DOMAIN]["myhome/bus_monitor/history"]
    async_setup_websocket_api(hass)
    assert "myhome/bus_monitor/history" in hass.data[websocket_api.DOMAIN]


async def test_websocket_schemas_validation():
    """Test schema validation for all bus monitor websocket commands."""
    from custom_components.myhome.websocket import (
        ws_bus_monitor_clear,
        ws_bus_monitor_history,
        ws_bus_monitor_info,
        ws_bus_monitor_send,
        ws_bus_monitor_stream,
    )

    # All handlers have _ws_schema attached by @websocket_command
    schema_hist = ws_bus_monitor_history._ws_schema
    res1 = schema_hist({"id": 1, "type": "myhome/bus_monitor/history", "mac": None})
    assert res1["mac"] is None
    assert res1["limit"] == 100

    schema_stream = ws_bus_monitor_stream._ws_schema
    res2 = schema_stream({"id": 2, "type": "myhome/bus_monitor/stream", "mac": None})
    assert res2["mac"] is None

    schema_send = ws_bus_monitor_send._ws_schema
    res3 = schema_send({"id": 3, "type": "myhome/bus_monitor/send", "frame": "*1*1*12##", "mac": None})
    assert res3["mac"] is None
    assert res3["frame"] == "*1*1*12##"

    schema_clear = ws_bus_monitor_clear._ws_schema
    res4 = schema_clear({"id": 4, "type": "myhome/bus_monitor/clear", "mac": None})
    assert res4["mac"] is None

    schema_info = ws_bus_monitor_info._ws_schema
    res5 = schema_info({"id": 5, "type": "myhome/bus_monitor/info", "mac": None})
    assert res5["mac"] is None

    # Nullable who, where, direction in history and stream schemas
    res_null_hist = schema_hist({
        "id": 6,
        "type": "myhome/bus_monitor/history",
        "mac": None,
        "who": None,
        "where": None,
        "direction": None,
    })
    assert res_null_hist["who"] is None
    assert res_null_hist["where"] is None
    assert res_null_hist["direction"] is None

    res_null_stream = schema_stream({
        "id": 7,
        "type": "myhome/bus_monitor/stream",
        "mac": None,
        "who": None,
        "where": None,
        "direction": None,
    })
    assert res_null_stream["who"] is None
    assert res_null_stream["where"] is None
    assert res_null_stream["direction"] is None


def test_get_gateway_and_monitor_runtime_data_first(hass: HomeAssistant, attach_gateway):
    """runtime_data is the source of truth; hass.data is only a legacy fallback."""
    monitor = BusMonitor(maxlen=10)
    gw = MagicMock()
    attach_gateway("00:03:50:aa:bb:cc", gw, monitor)
    # Poison the legacy mapping: it must not be consulted when runtime_data is set
    hass.data[DOMAIN]["00:03:50:aa:bb:cc"][CONF_ENTITY] = "stale"

    assert _get_gateway_and_monitor(hass) == (gw, monitor)
    assert _get_gateway_and_monitor(hass, "00:03:50:AA:BB:CC") == (gw, monitor)
    assert _get_gateway_and_monitor(hass, "000350aabbcc") == (gw, monitor)


def test_get_gateway_and_monitor_ignores_entries_not_set_up(hass: HomeAssistant, attach_gateway):
    """An entry without runtime_data is not set up; the legacy hass.data alias is never consulted."""
    monitor = BusMonitor(maxlen=10)
    gw = MagicMock()
    attach_gateway("00:03:50:aa:bb:cc", gw, monitor, legacy_only=True)
    assert _get_gateway_and_monitor(hass, "00:03:50:aa:bb:cc") == (None, None)
    assert _get_gateway_and_monitor(hass) == (None, None)


def test_get_gateway_and_monitor_never_substitutes_gateway(hass: HomeAssistant, attach_gateway):
    """A MAC filter must return that gateway or nothing, never another bus."""
    gw_a, gw_b = MagicMock(), MagicMock()
    attach_gateway("00:03:50:00:00:0a", gw_a, BusMonitor(maxlen=5))
    attach_gateway("00:03:50:00:00:0b", gw_b, BusMonitor(maxlen=5))

    assert _get_gateway_and_monitor(hass, "00:03:50:00:00:0b")[0] is gw_b
    assert _get_gateway_and_monitor(hass, "00:03:50:00:00:0a")[0] is gw_a
    assert _get_gateway_and_monitor(hass, "00:03:50:ff:ff:ff") == (None, None)
    # No filter -> first configured gateway
    assert _get_gateway_and_monitor(hass)[0] is gw_a


async def test_ws_history_no_gateway(hass: HomeAssistant, mock_ws_connection):
    """Test history request returns ERR_NOT_FOUND when no gateway is configured."""
    hass.data[DOMAIN] = {}
    ws_bus_monitor_history(hass, mock_ws_connection, {"id": 1, "type": "myhome/bus_monitor/history"})
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        1, websocket_api.ERR_NOT_FOUND, "No active MyHOME gateway or bus monitor found"
    )


async def test_ws_history_with_frames_and_filters(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """Test history request returns frames filtered by who, where, and direction."""
    monitor = BusMonitor(maxlen=100)
    attach_gateway("00:03:50:00:12:34", MagicMock(), monitor)

    # Record some frames
    ev1 = OWNEvent.parse("*1*1*12##")
    ev2 = OWNEvent.parse("*2*1*25##")
    ev3 = OWNEvent.parse("*1*0*12##")

    monitor.record_frame("rx", "*1*1*12##", ev1)
    monitor.record_frame("tx", "*2*1*25##", ev2)
    monitor.record_frame("rx", "*1*0*12##", ev3)

    # 1. Unfiltered request with limit
    ws_bus_monitor_history(
        hass,
        mock_ws_connection,
        {"id": 2, "type": "myhome/bus_monitor/history", "limit": 2},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once()
    msg_id, result = mock_ws_connection.send_result.call_args[0]
    assert msg_id == 2
    assert len(result["frames"]) == 2
    assert result["stats"]["captured"] == 3

    # 2. Filter by WHO=1 and where=12
    mock_ws_connection.send_result.reset_mock()
    ws_bus_monitor_history(
        hass,
        mock_ws_connection,
        {"id": 3, "type": "myhome/bus_monitor/history", "who": "1", "where": "12", "direction": "rx"},
    )
    await hass.async_block_till_done()
    _, result = mock_ws_connection.send_result.call_args[0]
    assert len(result["frames"]) == 2
    assert all(f["who"] == "1" and f["where"] == "12" and f["direction"] == "rx" for f in result["frames"])


async def test_ws_stream_subscription_and_dispatch(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """Test real-time bus stream subscription, frame dispatching, and filtering."""
    monitor = BusMonitor(maxlen=50)
    attach_gateway("00:03:50:00:12:34", MagicMock(), monitor)

    # Subscribe with filter: who=1
    ws_bus_monitor_stream(
        hass,
        mock_ws_connection,
        {"id": 10, "type": "myhome/bus_monitor/stream", "who": 1, "direction": "rx"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once_with(10)
    assert 10 in mock_ws_connection.subscriptions

    # Record matching frame
    ev1 = OWNEvent.parse("*1*1*14##")
    monitor.record_frame("rx", "*1*1*14##", ev1)

    mock_ws_connection.send_message.assert_called_once()
    sent = mock_ws_connection.send_message.call_args[0][0]
    assert sent["id"] == 10
    assert sent["type"] == "event"
    assert sent["event"]["raw"] == "*1*1*14##"

    # Record non-matching frame (who=2, automation)
    mock_ws_connection.send_message.reset_mock()
    ev2 = OWNEvent.parse("*2*1*21##")
    monitor.record_frame("rx", "*2*1*21##", ev2)
    mock_ws_connection.send_message.assert_not_called()

    # Unsubscribe
    unsub = mock_ws_connection.subscriptions[10]
    unsub()
    assert len(monitor._subscribers) == 0


async def test_ws_stream_no_gateway(hass: HomeAssistant, mock_ws_connection):
    """Test stream subscription returns error when no gateway is configured."""
    hass.data[DOMAIN] = {}
    ws_bus_monitor_stream(hass, mock_ws_connection, {"id": 11, "type": "myhome/bus_monitor/stream"})
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        11, websocket_api.ERR_NOT_FOUND, "No active MyHOME gateway or bus monitor found"
    )


async def test_ws_send_frame(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """Test transmitting an OpenWebNet frame via the WebSocket API."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    attach_gateway("00:03:50:00:12:34", gateway)

    # 1. Successful frame send
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 20, "type": "myhome/bus_monitor/send", "frame": "*1*1*12##"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once_with(
        20, {"success": True, "frame": "*1*1*12##"}
    )
    gateway.send.assert_awaited_once()

    # 2. Invalid frame format
    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 21, "type": "myhome/bus_monitor/send", "frame": "invalid_frame"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        21, websocket_api.ERR_INVALID_FORMAT, "Invalid OpenWebNet frame format: invalid_frame"
    )

    # 3. Gateway send error
    gateway.send.side_effect = ConnectionError("Gateway disconnected")
    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 22, "type": "myhome/bus_monitor/send", "frame": "*1*0*12##"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        22, websocket_api.ERR_UNKNOWN_ERROR, "Failed to transmit frame: Gateway disconnected"
    )

    # 4. Frame parse fallback to raw OWNMessage
    gateway.send.side_effect = None
    mock_ws_connection.send_result.reset_mock()
    with patch("custom_components.myhome.websocket.OWNMessage.parse", return_value=None):
        ws_bus_monitor_send(
            hass,
            mock_ws_connection,
            {"id": 24, "type": "myhome/bus_monitor/send", "frame": "*999*999*999##"},
        )
        await hass.async_block_till_done()
        mock_ws_connection.send_result.assert_called_once_with(
            24, {"success": True, "frame": "*999*999*999##"}
        )


async def test_ws_send_no_gateway(hass: HomeAssistant, mock_ws_connection):
    """Test send returns error when no gateway is configured."""
    hass.data[DOMAIN] = {}
    ws_bus_monitor_send(hass, mock_ws_connection, {"id": 23, "type": "myhome/bus_monitor/send", "frame": "*1*1*12##"})
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        23, websocket_api.ERR_NOT_FOUND, "No active MyHOME gateway found to transmit frame"
    )


async def test_ws_clear_buffer(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """Test clearing the bus monitor ring buffer."""
    monitor = BusMonitor(maxlen=50)
    monitor.record_frame("rx", "*1*1*12##")
    assert monitor.get_stats()["captured"] == 1

    entry = attach_gateway("00:03:50:00:12:34", MagicMock(), monitor)

    ws_bus_monitor_clear(hass, mock_ws_connection, {"id": 30, "type": "myhome/bus_monitor/clear"})
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once_with(30, {"success": True})
    assert monitor.get_stats()["captured"] == 0

    # Test error when no gateway
    entry.runtime_data = None
    hass.data[DOMAIN] = {}
    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_clear(hass, mock_ws_connection, {"id": 31, "type": "myhome/bus_monitor/clear"})
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        31, websocket_api.ERR_NOT_FOUND, "No active MyHOME gateway or bus monitor found"
    )


def test_matches_filter_helpers():
    """Test _matches_filter logic covering all branches."""
    frame = BusFrame(direction="rx", raw="*1*1*12##", parsed=OWNEvent.parse("*1*1*12##"))
    assert _matches_filter(frame, who="1", where="12", direction="rx") is True
    assert _matches_filter(frame, who="2") is False
    assert _matches_filter(frame, where="99") is False
    assert _matches_filter(frame, direction="tx") is False
    assert _matches_filter(frame, direction="all") is True

    frame_dict = frame.to_dict()
    assert _matches_filter(frame_dict, who=1, where="12", direction="rx") is True
    assert _matches_filter(frame_dict, who=4) is False


async def test_ws_history_returns_gateway_info(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """Test history request returns enriched gateway parameters."""
    monitor = BusMonitor(maxlen=100)
    mock_gw = MagicMock()
    mock_gw.gateway.model_name = "F454"
    mock_gw.gateway.manufacturer = "BTicino S.p.A."
    mock_gw.gateway.firmware = "1.0.42"
    mock_gw.gateway.host = "192.168.1.50"
    mock_gw.gateway.port = 20000
    mock_gw.mac = "00:03:50:ab:cd:ef"
    mock_gw.is_connected = True
    mock_profile = MagicMock()
    mock_profile.command_queue_delay = 0.05
    mock_gw.gateway.profile = mock_profile
    mock_gw.sending_workers = [MagicMock()]
    mock_send_buffer = MagicMock()
    mock_send_buffer.qsize.return_value = 2
    mock_gw.send_buffer = mock_send_buffer
    mock_gw.config_entry.data = {}
    attach_gateway("00:03:50:ab:cd:ef", mock_gw, monitor)
    hass.data[DOMAIN][DATA_OWND_VERSION] = "9.9.9"

    ws_bus_monitor_history(
        hass,
        mock_ws_connection,
        {"id": 4, "type": "myhome/bus_monitor/history"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once()
    msg_id, result = mock_ws_connection.send_result.call_args[0]
    assert msg_id == 4
    assert "gateway" in result
    gw_info = result["gateway"]
    assert gw_info["model"] == "F454"
    assert gw_info["manufacturer"] == "BTicino S.p.A."
    assert gw_info["firmware"] == "1.0.42"
    assert gw_info["host"] == "192.168.1.50"
    assert gw_info["port"] == 20000
    assert gw_info["mac_prefix"] == "00:03:50"
    assert gw_info["queue_pacing"] == 0.05
    assert gw_info["worker_count"] == 1
    assert gw_info["queue_depth"] == 2
    assert gw_info["is_connected"] is True
    assert gw_info["ownd_version"] == "9.9.9"
    assert gw_info["integration_version"] == INTEGRATION_VERSION


async def test_ws_info_endpoint(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """Test ws_bus_monitor_info endpoint returns runtime stats and gateway info."""
    monitor = BusMonitor(maxlen=100)
    monitor.record_frame("rx", "*1*1*12##")
    mock_gw = MagicMock()
    mock_gw.gateway.model_name = "MH200N"
    mock_gw.gateway.firmware = "2.0.1"
    mock_gw.mac = "00:03:50:11:22:33"
    mock_gw.gateway.host = "10.0.0.1"
    mock_gw.gateway.port = 20000
    mock_gw.is_connected = True
    mock_gw.sending_workers = [MagicMock(), MagicMock()]
    mock_gw.send_buffer = None
    mock_gw.config_entry.data = {}
    attach_gateway("00:03:50:11:22:33", mock_gw, monitor)

    ws_bus_monitor_info(
        hass,
        mock_ws_connection,
        {"id": 40, "type": "myhome/bus_monitor/info"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once()
    msg_id, result = mock_ws_connection.send_result.call_args[0]
    assert msg_id == 40
    assert result["stats"]["captured"] == 1
    assert result["gateway"]["model"] == "MH200N"
    assert result["gateway"]["worker_count"] == 2
    # No cached version resolved at setup -> "unknown", never a disk read on the loop
    assert result["gateway"]["ownd_version"] == "unknown"


async def test_ws_info_no_gateway(hass: HomeAssistant, mock_ws_connection):
    """Test ws_bus_monitor_info endpoint returns ERR_NOT_FOUND when no gateway is configured."""
    hass.data[DOMAIN] = {}
    ws_bus_monitor_info(
        hass,
        mock_ws_connection,
        {"id": 41, "type": "myhome/bus_monitor/info"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        41, websocket_api.ERR_NOT_FOUND, "No active MyHOME gateway or bus monitor found"
    )


def test_extract_gateway_info_edge_cases():
    """Test _extract_gateway_info with edge cases and serial connections."""
    # 1. None
    assert _extract_gateway_info(None) == {}

    # 2. Unconfigured MagicMock
    raw_mock = MagicMock()
    info = _extract_gateway_info(raw_mock)
    assert isinstance(info, dict)
    assert info["model"] == "Generic"
    assert info["queue_depth"] == 0
    assert info["queue_pacing"] == 0.0

    # 3. Serial Gateway
    serial_gw = MagicMock()
    serial_gw.gateway = None
    serial_gw.config_entry.data = {
        "name": "Legrand 3578 USB/Serial",
        "serial_port": "/dev/ttyUSB0",
        "firmware": "3.1.0",
        "mac": "SERIAL_GW_12345",
        "command_worker_count": 2,
    }
    serial_gw.mac = "SERIAL_GW_12345"
    serial_gw.sending_workers = []
    serial_gw.send_buffer = None
    serial_gw.is_connected = True

    s_info = _extract_gateway_info(serial_gw)
    assert s_info["model"] == "Legrand 3578 USB/Serial"
    assert s_info["serial_port"] == "/dev/ttyUSB0"
    assert s_info["mac_prefix"] == "SERIAL_G"
    assert s_info["worker_count"] == 2

    # 4. raw_gw.model attribute fallback
    gw_with_model_attr = MagicMock()
    gw_with_model_attr.gateway.model_name = None
    gw_with_model_attr.gateway.model = "MH200"
    gw_with_model_attr.config_entry = None
    assert _extract_gateway_info(gw_with_model_attr)["model"] == "MH200"

    # 5. gw.model attribute fallback
    gw_with_gw_model = MagicMock()
    gw_with_gw_model.gateway = None
    gw_with_gw_model.model = "F452"
    gw_with_gw_model.config_entry.data = {}
    assert _extract_gateway_info(gw_with_gw_model)["model"] == "F452"

    # 6. config_data host, port, worker count fallback
    gw_cfg = MagicMock()
    gw_cfg.gateway = None
    gw_cfg.config_entry.data = {
        "host": "192.168.1.99",
        "port": 20000,
        "firmware": "2.1",
        "command_worker_count": 3,
    }
    gw_cfg.mac = None
    gw_cfg.sending_workers = []
    cfg_info = _extract_gateway_info(gw_cfg)
    assert cfg_info["host"] == "192.168.1.99"
    assert cfg_info["port"] == 20000
    assert cfg_info["worker_count"] == 3

    # 7. send_buffer.qsize exception branch
    gw_buf_err = MagicMock()
    gw_buf_err.gateway = None
    gw_buf_err.send_buffer.qsize.side_effect = RuntimeError("Buffer failure")
    gw_buf_err.config_entry = None
    assert _extract_gateway_info(gw_buf_err)["queue_depth"] == 0

    # 8. Real serial config entry from config_flow (transport_type='serial', host='/dev/ttyUSB0')
    real_serial_gw = MagicMock()
    real_serial_gw.gateway = None
    real_serial_gw.config_entry.data = {
        "name": "Legrand 3578 Dongle",
        "host": "/dev/ttyUSB0",
        "port": 19200,
        "transport_type": "serial",
        "baudrate": 19200,
    }
    real_serial_gw.mac = None
    real_serial_gw.sending_workers = []
    rs_info = _extract_gateway_info(real_serial_gw)
    assert rs_info["model"] == "Legrand 3578 Dongle"
    assert rs_info["serial_port"] == "/dev/ttyUSB0"
    assert rs_info["host"] == ""
    assert rs_info["port"] is None
    assert rs_info["integration_version"] == INTEGRATION_VERSION
    assert "ownd_version" in rs_info

    # 9. String port on raw_gw (e.g. COM3)
    com_gw = MagicMock()
    com_gw.gateway.model_name = "Legrand 3578"
    com_gw.gateway.port = "COM3"
    com_gw.config_entry = None
    com_info = _extract_gateway_info(com_gw)
    assert com_info["serial_port"] == "COM3"
    assert com_info["host"] == ""
    assert com_info["port"] is None

    # 10. String port in config_data without transport_type
    gw_port_str = MagicMock()
    gw_port_str.gateway = None
    gw_port_str.config_entry.data = {"port": "/dev/ttyACM0"}
    assert _extract_gateway_info(gw_port_str)["serial_port"] == "/dev/ttyACM0"


async def test_mutating_commands_require_admin(hass: HomeAssistant, attach_gateway):
    """Injecting frames onto the bus and wiping the buffer are admin-only."""
    from homeassistant.exceptions import Unauthorized

    gateway = MagicMock()
    gateway.send = AsyncMock()
    attach_gateway("00:03:50:00:12:34", gateway, BusMonitor(maxlen=5))

    conn = MagicMock(spec=websocket_api.ActiveConnection)
    conn.user.is_admin = False
    for handler, msg in (
        (ws_bus_monitor_send, {"id": 1, "type": "myhome/bus_monitor/send", "frame": "*5*2*0##"}),
        (ws_bus_monitor_clear, {"id": 2, "type": "myhome/bus_monitor/clear"}),
    ):
        with pytest.raises(Unauthorized):
            handler(hass, conn, msg)
    gateway.send.assert_not_awaited()

    # Read-only commands stay available to any authenticated user
    ws_bus_monitor_history(hass, conn, {"id": 3, "type": "myhome/bus_monitor/history"})
    ws_bus_monitor_info(hass, conn, {"id": 4, "type": "myhome/bus_monitor/info"})
    ws_bus_monitor_stream(hass, conn, {"id": 5, "type": "myhome/bus_monitor/stream"})
    await hass.async_block_till_done()
    assert conn.send_result.call_count == 3


def test_cached_ownd_version_without_domain_data(hass: HomeAssistant):
    """The cached version helper never touches the filesystem, even when unset."""
    from custom_components.myhome.websocket import _cached_ownd_version

    hass.data.pop(DOMAIN, None)
    assert _cached_ownd_version(hass) == "unknown"
    hass.data[DOMAIN] = "not-a-dict"
    assert _cached_ownd_version(hass) == "unknown"


async def test_ws_cover_calibration_trace(hass: HomeAssistant, mock_ws_connection, attach_gateway):
    """myhome/cover/calibration_trace returns the selected gateway's recorded frames, and names it."""
    from custom_components.myhome.websocket import ws_cover_calibration_trace

    gw = MagicMock()
    gw.mac = "00:03:50:AA:BB:CC"
    attach_gateway(gw.mac, gw)
    with patch("custom_components.myhome.cover.get_last_calibration_trace", return_value=[{"raw": "*2*1*21##"}]) as trace:
        ws_cover_calibration_trace(hass, mock_ws_connection, {"id": 77, "type": "myhome/cover/calibration_trace"})
        await hass.async_block_till_done()
    trace.assert_called_once_with(gateway_mac="00:03:50:aa:bb:cc")
    mock_ws_connection.send_result.assert_called_once_with(77, {"mac": "00:03:50:aa:bb:cc", "frames": [{"raw": "*2*1*21##"}]})
