"""Tests for the MyHOME custom component initialization."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_PLATFORMS, DOMAIN
from tests.conftest import attach_runtime


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


@pytest.mark.parametrize("reason", ["password_error", "password_required"])
async def test_setup_entry_auth_failed_starts_reauth(hass: HomeAssistant, reason: str):
    """A rejected password raises ConfigEntryAuthFailed and HA opens the reauth flow."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": False, "Message": reason}
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

        assert not result
        assert config_entry.state is ConfigEntryState.SETUP_ERROR
        # Home Assistant itself started the reauth flow for this entry
        flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        assert [f["context"]["source"] for f in flows] == ["reauth"]
        assert flows[0]["context"]["entry_id"] == config_entry.entry_id
        # Nothing was published for the entry: no runtime data, no legacy alias
        assert getattr(config_entry, "runtime_data", None) is None
        assert config_entry.data["mac"] not in hass.data[DOMAIN]


async def test_setup_entry_generic_test_failure_retries(hass: HomeAssistant):
    """A non-auth connection test failure raises ConfigEntryNotReady (retry)."""
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": False, "Message": "negotiation_refused"}
    ):
        config_entry = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.35", "port": 20000, "mac": "00:03:50:00:12:36", "name": "F454"},
            unique_id="00:03:50:00:12:36",
        )
        config_entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.SETUP_RETRY
        assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_unload_entry_keeps_state_when_platform_unload_fails(hass: HomeAssistant):
    """If a platform refuses to unload, runtime data and the gateway stay intact."""
    from custom_components.myhome import async_unload_entry
    from custom_components.myhome.const import CONF_ENTITY

    mac = "00:03:50:00:12:37"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac, "host": "1.2.3.4", "port": 20000}, unique_id=mac)
    entry.add_to_hass(hass)
    gateway = MagicMock()
    gateway.close_listener = AsyncMock(return_value=True)
    hass.data.setdefault(DOMAIN, {})[mac] = {CONF_ENTITY: gateway}
    runtime = attach_runtime(hass, entry, mac, gateway)

    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert await async_unload_entry(hass, entry) is False
    assert hass.data[DOMAIN][mac][CONF_ENTITY] is gateway
    assert entry.runtime_data is runtime
    gateway.close_listener.assert_not_awaited()

    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await async_unload_entry(hass, entry) is True
    assert mac not in hass.data[DOMAIN]
    assert entry.runtime_data is None
    gateway.close_listener.assert_awaited_once()


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

        # Test sync_time, send_message, and sweep_bus when no gateway is set up
        # (an entry without runtime_data does not count as a gateway)
        saved_runtime = config_entry.runtime_data
        config_entry.runtime_data = None
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
            config_entry.runtime_data = saved_runtime
        gateway.send.assert_not_called()


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

        pool = config_entry.runtime_data.decoder_pool
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
    # A mapping left under the legacy alias (an out-of-tree reader, an earlier
    # attempt) must not keep a half-initialised gateway visible during retries.
    hass.data.setdefault(DOMAIN, {})[timeout_entry.data["mac"]] = {
        "entity": MagicMock(), "bus_monitor": MagicMock(),
    }
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

    # A retry must not expose the half-initialised gateway or bus monitor to
    # services / WebSocket consumers while the entry is not loaded.
    failed_gateway_data = hass.data[DOMAIN].get(timeout_entry.data["mac"], {})
    assert "entity" not in failed_gateway_data
    assert "bus_monitor" not in failed_gateway_data


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

    # 4. add_extra_js_url failing is logged, registration still completes
    hass.data[DOMAIN]["_frontend_registered"] = False
    with patch("homeassistant.components.frontend.add_extra_js_url", side_effect=Exception("Frontend error")):
        await _async_register_frontend(hass)
        assert hass.data[DOMAIN]["_frontend_registered"] is True

    # 5. async_register_static_paths raising (already registered after a reload) is tolerated
    hass.data[DOMAIN]["_frontend_registered"] = False
    mock_http.async_register_static_paths = AsyncMock(side_effect=Exception("Async static paths failed"))
    with patch("homeassistant.components.http.StaticPathConfig", create=True):
        await _async_register_frontend(hass)
    assert hass.data[DOMAIN]["_frontend_registered"] is True
    mock_http.async_register_static_paths = AsyncMock()

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

        # Secondary bus devices through the real validator (#408): a MAC with a
        # dropped zero, an unquoted single-digit interface and a routed zone.
        multibus_yaml = """00:3:50:00:12:34:
  light:
    bus0_light:
      where: '13'
      name: 'Bus 0 Light'
    bus3_light:
      where: '13'
      interface: 3
      name: 'Bus 3 Light'
  climate:
    bus0_zone:
      zone: '1'
      name: 'Bus 0 Zone'
    bus3_zone:
      zone: '1'
      interface: '03'
      name: 'Bus 3 Zone'
"""
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as tmp:
            tmp.write(multibus_yaml)
            multibus_path = tmp.name
        entry_multibus = MockConfigEntry(
            domain=DOMAIN,
            data={"host": "192.168.0.35", "port": 20000, "password": "pass", "mac": "00:03:50:00:12:34"},
            options={"file_path": multibus_path},
            unique_id="00:03:50:00:12:34",
        )
        entry_multibus.add_to_hass(hass)
        try:
            with patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
                 patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
                 patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
                assert await hass.config_entries.async_setup(entry_multibus.entry_id)
                await hass.async_block_till_done()

                lights = entry_multibus.runtime_data.platforms["light"]
                assert lights["13"]["name"] == "Bus 0 Light"
                assert lights["1-13"]["name"] == "Bus 0 Light"
                assert lights["13#4#03"]["name"] == "Bus 3 Light"
                assert lights["1-13#4#03"]["name"] == "Bus 3 Light"
                assert "13#4#3" not in lights  # the validator normalises the interface

                zones = entry_multibus.runtime_data.platforms["climate"]
                assert zones["1"]["name"] == "Bus 0 Zone"
                assert zones["zone_1"]["name"] == "Bus 0 Zone"
                assert zones["1#4#03"]["name"] == "Bus 3 Zone"
                assert zones["4-1#4#03"]["name"] == "Bus 3 Zone"

                assert await hass.config_entries.async_unload(entry_multibus.entry_id)
                await hass.async_block_till_done()
        finally:
            os.unlink(multibus_path)


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
        assert dev_reg.async_get(orphan_device.id) is not None

        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # The orphan device with 0 entities should have been pruned
        assert dev_reg.async_get(orphan_device.id) is None

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
        assert dev_reg.async_get(orphan_device.id) is None
        assert dev_reg.async_get(empty_dry_contact.id) is None
        # The CEN and CEN+ devices must be preserved
        assert dev_reg.async_get(cen_device.id) is not None
        assert dev_reg.async_get(cenplus_device.id) is not None

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
        gw_device = next(d for d in dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id) if (DOMAIN, "00:03:50:00:12:99") in d.identifiers)
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
        gw_device = next(d for d in dr.async_entries_for_config_entry(dev_reg, config_entry.entry_id) if (DOMAIN, "00:03:50:00:55:44") in d.identifiers)
        assert gw_device is not None
        assert isinstance(gw_device.sw_version, str)
        assert gw_device.sw_version == "2.1.0"

        await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


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


