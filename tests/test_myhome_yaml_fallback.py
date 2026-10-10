"""Tests for myhome.yaml backwards-compatibility fallback, WHO 14 buttons, and Lovelace registration."""
import os
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_FILE_PATH,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.validate import config_schema

SAMPLE_YAML_CONTENT = """
00:03:50:81:22:33:
  light:
    living_room:
      where: "12"
      name: "Living Room Light"
      dimmable: true
  cover:
    kitchen_blind:
      where: "25"
      name: "Kitchen Blind"
      advanced_shutter: true
"""


def test_schema_without_mac_key_and_with_advanced_shutter():
    """Test config_schema parses gateway without mac key and advanced_shutter alias."""
    parsed = yaml.safe_load(SAMPLE_YAML_CONTENT)
    validated = config_schema(parsed)

    # Keyed by formatted mac
    mac_key = "00:03:50:81:22:33"
    assert mac_key in validated
    assert "light" in validated[mac_key][CONF_PLATFORMS]
    assert "cover" in validated[mac_key][CONF_PLATFORMS]
    assert "button" in validated[mac_key][CONF_PLATFORMS]

    # Verify cover has advanced_shutter parsed (rekeyed to who-where "2-25")
    cover_cfg = validated[mac_key][CONF_PLATFORMS]["cover"]["2-25"]
    assert cover_cfg.get("advanced_shutter") is True
    assert cover_cfg.get("advanced") is True

    # Verify buttons created for both light and cover
    button_cfgs = validated[mac_key][CONF_PLATFORMS]["button"]
    assert "1-12" in button_cfgs
    assert "2-25" in button_cfgs


async def test_setup_entry_with_yaml_fallback(hass: HomeAssistant, tmp_path):
    """Test loading myhome.yaml fallback, entity seeding, and WHO 14 button pressing."""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(SAMPLE_YAML_CONTENT, encoding="utf-8")

    # Mock Lovelace resources collection
    resources_list = []
    mock_resources = MagicMock()
    mock_resources.async_items = MagicMock(side_effect=lambda: list(resources_list))
    async def _mock_create(item):
        resources_list.append(item)
    mock_resources.async_create_item = AsyncMock(side_effect=_mock_create)
    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    mac = "00:03:50:81:22:33"
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options={
            CONF_FILE_PATH: str(yaml_file),
        },
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        mock_send = AsyncMock()
        with patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send", mock_send):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()

            assert config_entry.state is ConfigEntryState.LOADED

            # Check Lovelace card registration was triggered
            mock_resources.async_create_item.assert_called_once()
            call_arg = mock_resources.async_create_item.call_args[0][0]
            assert call_arg["res_type"] == "module"
            assert call_arg["url"].startswith("/myhome_static/myhome-bus-card.js?v=")

            # Check entities were added
            ent_reg = er.async_get(hass)
            entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
            domains = {e.domain for e in entries}
            assert "light" in domains
            assert "cover" in domains
            assert "button" in domains

            # Find buttons and test WHO 14 press
            buttons = [e for e in entries if e.domain == "button"]
            assert len(buttons) >= 4  # Lock + Unlock for light and cover

            # Retrieve button entities from hass.data
            platforms = hass.data[DOMAIN][mac][CONF_PLATFORMS]
            assert "button" in platforms

            # Test pressing Lock on light 12 -> sends *14*0*12##
            button_entities = []
            for dev_id, dev_data in platforms.get("button", {}).items():
                if "entities" in dev_data:
                    button_entities.extend(dev_data["entities"].values())

            # Find disable/enable button entities
            for btn in button_entities:
                await btn.async_press()

            # Verify send was called with *14*
            sent_frames = [str(call.args[0]) for call in mock_send.call_args_list]
            assert any("*14*0*12##" in f for f in sent_frames)
            assert any("*14*1*12##" in f for f in sent_frames)
            assert any("*14*0*25##" in f for f in sent_frames)
            assert any("*14*1*25##" in f for f in sent_frames)

            # Cleanup
            assert await hass.config_entries.async_unload(config_entry.entry_id)
            await hass.async_block_till_done()


