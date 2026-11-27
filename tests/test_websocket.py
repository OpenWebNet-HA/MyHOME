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
    WS_TYPE_CHECK_FRAME,
    _extract_gateway_info,
    _get_gateway_and_monitor,
    _matches_filter,
    async_setup_websocket_api,
    ws_bus_monitor_check_frame,
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
    assert WS_TYPE_CHECK_FRAME in hass.data[websocket_api.DOMAIN]

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

    res_max = schema_hist({"id": 10, "type": "myhome/bus_monitor/history", "limit": 1000})
    assert res_max["limit"] == 1000
    import voluptuous as vol

    for bad_limit in (0, 1001):
        with pytest.raises(vol.Invalid):
            schema_hist({"id": 11, "type": "myhome/bus_monitor/history", "limit": bad_limit})

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

    # 3. Filter by F422 interface-routed WHERE (11#4#02 vs local 11)
    ev_local = OWNEvent.parse("*2*0*11##")
    ev_routed = OWNEvent.parse("*2*0*11#4#02##")
    monitor.record_frame("rx", "*2*0*11##", ev_local)
    monitor.record_frame("rx", "*2*0*11#4#02##", ev_routed)

    mock_ws_connection.send_result.reset_mock()
    ws_bus_monitor_history(
        hass,
        mock_ws_connection,
        {"id": 4, "type": "myhome/bus_monitor/history", "who": "2", "where": "11#4#02"},
    )
    await hass.async_block_till_done()
    _, routed_res = mock_ws_connection.send_result.call_args[0]
    assert [f["raw"] for f in routed_res["frames"]] == ["*2*0*11#4#02##"]
    assert routed_res["frames"][0]["where"] == "11#4#02"


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

    # 5. Block WHO 4 dimension 22 split-unit IR frame
    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 25, "type": "myhome/bus_monitor/send", "frame": "*#4*1*22##"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once_with(
        25,
        websocket_api.ERR_INVALID_FORMAT,
        "WHO 4 dimension 22 in this form crashes the gateway's thermostat daemon "
        "and is blocked for safety",
    )

    # 5b. Block OWNd destructive crash frames (bare WHO 22 power, WHO 18 dim 52)
    for msg_id, bad_frame in ((29, "*22*1*1##"), (30, "*#18*51*#52*1##")):
        mock_ws_connection.send_error.reset_mock()
        mock_ws_connection.send_result.reset_mock()
        ws_bus_monitor_send(
            hass,
            mock_ws_connection,
            {"id": msg_id, "type": "myhome/bus_monitor/send", "frame": bad_frame},
        )
        await hass.async_block_till_done()
        mock_ws_connection.send_result.assert_not_called()
        mock_ws_connection.send_error.assert_called_once()
        err_args = mock_ws_connection.send_error.call_args.args
        assert err_args[0] == msg_id
        assert err_args[1] == websocket_api.ERR_INVALID_FORMAT
        assert bad_frame in err_args[2]

    # 6. Block signaling and syntactically invalid frames that start with * and end with ##
    for msg_id, bad_frame in ((26, "*#*1##"), (27, "*1*2##"), (28, "*foo*bar##")):
        mock_ws_connection.send_error.reset_mock()
        ws_bus_monitor_send(
            hass,
            mock_ws_connection,
            {"id": msg_id, "type": "myhome/bus_monitor/send", "frame": bad_frame},
        )
        await hass.async_block_till_done()
        mock_ws_connection.send_error.assert_called_once_with(
            msg_id,
            websocket_api.ERR_INVALID_FORMAT,
            f"Invalid OpenWebNet frame format: {bad_frame}",
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

    # 3. Non-colon MAC and config_data name fallback
    non_colon_gw = MagicMock()
    non_colon_gw.gateway = None
    non_colon_gw.config_entry.data = {
        "name": "F454",
        "firmware": "3.1.0",
        "mac": "000350AABBCC",
        "command_worker_count": 2,
    }
    non_colon_gw.mac = "000350AABBCC"
    non_colon_gw.sending_workers = []
    non_colon_gw.send_buffer = None
    non_colon_gw.is_connected = True

    nc_info = _extract_gateway_info(non_colon_gw)
    assert nc_info["model"] == "F454"
    assert nc_info["mac_prefix"] == "000350AA"
    assert nc_info["worker_count"] == 2
    assert nc_info["integration_version"] == INTEGRATION_VERSION
    assert "ownd_version" in nc_info

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

    # 8. Real ConfigEntry with MappingProxyType data (#832)
    from types import MappingProxyType

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    real_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "name": "Legrand 067292",
            "host": "192.0.2.50",
            "port": 20000,
            "firmware": "1.2.3",
            "mac": "00:03:50:aa:bb:cc",
            "command_worker_count": 2,
        },
    )
    assert isinstance(real_entry.data, MappingProxyType)
    gw_mp = MagicMock()
    gw_mp.gateway = None
    gw_mp.model = None
    gw_mp.mac = None
    gw_mp.sending_workers = []
    gw_mp.send_buffer = None
    gw_mp.config_entry = real_entry
    mp_info = _extract_gateway_info(gw_mp)
    assert mp_info["model"] == "Legrand 067292"
    assert mp_info["host"] == "192.0.2.50"
    assert mp_info["port"] == 20000
    assert mp_info["firmware"] == "1.2.3"
    assert mp_info["mac_prefix"] == "00:03:50"
    assert mp_info["worker_count"] == 2


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
    trace.assert_called_once_with(gateway_mac="00:03:50:aa:bb:cc", hass=hass)
    mock_ws_connection.send_result.assert_called_once_with(77, {"mac": "00:03:50:aa:bb:cc", "frames": [{"raw": "*2*1*21##"}]})


