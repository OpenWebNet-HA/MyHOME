# privacy-check: allow-samples - the redaction test feeds the diagnostics a household identity
"""Tests for MyHOME config entry diagnostics."""
from unittest.mock import MagicMock

import pytest
from homeassistant.const import CONF_MAC, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from custom_components.myhome.bus_monitor import BusMonitor
from custom_components.myhome.const import CONF_ENTITIES, CONF_ENTITY, DOMAIN, INTEGRATION_VERSION
from custom_components.myhome.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_without_gateway_handler(hass: HomeAssistant):
    """Test diagnostics output when gateway handler is not yet registered."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_entry_123"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "My Gateway"
    mock_entry.data = {
        CONF_MAC: "00:03:50:11:22:33",
        CONF_PASSWORD: "super_secret_password",
        "pin": "1234",
        "normal_field": "visible_value",
    }
    mock_entry.options = {
        "secret": "top_secret",
        "scan_interval": 30,
    }

    hass.data[DOMAIN] = {}

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    # Verify versions
    assert diag["integration_version"] == INTEGRATION_VERSION
    assert "ownd_version" in diag

    # Verify redactions
    assert diag["config_entry"]["entry_id"] == "**REDACTED**"
    assert diag["config_entry"]["title"] == "MyHOME Gateway"  # not the user's title
    assert diag["config_entry"]["data"][CONF_PASSWORD] == "**REDACTED**"
    assert diag["config_entry"]["data"]["pin"] == "**REDACTED**"
    assert diag["config_entry"]["data"]["normal_field"] == "visible_value"
    assert diag["config_entry"]["options"]["secret"] == "**REDACTED**"
    assert diag["config_entry"]["options"]["scan_interval"] == 30

    # Verify empty gateway info
    assert diag["gateway"] == {}
    assert diag["profile"] == {}
    assert diag["queue"] == {}
    assert diag["bus_monitor"] == {}
    assert diag["platforms"] == {}


@pytest.mark.asyncio
async def test_diagnostics_with_full_gateway_and_bus_monitor(hass: HomeAssistant):
    """Test diagnostics output when gateway, profile, queue, and bus monitor are active."""
    mac = "00:03:50:aa:bb:cc"
    mock_entry = MagicMock()
    mock_entry.entry_id = "test_full_456"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "Living Room Gateway"
    mock_entry.data = {
        CONF_MAC: mac,
        CONF_PASSWORD: "pw",
    }
    mock_entry.options = {}

    # Mock gateway and profile
    mock_profile = MagicMock()
    mock_profile.name = "MH200N"
    mock_profile.command_queue_delay = 0.05
    mock_profile.max_queue_size = 250
    mock_profile.keepalive_interval = 90.0

    mock_gw = MagicMock()
    mock_gw.model_name = "MH200N"
    mock_gw.manufacturer = "BTicino S.p.A."
    mock_gw.firmware = "2.0.1"
    mock_gw.profile = mock_profile

    # Mock send buffer
    mock_send_buffer = MagicMock()
    mock_send_buffer.qsize.return_value = 3
    mock_send_buffer.maxsize = 250

    # Bus monitor
    bus_mon = BusMonitor(maxlen=10)
    bus_mon.record_frame(direction="rx", raw="*1*1*12##")

    # Gateway handler
    mock_handler = MagicMock()
    mock_handler.gateway = mock_gw
    mock_handler.is_connected = True
    mock_handler.sending_workers = [MagicMock(), MagicMock()]
    mock_handler.send_buffer = mock_send_buffer
    mock_handler.bus_monitor = bus_mon

    hass.data[DOMAIN] = {
        mac: {
            CONF_ENTITY: mock_handler,
            CONF_ENTITIES: {
                "light": [MagicMock(), MagicMock()],
                "switch": [MagicMock()],
            },
        }
    }

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    # Verify versions
    assert diag["integration_version"] == INTEGRATION_VERSION
    assert "ownd_version" in diag

    # Verify gateway details
    assert diag["gateway"]["model_name"] == "MH200N"
    assert diag["gateway"]["manufacturer"] == "BTicino S.p.A."
    assert diag["gateway"]["firmware"] == "2.0.1"
    assert diag["gateway"]["is_connected"] is True
    assert diag["gateway"]["send_workers"] == 2

    # Verify profile details
    assert diag["profile"]["name"] == "MH200N"
    assert diag["profile"]["command_queue_delay"] == 0.05
    assert diag["profile"]["max_queue_size"] == 250
    assert diag["profile"]["keepalive_interval"] == 90.0

    # Verify queue details
    assert diag["queue"]["queue_depth"] == 3
    assert diag["queue"]["max_size"] == 250

    # Verify bus monitor details
    assert diag["bus_monitor"]["stats"]["captured"] == 1
    assert len(diag["bus_monitor"]["recent_frames"]) == 1
    assert diag["bus_monitor"]["recent_frames"][0]["raw"] == "*1*1*12##"

    # Verify platform counts
    assert diag["platforms"] == {"light": 2, "switch": 1}


@pytest.mark.asyncio
async def test_diagnostics_carry_no_household_identity(hass: HomeAssistant):
    """A download is attached to public issues: no LAN address, MAC, SSDP identity, path or title."""
    mock_entry = MagicMock()
    mock_entry.entry_id = "01M284WWKZG4XTEG62NVW1DPVG"
    mock_entry.version = 1
    mock_entry.domain = DOMAIN
    mock_entry.title = "Casa Rossi"
    mock_entry.data = {
        "host": "192.168.1.50",
        "port": 20000,
        CONF_MAC: "00:03:50:24:70:01",
        "id": "00:03:50:a4:11:2e",
        "UDN": "uuid:12345678",
        "ssdp_location": "http://192.168.1.50:49153/description.xml",
        "friendly_name": "Rossi MyHomeServer1",
        CONF_PASSWORD: "12345",
        "name": "MyHomeServer1",
    }
    mock_entry.options = {"file_path": "C:/Users/rossi/myhome.yaml", "command_worker_count": 1}
    hass.data[DOMAIN] = {}

    diag = await async_get_config_entry_diagnostics(hass, mock_entry)

    data, options = diag["config_entry"]["data"], diag["config_entry"]["options"]
    for key in ("host", CONF_MAC, "id", "UDN", "ssdp_location", "friendly_name", CONF_PASSWORD):
        assert data[key] == "**REDACTED**", key
    assert options["file_path"] == "**REDACTED**"
    assert data["port"] == 20000 and data["name"] == "MyHomeServer1" and options["command_worker_count"] == 1
    assert diag["config_entry"]["entry_id"] == "**REDACTED**"
    assert diag["config_entry"]["title"] == "MyHOME Gateway"
    text = str(diag)
    assert "192.168" not in text and "Rossi" not in text and "rossi" not in text and "01M284" not in text
