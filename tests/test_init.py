"""Tests for the MyHOME custom component initialization."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_PLATFORMS, DOMAIN


async def test_setup_entry_success(hass: HomeAssistant):
    """Test successful setup of the integration via MockConfigEntry."""
    # 1. Provide a realistic connection mock that succeeds
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:34",
                "ssdp_location": "http://192.168.0.35:49153/description.xml",
                "ssdp_st": "urn:schemas-upnp-org:device:Basic:1",
                "deviceType": "urn:schemas-upnp-org:device:Basic:1",
                "friendly_name": "MyHOME Gateway",
                "manufacturer": "BTicino",
                "manufacturerURL": "http://www.bticino.com",
                "name": "F454",
                "firmware": "2.0.0",
                "UDN": "uuid:12345678-1234-1234-1234-123456789012"
            },
            unique_id="00:03:50:00:12:34",
        )
        config_entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Check entry loaded
        assert config_entry.state is ConfigEntryState.LOADED

        # Cleanup
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_entry_connection_failed(hass: HomeAssistant):
    """Test failing setup due to bad password or timeout."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": False, "Message": "password_error"}
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "wrong",
                "mac": "00:03:50:00:12:34",
                "ssdp_location": "http://192.168.0.35:49153/description.xml",
                "ssdp_st": "urn:schemas-upnp-org:device:Basic:1",
                "deviceType": "urn:schemas-upnp-org:device:Basic:1",
                "friendly_name": "MyHOME Gateway",
                "manufacturer": "BTicino",
                "manufacturerURL": "http://www.bticino.com",
                "name": "F454",
                "firmware": "2.0.0",
                "UDN": "uuid:12345678-1234-1234-1234-123456789012"
            },
            unique_id="00:03:50:00:12:35",
        )
        config_entry.add_to_hass(hass)

        result = await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Should return False
        assert not result
        assert config_entry.state is ConfigEntryState.SETUP_ERROR

async def test_setup_yaml(hass: HomeAssistant):
    """Test setup from yaml configurations returns false."""
    from custom_components.myhome import async_setup
    result = await async_setup(hass, {DOMAIN: {}})
    assert not result

async def test_services(hass: HomeAssistant):
    """Test sync_time and send_message services."""
    from custom_components.myhome.const import ATTR_GATEWAY, ATTR_MESSAGE

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:34",
                "ssdp_location": "http://192.168.0.35:49153/description.xml",
                "ssdp_st": "urn:schemas-upnp-org:device:Basic:1",
                "deviceType": "urn:schemas-upnp-org:device:Basic:1",
                "friendly_name": "MyHOME Gateway",
                "manufacturer": "BTicino",
                "manufacturerURL": "http://www.bticino.com",
                "name": "F454",
                "firmware": "2.0.0",
                "UDN": "uuid:12345678-1234-1234-1234-123456789012"
            },
            unique_id="00:03:50:00:12:34",
        )
        config_entry.add_to_hass(hass)

        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        gateway = hass.data[DOMAIN]["00:03:50:00:12:34"]["entity"]
        gateway.send = AsyncMock()

        # Test sync_time service
        await hass.services.async_call(
            DOMAIN, "sync_time", {ATTR_GATEWAY: "00:03:50:00:12:34"}, blocking=True
        )
        gateway.send.assert_called_once()
        gateway.send.reset_mock()

        # Test sync_time without gateway specified
        await hass.services.async_call(
            DOMAIN, "sync_time", {}, blocking=True
        )
        gateway.send.assert_called_once()
        gateway.send.reset_mock()

        # Test send_message service (valid)
        await hass.services.async_call(
            DOMAIN, "send_message", {ATTR_GATEWAY: "00:03:50:00:12:34", ATTR_MESSAGE: "*1*1*12##"}, blocking=True
        )
        gateway.send.assert_called_once()
        gateway.send.reset_mock()

        # Test send_message service (invalid)
        await hass.services.async_call(
            DOMAIN, "send_message", {ATTR_GATEWAY: "00:03:50:00:12:34", ATTR_MESSAGE: "invalid"}, blocking=True
        )
        gateway.send.assert_not_called()

        # Test missing gateway
        await hass.services.async_call(
            DOMAIN, "send_message", {ATTR_GATEWAY: "00:03:50:00:00:00", ATTR_MESSAGE: "*1*1*12##"}, blocking=True
        )
        gateway.send.assert_not_called()

        # Test send_message without gateway specified (hits line 266: gateway = _gw_keys[0])
        await hass.services.async_call(
            DOMAIN, "send_message", {ATTR_MESSAGE: "*1*1*12##"}, blocking=True
        )
        gateway.send.assert_called_once()
        gateway.send.reset_mock()

        # Test sync_time with invalid MAC format (lines 237-241)
        with patch("homeassistant.helpers.device_registry.format_mac", return_value=None):
            await hass.services.async_call(
                DOMAIN, "sync_time", {ATTR_GATEWAY: "invalid_mac"}, blocking=True
            )

        # Test sync_time with unconfigured valid MAC (lines 250-254)
        await hass.services.async_call(
            DOMAIN, "sync_time", {ATTR_GATEWAY: "00:03:50:00:99:99"}, blocking=True
        )

        # Test send_message with invalid MAC format (lines 270-275)
        with patch("homeassistant.helpers.device_registry.format_mac", return_value=None):
            await hass.services.async_call(
                DOMAIN, "send_message", {ATTR_GATEWAY: "invalid_mac", ATTR_MESSAGE: "*1*1*12##"}, blocking=True
            )

        # Test sweep_bus service with specific gateway
        gateway.send.reset_mock()
        await hass.services.async_call(
            DOMAIN, "sweep_bus", {ATTR_GATEWAY: "00:03:50:00:12:34"}, blocking=True
        )
        assert gateway.send.call_count >= 5
        gateway.send.reset_mock()

        # Test sweep_bus without gateway specified (sweeps all active gateways)
        await hass.services.async_call(
            DOMAIN, "sweep_bus", {}, blocking=True
        )
        assert gateway.send.call_count >= 5
        gateway.send.reset_mock()

        # Test sweep_bus with unconfigured gateway
        with patch("homeassistant.helpers.device_registry.format_mac", return_value="00:03:50:99:99:99"):
            await hass.services.async_call(
                DOMAIN, "sweep_bus", {ATTR_GATEWAY: "00:03:50:99:99:99"}, blocking=True
            )
        gateway.send.assert_not_called()

        # Test sync_time, send_message, and sweep_bus when no gateways exist in hass.data[DOMAIN]
        saved_data = hass.data[DOMAIN]
        hass.data[DOMAIN] = {}
        try:
            await hass.services.async_call(
                DOMAIN, "sync_time", {}, blocking=True
            )
            await hass.services.async_call(
                DOMAIN, "send_message", {ATTR_MESSAGE: "*1*1*12##"}, blocking=True
            )
            await hass.services.async_call(
                DOMAIN, "sweep_bus", {}, blocking=True
            )
        finally:
            hass.data[DOMAIN] = saved_data