async def test_ws_check_frame_schema_and_non_admin_access(hass: HomeAssistant, attach_gateway):
    """myhome/bus_monitor/check_frame schema validates input and is read-only for non-admin users."""
    import voluptuous as vol

    schema_check = ws_bus_monitor_check_frame._ws_schema
    parsed_msg = schema_check(
        {"id": 1, "type": WS_TYPE_CHECK_FRAME, "frame": "*1*1*12##", "mac": None}
    )
    assert parsed_msg["frame"] == "*1*1*12##"
    assert parsed_msg["mac"] is None
    with pytest.raises(vol.Invalid):
        schema_check({"id": 2, "type": WS_TYPE_CHECK_FRAME})

    from types import SimpleNamespace

    gw = SimpleNamespace(
        _model_key="F461",
        mac="00:03:50:aa:bb:cc",
        gateway=SimpleNamespace(model_name="F461", model="F461"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )
    attach_gateway("00:03:50:aa:bb:cc", gw, BusMonitor(maxlen=5))

    conn = MagicMock(spec=websocket_api.ActiveConnection)
    conn.user.is_admin = False
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()

    ws_bus_monitor_check_frame(
        hass,
        conn,
        {"id": 101, "type": WS_TYPE_CHECK_FRAME, "frame": "*1*1*12##"},
    )
    await hass.async_block_till_done()
    conn.send_error.assert_not_called()
    conn.send_result.assert_called_once()
    msg_id, res = conn.send_result.call_args[0]
    assert msg_id == 101
    assert res["valid"] is True
    assert res["blocked"] is False
    assert res["severity"] is None
    assert res["rule_id"] is None
    assert res["target_model"] == "F461"


async def test_ws_check_frame_blanket_gateway_delegated_warnings_and_not_found(
    hass: HomeAssistant,
    mock_ws_connection,
    attach_gateway,
):
    """myhome/bus_monitor/check_frame reports blanket blocks, gateway blocks, delegation, warnings, and not_found."""
    from types import SimpleNamespace

    mh202 = SimpleNamespace(
        _model_key="MH202",
        mac="00:03:50:00:02:02",
        gateway=SimpleNamespace(model_name="MH202", model="MH202"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )
    f461 = SimpleNamespace(
        _model_key="F461",
        mac="00:03:50:00:04:61",
        gateway=SimpleNamespace(model_name="F461", model="F461"),
        config_entry=None,
        _delegated_target=lambda msg: mh202 if getattr(msg, "who", None) == 4 else None,
        _failover_target=lambda _msg: None,
    )
    h4684 = SimpleNamespace(
        _model_key="H4684",
        mac="00:03:50:00:46:84",
        gateway=SimpleNamespace(model_name="H4684", model="H4684"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )
    generic = SimpleNamespace(
        _model_key="",
        mac="00:03:50:00:99:99",
        gateway=SimpleNamespace(model_name="Generic", model="Generic"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )

    attach_gateway("00:03:50:00:04:61", f461, BusMonitor(maxlen=5))
    attach_gateway("00:03:50:00:02:02", mh202, BusMonitor(maxlen=5))
    attach_gateway("00:03:50:00:46:84", h4684, BusMonitor(maxlen=5))
    attach_gateway("00:03:50:00:99:99", generic, BusMonitor(maxlen=5))

    # 1. Blanket crash block
    ws_bus_monitor_check_frame(
        hass,
        mock_ws_connection,
        {"id": 1, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:00:04:61", "frame": "*#4*1*22##"},
    )
    await hass.async_block_till_done()
    _, res1 = mock_ws_connection.send_result.call_args[0]
    assert res1["blocked"] is True
    assert res1["severity"] == "crash"
    assert res1["scope"] == "blanket"
    assert res1["rule_id"] == "who4_dim22_no_actuator"

    # 2. Per-gateway crash block on H4684
    mock_ws_connection.send_result.reset_mock()
    ws_bus_monitor_check_frame(
        hass,
        mock_ws_connection,
        {"id": 2, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:00:46:84", "frame": "*#13**10##"},
    )
    await hass.async_block_till_done()
    _, res2 = mock_ws_connection.send_result.call_args[0]
    assert res2["blocked"] is True
    assert res2["severity"] == "crash"
    assert res2["scope"] == "gateway"
    assert res2["rule_id"] == "who13_network_dims_10_11_12"
    assert res2["target_model"] == "H4684"
    assert res2["delegated_from"] is None
    assert "H4684" in res2["affected_models"]

    # 3. Delegated target reporting: F461 delegating WHO 4 -> MH202
    mock_ws_connection.send_result.reset_mock()
    ws_bus_monitor_check_frame(
        hass,
        mock_ws_connection,
        {"id": 3, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:00:04:61", "frame": "*#4*001##"},
    )
    await hass.async_block_till_done()
    _, res3 = mock_ws_connection.send_result.call_args[0]
    assert res3["blocked"] is True
    assert res3["severity"] == "crash"
    assert res3["rule_id"] == "mh202_who4_all_probes_001_099"
    assert res3["target_model"] == "MH202"
    assert res3["delegated_from"] == "F461"

    # 4. Amber no-reply warning on H4684
    mock_ws_connection.send_result.reset_mock()
    ws_bus_monitor_check_frame(
        hass,
        mock_ws_connection,
        {"id": 4, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:00:46:84", "frame": "*1*19*31##"},
    )
    await hass.async_block_till_done()
    _, res4 = mock_ws_connection.send_result.call_args[0]
    assert res4["valid"] is True
    assert res4["blocked"] is False
    assert res4["severity"] == "warning"
    assert res4["rule_id"] == "noreply_who1_what19"

    # 5. Amber warning on unknown (Generic) gateway for model-specific crash rule
    mock_ws_connection.send_result.reset_mock()
    ws_bus_monitor_check_frame(
        hass,
        mock_ws_connection,
        {"id": 5, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:00:99:99", "frame": "*#13**10##"},
    )
    await hass.async_block_till_done()
    _, res5 = mock_ws_connection.send_result.call_args[0]
    assert res5["valid"] is True
    assert res5["blocked"] is False
    assert res5["severity"] == "warning"
    assert res5["rule_id"] == "unknown_gateway_model_specific"
    assert "H4684" in res5["affected_models"]
    assert "MH200" in res5["affected_models"]

    # 6. Unknown MAC -> ERR_NOT_FOUND
    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_check_frame(
        hass,
        mock_ws_connection,
        {"id": 6, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:ff:ff:ff", "frame": "*1*1*12##"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_error.assert_called_once()
    err_args = mock_ws_connection.send_error.call_args[0]
    assert err_args[0] == 6
    assert err_args[1] == websocket_api.ERR_NOT_FOUND


async def test_ws_info_includes_model_key_and_crash_protections(
    hass: HomeAssistant,
    mock_ws_connection,
    attach_gateway,
):
    """myhome/bus_monitor/info includes normalized model_key and crash_protections metadata."""
    from types import SimpleNamespace

    monitor = BusMonitor(maxlen=50)
    gw = SimpleNamespace(
        _model_key="MH200N",
        mac="00:03:50:11:22:33",
        is_connected=True,
        sending_workers=[],
        send_buffer=None,
        config_entry=SimpleNamespace(data={}),
        gateway=SimpleNamespace(
            model_name="MH200N",
            model="MH200N",
            manufacturer="BTicino S.p.A.",
            firmware="2.0.1",
            host="10.0.0.1",
            port=20000,
            profile=None,
        ),
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )
    attach_gateway("00:03:50:11:22:33", gw, monitor)

    ws_bus_monitor_info(
        hass,
        mock_ws_connection,
        {"id": 42, "type": "myhome/bus_monitor/info", "mac": "00:03:50:11:22:33"},
    )
    await hass.async_block_till_done()
    mock_ws_connection.send_result.assert_called_once()
    msg_id, result = mock_ws_connection.send_result.call_args[0]
    assert msg_id == 42
    gw_info = result["gateway"]
    assert gw_info["model_key"] == "MH200N"
    prot = gw_info["crash_protections"]
    assert prot["model_key"] == "MH200N"
    assert prot["model_known"] is True
    assert prot["blanket_rule_count"] >= 6
    rule_ids = {r["rule_id"] for r in prot["gateway_rules"]}
    assert "who13_dim50" in rule_ids
    assert "who22_bare_what_33_44_45" in rule_ids
    assert isinstance(prot["delegated_peers"], list)


async def test_ws_send_refuses_delegated_crash_frame_with_routing_context(
    hass: HomeAssistant,
    mock_ws_connection,
    attach_gateway,
):
    """ws_bus_monitor_send refuses a delegated crash frame and names both target and primary gateways."""
    from types import SimpleNamespace

    mh202 = SimpleNamespace(
        _model_key="MH202",
        mac="00:03:50:00:02:02",
        gateway=SimpleNamespace(model_name="MH202", model="MH202"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )
    f461 = SimpleNamespace(
        _model_key="F461",
        mac="00:03:50:00:04:61",
        gateway=SimpleNamespace(model_name="F461", model="F461"),
        config_entry=None,
        _delegated_target=lambda msg: mh202 if getattr(msg, "who", None) == 4 else None,
        _failover_target=lambda _msg: None,
        send=AsyncMock(),
        send_status_request=AsyncMock(),
    )
    attach_gateway("00:03:50:00:04:61", f461, BusMonitor(maxlen=5))

    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {
            "id": 55,
            "type": "myhome/bus_monitor/send",
            "mac": "00:03:50:00:04:61",
            "frame": "*#4*001##",
        },
    )
    await hass.async_block_till_done()

    mock_ws_connection.send_result.assert_not_called()
    mock_ws_connection.send_error.assert_called_once()
    err_id, err_code, err_msg = mock_ws_connection.send_error.call_args[0]
    assert err_id == 55
    assert err_code == websocket_api.ERR_INVALID_FORMAT
    assert "MH202 (routed via F461)" in err_msg
    f461.send.assert_not_awaited()
    f461.send_status_request.assert_not_awaited()


async def test_ws_info_unknown_and_onboarding_models_emit_empty_model_key(
    hass: HomeAssistant,
    mock_ws_connection,
    attach_gateway,
):
    """Finding 2: _extract_gateway_info and ws_bus_monitor_info gate model_key on model_known."""
    from types import SimpleNamespace

    for idx, (gw_model, title, direct_key) in enumerate(
        (
            ("Custom (IP Gateway)", "Custom (IP Gateway) Gateway", ""),
            ("Unknown model", "Unknown model Gateway", ""),
            ("MyHOME Gateway", "MyHOME Gateway", ""),
            ("Generic", "Basement Gateway", ""),
            ("MyHOME 192.168.34.88", "MyHOME 192.168.34.88", ""),
            ("ExperimentalX99", "ExperimentalX99 Gateway", "EXPERIMENTALX99"),
        ),
        start=1,
    ):
        mac = f"00:03:50:aa:bb:{idx:02x}"
        gw = SimpleNamespace(
            _model_key=direct_key,
            mac=mac,
            is_connected=True,
            sending_workers=[],
            send_buffer=None,
            config_entry=SimpleNamespace(data={"name": gw_model}, title=title),
            gateway=SimpleNamespace(
                model_name=gw_model,
                model=gw_model,
                manufacturer="BTicino S.p.A.",
                firmware="",
                host="192.168.34.88",
                port=20000,
                profile=None,
            ),
            _delegated_target=lambda _msg: None,
            _failover_target=lambda _msg: None,
        )
        info = _extract_gateway_info(gw)
        assert info["model_key"] == ""
        assert info["crash_protections"]["model_known"] is False
        if not direct_key:
            assert info["crash_protections"]["model_key"] == ""

        attach_gateway(mac, gw, BusMonitor(maxlen=5))
        mock_ws_connection.send_result.reset_mock()
        ws_bus_monitor_info(
            hass,
            mock_ws_connection,
            {"id": 200 + idx, "type": "myhome/bus_monitor/info", "mac": mac},
        )
        await hass.async_block_till_done()
        _, res = mock_ws_connection.send_result.call_args[0]
        assert res["gateway"]["model_key"] == ""
        assert res["gateway"]["crash_protections"]["model_known"] is False


async def test_ws_check_and_send_unparseable_crash_frames_report_crash_not_invalid_format(
    hass: HomeAssistant,
    mock_ws_connection,
    attach_gateway,
):
    """Finding 4: Bare *#4*2*#22##, leading-zero dim 22, and bare *#18*51*#52## report crash reasons over WS."""
    from types import SimpleNamespace

    f461 = SimpleNamespace(
        _model_key="F461",
        mac="00:03:50:00:04:61",
        gateway=SimpleNamespace(model_name="F461", model="F461"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
        send=AsyncMock(),
        send_status_request=AsyncMock(),
    )
    attach_gateway("00:03:50:00:04:61", f461, BusMonitor(maxlen=5))

    for idx, frame in enumerate(("*#4*2*#22##", "*#04*2*#22##", "*#4*2*#022##"), start=1):
        mock_ws_connection.send_result.reset_mock()
        ws_bus_monitor_check_frame(
            hass,
            mock_ws_connection,
            {"id": 300 + idx, "type": WS_TYPE_CHECK_FRAME, "mac": "00:03:50:00:04:61", "frame": frame},
        )
        await hass.async_block_till_done()
        _, check_res = mock_ws_connection.send_result.call_args[0]
        assert check_res["blocked"] is True
        assert check_res["severity"] == "crash"
        assert check_res["rule_id"] == "who4_dim22_no_actuator"

        mock_ws_connection.send_error.reset_mock()
        ws_bus_monitor_send(
            hass,
            mock_ws_connection,
            {"id": 400 + idx, "type": "myhome/bus_monitor/send", "mac": "00:03:50:00:04:61", "frame": frame},
        )
        await hass.async_block_till_done()
        mock_ws_connection.send_error.assert_called_once_with(
            400 + idx,
            websocket_api.ERR_INVALID_FORMAT,
            "WHO 4 dimension 22 in this form crashes the gateway's thermostat daemon "
            "and is blocked for safety",
        )

    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 450, "type": "myhome/bus_monitor/send", "mac": "00:03:50:00:04:61", "frame": "*#18*51*#52##"},
    )
    await hass.async_block_till_done()
    _, err_code, err_msg = mock_ws_connection.send_error.call_args[0]
    assert err_code == websocket_api.ERR_INVALID_FORMAT
    assert "Refusing to send *#18*51*#52## on F461:" in err_msg
    f461.send.assert_not_awaited()

    # Delegated peer resolution also applies to unparseable crash frames (*#4*001*#20## -> MH202)
    mh202_peer = SimpleNamespace(
        _model_key="MH202",
        mac="00:03:50:00:02:02",
        gateway=SimpleNamespace(model_name="MH202", model="MH202"),
        config_entry=None,
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
    )
    f461._delegated_target = lambda msg: mh202_peer if getattr(msg, "who", None) == 4 else None
    mock_ws_connection.send_error.reset_mock()
    ws_bus_monitor_send(
        hass,
        mock_ws_connection,
        {"id": 451, "type": "myhome/bus_monitor/send", "mac": "00:03:50:00:04:61", "frame": "*#4*001*#20##"},
    )
    await hass.async_block_till_done()
    _, del_err_code, del_err_msg = mock_ws_connection.send_error.call_args[0]
    assert del_err_code == websocket_api.ERR_INVALID_FORMAT
    assert "MH202 (routed via F461)" in del_err_msg
    f461.send.assert_not_awaited()
