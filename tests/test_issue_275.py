"""Unit tests for Issue #275: WHO=5 filter support and generalized card field filtering."""
from unittest.mock import MagicMock

import pytest
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent, OWNSignaling

from custom_components.myhome.bus_monitor import BusFrame, BusMonitor
from custom_components.myhome.const import CONF_ENTITY, DOMAIN
from custom_components.myhome.websocket import (
    _matches_filter,
    ws_bus_monitor_history,
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


# Frontend regression coverage lives in tests/frontend/bus-monitor.test.mjs,
# where the shared view and both legacy card names are exercised in the DOM.


def test_backend_filter_matches_who_5():
    """Verify backend _matches_filter accurately matches WHO=5 frames."""
    alarm_msg = OWNEvent.parse("*5*1*0##")
    frame = BusFrame(direction="rx", raw="*5*1*0##", parsed=alarm_msg)

    # Frame who is 5
    assert frame.who == 5 or frame.who == "5"

    # Matches when who="5" or 5
    assert _matches_filter(frame, who="5") is True
    assert _matches_filter(frame, who=5) is True

    # Rejects when who is a different subsystem
    assert _matches_filter(frame, who="1") is False
    assert _matches_filter(frame, who="2") is False

    # Matches "all"
    assert _matches_filter(frame, who="all") is True


def test_backend_filter_ack_nack_direction():
    """Verify backend _matches_filter supports direction='ack' and 'nack'."""
    ack_frame = BusFrame(direction="rx", raw="*#*1##", parsed=OWNSignaling("*#*1##"))
    nack_frame = BusFrame(direction="rx", raw="*#*0##", parsed=OWNSignaling("*#*0##"))
    normal_frame = BusFrame(direction="rx", raw="*1*1*12##", parsed=OWNEvent.parse("*1*1*12##"))

    assert _matches_filter(ack_frame, direction="ack") is True
    assert _matches_filter(ack_frame, direction="nack") is False
    assert _matches_filter(ack_frame, direction="rx") is True

    assert _matches_filter(nack_frame, direction="nack") is True
    assert _matches_filter(nack_frame, direction="ack") is False
    assert _matches_filter(nack_frame, direction="rx") is True

    assert _matches_filter(normal_frame, direction="ack") is False
    assert _matches_filter(normal_frame, direction="nack") is False


async def test_ws_history_with_who_5(hass: HomeAssistant, mock_ws_connection):
    """Test history request returns frames filtered specifically by WHO=5."""
    monitor = BusMonitor(maxlen=100)
    gateway = MagicMock()
    mac = "00:03:50:00:12:34"
    hass.data[DOMAIN] = {
        mac: {
            CONF_ENTITY: gateway,
            "bus_monitor": monitor,
        }
    }

    # Record mixed frames: light (WHO=1), automation (WHO=2), alarm (WHO=5)
    ev_light = OWNEvent.parse("*1*1*12##")
    ev_cover = OWNEvent.parse("*2*1*21##")
    ev_alarm1 = OWNEvent.parse("*5*1*0##")
    ev_alarm2 = OWNEvent.parse("*5*0*0##")

    monitor.record_frame("rx", "*1*1*12##", ev_light)
    monitor.record_frame("tx", "*2*1*21##", ev_cover)
    monitor.record_frame("rx", "*5*1*0##", ev_alarm1)
    monitor.record_frame("tx", "*5*0*0##", ev_alarm2)

    # Filter specifically by WHO=5
    ws_bus_monitor_history(
        hass,
        mock_ws_connection,
        {"id": 42, "type": "myhome/bus_monitor/history", "who": "5"},
    )
    await hass.async_block_till_done()

    mock_ws_connection.send_result.assert_called_once()
    msg_id, result = mock_ws_connection.send_result.call_args[0]
    assert msg_id == 42
    assert len(result["frames"]) == 2
    assert all(f["who"] == "5" for f in result["frames"])
    assert result["frames"][0]["raw"] == "*5*1*0##"
    assert result["frames"][1]["raw"] == "*5*0*0##"