async def test_ownd_version_resolved_once_off_loop(hass: HomeAssistant):
    """The OWNd version is read from disk once in the executor and cached in hass.data."""
    from custom_components.myhome import _async_resolve_ownd_version
    from custom_components.myhome.const import DATA_OWND_VERSION, get_ownd_version

    executor_targets = []
    real_executor = hass.async_add_executor_job

    async def track_executor(target, *args, **kwargs):
        executor_targets.append(target)
        return await real_executor(target, *args, **kwargs)

    hass.data.pop(DOMAIN, None)
    with patch.object(hass, "async_add_executor_job", side_effect=track_executor):
        first = await _async_resolve_ownd_version(hass)
        second = await _async_resolve_ownd_version(hass)
    assert first == second == hass.data[DOMAIN][DATA_OWND_VERSION]
    assert executor_targets == [get_ownd_version]


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

    hass.data.pop(DOMAIN, None)
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        side_effect=asyncio.TimeoutError("Timeout"),
    ), patch.object(hass, "async_add_executor_job", side_effect=track_executor):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    assert get_ownd_version in executor_targets


async def test_remove_config_entry_device_refuses_gateway_allows_others(hass: HomeAssistant):
    """Quality-scale stale-devices: bus devices may be deleted, the gateway may not."""
    from homeassistant.helpers import device_registry as dr

    from custom_components.myhome import async_remove_config_entry_device

    mac = "00:03:50:00:12:40"
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": mac, "host": "1.2.3.4", "port": 20000}, unique_id=mac)
    entry.add_to_hass(hass)
    gateway = MagicMock()
    gateway.mac = mac
    gateway.unique_id = mac
    gateway.id = mac
    attach_runtime(hass, entry, mac, gateway)

    registry = dr.async_get(hass)
    gateway_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, mac)},
        identifiers={(DOMAIN, mac)},
        name="Gateway",
    )
    light_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-1-12")},
        name="Light 12",
    )
    mac_only_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, mac.upper())},
        identifiers={("other", "x")},
        name="Gateway by MAC",
    )

    assert await async_remove_config_entry_device(hass, entry, gateway_device) is False
    assert await async_remove_config_entry_device(hass, entry, mac_only_device) is False
    assert await async_remove_config_entry_device(hass, entry, light_device) is True

    # An entry that is not set up still allows removing bus devices, never the gateway
    entry.runtime_data = None
    assert await async_remove_config_entry_device(hass, entry, light_device) is True
    assert await async_remove_config_entry_device(hass, entry, gateway_device) is False


async def test_options_update_flags_a_stream_incompatible_decoder(hass: HomeAssistant):
    """Saving options raises the cambridge_audio repair at once, not after a restart."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import issue_registry as ir

    from custom_components.myhome.const import CONF_DECODER_ENTITY, CONF_DECODER_SOURCE

    er.async_get(hass).async_get_or_create(
        "media_player", "cambridge_audio", "unique_cxn", suggested_object_id="cxn"
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

        issue_id = f"incompatible_decoder_platform_{config_entry.entry_id}_media_player_cxn"
        hass.config_entries.async_update_entry(
            config_entry,
            options={
                CONF_DECODER_ENTITY.format(1): "media_player.cxn",
                CONF_DECODER_SOURCE.format(1): 1,
            },
        )
        await hass.async_block_till_done()
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
        pool = config_entry.runtime_data.decoder_pool
        assert pool.stream_incompatible == frozenset({"media_player.cxn"})

        hass.config_entries.async_update_entry(config_entry, options={})
        await hass.async_block_till_done()
        assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
