"""Test the MyHOME config flow."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.myhome.config_flow import (
    TEST_CONNECTION_ABORT_REASONS,
    TEST_CONNECTION_RETRY_DELAY,
)
from custom_components.myhome.const import DOMAIN


async def test_form(hass: HomeAssistant) -> None:
    """Test the full config flow: user -> custom (auto-discover) -> test_connection creates an entry."""
    mock_discovered = {
        "address": "192.0.2.10",
        "port": 20000,
        "serialNumber": "00:03:50:00:12:34",
        "modelName": "F454",
        "ssdp_location": None,
        "ssdp_st": None,
        "deviceType": None,
        "friendlyName": None,
        "manufacturer": "BTicino S.p.A.",
        "manufacturerURL": "http://www.bticino.it",
        "modelNumber": None,
        "UDN": None,
    }
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch(
            "custom_components.myhome.config_flow.get_gateway",
            return_value=mock_discovered,
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch(
            "custom_components.myhome.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
    ):
        # Step 1: user step – returns a form with a "serial" dropdown
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        # Step 2: select "Custom" (serial = 00:00:00:00:00:00)
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        assert result2["type"] == FlowResultType.FORM
        assert result2["step_id"] == "custom"

        # Step 3: fill custom form with address/port — MAC auto-discovered via get_gateway
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "address": "192.0.2.10",
                "port": 20000,
            },
        )
        await hass.async_block_till_done()

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert "Gateway" in result3["title"]
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_cannot_connect(hass: HomeAssistant) -> None:
    """Test we handle cannot connect via abort."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch(
            "custom_components.myhome.config_flow.get_gateway",
            return_value=None,
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": False, "Message": "connection_refused"},
        ),
    ):
        # Step 1: user step
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        # Step 2: select Custom
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )

        # Step 3: fill custom form (discovery fails -> falls to custom_manual)
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "address": "192.0.2.10",
                "port": 20000,
            },
        )
        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "custom_manual"

        # Step 4: fill manual form with serialNumber/modelName
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {
                "serialNumber": "00:03:50:00:12:34",
                "modelName": "F454",
            },
        )

    # connection_refused should cause an abort
    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "connection_refused"