async def test_options_update_rebuilds_decoder_pool(hass: HomeAssistant):
    """Test options update listener rebuilds decoder pool."""
    from custom_components.myhome.const import (
        CONF_DECODER_ENTITY,
        CONF_DECODER_PRE_GAIN,
        CONF_DECODER_SOURCE,
    )

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:34",
            },
            options={},
            unique_id="00:03:50:00:12:34",
        )
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Update options with a decoder mapping (lines 196-197)
        new_options = {
            CONF_DECODER_ENTITY.format(1): "media_player.zone1",
            CONF_DECODER_SOURCE.format(1): 1,
            CONF_DECODER_PRE_GAIN.format(1): 2,
        }
        hass.config_entries.async_update_entry(config_entry, options=new_options)
        await hass.async_block_till_done()

        pool = hass.data[DOMAIN]["00:03:50:00:12:34"]["decoder_pool"]
        assert pool is not None
        assert pool.is_configured is True
        assert "media_player.zone1" in pool._decoder_map


async def test_entity_migration_and_yaml_recovery_edges(hass: HomeAssistant):
    """Test entity registry auto-migration branches and customize.yaml error handling."""
    from unittest.mock import MagicMock
    mock_entry_reg = MagicMock()

    # Entity 1: unformatted MAC e.g. "000350001234-12" (lines 82-86)
    reg_e1 = MagicMock()
    reg_e1.domain = "light"
    reg_e1.entity_id = "light.myhome_light_12"
    reg_e1.unique_id = "000350001234-12"

    # Entity 2: causes ValueError in async_update_entity (lines 100-101)
    reg_e2 = MagicMock()
    reg_e2.domain = "light"
    reg_e2.entity_id = "light.myhome_conflict"
    reg_e2.unique_id = "00:03:50:00:12:34-13"

    # Entity 3: causes Exception in dr.format_mac (lines 85-86)
    reg_e3 = MagicMock()
    reg_e3.domain = "light"
    reg_e3.entity_id = "light.bad_mac"
    reg_e3.unique_id = "bad_mac-14"

    from homeassistant.helpers import device_registry as dr
    real_format_mac = dr.format_mac

    def fake_format_mac(val):
        if val == "bad_mac":
            raise ValueError("Corrupt MAC")
        return real_format_mac(val)

    mock_entry_reg.async_get_entity_id.return_value = None

    def update_side_effect(entity_id, new_unique_id):
        if "conflict" in entity_id:
            raise ValueError("Conflict")
        return MagicMock()

    mock_entry_reg.async_update_entity.side_effect = update_side_effect

    real_isfile = os.path.isfile

    def fake_isfile(p):
        if "customize.yaml" in str(p):
            return True
        return real_isfile(p)

    mock_dev_reg = MagicMock()
    old_dev1 = MagicMock(id="dev1")
    old_dev2 = MagicMock(id="dev2")
    mock_dev_reg.async_get_device.side_effect = [old_dev1, None, old_dev2, None, None, None]
    mock_dev_reg.async_update_device.side_effect = [None, Exception("Device update conflict")]

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch(
        "homeassistant.helpers.entity_registry.async_get",
        return_value=mock_entry_reg,
    ), patch(
        "homeassistant.helpers.device_registry.async_get",
        return_value=mock_dev_reg,
    ), patch(
        "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
        return_value=[reg_e1, reg_e2, reg_e3],
    ), patch(
        "homeassistant.helpers.device_registry.format_mac",
        side_effect=fake_format_mac,
    ), patch(
        "os.path.isfile",
        side_effect=fake_isfile,
    ), patch(
        "homeassistant.util.yaml.loader.load_yaml",
        side_effect=Exception("Corrupt YAML"),
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:34",
            },
            unique_id="00:03:50:00:12:34",
        )
        config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Both entities processed without crash despite ValueError and Corrupt YAML
        assert config_entry.state is ConfigEntryState.LOADED


