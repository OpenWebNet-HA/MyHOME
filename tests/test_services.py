"""Test services module directly."""
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.myhome.const import (
    ATTR_GATEWAY,
    ATTR_MESSAGE,
    CONF_ENTITY,
    DOMAIN,
)
from custom_components.myhome.services import (
    SERVICE_SEND_MESSAGE,
    SERVICE_SWEEP_BUS,
    SERVICE_SYNC_TIME,
    _get_gateway_handler,
    async_setup_services,
    async_unload_services,
)


async def test_services_setup_and_unload(hass: HomeAssistant) -> None:
    """Test setting up and unloading services idempotently."""
    assert not hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)
    assert not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SWEEP_BUS)

    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)
    assert hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)
    assert hass.services.has_service(DOMAIN, SERVICE_SWEEP_BUS)

    # Idempotent re-setup
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)

    # Unload
    await async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SYNC_TIME)
    assert not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SWEEP_BUS)


async def test_get_gateway_handler_helper(hass: HomeAssistant) -> None:
    """Test _get_gateway_handler lookup helper."""
    # When DOMAIN not in hass.data
    hass.data.pop(DOMAIN, None)
    assert _get_gateway_handler(hass, None) is None

    # When no gateways configured
    hass.data[DOMAIN] = {}
    assert _get_gateway_handler(hass, None) is None

    # When valid gateway configured
    mock_handler = MagicMock()
    gw_mac = "00:03:50:AA:BB:CC"
    hass.data[DOMAIN][gw_mac] = {CONF_ENTITY: mock_handler}

    # Default lookup (None)
    assert _get_gateway_handler(hass, None) == mock_handler

    # Specific valid lookup
    assert _get_gateway_handler(hass, gw_mac) == mock_handler

    # Invalid MAC format
    assert _get_gateway_handler(hass, "invalid_mac") is None

    # Unconfigured MAC
    assert _get_gateway_handler(hass, "00:03:50:99:99:99") is None

    # Format MAC lookup
    gw_mac_fmt = "00:03:50:11:22:33"
    hass.data[DOMAIN][gw_mac_fmt] = {CONF_ENTITY: mock_handler}
    assert _get_gateway_handler(hass, "000350112233") == mock_handler

    # Case-insensitive MAC lookup
    gw_mac_case = "00:03:50:AA:BB:DD"
    hass.data[DOMAIN][gw_mac_case] = {CONF_ENTITY: mock_handler}
    assert _get_gateway_handler(hass, "000350aabbdd") == mock_handler


async def test_services_edge_cases(hass: HomeAssistant) -> None:
    """Test edge cases for service calls."""
    await async_setup_services(hass)

    mock_handler = MagicMock()
    gw_mac = "00:03:50:aa:bb:cc"
    hass.data[DOMAIN] = {gw_mac: {CONF_ENTITY: mock_handler}}

    # Test send_message with message=None
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        {ATTR_GATEWAY: gw_mac, ATTR_MESSAGE: None},
        blocking=True,
    )

    # Test sweep_bus when no gateways configured
    hass.data[DOMAIN] = {}
    await hass.services.async_call(DOMAIN, SERVICE_SWEEP_BUS, {}, blocking=True)


async def test_sweep_bus_queries_sent(hass: HomeAssistant) -> None:
    """Test sweep_bus sends updated queries including firmware and excluding invalid lighting query."""
    from unittest.mock import AsyncMock
    await async_setup_services(hass)

    mock_handler = MagicMock()
    mock_handler.send = AsyncMock()
    gw_mac = "00:03:50:aa:bb:cc"
    hass.data[DOMAIN] = {gw_mac: {CONF_ENTITY: mock_handler}}

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SWEEP_BUS,
        {ATTR_GATEWAY: gw_mac},
        blocking=True,
    )

    sent_raw = [str(call.args[0]) for call in mock_handler.send.await_args_list]
    assert "*#13**0##" in sent_raw
    assert "*#13**15##" in sent_raw
    assert "*#13**16##" in sent_raw
    assert "*#2*0##" in sent_raw
    assert "*#4*0##" in sent_raw
    assert "*#1*0##" not in sent_raw