async def test_form_already_configured(hass: HomeAssistant) -> None:
    """Test aborting when the gateway is already configured."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.10",
            "port": 20000,
            "mac": "00:03:50:00:12:34",
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch(
            "custom_components.myhome.config_flow.get_gateway",
            return_value=None,
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )

        # Discovery fails -> custom_manual
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "address": "192.0.2.10",
                "port": 20000,
            },
        )
        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "custom_manual"

        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {
                "serialNumber": "00:03:50:00:12:34",
                "modelName": "F454",
            },
        )

    assert result4["type"] == FlowResultType.ABORT
    assert result4["reason"] == "already_configured"


async def test_form_discovery(hass: HomeAssistant) -> None:
    """Test user selecting a discovered gateway."""
    mock_discovery = {
        "00:03:50:00:12:34": {
            "address": "192.0.2.10",
            "port": 20000,
            "serialNumber": "00:03:50:00:12:34",
            "modelName": "F454",
        }
    }
    with (
        patch(
            "custom_components.myhome.config_flow.find_gateways",
            return_value=[mock_discovery["00:03:50:00:12:34"]],
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch(
            "custom_components.myhome.async_setup_entry",
            return_value=True,
        ) as mock_setup_entry,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:03:50:00:12:34"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert "F454" in result2["title"]
    assert len(mock_setup_entry.mock_calls) == 1


async def test_form_discovery_already_configured(hass: HomeAssistant) -> None:
    """Test aborting when a discovered gateway is already selected."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.10",
            "port": 20000,
            "mac": "00:03:50:00:12:34",
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    mock_discovery = {
        "00:03:50:00:12:34": {
            "address": "192.0.2.10",
            "port": 20000,
            "serialNumber": "00:03:50:00:12:34",
            "modelName": "F454",
        }
    }
    with (
        patch(
            "custom_components.myhome.config_flow.find_gateways",
            return_value=[mock_discovery["00:03:50:00:12:34"]],
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Note: if it's already configured, the flow filters it from dropdown, but if manually passed:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:03:50:00:12:34"},
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


@patch(
    "custom_components.myhome.gateway.OWNSession.test_connection",
    return_value={"Success": True, "Message": None},
)
@patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop")
@patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop")
async def test_options_flow(
    mock_sending, mock_listening, mock_test_connection, hass: HomeAssistant
) -> None:
    """Test options config flow."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.10",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:00:12:34",
            "ssdp_location": "http://192.0.2.10:49153/description.xml",
            "ssdp_st": "urn:schemas-upnp-org:device:Basic:1",
            "deviceType": "urn:schemas-upnp-org:device:Basic:1",
            "friendly_name": "MyHOME Gateway",
            "manufacturer": "BTicino",
            "manufacturerURL": "http://www.bticino.com",
            "name": "F454",
            "firmware": "2.0.0",
            "UDN": "uuid:12345678-1234-1234-1234-123456789012",
        },
        options={
            "command_worker_count": 1,
            "generate_events": False,
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.myhome.config_flow.find_gateways"):
        # Initialize option flow
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    # Test invalid ip address config error
    result_invalid = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "command_worker_count": 3,
            "generate_events": True,
            "address": "invalid_ip",
            "password": "new_password",
        },
    )
    assert result_invalid["type"] == FlowResultType.FORM
    assert result_invalid["errors"]["address"] == "invalid_ip"

    # Submit updated options (valid)
    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "command_worker_count": 3,
                "generate_events": True,
                "address": "192.168.1.136",
                "password": "new_password",
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["command_worker_count"] == 3
    assert entry.options["generate_events"] is True
    assert entry.data["host"] == "192.168.1.136"
    assert entry.data["password"] == "new_password"
    assert mock_reload.called


async def test_ssdp_discovery(hass: HomeAssistant) -> None:
    """Test SSDP discovery flow with user confirmation."""

    class SsdpServiceInfo:
        def __init__(self, ssdp_usn, ssdp_st, ssdp_location, upnp, ssdp_headers):
            self.ssdp_usn = ssdp_usn
            self.ssdp_st = ssdp_st
            self.ssdp_location = ssdp_location
            self.upnp = upnp
            self.ssdp_headers = ssdp_headers

    ssdp_info = SsdpServiceInfo(
        ssdp_usn="mock_usn",
        ssdp_st="mock_st",
        ssdp_location="http://192.0.2.10:49153/description.xml",
        upnp={
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
            "friendlyName": "Gateway",
            "UDN": "uuid",
            "modelNumber": "2.0",
        },
        ssdp_headers={"_host": "192.0.2.10"},
    )

    # Step 1: SSDP discovery initiates flow -> shows discovery_confirm form (not auto-created)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=ssdp_info
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"]["name"] == "F454"
    assert result["description_placeholders"]["host"] == "192.0.2.10"

    # Step 2: User confirms -> tests connection and creates entry. Creating the entry
    # sets it up straight away, so keep the gateway loops from opening a real socket.
    with (
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["host"] == "192.0.2.10"


async def test_ssdp_discovery_already_configured(hass: HomeAssistant) -> None:
    """Test SSDP discovery flow when gateway is already configured."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.10",
            "port": 20000,
            "mac": "00:03:50:00:12:34",
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    class SsdpServiceInfo:
        def __init__(self, ssdp_usn, ssdp_st, ssdp_location, upnp, ssdp_headers):
            self.ssdp_usn = ssdp_usn
            self.ssdp_st = ssdp_st
            self.ssdp_location = ssdp_location
            self.upnp = upnp
            self.ssdp_headers = ssdp_headers

    ssdp_info = SsdpServiceInfo(
        ssdp_usn="mock_usn",
        ssdp_st="mock_st",
        ssdp_location="http://192.0.2.10:49153/description.xml",
        upnp={
            "modelName": "F454",
            "serialNumber": "00:03:50:00:12:34",
            "friendlyName": "Gateway",
            "UDN": "uuid",
            "modelNumber": "2.0",
        },
        ssdp_headers={"_host": "192.0.2.10"},
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=ssdp_info
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow(hass: HomeAssistant) -> None:
    """Test reauth flow triggered by a password error."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.0.2.10", "mac": "00:03:50:00:12:34", "password": "wrong"},
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.myhome.config_flow.MyHOMEGatewayHandler") as mock_gateway_handler:
        # Provide the mock gateway host/serial properties
        mock_gateway_handler.return_value.gateway.host = "192.0.2.10"
        mock_gateway_handler.return_value.gateway.model = "F454"
        mock_gateway_handler.return_value.gateway.serial = "00:03:50:00:12:34"
        mock_gateway_handler.return_value.gateway.password = "wrong"

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "password"

    with patch(
        "custom_components.myhome.config_flow.OWNSession.test_connection",
        return_value={"Success": True},
    ), patch.object(
        hass.config_entries,
        "async_reload",
        return_value=True,
    ) as mock_reload:
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"password": "correct_password"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["password"] == "correct_password"
    assert mock_reload.called


async def test_password_required_and_error(hass: HomeAssistant) -> None:
    """Test manual connection with password error states."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch(
            "custom_components.myhome.config_flow.get_gateway",
            return_value=None,
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            side_effect=[
                {"Success": False, "Message": "password_required"},
                {"Success": False, "Message": "password_error"},
                {"Success": True},
            ],
        ),
        patch(
            "custom_components.myhome.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        # Custom step: discovery fails -> custom_manual
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {
                "address": "192.0.2.10",
                "port": 20000,
            },
        )
        assert result3["type"] == FlowResultType.FORM
        assert result3["step_id"] == "custom_manual"

        # Fill manual form
        result3b = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {
                "serialNumber": "00:03:50:00:12:34",
                "modelName": "F454",
            },
        )
        # 1st attempt: password required
        assert result3b["type"] == FlowResultType.FORM
        assert result3b["step_id"] == "password"

        # 2nd attempt: password error
        result4 = await hass.config_entries.flow.async_configure(
            result3b["flow_id"], {"password": "wrong_password"}
        )
        assert result4["type"] == FlowResultType.FORM
        assert result4["step_id"] == "password"
        assert result4["errors"]["password"] == "password_error"

        # 3rd attempt: successful setup
        result5 = await hass.config_entries.flow.async_configure(
            result4["flow_id"], {"password": "correct_password"}
        )
        assert result5["type"] == FlowResultType.CREATE_ENTRY


def test_mac_address_unit():
    """Test MACAddress validation and repr."""
    import pytest

    from custom_components.myhome.config_flow import MACAddress

    mac = MACAddress("00:03:50:00:12:34")
    assert repr(mac) == "00:03:50:00:12:34"
    assert str(mac) == "00:03:50:00:12:34"

    with pytest.raises(ValueError):
        MACAddress("invalid_mac")

    with pytest.raises(ValueError):
        MACAddress("00:03:50:00:12:ZZ")


async def test_user_step_discovery_timeout(hass: HomeAssistant) -> None:
    """Test user step aborts with discovery_timeout on timeout."""
    import asyncio

    with patch(
        "custom_components.myhome.config_flow.find_gateways",
        side_effect=asyncio.TimeoutError("SSDP timeout"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "discovery_timeout"


async def test_custom_step_validation_and_timeout(hass: HomeAssistant) -> None:
    """Test custom step error on invalid IP and timeout fallback to custom_manual."""
    import asyncio

    with patch("custom_components.myhome.config_flow.find_gateways", return_value=[]):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        assert result2["step_id"] == "custom"

        # 1. Invalid IP
        result_err = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "999.999.999.999", "port": 20000},
        )
        assert result_err["type"] == FlowResultType.FORM
        assert result_err["errors"]["address"] == "invalid_ip"

        # 2. Timeout in get_gateway -> falls back to custom_manual (lines 165-166)
        with patch(
            "custom_components.myhome.config_flow.get_gateway", side_effect=asyncio.TimeoutError()
        ):
            result_manual = await hass.config_entries.flow.async_configure(
                result_err["flow_id"],
                {"address": "192.168.1.50", "port": 20000},
            )
            assert result_manual["type"] == FlowResultType.FORM
            assert result_manual["step_id"] == "custom_manual"


async def test_custom_manual_step_invalid_inputs(hass: HomeAssistant) -> None:
    """Test custom_manual step errors on invalid IP and invalid MAC address."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.168.1.50", "port": 20000},
        )
        assert result3["step_id"] == "custom_manual"

        # Pass invalid MAC (lines 227-228)
        result_bad_mac = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "not_a_mac", "modelName": "F454"},
        )
        assert result_bad_mac["errors"]["serialNumber"] == "invalid_mac"


async def test_step_port_and_ssdp_missing_port(hass: HomeAssistant) -> None:
    """Test step_port validation and ssdp step when port is missing."""
    from custom_components.myhome.config_flow import MyhomeFlowHandler

    handler = MyhomeFlowHandler()
    handler.hass = hass
    handler.context = {
        "host": "192.168.1.50",
        "name": "F454",
        "mac": "00:03:50:00:12:34",
    }
    handler.gateway_handler = MagicMock()

    # Invalid port (lines 383-384)
    res_bad = await handler.async_step_port({"port": 99999})
    assert res_bad["type"] == FlowResultType.FORM
    assert res_bad["errors"]["port"] == "invalid_port"

    # Valid port (lines 380-382)
    with patch.object(handler, "async_step_test_connection", return_value={"type": "create_entry"}):
        await handler.async_step_port({"port": 20000})
        assert handler.gateway_handler.port == 20000

    # Test ssdp with missing port (line 465)
    discovery_info = MagicMock()
    discovery_info.upnp = {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"}
    discovery_info.ssdp_st = "st"
    discovery_info.ssdp_location = "http://192.168.1.50/desc.xml"
    discovery_info.ssdp_headers = {"_host": "192.168.1.50"}

    mock_gw = MagicMock()
    mock_gw.port = None
    mock_gw.address = "192.168.1.50"
    mock_gw.unique_id = "00:03:50:00:12:34"
    mock_gw.model_name = "F454"
    mock_gw.friendly_name = "F454"
    mock_gw.udn = "udn"
    mock_gw.firmware = "1.0"

    with patch(
        "custom_components.myhome.config_flow.OWNGateway.build_from_discovery_info",
        return_value=mock_gw,
    ):
        res_ssdp = await handler.async_step_ssdp(discovery_info)
        assert res_ssdp["type"] == FlowResultType.FORM
        assert res_ssdp["step_id"] == "discovery_confirm"

        with patch.object(
            handler, "async_step_port", return_value={"type": "form", "step_id": "port"}
        ):
            res_confirm = await handler.async_step_discovery_confirm({})
            assert res_confirm["step_id"] == "port"

    # Test ssdp when build_from_discovery_info returns None (line 533)
    with patch(
        "custom_components.myhome.config_flow.OWNGateway.build_from_discovery_info",
        return_value=None,
    ):
        res_abort = await handler.async_step_ssdp(discovery_info)
        assert res_abort["type"] == FlowResultType.ABORT
        assert res_abort["reason"] == "unknown"


async def test_reauth_with_config_dict(hass: HomeAssistant) -> None:
    """Test async_step_reauth when entry_id is missing and config dict is provided."""
    from homeassistant.const import CONF_MAC

    from custom_components.myhome.config_flow import MyhomeFlowHandler

    handler = MyhomeFlowHandler()
    handler.hass = hass
    handler.context = {}

    config = {
        CONF_MAC: "00:03:50:00:12:34",
        "address": "192.168.1.50",
        "port": 20000,
        "modelName": "F454",
    }
    with patch.object(
        handler, "async_step_password", return_value={"type": "form", "step_id": "password"}
    ):
        res = await handler.async_step_reauth(config=config)
        assert res["step_id"] == "password"


async def test_options_flow_existing_decoders_and_handler_lookup(hass: HomeAssistant) -> None:
    """Test options flow with existing decoders (line 602) and handler property (line 485)."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.config_flow import MyhomeOptionsFlowHandler
    from custom_components.myhome.const import CONF_DECODER_ENTITY, CONF_DECODER_SOURCE

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:12:34", "host": "192.168.1.50", "port": 20000},
        options={
            CONF_DECODER_ENTITY.format(1): "media_player.living_room",
            CONF_DECODER_SOURCE.format(1): 1,
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    # 1. Options flow with config_entry passed
    opt_flow = MyhomeOptionsFlowHandler(entry)
    opt_flow.hass = hass
    form = await opt_flow.async_step_init()
    assert form["type"] == FlowResultType.FORM
    assert form["step_id"] == "user"

    # 2. Options flow with handler attribute (line 485)
    opt_flow2 = MyhomeOptionsFlowHandler(None)
    opt_flow2.hass = hass
    opt_flow2.handler = entry.entry_id
    assert opt_flow2.config_entry == entry

    # 3. Options flow with None config_entry and no handler (line 495)
    opt_flow3 = MyhomeOptionsFlowHandler(None)
    assert opt_flow3.config_entry is None

    # 4. Direct call to async_step_user without init (lines 515, 517)
    opt_flow4 = MyhomeOptionsFlowHandler(entry)
    opt_flow4.hass = hass
    assert opt_flow4.options is None
    form4 = await opt_flow4.async_step_user()
    assert form4["type"] == FlowResultType.FORM

    # 5. Options submission validation: not_a_media_player and mass_entity_not_allowed (lines 528-534)
    from custom_components.myhome.const import (
        CONF_ADDRESS,
        CONF_DECODER_PRE_GAIN,
        CONF_GENERATE_EVENTS,
        CONF_OWN_PASSWORD,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
    )

    mock_reg = MagicMock()
    mass_entry = MagicMock()
    mass_entry.platform = "mass"
    myhome_entry = MagicMock()
    myhome_entry.platform = "myhome"
    mock_reg.async_get.side_effect = (
        lambda eid: mass_entry if "mass" in eid else (myhome_entry if "myhome" in eid else MagicMock(platform="sonos"))
    )

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_reg):
        # Invalid: not starting with media_player.
        res_not_mp = await opt_flow.async_step_user(
            {
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software",
                CONF_DECODER_ENTITY.format(1): "light.living_room",
                CONF_DECODER_SOURCE.format(1): 1,
                CONF_DECODER_PRE_GAIN.format(1): 0,
            }
        )
        assert res_not_mp["errors"][CONF_DECODER_ENTITY.format(1)] == "not_a_media_player"

        # Invalid: Music Assistant clone entity (lines 533-534)
        res_mass = await opt_flow.async_step_user(
            {
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software",
                CONF_DECODER_ENTITY.format(1): "media_player.mass_zone",
                CONF_DECODER_SOURCE.format(1): 1,
                CONF_DECODER_PRE_GAIN.format(1): 0,
            }
        )
        assert res_mass["errors"][CONF_DECODER_ENTITY.format(1)] == "mass_entity_not_allowed"

        # Invalid: MyHOME matrix zone entity (cannot be used as source decoder)
        res_myhome = await opt_flow.async_step_user({
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
            CONF_TRANSITION_MODE: "software",
            CONF_DECODER_ENTITY.format(1): "media_player.myhome_zone",
            CONF_DECODER_SOURCE.format(1): 1,
            CONF_DECODER_PRE_GAIN.format(1): 0,
        })
        assert res_myhome["errors"][CONF_DECODER_ENTITY.format(1)] == "myhome_entity_not_allowed"

        # Invalid: Duplicate decoder source input
        res_dup_src = await opt_flow.async_step_user(
            {
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software",
                CONF_DECODER_ENTITY.format(1): "media_player.sonos_1",
                CONF_DECODER_SOURCE.format(1): 1,
                CONF_DECODER_PRE_GAIN.format(1): 0,
                CONF_DECODER_ENTITY.format(2): "media_player.sonos_2",
                CONF_DECODER_SOURCE.format(2): 1,
                CONF_DECODER_PRE_GAIN.format(2): 0,
            }
        )
        assert res_dup_src["errors"][CONF_DECODER_SOURCE.format(2)] == "duplicate_decoder_source"

        # Valid options submission (lines 536-550)
        res_valid = await opt_flow.async_step_user(
            {
                CONF_ADDRESS: "192.168.1.50",
                CONF_OWN_PASSWORD: None,
                CONF_WORKER_COUNT: 2,
                CONF_GENERATE_EVENTS: True,
                CONF_TRANSITION_MODE: "native",
                CONF_DECODER_ENTITY.format(1): "media_player.sonos_zone",
                CONF_DECODER_SOURCE.format(1): 2,
                CONF_DECODER_PRE_GAIN.format(1): 10,
            }
        )
        assert res_valid["type"] == FlowResultType.CREATE_ENTRY


async def test_custom_manual_invalid_address(hass: HomeAssistant) -> None:
    """Test custom_manual step with invalid IP address (lines 222-223)."""
    from custom_components.myhome.config_flow import MyhomeFlowHandler

    handler = MyhomeFlowHandler()
    handler.hass = hass
    handler._custom_address = "999.999.999.999"  # Invalid IP triggers lines 222-223
    handler._custom_port = 20000

    res = await handler.async_step_custom_manual(
        user_input={"serialNumber": "00:03:50:00:12:34", "modelName": "CustomUnlistedModel"}
    )
    assert res["type"] == FlowResultType.FORM
    assert res["errors"]["address"] == "invalid_ip"


async def test_custom_manual_entry_manufacturer_type(hass: HomeAssistant) -> None:
    """Test that custom_manual step stores manufacturer and manufacturerURL as strings, not tuples."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch(
            "custom_components.myhome.config_flow.get_gateway",
            return_value=None,
        ),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": True},
        ),
        patch(
            "custom_components.myhome.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "MH200"},
        )
        assert result4["type"] == FlowResultType.CREATE_ENTRY
        entry_data = result4["data"]
        assert isinstance(entry_data["manufacturer"], str)
        assert entry_data["manufacturer"] == "BTicino S.p.A."
        assert isinstance(entry_data["manufacturerURL"], str)
        assert entry_data["manufacturerURL"] == "http://www.bticino.it"


async def test_options_flow_update_gateway_model(hass: HomeAssistant) -> None:
    """Test updating the gateway model name via Options Flow."""
    from homeassistant.const import (
        CONF_HOST,
        CONF_MAC,
        CONF_NAME,
        CONF_PORT,
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_ADDRESS,
        CONF_GENERATE_EVENTS,
        CONF_OWN_PASSWORD,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:00:12:34",
            CONF_NAME: "CustomUnlistedModel",
        },
        options={
            CONF_WORKER_COUNT: 2,
            CONF_GENERATE_EVENTS: False,
            CONF_TRANSITION_MODE: "software_stepped",
            "broadcast_resync": True,
            CONF_OWN_PASSWORD: None,
            "bus_topology": "standalone",
            "gateway_role": "primary",
        },
        title="CustomUnlistedModel Gateway",
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        assert form["type"] == FlowResultType.FORM
        res = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_ADDRESS: "192.0.2.10",
                CONF_NAME: "MyHomeServer1",
                CONF_OWN_PASSWORD: None,
                CONF_WORKER_COUNT: 2,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software_stepped",
            },
        )
        await hass.async_block_till_done()

    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_NAME] == "MyHomeServer1"
    assert entry.title == "MyHomeServer1 Gateway"
    assert mock_reload.called



async def test_options_flow_model_selection_survives_reload_and_next_who13(
    hass: HomeAssistant,
) -> None:
    """PR #345 review: selecting a model in the options flow is authoritative.

    An entry labelled MH200N from WHO=13 is switched to MH200; after the reload the new
    handler receives device type 44 (MH200N, Nmap's table: field evidence) again and
    must keep MH200. The reload also clears a mismatch warning left in the issue
    registry by the previous handler.
    """
    from homeassistant.const import CONF_HOST, CONF_MAC, CONF_NAME, CONF_PORT
    from homeassistant.helpers import issue_registry as ir
    from OWNd.message import OWNEvent
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_ADDRESS,
        CONF_GENERATE_EVENTS,
        CONF_OWN_PASSWORD,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
        IDENTIFICATION_MANUAL,
        IDENTIFICATION_WHO13,
    )
    from custom_components.myhome.gateway import MyHOMEGatewayHandler
    from custom_components.myhome.repairs import ISSUE_GATEWAY_IDENTITY, async_create_identity_issue

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:00:12:34",
            CONF_NAME: "MH200N",
            "model_source": IDENTIFICATION_WHO13,
        },
        title="MH200N Gateway",
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)
    # A warning the previous handler raised and never cleared before the reload.
    async_create_identity_issue(
        hass, entry.entry_id, "MH200N", "MyHomeServer1", "200", "who13", False
    )
    issue_id = f"{ISSUE_GATEWAY_IDENTITY}_{entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        assert form["type"] == FlowResultType.FORM
        res = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_ADDRESS: "192.0.2.10",
                CONF_NAME: "MH200",
                CONF_OWN_PASSWORD: None,
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software_stepped",
            },
        )
        await hass.async_block_till_done()

    assert res["type"] == FlowResultType.CREATE_ENTRY
    assert mock_reload.called
    assert entry.data[CONF_NAME] == "MH200"
    assert entry.data["model_source"] == IDENTIFICATION_MANUAL

    # The reload builds a fresh handler from the updated entry ...
    handler = MyHOMEGatewayHandler(hass, entry)
    assert handler.gateway.model_name == "MH200"
    assert handler.identification_source == IDENTIFICATION_MANUAL
    assert handler._identity_conflict is None

    # ... and the next WHO=13 device-type 44 reply (Nmap's MH200N code: it cannot contradict) leaves it alone.
    handler._handle_gateway_diagnostics(OWNEvent.parse("*#13**15*44##"))
    assert handler.gateway.model_name == "MH200"
    assert entry.data[CONF_NAME] == "MH200"
    assert entry.data["model_source"] == IDENTIFICATION_MANUAL
    assert handler.identification()["conflict"] is None
    # The stale warning from before the reload is gone.
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_reconfigure_flow_ip_gateway_success(hass: HomeAssistant) -> None:
    """Test reconfiguring an IP gateway successfully."""
    from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: "1234",
            CONF_MAC: "00:03:50:AA:BB:CC",
        },
        unique_id="00:03:50:AA:BB:CC",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": getattr(config_entries, "SOURCE_RECONFIGURE", "reconfigure"),
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.1.100",
                CONF_PORT: 20000,
                CONF_PASSWORD: "5678",
            },
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.100"
    assert entry.data[CONF_PASSWORD] == "5678"


async def test_reconfigure_flow_ip_gateway_invalid_ip(hass: HomeAssistant) -> None:
    """Test reconfiguring with invalid IP address."""
    from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PASSWORD, CONF_PORT
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_PASSWORD: None,
            CONF_MAC: "00:03:50:AA:BB:CC",
        },
        unique_id="00:03:50:AA:BB:CC",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": getattr(config_entries, "SOURCE_RECONFIGURE", "reconfigure"),
            "entry_id": entry.entry_id,
        },
    )

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: "invalid_not_an_ip",
            CONF_PORT: 20000,
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"][CONF_HOST] == "invalid_ip"


async def test_reconfigure_flow_serial_gateway(hass: HomeAssistant) -> None:
    """Test reconfiguring a USB/Serial gateway."""
    from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PORT
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "/dev/ttyUSB0",
            CONF_PORT: 19200,
            CONF_MAC: "35:78:00:11:22:33",
            "transport_type": "serial",
            "baudrate": 19200,
        },
        unique_id="35:78:00:11:22:33",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": getattr(config_entries, "SOURCE_RECONFIGURE", "reconfigure"),
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    # Empty port validation
    result_err = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "port": "",
            "baudrate": 38400,
        },
    )
    assert result_err["type"] == FlowResultType.FORM
    assert result_err["errors"]["port"] == "invalid_port"

    # Valid reconfigure
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "port": "/dev/ttyUSB1",
                "baudrate": 38400,
            },
        )

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "/dev/ttyUSB1"
    assert entry.data["baudrate"] == 38400


async def test_reconfigure_flow_missing_entry(hass: HomeAssistant) -> None:
    """Test reconfigure flow aborts if entry is missing."""
    unknown_entry_cls = getattr(config_entries, "UnknownEntry", Exception)
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": getattr(config_entries, "SOURCE_RECONFIGURE", "reconfigure"),
                "entry_id": "non_existent_entry_id",
            },
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "unknown"
    except unknown_entry_cls:
        # Home Assistant 2024.4+ validates entry existence prior to flow execution
        pass

    # Direct invocation guarantees 100% coverage of async_step_reconfigure abort logic
    from homeassistant.const import CONF_HOST, CONF_MAC, CONF_PORT
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.config_flow import MyhomeFlowHandler

    flow = MyhomeFlowHandler()
    flow.hass = hass
    flow.context = {
        "source": getattr(config_entries, "SOURCE_RECONFIGURE", "reconfigure"),
        "entry_id": "non_existent_entry_id",
    }
    result_direct = await flow.async_step_reconfigure()
    assert result_direct["type"] == FlowResultType.ABORT
    assert result_direct["reason"] == "unknown"

    # Newer cores raise UnknownEntry from _get_reconfigure_entry: still a clean abort
    flow_raise = MyhomeFlowHandler()
    flow_raise.hass = hass
    flow_raise.context = dict(flow.context)
    flow_raise._get_reconfigure_entry = MagicMock(side_effect=RuntimeError("UnknownEntry"))
    result_raise = await flow_raise.async_step_reconfigure()
    assert result_raise["type"] == FlowResultType.ABORT
    assert result_raise["reason"] == "unknown"

    # Also test direct step invocation with invalid port to cover defensive error handling
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:AA:BB:DD",
        },
        unique_id="00:03:50:AA:BB:DD",
    )
    entry.add_to_hass(hass)
    flow2 = MyhomeFlowHandler()
    flow2.hass = hass
    flow2.context = {
        "source": getattr(config_entries, "SOURCE_RECONFIGURE", "reconfigure"),
        "entry_id": entry.entry_id,
    }
    if hasattr(flow2, "_reconfigure_entry"):
        flow2._reconfigure_entry = entry

    res_err1 = await flow2.async_step_reconfigure({CONF_HOST: "192.168.1.50", CONF_PORT: 70000})
    assert res_err1["errors"][CONF_PORT] == "invalid_port"

    res_err2 = await flow2.async_step_reconfigure(
        {CONF_HOST: "192.168.1.50", CONF_PORT: "not_a_port"}
    )
    assert res_err2["errors"][CONF_PORT] == "invalid_port"


async def test_options_flow_source_names_and_environment_defaults(hass: HomeAssistant) -> None:
    """Audio zones discovered from the registry get one default-source field each.

    Defaults are offered per environment rather than per zone: the F441M routes
    per output and an output serves a whole environment, so two amplifiers in
    one room cannot sit on different inputs.
    """
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.config_flow import MyhomeOptionsFlowHandler
    from custom_components.myhome.const import (
        CONF_SOURCE_DEFAULT_FIELD,
        CONF_SOURCE_DEFAULTS,
        CONF_SOURCE_NAME,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:12:34", "host": "192.168.1.50", "port": 20000},
        options={CONF_SOURCE_NAME.format(2): "Cambridge"},
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    registry = er.async_get(hass)
    # Two amplifiers in environment 2, one in environment 3, plus a non-audio
    # entity and an audio entity with a non-numeric zone that must be ignored.
    # Amplifier 05 sits in environment 0, which has no routing address (10S is
    # a source device), so no default can be offered for it.
    for zone in ("22", "23", "36", "05"):
        registry.async_get_or_create(
            "media_player",
            DOMAIN,
            f"00:03:50:00:12:34-16-{zone}#16",
            config_entry=entry,
            suggested_object_id=f"audio_zone_{zone}",
        )
    registry.async_get_or_create(
        "light",
        DOMAIN,
        "00:03:50:00:12:34-1-11",
        config_entry=entry,
        suggested_object_id="light_11",
    )
    registry.async_get_or_create(
        "media_player",
        DOMAIN,
        "00:03:50:00:12:34-16-gen#16",
        config_entry=entry,
        suggested_object_id="audio_zone_gen",
    )

    flow = MyhomeOptionsFlowHandler(entry)
    flow.hass = hass

    assert flow._audio_environments() == ["2", "3"]

    form = await flow.async_step_init()
    assert form["type"] == FlowResultType.FORM
    keys = {str(k) for k in form["data_schema"].schema}
    assert CONF_SOURCE_NAME.format(2) in keys
    assert CONF_SOURCE_DEFAULT_FIELD.format(2) in keys
    assert CONF_SOURCE_DEFAULT_FIELD.format(3) in keys
    # Environment 1 has no zones, so it is not offered
    assert CONF_SOURCE_DEFAULT_FIELD.format(1) not in keys

    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await flow.async_step_user(
            {
                "address": "192.168.1.50",
                "password": "12345",
                "command_worker_count": 1,
                "generate_events": False,
                CONF_SOURCE_NAME.format(2): "  Cambridge  ",
                CONF_SOURCE_DEFAULT_FIELD.format(2): "2",
                CONF_SOURCE_DEFAULT_FIELD.format(3): "none",
            }
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    options = result["data"]
    assert options[CONF_SOURCE_NAME.format(2)] == "Cambridge"
    # "none" means leave the routing alone, so environment 3 is not stored
    assert options[CONF_SOURCE_DEFAULTS] == {"2": 2}


async def test_options_flow_auto_join_streaming_toggle(hass: HomeAssistant) -> None:
    """Validate CONF_AUTO_JOIN_STREAMING default, toggle, and persistence."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.config_flow import MyhomeOptionsFlowHandler
    from custom_components.myhome.const import (
        CONF_AUTO_JOIN_STREAMING,
        DEFAULT_AUTO_JOIN_STREAMING,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:12:34", "host": "192.168.1.50", "port": 20000},
        options={},
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    flow = MyhomeOptionsFlowHandler(entry)
    flow.hass = hass

    form = await flow.async_step_init()
    assert form["type"] == FlowResultType.FORM
    assert flow.options[CONF_AUTO_JOIN_STREAMING] is DEFAULT_AUTO_JOIN_STREAMING

    schema_keys = {str(k): k for k in form["data_schema"].schema}
    assert CONF_AUTO_JOIN_STREAMING in schema_keys
    field = schema_keys[CONF_AUTO_JOIN_STREAMING]
    assert field.description["suggested_value"] is True

    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await flow.async_step_user(
            {
                "address": "192.168.1.50",
                "password": "12345",
                "command_worker_count": 1,
                "generate_events": False,
                CONF_AUTO_JOIN_STREAMING: False,
            }
        )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AUTO_JOIN_STREAMING] is False
    hass.config_entries.async_update_entry(entry, options=result["data"])

    flow2 = MyhomeOptionsFlowHandler(entry)
    flow2.hass = hass
    form2 = await flow2.async_step_init()
    schema_keys2 = {str(k): k for k in form2["data_schema"].schema}
    field2 = schema_keys2[CONF_AUTO_JOIN_STREAMING]
    assert field2.description["suggested_value"] is False


async def test_options_flow_environments_without_registry(hass: HomeAssistant) -> None:
    """A registry lookup that fails leaves the form without default-source fields."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.config_flow import MyhomeOptionsFlowHandler

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:12:34", "host": "192.168.1.50", "port": 20000},
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    flow = MyhomeOptionsFlowHandler(entry)
    flow.hass = None  # entity_registry.async_get raises without a hass

    assert flow._audio_environments() == []


async def test_options_flow_options_only_reloads_entry(hass: HomeAssistant) -> None:
    """Test updating options only via options flow schedules reload and updates options."""
    from homeassistant.const import (
        CONF_HOST,
        CONF_MAC,
        CONF_NAME,
        CONF_PASSWORD,
        CONF_PORT,
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_ADDRESS,
        CONF_GENERATE_EVENTS,
        CONF_OWN_PASSWORD,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:00:12:34",
            CONF_NAME: "MyHomeServer1",
            CONF_PASSWORD: "pass",
        },
        options={
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        assert form["type"] == FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_ADDRESS: "192.168.1.50",
                CONF_NAME: "MyHomeServer1",
                CONF_OWN_PASSWORD: "pass",
                CONF_WORKER_COUNT: 3,
                CONF_GENERATE_EVENTS: True,
                CONF_TRANSITION_MODE: "software_stepped",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_WORKER_COUNT] == 3
    assert entry.options[CONF_GENERATE_EVENTS] is True
    assert mock_reload.called


async def test_options_flow_data_only_reloads_entry(hass: HomeAssistant) -> None:
    """Test updating data only via options flow schedules reload and updates data."""
    from homeassistant.const import (
        CONF_HOST,
        CONF_MAC,
        CONF_NAME,
        CONF_PASSWORD,
        CONF_PORT,
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_ADDRESS,
        CONF_AUTO_JOIN_STREAMING,
        CONF_BROADCAST_RESYNC,
        CONF_BUS_TOPOLOGY,
        CONF_DECODER_ENTITY,
        CONF_DECODER_PRE_GAIN,
        CONF_DECODER_SLOTS,
        CONF_DECODER_SOURCE,
        CONF_GATEWAY_ROLE,
        CONF_GENERATE_EVENTS,
        CONF_OWN_PASSWORD,
        CONF_SOURCE_DEFAULTS,
        CONF_SOURCE_NAME,
        CONF_SOURCE_SLOTS,
        CONF_SOURCE_TUNER,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
        DEFAULT_AUTO_JOIN_STREAMING,
        ROLE_PRIMARY,
        TOPOLOGY_STANDALONE,
    )

    initial_options = {
        CONF_WORKER_COUNT: 1,
        CONF_GENERATE_EVENTS: False,
        CONF_BROADCAST_RESYNC: True,
        CONF_AUTO_JOIN_STREAMING: DEFAULT_AUTO_JOIN_STREAMING,
        CONF_TRANSITION_MODE: "software_stepped",
        CONF_SOURCE_DEFAULTS: {},
        CONF_BUS_TOPOLOGY: TOPOLOGY_STANDALONE,
        CONF_GATEWAY_ROLE: ROLE_PRIMARY,
        **{CONF_SOURCE_NAME.format(i): "" for i in range(1, CONF_SOURCE_SLOTS + 1)},
        **{CONF_SOURCE_TUNER.format(i): False for i in range(1, CONF_SOURCE_SLOTS + 1)},
        **{CONF_DECODER_ENTITY.format(i): "" for i in range(1, CONF_DECODER_SLOTS + 1)},
        **{CONF_DECODER_SOURCE.format(i): i for i in range(1, CONF_DECODER_SLOTS + 1)},
        **{CONF_DECODER_PRE_GAIN.format(i): 0 for i in range(1, CONF_DECODER_SLOTS + 1)},
    }

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:00:12:34",
            CONF_NAME: "MyHomeServer1",
            CONF_PASSWORD: "pass",
        },
        options=initial_options,
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    # Update only data (host IP) with identical options
    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        assert form["type"] == FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_ADDRESS: "192.168.1.99",
                CONF_NAME: "MyHomeServer1",
                CONF_OWN_PASSWORD: "pass",
                CONF_WORKER_COUNT: 1,
                CONF_GENERATE_EVENTS: False,
                CONF_TRANSITION_MODE: "software_stepped",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_HOST] == "192.168.1.99"
    assert mock_reload.called


async def test_options_flow_data_and_options_combined_reloads_entry(hass: HomeAssistant) -> None:
    """Test updating both data and options schedules reload with all new values."""
    from homeassistant.const import (
        CONF_HOST,
        CONF_MAC,
        CONF_NAME,
        CONF_PASSWORD,
        CONF_PORT,
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_ADDRESS,
        CONF_GENERATE_EVENTS,
        CONF_OWN_PASSWORD,
        CONF_TRANSITION_MODE,
        CONF_WORKER_COUNT,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:00:12:34",
            CONF_NAME: "MyHomeServer1",
            CONF_PASSWORD: "pass",
        },
        options={
            CONF_WORKER_COUNT: 1,
            CONF_GENERATE_EVENTS: False,
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    with patch.object(hass.config_entries, "async_reload", return_value=True) as mock_reload:
        form = await hass.config_entries.options.async_init(entry.entry_id)
        assert form["type"] == FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_ADDRESS: "192.168.1.105",
                CONF_NAME: "MyHomeServer1",
                CONF_OWN_PASSWORD: "new_pass",
                CONF_WORKER_COUNT: 2,
                CONF_GENERATE_EVENTS: True,
                CONF_TRANSITION_MODE: "software_stepped",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_HOST] == "192.168.1.105"
    assert entry.data[CONF_PASSWORD] == "new_pass"
    assert entry.options[CONF_WORKER_COUNT] == 2
    assert entry.options[CONF_GENERATE_EVENTS] is True
    assert mock_reload.called


async def test_reauth_successful_reload_and_abort(hass: HomeAssistant) -> None:
    """Test reauth flow completes with async_update_reload_and_abort and new password."""
    from homeassistant.const import (
        CONF_HOST,
        CONF_MAC,
        CONF_NAME,
        CONF_PASSWORD,
        CONF_PORT,
    )
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.50",
            CONF_PORT: 20000,
            CONF_MAC: "00:03:50:00:12:34",
            CONF_NAME: "F454",
            CONF_PASSWORD: "wrong_password",
        },
        unique_id="00:03:50:00:12:34",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
            "unique_id": entry.unique_id,
        },
        data=entry.data,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "password"

    with patch(
        "custom_components.myhome.config_flow.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch.object(
        hass.config_entries, "async_reload", return_value=True
    ) as mock_reload:
        res = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"password": "correct_password"},
        )
        await hass.async_block_till_done()

    assert res["type"] == FlowResultType.ABORT
    assert res["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "correct_password"
    assert mock_reload.called


@pytest.mark.asyncio
async def test_config_flow_delegated_whos_validation(hass: HomeAssistant) -> None:
    """Test validation of delegated WHOs in options flow."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        DOMAIN,
        ROLE_PRIMARY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )

    pri = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:01",
        title="Primary",
        data={
            "host": "192.168.1.1",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:01",
        },
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    pri.add_to_hass(hass)

    sec1 = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:02",
        title="Secondary 1",
        data={
            "host": "192.168.1.2",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:02",
            "name": "MyHomeServer1",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: ["1"],
        },
    )
    sec1.add_to_hass(hass)

    sec2 = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:03",
        title="Secondary 2",
        data={
            "host": "192.168.1.3",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:03",
            "name": "MyHomeServer1",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    sec2.add_to_hass(hass)

    with patch(
        "custom_components.myhome.config_flow.MyHOMEGatewayHandler", return_value=AsyncMock()
    ):
        result = await hass.config_entries.options.async_init(sec2.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "address": "192.168.1.3",
                "name": "MyHomeServer1",
                "command_worker_count": 1,
                "generate_events": False,
                "transition_mode": "software_stepped",
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_SECONDARY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
                CONF_DELEGATED_WHOS: ["1"],
            },
        )
        assert result["errors"] == {CONF_DELEGATED_WHOS: "overlapping_delegated_whos"}