async def test_setup_entry_duplicate_and_timeout(hass: HomeAssistant):
    """Test duplicate entry setup and gateway connection timeout error."""
    import asyncio

    from homeassistant.exceptions import ConfigEntryNotReady

    # 1. Config entry unique_id migration (lines 58-62)
    hass.data.setdefault(DOMAIN, {})
    unformatted_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:99:88", "host": "192.168.0.99", "port": 20000},
        unique_id="000350009988",  # Unformatted MAC triggers lines 58-62
    )
    unformatted_entry.add_to_hass(hass)
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        assert await hass.config_entries.async_setup(unformatted_entry.entry_id)
        await hass.async_block_till_done()
        assert unformatted_entry.unique_id == "00:03:50:00:99:88"
        assert unformatted_entry.state is ConfigEntryState.LOADED

        assert await hass.config_entries.async_unload(unformatted_entry.entry_id)
        await hass.async_block_till_done()

    # 2. Gateway test raises TimeoutError -> ConfigEntryNotReady (lines 126-133)
    timeout_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"mac": "00:03:50:00:99:77", "host": "192.168.0.98", "port": 20000},
        unique_id="00:03:50:00:99:77",
    )
    timeout_entry.add_to_hass(hass)
    from custom_components.myhome import async_setup_entry
    real_isfile = os.path.isfile

    def fake_isfile(p):
        if "customize.yaml" in str(p):
            return True
        return real_isfile(p)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        side_effect=asyncio.TimeoutError("Timed out connecting"),
    ), patch(
        "os.path.isfile",
        side_effect=fake_isfile,
    ), patch(
        "homeassistant.util.yaml.loader.load_yaml",
        return_value={"light.test": {"friendly_name": "Test Light"}},
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, timeout_entry)