async def test_yaml_fallback_default_locations(hass: HomeAssistant, tmp_path):
    """Test fallback search paths when file_path option is not provided."""
    fake_config_dir = tmp_path / "config"
    fake_config_dir.mkdir()
    yaml_file = fake_config_dir / "myhome.yaml"
    yaml_file.write_text(SAMPLE_YAML_CONTENT, encoding="utf-8")

    mac = "00:03:50:81:22:33"
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options={},
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    with patch.object(hass.config, "path", return_value=str(yaml_file)), patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Platforms should have been seeded from myhome.yaml
        assert mac in hass.data[DOMAIN]
        platforms = hass.data[DOMAIN][mac][CONF_PLATFORMS]
        assert "light" in platforms
        assert "cover" in platforms

        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_device_registry_migration_from_legacy(hass: HomeAssistant):
    """Test transparent migration of device_registry identifiers from {mac}-{where} to {mac}-{who}-{where}."""
    mac = "00:03:50:81:22:33"
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options={},
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    # Create legacy device and entity
    legacy_device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-12")},
        name="Old Light Device",
    )
    legacy_entity = ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-12",
        config_entry=config_entry,
        device_id=legacy_device.id,
    )

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        # Check that entity unique_id was migrated
        migrated_entry = ent_reg.async_get(legacy_entity.entity_id)
        assert migrated_entry.unique_id == f"{mac}-1-12"

        # Check that device identifier was migrated
        updated_device = dev_reg.async_get(legacy_device.id)
        assert (DOMAIN, f"{mac}-1-12") in updated_device.identifiers

        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_lovelace_registration_exception(hass: HomeAssistant):
    """Test exception handling during Lovelace resource auto-registration."""
    mock_resources = MagicMock()
    mock_resources.async_items.return_value = []
    mock_resources.async_create_item = AsyncMock(side_effect=Exception("Lovelace storage write failure"))
    hass.data["lovelace"] = MagicMock()
    hass.data["lovelace"].resources = mock_resources

    from custom_components.myhome import _async_register_frontend
    # Should safely swallow exception and log debug without raising
    await _async_register_frontend(hass)


async def test_yaml_root_config_fallback_and_missing_where(hass: HomeAssistant, tmp_path):
    """Test /config/myhome.yaml fallback, where-less device dictionaries, and unformatted MAC matching."""
    yaml_content = """
000350812233:
  light:
    "12":
      name: "Light 12 No Where"
"""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")

    mac = "00:03:50:81:22:33"
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options={},
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    real_isfile = os.path.isfile
    def fake_isfile(path):
        if str(path) == "/config/myhome.yaml":
            return True
        if str(path) == str(yaml_file):
            return True
        return real_isfile(path)

    with patch("os.path.isfile", side_effect=fake_isfile), \
         patch("homeassistant.util.yaml.loader.load_yaml", return_value=yaml.safe_load(yaml_content)), \
         patch("custom_components.myhome.gateway.OWNSession.test_connection", return_value={"Success": True, "Message": None}), \
         patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"), \
         patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

        assert "light" in hass.data[DOMAIN][mac][CONF_PLATFORMS]
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_yaml_load_error_and_device_migration_error(hass: HomeAssistant, tmp_path):
    """Test graceful error handling when load_yaml or device migration fails."""
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text("corrupted content: [", encoding="utf-8")

    mac = "00:03:50:81:22:33"
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options={
            CONF_FILE_PATH: str(yaml_file),
        },
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    # Legacy device to trigger migration error
    legacy_device = dev_reg.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"{mac}-14")},
        name="Old Light 14",
    )
    ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-14",
        config_entry=config_entry,
        device_id=legacy_device.id,
    )

    orig_update = dev_reg.async_update_device
    call_count = [0]
    def fake_update(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("Database lock error")
        return orig_update(*args, **kwargs)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ), patch.object(
        dev_reg, "async_update_device", side_effect=fake_update
    ):
        # Should complete setup smoothly without crashing
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()