@pytest.mark.asyncio
async def test_config_flow_multiple_standbys(hass: HomeAssistant) -> None:
    """Test rejection of multiple standbys."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        DOMAIN,
        ROLE_PRIMARY,
        ROLE_STANDBY,
        TOPOLOGY_SHARED,
    )

    pri = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:01",
        title="Primary",
        data={
            "host": "192.168.1.1",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:01",
        },
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    pri.add_to_hass(hass)

    stb1 = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:02",
        title="Standby 1",
        data={
            "host": "192.168.1.2",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:02",
            "name": "MyHomeServer1",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    stb1.add_to_hass(hass)

    stb2 = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:03",
        title="Standby 2",
        data={
            "host": "192.168.1.3",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:03",
            "name": "MyHomeServer1",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_STANDBY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
        },
    )
    stb2.add_to_hass(hass)

    with patch(
        "custom_components.myhome.config_flow.MyHOMEGatewayHandler", return_value=AsyncMock()
    ):
        result = await hass.config_entries.options.async_init(stb2.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "address": "192.168.1.3",
                "name": "MyHomeServer1",
                "command_worker_count": 1,
                "generate_events": False,
                "transition_mode": "software_stepped",
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_STANDBY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            },
        )
        assert result["errors"] == {CONF_GATEWAY_ROLE: "multiple_standbys"}


@pytest.mark.asyncio
async def test_config_flow_who_not_supported(hass: HomeAssistant) -> None:
    """Test rejection of unsupported WHO for delegated WHOs."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        DOMAIN,
        ROLE_PRIMARY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )

    pri = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:01",
        title="Primary",
        data={
            "host": "192.168.1.1",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:01",
        },
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    pri.add_to_hass(hass)

    sec = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:04",
        title="Secondary",
        data={
            "host": "192.168.1.4",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:04",
            "name": "MyHomeServer1",
        },
    )
    sec.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.config_flow.MyHOMEGatewayHandler", return_value=AsyncMock()
        ),
        patch("OWNd.profiles.get_gateway_profile") as mock_get_profile,
    ):
        mock_profile = type("MockProfile", (), {"supports_who": lambda self, w: False})()
        mock_get_profile.return_value = mock_profile
        result = await hass.config_entries.options.async_init(sec.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "address": "192.168.1.4",
                "name": "MyHomeServer1",
                "command_worker_count": 1,
                "generate_events": False,
                "transition_mode": "software_stepped",
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_SECONDARY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
                CONF_DELEGATED_WHOS: ["2"],
            },
        )
        assert result["errors"] == {CONF_DELEGATED_WHOS: "who_not_supported_by_gateway"}