async def test_register_frontend_branches(hass: HomeAssistant):
    """Test _async_register_frontend static path and frontend script registration."""
    from custom_components.myhome import _async_register_frontend, _get_card_url
    card_path = os.path.join(os.path.dirname(__file__), "..", "custom_components", "myhome", "frontend", "myhome-bus-card.js")
    expected_url = _get_card_url(card_path)
    assert "?v=" in expected_url

    # 1. Reset flag & test when http is None, early return
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["_frontend_registered"] = False
    hass.http = None
    with patch.object(hass, "is_running", False):
        await _async_register_frontend(hass)
    assert hass.data[DOMAIN]["_frontend_registered"] is False

    # 2. Simulate modern async_register_static_paths
    mock_http = MagicMock()
    mock_http.async_register_static_paths = AsyncMock()
    hass.http = mock_http

    with patch("homeassistant.components.http.StaticPathConfig", create=True), patch(
        "homeassistant.components.frontend.add_extra_js_url"
    ) as mock_add_url:
        await _async_register_frontend(hass)
        assert hass.data[DOMAIN]["_frontend_registered"] is True
        mock_add_url.assert_called_once_with(hass, expected_url)

    # 3. Early return when already registered
    mock_http.async_register_static_paths.reset_mock()
    await _async_register_frontend(hass)
    mock_http.async_register_static_paths.assert_not_called()

    # 4. Fallback to register_static_path and exception handling
    hass.data[DOMAIN]["_frontend_registered"] = False
    del mock_http.async_register_static_paths
    mock_http.register_static_path = MagicMock()
    with patch("homeassistant.components.frontend.add_extra_js_url", side_effect=Exception("Frontend error")):
        await _async_register_frontend(hass)
        assert hass.data[DOMAIN]["_frontend_registered"] is True
        assert mock_http.register_static_path.call_count >= 1

    # 5. async_register_static_paths raises exception and falls back to register_static_path
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_http.async_register_static_paths = AsyncMock(side_effect=Exception("Async static paths failed"))
    mock_http.register_static_path.reset_mock()
    with patch("homeassistant.components.http.StaticPathConfig", create=True):
        await _async_register_frontend(hass)
    assert hass.data[DOMAIN]["_frontend_registered"] is True
    assert mock_http.register_static_path.call_count >= 1

    # 5b. When static_path_cls is None, falls back to register_static_path even if async_register_static_paths exists
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_http.async_register_static_paths = AsyncMock()
    mock_http.register_static_path.reset_mock()
    with patch("homeassistant.components.http.StaticPathConfig", None, create=True):
        await _async_register_frontend(hass)
    assert hass.data[DOMAIN]["_frontend_registered"] is True
    assert mock_http.register_static_path.call_count >= 1

    # 6. Lovelace resource auto-registration (lines 69-78)
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_resources = MagicMock()
    mock_resources.async_items.return_value = [{"url": "/other/resource.js"}]
    mock_resources.async_create_item = AsyncMock()
    mock_lovelace = MagicMock()
    mock_lovelace.resources = mock_resources
    hass.data["lovelace"] = mock_lovelace

    await _async_register_frontend(hass)
    mock_resources.async_create_item.assert_awaited_once_with({
        "res_type": "module",
        "url": expected_url,
    })

    # 7. Lovelace resource already exists with old URL -> updates via async_update_item
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_resources.async_items.return_value = [{"id": "item1", "url": "/myhome_static/myhome-bus-card.js"}]
    mock_resources.async_create_item.reset_mock()
    mock_resources.async_update_item = AsyncMock()
    await _async_register_frontend(hass)
    mock_resources.async_create_item.assert_not_called()
    mock_resources.async_update_item.assert_awaited_once_with("item1", {
        "res_type": "module",
        "url": expected_url,
    })

    # 7b. Lovelace resource has old query param but async_update_item not available
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_res_noupdate = MagicMock()
    del mock_res_noupdate.async_update_item
    mock_res_noupdate.loaded = True
    mock_res_noupdate.async_items.return_value = [{"url": "/myhome_static/myhome-bus-card.js?v=old"}]
    mock_res_noupdate.async_create_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=mock_res_noupdate)
    await _async_register_frontend(hass)
    mock_res_noupdate.async_create_item.assert_not_called()

    # 8. Lovelace raises exception
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_resources.async_items.side_effect = Exception("Lovelace storage error")
    hass.data["lovelace"] = MagicMock(resources=mock_resources)
    with patch.object(hass, "is_running", False):
        await _async_register_frontend(hass)

    # 9. Lovelace has no resources attribute (returns False)
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_lovelace_no_res = MagicMock()
    mock_lovelace_no_res.resources = None
    hass.data["lovelace"] = mock_lovelace_no_res
    with patch.object(hass, "is_running", False):
        await _async_register_frontend(hass)

    # 10. Lovelace resources.loaded is False, verifies async_load is called
    hass.data[DOMAIN]["_frontend_registered"] = False
    unloaded_res = MagicMock()
    unloaded_res.loaded = False
    unloaded_res.async_load = AsyncMock()
    unloaded_res.async_items.return_value = []
    unloaded_res.async_create_item = AsyncMock()
    mock_lovelace.resources = unloaded_res
    hass.data["lovelace"] = mock_lovelace
    await _async_register_frontend(hass)
    unloaded_res.async_load.assert_awaited_once()
    assert unloaded_res.loaded is True
    unloaded_res.async_create_item.assert_awaited_once()

    # 11. Lovelace deferred registration on EVENT_HOMEASSISTANT_STARTED
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
    del hass.data["lovelace"]

    deferred_res = MagicMock()
    deferred_res.loaded = True
    deferred_res.async_items.return_value = []
    deferred_res.async_create_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=deferred_res)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    deferred_res.async_create_item.assert_awaited_once_with({
        "res_type": "module",
        "url": expected_url,
    })

    # 12. Lovelace resource with matching versioned URL does not duplicate or update
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_res_query = MagicMock()
    mock_res_query.loaded = True
    mock_res_query.async_items.return_value = [{"id": "item1", "url": expected_url}]
    mock_res_query.async_create_item = AsyncMock()
    mock_res_query.async_update_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=mock_res_query)
    await _async_register_frontend(hass)
    mock_res_query.async_create_item.assert_not_called()
    mock_res_query.async_update_item.assert_not_called()

    # 13. Lovelace not available registers started listener
    hass.data[DOMAIN]["_frontend_registered"] = False
    hass.data[DOMAIN]["_lovelace_listener_registered"] = False
    del hass.data["lovelace"]
    with patch.object(hass, "is_running", False):
        await _async_register_frontend(hass)
    assert hass.data[DOMAIN]["_lovelace_listener_registered"] is True

    # 14. Lovelace not available while hass is already running creates delayed retry task
    hass.data[DOMAIN]["_frontend_registered"] = False
    hass.data[DOMAIN]["_lovelace_listener_registered"] = False
    with patch.object(hass, "is_running", True), patch.object(hass, "async_create_task") as mock_create_task:
        await _async_register_frontend(hass)
        assert hass.data[DOMAIN]["_lovelace_listener_registered"] is True
        mock_create_task.assert_called_once()
        # Verify delayed retry coroutine executes _async_register_lovelace_resource
        coro = mock_create_task.call_args[0][0]
        retry_res = MagicMock(loaded=True, async_items=MagicMock(return_value=[]), async_create_item=AsyncMock())
        hass.data["lovelace"] = MagicMock(resources=retry_res)
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await coro
            mock_sleep.assert_awaited_once_with(1)
            retry_res.async_create_item.assert_awaited_once()

    # 15. Lovelace resources contains items with non-string or None URLs
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_res_malformed = MagicMock(
        loaded=True,
        async_items=MagicMock(return_value=[{"url": None}, {"url": 123}, {"other": "value"}]),
        async_create_item=AsyncMock(),
    )
    hass.data["lovelace"] = MagicMock(resources=mock_res_malformed)
    await _async_register_frontend(hass)
    mock_res_malformed.async_create_item.assert_awaited_once_with({
        "res_type": "module",
        "url": expected_url,
    })

    # 16. Test _get_card_url helper directly (fallbacks)
    assert _get_card_url("/non/existent/path/card.js") == "/myhome_static/myhome-bus-card.js"
    with patch("builtins.open", side_effect=Exception("Read error")):
        assert _get_card_url(card_path) == "/myhome_static/myhome-bus-card.js"

    # 17. Test _sync_www_card helper directly
    import tempfile

    from custom_components.myhome import _sync_www_card
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "src.js")
        dst = os.path.join(tmpdir, "sub", "dst.js")
        with open(src, "w") as f:
            f.write("test")
        _sync_www_card(src, dst)
        assert os.path.isfile(dst)
        with open(dst, "r") as f:
            assert f.read() == "test"
        # Exception branch
        with patch("shutil.copy2", side_effect=Exception("Copy error")):
            _sync_www_card(src, dst)