async def test_switch_yaml_fallback_deduplication_and_no_ghost_light(hass: HomeAssistant, tmp_path):
    """Test Issue #241: switch from myhome.yaml is not duplicated, cleans ghost lights, and updates from bus."""
    from homeassistant.components.switch import SwitchDeviceClass
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from OWNd.message import OWNLightingEvent

    yaml_content = """
00:03:50:81:22:33:
  switch:
    prise_sam:
      where: '06'
      name: "Living room Socket"
      class: "outlet"
      manufacturer: "BTicino"
      model: "F411/4"
"""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")

    mac = "00:03:50:81:22:33"
    ent_reg = er.async_get(hass)

    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac,
            "name": "F454",
            "firmware": "2.0.0",
        },
        options={
            CONF_FILE_PATH: str(yaml_file),
        },
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    # Pre-seed a ghost light and a corrupted duplicate switch in entity registry
    ghost_light = ent_reg.async_get_or_create(
        domain="light",
        platform=DOMAIN,
        unique_id=f"{mac}-1-06",
        config_entry=config_entry,
    )
    corrupted_switch = ent_reg.async_get_or_create(
        domain="switch",
        platform=DOMAIN,
        unique_id=f"{mac}-1-1-06",
        config_entry=config_entry,
    )

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        mock_send = AsyncMock()
        with patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.send", mock_send):
            assert await hass.config_entries.async_setup(config_entry.entry_id)
            await hass.async_block_till_done()
            hass.data[DOMAIN][mac][CONF_ENTITY]._on_event_connection_state_change(True)
            await hass.async_block_till_done()

            # Verify ghost light and corrupted duplicate switch were purged
            assert ent_reg.async_get(ghost_light.entity_id) is None
            assert ent_reg.async_get(corrupted_switch.entity_id) is None

            # Verify exactly ONE switch entity exists
            entries = er.async_entries_for_config_entry(ent_reg, config_entry.entry_id)
            switches = [e for e in entries if e.domain == "switch"]
            assert len(switches) == 1
            assert switches[0].unique_id == f"{mac}-1-06"

            # Check switch entity state and attributes
            sw_state = hass.states.get("switch.living_room_socket")
            assert sw_state is not None
            assert sw_state.attributes.get("device_class") == SwitchDeviceClass.OUTLET
            assert sw_state.attributes.get("friendly_name") == "Living room Socket"

            # Simulate incoming WHO=1 message for where 06 (e.g. from active discovery or bus event)
            msg = OWNLightingEvent.parse("*1*1*06##")
            async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
            await hass.async_block_till_done()

            # Verify NO light entity was created
            assert hass.states.get("light.light_06") is None
            assert hass.states.get("light.living_room_socket") is None

            # Verify switch state updated to 'on'
            sw_state_updated = hass.states.get("switch.living_room_socket")
            assert sw_state_updated.state == "on"

            # Test switch turn_off sends correct OWN command
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": "switch.living_room_socket"}, blocking=True
            )
            sent_frames = [str(call.args[0]) for call in mock_send.call_args_list]
            assert any("*1*0*06##" in f for f in sent_frames)

            assert await hass.config_entries.async_unload(config_entry.entry_id)
            await hass.async_block_till_done()


async def test_yaml_root_level_platforms_single_vs_multiple_gateways(hass: HomeAssistant, tmp_path, caplog):
    """Test single-gateway auto-bind vs multi-gateway safe error when MAC header is omitted."""
    import logging
    caplog.set_level(logging.ERROR)

    yaml_content = """
switch:
  socket_hall:
    where: '08'
    name: "Hall Socket"
    class: "outlet"
"""
    yaml_file = tmp_path / "myhome.yaml"
    yaml_file.write_text(yaml_content, encoding="utf-8")

    mac1 = "00:03:50:81:22:33"
    entry1 = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.1.50",
            "port": 20000,
            "password": "12345",
            "mac": mac1,
            "name": "F454 Gateway 1",
            "firmware": "2.0.0",
        },
        options={
            CONF_FILE_PATH: str(yaml_file),
        },
        unique_id=mac1,
    )
    entry1.add_to_hass(hass)

    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None},
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        # Single gateway: should auto-bind successfully
        assert await hass.config_entries.async_setup(entry1.entry_id)
        await hass.async_block_till_done()

        assert "switch" in hass.data[DOMAIN][mac1][CONF_PLATFORMS]
        assert hass.states.get("switch.hall_socket") is not None

        # Add a second gateway to test multi-gateway safety
        mac2 = "00:03:50:81:22:44"
        entry2 = MockConfigEntry(
            domain=DOMAIN,
            data={
                "host": "192.168.1.51",
                "port": 20000,
                "password": "12345",
                "mac": mac2,
                "name": "F454 Gateway 2",
                "firmware": "2.0.0",
            },
            options={
                CONF_FILE_PATH: str(yaml_file),
            },
            unique_id=mac2,
        )
        entry2.add_to_hass(hass)

        # Setting up entry2 with root-level platforms in myhome.yaml should log an error and NOT guess
        caplog.clear()
        assert await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()

        assert any("myhome.yaml contains top-level platform configurations without a gateway MAC" in rec.message for rec in caplog.records)

        assert await hass.config_entries.async_unload(entry1.entry_id)
        assert await hass.config_entries.async_unload(entry2.entry_id)
        await hass.async_block_till_done()