@pytest.mark.asyncio
async def test_config_flow_unknown_model(hass: HomeAssistant) -> None:
    """Test options flow with an unknown model gracefully falling back."""
    from homeassistant.data_entry_flow import FlowResultType
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:08",
        data={
            "host": "192.168.1.8",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:08",
            "name": "Unknown Gateway",
        },
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.myhome.config_flow.MyHOMEGatewayHandler", return_value=AsyncMock()
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == FlowResultType.FORM


@pytest.mark.asyncio
async def test_options_flow_update_delegated_whos_self_skip(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.myhome.const import (
        CONF_BUS_TOPOLOGY,
        CONF_DELEGATED_WHOS,
        CONF_GATEWAY_ROLE,
        CONF_PRIMARY_GATEWAY,
        ROLE_PRIMARY,
        ROLE_SECONDARY,
        TOPOLOGY_SHARED,
    )

    pri = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:01",
        title="Primary",
        data={
            "host": "192.168.1.1",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:01",
            "name": "MyHomeServer1",
        },
        options={CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED, CONF_GATEWAY_ROLE: ROLE_PRIMARY},
    )
    pri.add_to_hass(hass)

    sec = MockConfigEntry(
        domain=DOMAIN,
        unique_id="00:03:50:aa:bb:02",
        title="Secondary",
        data={
            "host": "192.168.1.2",
            "port": 20000,
            "password": "abc",
            "serialNumber": "00:03:50:aa:bb:02",
            "name": "MyHomeServer1",
        },
        options={
            CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
            CONF_GATEWAY_ROLE: ROLE_SECONDARY,
            CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
            CONF_DELEGATED_WHOS: ["1"],
        },
    )
    sec.add_to_hass(hass)

    with patch(
        "custom_components.myhome.config_flow.MyHOMEGatewayHandler", return_value=AsyncMock()
    ):
        result = await hass.config_entries.options.async_init(sec.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "address": "192.168.1.2",
                "name": "MyHomeServer1",
                "command_worker_count": 1,
                "generate_events": False,
                "transition_mode": "software_stepped",
                CONF_BUS_TOPOLOGY: TOPOLOGY_SHARED,
                CONF_GATEWAY_ROLE: ROLE_SECONDARY,
                CONF_PRIMARY_GATEWAY: "00:03:50:aa:bb:01",
                CONF_DELEGATED_WHOS: ["1"],
            },
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY


async def test_form_connection_closed_aborts(hass: HomeAssistant) -> None:
    """Test connection_closed causes an abort with the proper reason."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": False, "Message": "connection_closed"},
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == "connection_closed"
        assert mock_sleep.await_count == 1
        mock_sleep.assert_any_await(TEST_CONNECTION_RETRY_DELAY)


async def test_form_transient_retry_success(hass: HomeAssistant) -> None:
    """Test transient connection_closed recovers if retry succeeds."""
    mock_discovered = {
        "address": "192.0.2.10",
        "port": 20000,
        "serialNumber": "00:03:50:00:12:34",
        "modelName": "F454",
    }
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=mock_discovered),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            side_effect=[
                {"Success": False, "Message": "connection_closed"},
                {"Success": True},
            ],
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("custom_components.myhome.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        await hass.async_block_till_done()
        assert result3["type"] == FlowResultType.CREATE_ENTRY
        mock_sleep.assert_any_await(TEST_CONNECTION_RETRY_DELAY)


async def test_form_connection_closed_retry_to_password(hass: HomeAssistant) -> None:
    """Test transient connection_closed recovers to password step if retry reports password_required."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            side_effect=[
                {"Success": False, "Message": "connection_closed"},
                {"Success": False, "Message": "password_required"},
            ],
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.FORM
        assert result4["step_id"] == "password"
        mock_sleep.assert_any_await(TEST_CONNECTION_RETRY_DELAY)