async def test_setup_entry_myhome_yaml_loading(hass: HomeAssistant):
    """Test loading legacy myhome.yaml with all branches."""
    import tempfile


    mac = "00:03:50:00:12:34"
    sample_yaml = """00:03:50:00:12:34:
  light:
    '01':
      name: 'Salon Plafonnier'
      dimmable: true
  cover:
    '02':
      name: 'Volet Salon'
000350001239:
  light:
    '03':
      name: 'Test'
"""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as tmp:
        tmp.write(sample_yaml)
        tmp_path = tmp.name

    try:
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": mac,
            },
            options={
                "file_path": tmp_path,
            },
            unique_id=mac,
        )
        config_entry.add_to_hass(hass)

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()

            assert "01" in hass.data[DOMAIN][mac][CONF_PLATFORMS]["light"]
            assert "02" in hass.data[DOMAIN][mac][CONF_PLATFORMS]["cover"]

            assert await hass.config_entries.async_unload(config_entry.entry_id)
            await hass.async_block_till_done()

        # Test mac matching branch: elif entry.data[CONF_MAC] in _validated
        entry_raw_mac = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.35", "port": 20000, "password": "pass", "mac": "000350001234"},
            options={"file_path": tmp_path},
            unique_id="00:03:50:00:12:34",
        )
        entry_raw_mac.add_to_hass(hass)
        mock_val_raw = {"000350001234": {CONF_PLATFORMS: {"light": {"01": {"where": "01"}}}}}
        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"), \
             patch("custom_components.myhome.validate.config_schema", return_value=mock_val_raw):
            assert await hass.config_entries.async_setup(entry_raw_mac.entry_id)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(entry_raw_mac.entry_id)
            await hass.async_block_till_done()

        # Test mac matching branch: else for k in _validated.keys() with invalid mac exception
        entry_fuzzy_mac = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.35", "port": 20000, "password": "pass", "mac": "00:03:50:00:12:34"},
            options={"file_path": tmp_path},
            unique_id="00:03:50:00:12:34",
        )
        entry_fuzzy_mac.add_to_hass(hass)
        mock_val_fuzzy = {
            12345: {},
            "000350001234": {CONF_PLATFORMS: {"light": {"01": {"where": "01"}}}},
        }
        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"), \
             patch("custom_components.myhome.validate.config_schema", return_value=mock_val_fuzzy):
            assert await hass.config_entries.async_setup(entry_fuzzy_mac.entry_id)
            await hass.async_block_till_done()
            assert await hass.config_entries.async_unload(entry_fuzzy_mac.entry_id)
            await hass.async_block_till_done()


        # Test fallback to /config/myhome.yaml (line 112)
        config_entry_fallback = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:35",
            },
            unique_id="00:03:50:00:12:35",
        )
        config_entry_fallback.add_to_hass(hass)

        def fake_isfile_fallback(path):
            if str(path) == "/config/myhome.yaml":
                return True
            if "customize.yaml" in str(path):
                return False
            return False

        with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"), \
             patch("os.path.isfile", side_effect=fake_isfile_fallback), \
             patch("homeassistant.util.yaml.loader.load_yaml", side_effect=Exception("Corrupt YAML")):
            assert await hass.config_entries.async_setup(config_entry_fallback.entry_id)
            await hass.async_block_till_done()

            assert await hass.config_entries.async_unload(config_entry_fallback.entry_id)
            await hass.async_block_till_done()

    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


