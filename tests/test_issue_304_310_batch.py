"""Regression tests for the September 2026 issue batch (#304, #307, #308, #310)."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNEvent

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler, _registry_supports_via_device_id


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
    entry.entry_id = "entry_batch"
    entry.data = {
        "host": "192.168.1.5",
        "port": 20000,
        "password": "12345",
        "mac": "00:11:22:33:44:55",
        "name": "MH202",
        "manufacturer": "BTicino",
        "firmware": "1.0",
    }
    entry.options = {}
    entry.title = "MH202 Gateway"
    return entry


@pytest.fixture
def gateway_handler(mock_config_entry):
    mock_hass = MagicMock()
    mock_hass.data = {}
    return MyHOMEGatewayHandler(mock_hass, mock_config_entry)


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:11:22:33:44:55"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.availability_signal = "myhome_avail"
    gw.available = True
    gw.log_id = "[test]"
    gw.device_registry_id = None
    return gw


# ── #304: OWNd yields None while reconnecting ────────────────────────────


async def test_listening_loop_skips_none_without_warning(gateway_handler, caplog):
    """A None cycle from OWNd.get_next is a reconnect, not a bad frame (#304)."""
    real_event = OWNEvent.parse("*2*1*21##")
    with patch("custom_components.myhome.gateway.OWNEventSession") as session_cls:
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.get_next = AsyncMock(side_effect=[None, real_event, asyncio.CancelledError()])
        session_cls.return_value = session
        gateway_handler._process_message = AsyncMock()

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(asyncio.CancelledError):
                await gateway_handler.listening_loop()

    # Only the real frame reaches the dispatcher and the ring buffer
    gateway_handler._process_message.assert_awaited_once_with(real_event)
    assert gateway_handler.bus_monitor.get_stats()["captured"] == 1
    assert "Data received is not a message" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "None" in r.getMessage()]


# ── #310: via_device -> via_device_id ────────────────────────────────────


def test_cen_device_uses_via_device_id_when_supported(gateway_handler):
    """On cores that accept via_device_id the deprecated via_device is not passed (#310)."""
    mock_dr = MagicMock()
    gateway_handler.device_registry_id = "gateway_dev_id"
    with patch("custom_components.myhome.gateway._registry_supports_via_device_id", return_value=True), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "7")
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    assert kwargs["via_device_id"] == "gateway_dev_id"
    assert "via_device" not in kwargs
    assert kwargs["identifiers"] == {(DOMAIN, f"{gateway_handler.mac}-15-7")}

    # Without a gateway device id yet, neither link is passed (never a stale via_device)
    mock_dr.reset_mock()
    gateway_handler._cen_devices.clear()
    gateway_handler.device_registry_id = None
    with patch("custom_components.myhome.gateway._registry_supports_via_device_id", return_value=True), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(25, "3")
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    assert "via_device_id" not in kwargs and "via_device" not in kwargs


def test_cen_device_falls_back_to_via_device_on_old_cores(gateway_handler):
    """Older cores without via_device_id keep the legacy tuple link."""
    mock_dr = MagicMock()
    with patch("custom_components.myhome.gateway._registry_supports_via_device_id", return_value=False), \
         patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "9")
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    assert kwargs["via_device"] == (DOMAIN, gateway_handler.mac)
    assert "via_device_id" not in kwargs


def test_registry_probe_matches_installed_core():
    """The signature probe reflects the running Home Assistant, and is cached."""
    import inspect

    from homeassistant.helpers import device_registry as dr

    expected = "via_device_id" in inspect.signature(dr.DeviceRegistry.async_get_or_create).parameters
    _registry_supports_via_device_id.cache_clear()
    assert _registry_supports_via_device_id() is expected
    assert _registry_supports_via_device_id.cache_info().hits == 0
    _registry_supports_via_device_id()
    assert _registry_supports_via_device_id.cache_info().hits == 1