async def test_form_unknown_negotiation_error_fallback(hass: HomeAssistant) -> None:
    """Test unknown negotiation error safely falls back to cannot_connect."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": False, "Message": "mysterious_hardware_failure"},
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == "cannot_connect"


@pytest.mark.parametrize(
    "catalog_file",
    [
        "strings.json",
        "translations/en.json",
        "translations/fr.json",
        "translations/it.json",
        "translations/nl.json",
    ],
)
def test_all_test_connection_abort_reasons_in_catalogs(catalog_file: str) -> None:
    """Verify all abort reasons referenced by config_flow exist in strings and translation catalogs."""
    import json
    from pathlib import Path

    catalog_path = Path(__file__).parent.parent / "custom_components" / "myhome" / catalog_file
    with open(catalog_path, encoding="utf-8") as f:
        data = json.load(f)

    abort_keys = set(data.get("config", {}).get("abort", {}).keys())
    missing = TEST_CONNECTION_ABORT_REASONS - abort_keys
    assert not missing, f"Missing abort reasons in {catalog_file}: {missing}"


async def test_form_connection_error_aborts(hass: HomeAssistant) -> None:
    """Test connection_error causes an abort immediately without retry sleep."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": False, "Message": "connection_error"},
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == "connection_error"
        mock_sleep.assert_not_called()