async def test_empty_orphaned_device_pruning(hass: HomeAssistant):
    """Test that orphaned devices with 0 entities are pruned from the device registry on setup."""
    from homeassistant.helpers import device_registry as dr

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:88:99",
            },
            unique_id="00:03:50:00:88:99",
        )
        config_entry.add_to_hass(hass)

        dev_reg = dr.async_get(hass)
        orphan_device = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "00:03:50:00:88:99-orphan")},
            name="Orphaned Old Device",
        )
        assert orphan_device.id in dev_reg.devices

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # The orphan device with 0 entities should have been pruned
        assert orphan_device.id not in dev_reg.devices

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_prunes_empty_devices_but_preserves_cen(hass: HomeAssistant):
    """Test that setup prunes empty orphaned devices but preserves CEN scenario devices."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:88:77",
            },
            unique_id="00:03:50:00:88:77",
        )
        config_entry.add_to_hass(hass)

        dev_reg = dr.async_get(hass)
        cen_device = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "00:03:50:00:88:77-15-3")},
            name="CEN Unit 3",
        )
        cenplus_device = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "00:03:50:00:88:77-25-10")},
            name="CEN+ Unit 10",
        )
        empty_dry_contact = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "00:03:50:00:88:77-25-31")},
            name="Dry Contact 31",
            model="Dry Contact Interface",
        )
        orphan_device = dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "00:03:50:00:88:77-orphan")},
            name="Orphaned Device",
        )

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # The orphan device and empty dry contact with 0 entities should have been pruned
        assert orphan_device.id not in dev_reg.devices
        assert empty_dry_contact.id not in dev_reg.devices
        # The CEN and CEN+ devices must be preserved
        assert cen_device.id in dev_reg.devices
        assert cenplus_device.id in dev_reg.devices

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_async_customize_yaml(hass: HomeAssistant, tmp_path):
    """Test customize.yaml is loaded asynchronously using async_add_executor_job without blocking the loop."""
    custom_yaml_path = tmp_path / "customize.yaml"
    custom_yaml_path.write_text("light.living:\n  friendly_name: Living Spot\n", encoding="utf-8")

    with patch.object(hass.config, "path", return_value=str(custom_yaml_path)), patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch.object(
        hass, "async_add_executor_job", wraps=hass.async_add_executor_job
    ) as mock_executor:
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:88",
            },
            unique_id="00:03:50:00:12:88",
        )
        config_entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Verify async_add_executor_job was called to load customize.yaml
        assert mock_executor.called
        assert hass.data[DOMAIN]["customizations"].get("light.living", {}).get("friendly_name") == "Living Spot"

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_manufacturer_tuple_normalization(hass: HomeAssistant):
    """Test manufacturer passed as tuple/list is normalized to string for device registry."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:12:99",
                "manufacturer": ("BTicino S.p.A.",),
            },
            unique_id="00:03:50:00:12:99",
        )
        config_entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        dev_reg = dr.async_get(hass)
        gw_device = dev_reg.async_get_device(identifiers={(DOMAIN, "00:03:50:00:12:99")})
        assert gw_device is not None
        assert isinstance(gw_device.manufacturer, str)
        assert gw_device.manufacturer == "BTicino S.p.A."

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_device_pruning_uses_async_entries_for_config_entry(hass: HomeAssistant):
    """Test device pruning uses async_entries_for_config_entry instead of deprecated device_registry.devices."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch(
        "homeassistant.helpers.device_registry.async_entries_for_config_entry",
        wraps=dr.async_entries_for_config_entry,
    ) as mock_async_entries:
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:77:66",
            },
            unique_id="00:03:50:00:77:66",
        )
        config_entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert mock_async_entries.called

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_sw_version_list_normalization(hass: HomeAssistant):
    """Test sw_version passed as tuple/list is normalized to string for device registry."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:55:44",
                "firmware": ["2", "1", "0"],
            },
            unique_id="00:03:50:00:55:44",
        )
        config_entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        dev_reg = dr.async_get(hass)
        gw_device = dev_reg.async_get_device(identifiers={(DOMAIN, "00:03:50:00:55:44")})
        assert gw_device is not None
        assert isinstance(gw_device.sw_version, str)
        assert gw_device.sw_version == "2.1.0"

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_async_register_lovelace_resource_dict_storage_collection(hass: HomeAssistant):
    """Test auto-registering lovelace resource when hass.data['lovelace'] is a dictionary (real HA Core structure)."""
    from custom_components.myhome import _async_register_lovelace_resource

    mock_resources = MagicMock()
    mock_resources.loaded = False
    mock_resources.async_load = AsyncMock()
    mock_resources.async_items.return_value = []
    mock_resources.async_create_item = AsyncMock()

    hass.data["lovelace"] = {
        "mode": "storage",
        "dashboards": {},
        "resources": mock_resources,
    }

    result = await _async_register_lovelace_resource(hass, "/myhome_static/myhome-bus-card.js")
    assert result is True
    mock_resources.async_load.assert_awaited_once()
    assert mock_resources.loaded is True
    mock_resources.async_create_item.assert_awaited_once_with({
        "res_type": "module",
        "url": "/myhome_static/myhome-bus-card.js",
    })


