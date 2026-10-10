"""Tests for the USB / Serial gateway config flow."""
import sys
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.myhome.config_flow import _get_serial_ports
from custom_components.myhome.const import DOMAIN


async def test_config_flow_select_serial_gateway(hass: HomeAssistant):
    """Test user selecting USB / Serial gateway in the user step."""
    with patch("custom_components.myhome.config_flow.find_gateways", return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        # User chooses serial_gateway
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "serial_gateway"},
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["step_id"] == "serial"


async def test_config_flow_serial_with_detected_ports(hass: HomeAssistant):
    """Test serial step when serial.tools.list_ports detects available devices."""
    mock_port = MagicMock()
    mock_port.device = "/dev/ttyUSB0"
    mock_port.description = "Legrand 3578 USB Interface"

    with patch("custom_components.myhome.config_flow.find_gateways", return_value=[]), patch(
        "custom_components.myhome.config_flow._get_serial_ports", return_value=[mock_port]
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "serial_gateway"},
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["step_id"] == "serial"

        # Submit valid selection
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "port": "/dev/ttyUSB0",
                "baudrate": 19200,
                "friendly_name": "MyHOME 3578 Dongle",
            },
        )
        assert result3["type"] is FlowResultType.CREATE_ENTRY
        assert result3["title"] == "MyHOME 3578 Dongle"
        assert result3["data"]["host"] == "/dev/ttyUSB0"
        assert result3["data"]["transport_type"] == "serial"
        assert result3["data"]["baudrate"] == 19200


async def test_config_flow_serial_empty_port_error(hass: HomeAssistant):
    """Test serial step with an empty port returns validation error."""
    with patch("custom_components.myhome.config_flow.find_gateways", return_value=[]), patch(
        "custom_components.myhome.config_flow._get_serial_ports", return_value=[]
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "serial_gateway"},
        )
        assert result2["type"] is FlowResultType.FORM

        # Submit empty port
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"port": "   ", "baudrate": 19200},
        )
        assert result3["type"] is FlowResultType.FORM
        assert result3["errors"] == {"port": "invalid_port"}


async def test_config_flow_serial_executor_exception(hass: HomeAssistant):
    """Test serial step when executor job encounters an exception."""
    with patch("custom_components.myhome.config_flow.find_gateways", return_value=[]), patch(
        "custom_components.myhome.config_flow._get_serial_ports", side_effect=RuntimeError("Port error")
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "serial_gateway"},
        )
        assert result2["type"] is FlowResultType.FORM
        assert result2["step_id"] == "serial"


def test_get_serial_ports_fallback():
    """Test _get_serial_ports returns empty list when serial import fails."""
    with patch.dict(sys.modules, {"serial": None, "serial.tools": None, "serial.tools.list_ports": None}):
        ports = _get_serial_ports()
        assert ports == []


def test_get_serial_ports_success():
    """Test _get_serial_ports returns ports when serial is available."""
    mock_list_ports = MagicMock()
    mock_list_ports.comports.return_value = ["COM1"]
    mock_tools = MagicMock()
    mock_tools.list_ports = mock_list_ports
    mock_serial = MagicMock()
    mock_serial.tools = mock_tools

    with patch.dict(
        sys.modules,
        {
            "serial": mock_serial,
            "serial.tools": mock_tools,
            "serial.tools.list_ports": mock_list_ports,
        },
    ):
        ports = _get_serial_ports()
        assert ports == ["COM1"]