@pytest.mark.parametrize(
    "abort_reason",
    [
        "connection_refused",
        "negotiation_timeout",
        "negotiation_failed",
        "negotiation_refused",
        "negotiation_error",
    ],
)
async def test_form_non_transient_negotiation_errors_abort_without_retry(
    hass: HomeAssistant, abort_reason: str
) -> None:
    """Test non-transient negotiation errors abort immediately without sleep retry."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value={"Success": False, "Message": abort_reason},
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == abort_reason
        mock_sleep.assert_not_called()


@pytest.mark.parametrize(
    "malformed_result",
    [
        None,
        {},
        {"Message": "hardware_glitch"},
    ],
)
async def test_form_none_or_malformed_test_result_fallback(
    hass: HomeAssistant, malformed_result: Any
) -> None:
    """Test None, empty dict, or missing Success key safely falls back to cannot_connect."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            return_value=malformed_result,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == "cannot_connect"


async def test_form_communication_exception_aborts_without_retry(hass: HomeAssistant) -> None:
    """Test network exceptions (OSError/TimeoutError/ConnectionError) map to connection_error without retry sleep."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            side_effect=OSError("Low-level socket reset"),
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == "connection_error"
        mock_sleep.assert_not_called()


async def test_form_unexpected_exception_aborts_to_cannot_connect(hass: HomeAssistant) -> None:
    """Test unexpected programming exceptions map to cannot_connect without retry sleep."""
    with (
        patch("custom_components.myhome.config_flow.find_gateways", return_value=[]),
        patch("custom_components.myhome.config_flow.get_gateway", return_value=None),
        patch(
            "custom_components.myhome.config_flow.OWNSession.test_connection",
            side_effect=RuntimeError("Unexpected bug"),
        ),
        patch("custom_components.myhome.config_flow.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"serial": "00:00:00:00:00:00"},
        )
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"address": "192.0.2.10", "port": 20000},
        )
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"serialNumber": "00:03:50:00:12:34", "modelName": "F454"},
        )
        assert result4["type"] == FlowResultType.ABORT
        assert result4["reason"] == "cannot_connect"
        mock_sleep.assert_not_called()


@pytest.mark.parametrize(
    "catalog_file",
    [
        "strings.json",
        "translations/en.json",
        "translations/fr.json",
        "translations/it.json",
        "translations/nl.json",
    ],
)
def test_all_config_flow_literal_abort_reasons_in_catalogs(catalog_file: str) -> None:
    """Verify all literal abort reasons in config_flow.py exist in all catalogs."""
    import ast
    import json
    from pathlib import Path

    config_flow_path = Path(__file__).parent.parent / "custom_components" / "myhome" / "config_flow.py"
    tree = ast.parse(config_flow_path.read_text(encoding="utf-8"))

    literal_reasons = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if "abort" in node.func.attr:
                for kw in node.keywords:
                    if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                        literal_reasons.add(kw.value.value)

    catalog_path = Path(__file__).parent.parent / "custom_components" / "myhome" / catalog_file
    with open(catalog_path, encoding="utf-8") as f:
        data = json.load(f)

    abort_keys = set(data.get("config", {}).get("abort", {}).keys())
    missing = literal_reasons - abort_keys
    assert not missing, f"Missing literal abort reasons in {catalog_file}: {missing}"


async def test_ssdp_discovery_null_serial(hass: HomeAssistant) -> None:
    """Test SSDP discovery aborts when gateway serial is None or empty."""
    from custom_components.myhome.config_flow import MyhomeFlowHandler

    flow = MyhomeFlowHandler()
    flow.hass = hass
    discovery_info = MagicMock()
    discovery_info.upnp = {"friendlyName": "MH201"}
    discovery_info.ssdp_st = "upnp:rootdevice"
    discovery_info.ssdp_location = "http://192.168.1.10:20000"
    discovery_info.ssdp_headers = {"_host": "192.168.1.10"}

    with patch(
        "custom_components.myhome.config_flow.OWNGateway.build_from_discovery_info",
        AsyncMock(return_value=MagicMock(unique_id=None, serial=None)),
    ):
        result = await flow.async_step_ssdp(discovery_info)
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_serial"