async def test_setup_entry_mfg_fw_fallbacks_and_pruning_branches(hass: HomeAssistant):
    """Test setup_entry manufacturer/firmware fallbacks and device pruning edge cases."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:88:99",
                "friendly_name": "MyHOME Gateway",
                "manufacturer": "",
                "name": "F454",
                "firmware": None,
            },
            unique_id="00:03:50:00:88:99",
        )
        config_entry.add_to_hass(hass)

        dev_reg = dr.async_get(hass)
        # Pre-create devices to test pruning branches:
        # 1. Device matching gateway_unique_id
        dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "00:03:50:00:88:99")},
            name="GW Unique Device",
        )
        # 2. Device matching gateway_id
        dev_reg.async_get_or_create(
            config_entry_id=config_entry.entry_id,
            identifiers={(DOMAIN, "myhome_gw_id")},
            name="GW ID Device",
        )

        mock_dev1 = MagicMock(id="dev_other_1", identifiers={(DOMAIN, "00:03:50:00:88:99")})
        mock_dev2 = MagicMock(id="dev_other_2", identifiers={(DOMAIN, "myhome_gw_id")})

        with patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.manufacturer", new=["Legrand"]), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.firmware", new=[]), \
             patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.id", new="myhome_gw_id", create=True), \
             patch("homeassistant.helpers.device_registry.async_entries_for_config_entry", return_value=[mock_dev1, mock_dev2]):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_entry_pruning_exception_handled(hass: HomeAssistant):
    """Test that exceptions during empty device pruning are caught and logged."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.manufacturer",
        new="",
    ), patch(
        "homeassistant.helpers.device_registry.async_entries_for_config_entry",
        side_effect=RuntimeError("Pruning failed")
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.0.35",
                "port": 20000,
                "password": "pass",
                "mac": "00:03:50:00:88:AA",
                "friendly_name": "MyHOME Gateway",
                "name": "F454",
            },
            unique_id="00:03:50:00:88:AA",
        )
        config_entry.add_to_hass(hass)

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


def test_get_ownd_version():
    """Test get_ownd_version helper returns version or fallback."""
    from custom_components.myhome.const import get_ownd_version

    get_ownd_version.cache_clear()

    # Success path is cached so later event-loop callers do not touch disk.
    with patch("importlib.metadata.version", return_value="2.0.0b5") as mock_version:
        assert get_ownd_version() == "2.0.0b5"
        assert get_ownd_version() == "2.0.0b5"
        mock_version.assert_called_once_with("OWNd")

    # Exception fallback
    get_ownd_version.cache_clear()
    with patch("importlib.metadata.version", side_effect=Exception("Package not found")):
        assert get_ownd_version() == "unknown"

    get_ownd_version.cache_clear()


async def test_async_ensure_ownd_engine_fast_path(hass: HomeAssistant):
    """Test async_ensure_ownd_engine fast-path when version matches."""
    from custom_components.myhome import async_ensure_ownd_engine
    from custom_components.myhome.const import REQUIRED_OWND_VERSION

    with patch("homeassistant.util.package.is_installed", return_value=True), \
         patch("custom_components.myhome.get_ownd_version", return_value=REQUIRED_OWND_VERSION):
        result = await async_ensure_ownd_engine(hass)
        assert result is True


async def test_async_ensure_ownd_engine_auto_install_success(hass: HomeAssistant):
    """Test async_ensure_ownd_engine self-heals by installing missing requirement."""
    from custom_components.myhome import async_ensure_ownd_engine
    from custom_components.myhome.const import REQUIRED_OWND_VERSION

    installed_status = [False, True]
    def mock_is_installed(req):
        return installed_status.pop(0) if installed_status else True

    with patch("homeassistant.util.package.is_installed", side_effect=mock_is_installed), \
         patch("homeassistant.requirements.async_process_requirements", new_callable=AsyncMock) as mock_proc, \
         patch("importlib.reload") as mock_reload, \
         patch("importlib.invalidate_caches"), \
         patch("custom_components.myhome.get_ownd_version", side_effect=["2.0.0b2", REQUIRED_OWND_VERSION]):
        result = await async_ensure_ownd_engine(hass)
        assert result is True
        mock_proc.assert_awaited_once_with(hass, "myhome", [f"OWNd=={REQUIRED_OWND_VERSION}"])
        assert mock_reload.called


async def test_async_ensure_ownd_engine_install_failed(hass: HomeAssistant):
    """Test async_ensure_ownd_engine handles install failure and creates notification."""
    from custom_components.myhome import async_ensure_ownd_engine

    with patch("homeassistant.util.package.is_installed", return_value=False), \
         patch("homeassistant.requirements.async_process_requirements", side_effect=RuntimeError("Pip network timeout")), \
         patch("custom_components.myhome.get_ownd_version", return_value="2.0.0b2"), \
         patch("homeassistant.components.persistent_notification.async_create") as mock_notify:
        result = await async_ensure_ownd_engine(hass)
        assert result is False
        mock_notify.assert_called_once()

    # And when notification creation itself raises
    with patch("homeassistant.util.package.is_installed", return_value=False), \
         patch("homeassistant.requirements.async_process_requirements", side_effect=RuntimeError("Pip network timeout")), \
         patch("custom_components.myhome.get_ownd_version", return_value="2.0.0b2"), \
         patch("homeassistant.components.persistent_notification.async_create", side_effect=RuntimeError("Notification error")):
        result = await async_ensure_ownd_engine(hass)
        assert result is False


async def test_async_ensure_ownd_engine_reload_error(hass: HomeAssistant):
    """Test async_ensure_ownd_engine logs and survives reload failures."""
    from custom_components.myhome import async_ensure_ownd_engine
    from custom_components.myhome.const import REQUIRED_OWND_VERSION

    installed_status = [False, True]
    def mock_is_installed(req):
        return installed_status.pop(0) if installed_status else True

    with patch("homeassistant.util.package.is_installed", side_effect=mock_is_installed), \
         patch("homeassistant.requirements.async_process_requirements", new_callable=AsyncMock), \
         patch("importlib.reload", side_effect=RuntimeError("Module reload failure")), \
         patch("importlib.invalidate_caches"), \
         patch("custom_components.myhome.get_ownd_version", side_effect=["2.0.0b2", REQUIRED_OWND_VERSION]):
        result = await async_ensure_ownd_engine(hass)
        assert result is True


async def test_async_setup_entry_engine_mismatch_raises_not_ready(hass: HomeAssistant):
    """Test async_setup_entry raises ConfigEntryNotReady when engine cannot be synchronized."""
    from homeassistant.exceptions import ConfigEntryNotReady

    from custom_components.myhome import async_setup_entry

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:00:12:99", "host": "1.2.3.4", "port": 20000}, unique_id="00:03:50:00:12:99")
    with patch("custom_components.myhome.async_ensure_ownd_engine", return_value=False):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

async def test_async_setup_entry_uses_executor_for_ownd_version(hass: HomeAssistant):
    """Test async_setup_entry offloads get_ownd_version to executor to prevent loop blocking."""
    import asyncio

    from homeassistant.exceptions import ConfigEntryNotReady

    from custom_components.myhome import async_setup_entry
    from custom_components.myhome.const import get_ownd_version

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:00:12:99", "host": "1.2.3.4", "port": 20000}, unique_id="00:03:50:00:12:99")

    executor_targets = []
    real_executor = hass.async_add_executor_job

    async def track_executor(target, *args, **kwargs):
        executor_targets.append(target)
        return await real_executor(target, *args, **kwargs)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        side_effect=asyncio.TimeoutError("Timeout"),
    ), patch.object(hass, "async_add_executor_job", side_effect=track_executor):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    assert get_ownd_version in executor_targets

