"""Tests for MyHOME HA platform entities handle_event methods using lightweight mocking."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNEvent


@pytest.fixture
def mock_hass():
    """Create a minimal mock Home Assistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.async_create_task = MagicMock()
    return hass

@pytest.fixture
def mock_gateway():
    """Create a minimal mock gateway handler."""
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.unique_id = "00:03:50:00:12:34"
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw

@pytest.fixture
def mock_entity_base_init():
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield

# ── Light Entity ─────────────────────────────────────────────────────────

class TestLightEntity:

    @pytest.fixture
    def light(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.light import MyHOMELight
        light_entity = MyHOMELight(
            hass=mock_hass,
            name="Light 1",
            entity_name="Light 1",
            icon="mdi:lightbulb",
            icon_on="mdi:lightbulb-on",
            device_id="1#21",
            who="1",
            where="21",
            interface="l",
            dimmable=False,
            manufacturer="BTicino",
            model="Dimmer",
            gateway=mock_gateway,
        )
        light_entity.async_schedule_update_ha_state = MagicMock()
        return light_entity

    def test_handle_event_on(self, light):
        msg = OWNEvent.parse("*1*1*21##")
        light.handle_event(msg)
        assert light._attr_is_on is True
        light.async_schedule_update_ha_state.assert_called()

    def test_handle_event_off(self, light):
        msg = OWNEvent.parse("*1*0*21##")
        light.handle_event(msg)
        assert light._attr_is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on(self, light):
        await light.async_turn_on()
        light._gateway_handler.send.assert_called_once()
        assert "*1*1*21#4#l##" in str(light._gateway_handler.send.call_args[0][0])

    @pytest.mark.asyncio
    async def test_async_turn_off(self, light):
        await light.async_turn_off()
        light._gateway_handler.send.assert_called_once()
        assert "*1*0*21#4#l##" == str(light._gateway_handler.send.call_args[0][0])

    @pytest.mark.asyncio
    async def test_async_update(self, light):
        await light.async_update()
        light._gateway_handler.send_status_request.assert_called()

# ── Switch Entity ─────────────────────────────────────────────────────────

class TestSwitchEntity:

    @pytest.fixture
    def switch(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.switch import MyHOMESwitch
        s = MyHOMESwitch(
            hass=mock_hass,
            name="Switch 1",
            entity_name="Switch 1",
            device_id="1#22",
            who="1",
            where="22",
            interface="s",
            device_class="switch",
            icon="mdi:flash",
            icon_on="mdi:flash",
            manufacturer="BTicino",
            model="Relay",
            gateway=mock_gateway,
        )
        s.async_schedule_update_ha_state = MagicMock()
        return s

    def test_handle_event_on(self, switch):
        msg = OWNEvent.parse("*1*1*22##")
        switch.handle_event(msg)
        assert switch._attr_is_on is True
        switch.async_schedule_update_ha_state.assert_called()

    def test_handle_event_off(self, switch):
        msg = OWNEvent.parse("*1*0*22##")
        switch.handle_event(msg)
        assert switch._attr_is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on(self, switch):
        await switch.async_turn_on()
        switch._gateway_handler.send.assert_called_once()
        assert "*1*1*22#4#s##" == str(switch._gateway_handler.send.call_args[0][0])

    @pytest.mark.asyncio
    async def test_async_turn_off(self, switch):
        await switch.async_turn_off()
        switch._gateway_handler.send.assert_called_once()
        assert "*1*0*22#4#s##" == str(switch._gateway_handler.send.call_args[0][0])

    @pytest.mark.asyncio
    async def test_async_update(self, switch):
        await switch.async_update()
        switch._gateway_handler.send_status_request.assert_called()

    def test_handle_event_outlet(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.switch import MyHOMESwitch
        s = MyHOMESwitch(
            hass=mock_hass,
            name="Outlet 1",
            entity_name="Outlet 1",
            device_id="1#23",
            who="1",
            where="23",
            interface=None,
            device_class="outlet",
            icon=None,
            icon_on=None,
            manufacturer="BTicino",
            model="Relay",
            gateway=mock_gateway,
        )
        s.async_schedule_update_ha_state = MagicMock()
        msg = OWNEvent.parse("*1*1*23##")
        s.handle_event(msg)
        assert s._attr_is_on is True

    def test_handle_event_other_device_class_and_runtime_error(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.switch import MyHOMESwitch
        s = MyHOMESwitch(
            hass=mock_hass,
            name="Generic 1",
            entity_name="Generic 1",
            device_id="1#24",
            who="1",
            where="24",
            interface=None,
            device_class="custom_class",
            icon="mdi:icon",
            icon_on="mdi:icon-on",
            manufacturer="BTicino",
            model="Relay",
            gateway=mock_gateway,
        )
        # Force a non-standard device class to trigger the `else:` branch in handle_event
        s._attr_device_class = "other"
        # Make async_schedule_update_ha_state raise RuntimeError
        s.async_schedule_update_ha_state = MagicMock(side_effect=RuntimeError("Loop closing"))
        msg = OWNEvent.parse("*1*0*24##")
        s.handle_event(msg)
        assert s._attr_is_on is False
        assert s._attr_icon == "mdi:icon"

    @pytest.mark.asyncio
    async def test_async_setup_and_unload_entry(self, mock_hass, mock_gateway):
        from custom_components.myhome.const import CONF_ENTITY, CONF_PLATFORMS, DOMAIN
        from custom_components.myhome.switch import async_setup_entry, async_unload_entry

        config_entry = MagicMock()
        config_entry.data = {"mac": "00:03:50:00:12:34"}

        # 0. Missing or unconfigured MAC -> returns True
        bad_entry = MagicMock()
        bad_entry.data = {"mac": "unknown_mac"}
        assert await async_setup_entry(mock_hass, bad_entry, MagicMock()) is True
        assert await async_unload_entry(mock_hass, bad_entry) is True

        # 1. PLATFORM not configured -> returns True
        mock_hass.data = {
            DOMAIN: {
                "00:03:50:00:12:34": {
                    CONF_PLATFORMS: {},
                    CONF_ENTITY: mock_gateway,
                }
            }
        }
        res_setup = await async_setup_entry(mock_hass, config_entry, MagicMock())
        assert res_setup is True
        res_unload = await async_unload_entry(mock_hass, config_entry)
        assert res_unload is True

        # 2. PLATFORM configured with existing registry entries -> restores and creates entities
        mock_hass.data[DOMAIN]["00:03:50:00:12:34"][CONF_PLATFORMS] = {
            "switch": {
                "sw1": {
                    "who": "1",
                    "where": "21",
                    "icon": "mdi:toggle-switch",
                    "icon_on": "mdi:toggle-switch-off",
                    "name": "Switch 1",
                    "entity_name": "Switch 1",
                    "device_class": "outlet",
                    "manufacturer": "BTicino",
                    "model": "F411",
                },
                "sw_dup": {
                    "where": "21",
                },
            }
        }

        # Mock existing registry entries: one corrupt with "-1-1-", one with "#4#01" interface, one standard
        corrupt_entry = MagicMock()
        corrupt_entry.domain = "switch"
        corrupt_entry.unique_id = "00:03:50:00:12:34-1-1-21"
        corrupt_entry.entity_id = "switch.corrupt"

        interface_entry = MagicMock()
        interface_entry.domain = "switch"
        interface_entry.unique_id = "00:03:50:00:12:34-1-22#4#01"
        interface_entry.entity_id = "switch.interfaced"

        standard_entry = MagicMock()
        standard_entry.domain = "switch"
        standard_entry.unique_id = "00:03:50:00:12:34-1-23"
        standard_entry.entity_id = "switch.standard"

        mock_registry = MagicMock()

        with patch(
            "custom_components.myhome.switch.er.async_get",
            return_value=mock_registry,
        ), patch(
            "custom_components.myhome.switch.er.async_entries_for_config_entry",
            return_value=[corrupt_entry, interface_entry, standard_entry],
        ):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, config_entry, async_add_entities)
            mock_registry.async_remove.assert_called_once_with("switch.corrupt")
            async_add_entities.assert_called_once()
            assert len(async_add_entities.call_args[0][0]) == 3

        # Test entity registry exception (lines 50-52)
        with patch("custom_components.myhome.switch.er.async_get", side_effect=Exception("Registry error")):
            await async_setup_entry(mock_hass, config_entry, MagicMock())

        # 3. Test MyHOMESwitch async_added_to_hass with interface
        from custom_components.myhome.switch import MyHOMESwitch
        sw_interface = MyHOMESwitch(
            hass=mock_hass,
            name="Switch Interfaced",
            entity_name="Switch Interfaced",
            icon="mdi:icon",
            icon_on="mdi:icon_on",
            device_id="22#4#01",
            who="1",
            where="22",
            interface="01",
            device_class="switch",
            manufacturer="BTicino",
            model="F411",
            gateway=mock_gateway,
        )
        sw_interface.hass = mock_hass
        sw_interface.async_on_remove = MagicMock()
        sw_interface.async_update = AsyncMock()

        with patch("custom_components.myhome.switch.async_dispatcher_connect"):
            await sw_interface.async_added_to_hass()
        # Connected to both full_where and base where
        assert sw_interface.async_on_remove.call_count == 3

        # 4. Unload
        await async_unload_entry(mock_hass, config_entry)
        assert "sw1" not in mock_hass.data[DOMAIN]["00:03:50:00:12:34"][CONF_PLATFORMS]["switch"]

# ── Cover Entity ─────────────────────────────────────────────────────────


class TestCoverEntity:

    @pytest.fixture
    def cover(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.cover import MyHOMECover
        c = MyHOMECover(
            hass=mock_hass,
            name="Cover 1",
            entity_name="Cover 1",
            device_id="2#23",
            who="2",
            where="23",
            interface="c",
            advanced=False,
            manufacturer="BTicino",
            model="Blind Actuator",
            gateway=mock_gateway,
        )
        c.async_schedule_update_ha_state = MagicMock()
        return c

    def test_handle_event_up(self, cover):
        msg = OWNEvent.parse("*2*1*23##")
        cover.handle_event(msg)
        assert cover._attr_is_opening is True

    def test_handle_event_down(self, cover):
        msg = OWNEvent.parse("*2*2*23##")
        cover.handle_event(msg)
        assert cover._attr_is_closing is True

    def test_handle_event_stop(self, cover):
        msg = OWNEvent.parse("*2*0*23##")
        cover.handle_event(msg)
        assert cover._attr_is_opening is False
        assert cover._attr_is_closing is False

    @pytest.mark.asyncio
    async def test_async_open_cover(self, cover):
        await cover.async_open_cover()
        cover._gateway_handler.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_close_cover(self, cover):
        await cover.async_close_cover()
        cover._gateway_handler.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_stop_cover(self, cover):
        await cover.async_stop_cover()
        cover._gateway_handler.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_update(self, cover):
        await cover.async_update()
        cover._gateway_handler.send_status_request.assert_called()

    @pytest.mark.asyncio
    async def test_cover_advanced_and_set_position(self, mock_hass, mock_gateway, mock_entity_base_init):
        from homeassistant.components.cover import CoverEntityFeature

        from custom_components.myhome.cover import MyHOMECover
        c = MyHOMECover(
            hass=mock_hass,
            name="Advanced Cover",
            entity_name=None,
            device_id="23#4#1",
            who="2",
            where="23",
            interface="1",
            advanced=True,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
        )
        assert c._attr_supported_features & CoverEntityFeature.SET_POSITION

        # Call async_set_cover_position with position
        await c.async_set_cover_position(position=75)
        mock_gateway.send.assert_called_once()
        assert "*2*1000#75*23#4#1##" in str(mock_gateway.send.call_args[0][0]) or "75" in str(mock_gateway.send.call_args[0][0])

        # Call async_set_cover_position without position (no-op)
        mock_gateway.send.reset_mock()
        await c.async_set_cover_position()
        mock_gateway.send.assert_not_called()

    def test_handle_event_closed_and_position_and_runtime_error(self, cover):
        msg = MagicMock()
        msg.is_opening = False
        msg.is_closing = False
        msg.is_closed = True
        msg.current_position = 0
        msg.human_readable_log = "Cover closed at 0%"

        cover.async_schedule_update_ha_state = MagicMock(side_effect=RuntimeError("Bus disconnected"))
        cover.handle_event(msg)
        assert cover._attr_is_closed is True
        assert cover._attr_current_cover_position == 0

    @pytest.mark.asyncio
    async def test_cover_async_setup_and_unload_entry(self, mock_hass, mock_gateway):
        from custom_components.myhome.const import CONF_ENTITY, DOMAIN
        from custom_components.myhome.cover import async_setup_entry, async_unload_entry

        config_entry = MagicMock()
        config_entry.data = {"mac": "00:03:50:00:12:34"}
        config_entry.entry_id = "test_entry"
        listeners = []
        config_entry.async_on_unload = MagicMock(side_effect=lambda cb: listeners.append(cb))

        mock_hass.data = {
            DOMAIN: {
                "00:03:50:00:12:34": {
                    CONF_ENTITY: mock_gateway,
                }
            }
        }

        # 1. Mock entity registry with an existing entry having #4# and one without #4# (lines 62-68)
        mock_er = MagicMock()
        entry_with_int = MagicMock()
        entry_with_int.domain = "cover"
        entry_with_int.unique_id = "00:03:50:00:12:34-2-18#4#02"

        entry_plain = MagicMock()
        entry_plain.domain = "cover"
        entry_plain.unique_id = "00:03:50:00:12:34-2-19"

        mock_er.entities = {"test1": entry_with_int, "test2": entry_plain}

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
             patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[entry_with_int, entry_plain]):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, config_entry, async_add_entities)
            async_add_entities.assert_called_once()
            restored = async_add_entities.call_args[0][0]
            assert len(restored) == 2
            assert restored[0]._where == "18"
            assert restored[0]._interface == "02"
            assert restored[1]._where == "19"
            assert restored[1]._interface is None

            # 2. Test message listener handling
            assert config_entry.async_on_unload.called
            assert len(listeners) == 1

        # Test async_unload_entry
        assert await async_unload_entry(mock_hass, config_entry) is True

    @pytest.mark.asyncio
    async def test_cover_discovery_message_branches(self, mock_hass, mock_gateway):
        from OWNd.message import OWNAutomationEvent, OWNEvent

        from custom_components.myhome.const import CONF_ENTITY, DOMAIN
        from custom_components.myhome.cover import async_setup_entry

        config_entry = MagicMock()
        config_entry.data = {"mac": "00:03:50:00:12:34"}
        config_entry.entry_id = "test_entry"
        dispatched_handlers = {}

        def fake_dispatcher_connect(hass, signal, target):
            dispatched_handlers[signal] = target
            return MagicMock()

        mock_hass.data = {
            DOMAIN: {
                "00:03:50:00:12:34": {
                    CONF_ENTITY: mock_gateway,
                }
            }
        }

        with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
             patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
             patch("custom_components.myhome.cover.async_dispatcher_connect", side_effect=fake_dispatcher_connect):
            async_add_entities = MagicMock()
            await async_setup_entry(mock_hass, config_entry, async_add_entities)

            # Handler registered for myhome_message_00:03:50:00:12:34
            msg_handler = dispatched_handlers[f"myhome_message_{config_entry.data['mac']}"]

            # Non-automation message is ignored by _handle_cover_message (line 129)
            non_auto_msg = OWNEvent.parse("*1*1*21##")
            msg_handler(non_auto_msg)

            # Case A: message without where attribute (line 93-94)
            empty_msg = MagicMock(spec=OWNAutomationEvent)
            empty_msg.where = None
            msg_handler(empty_msg)

            # Case B: group/area/general message (lines 97-98)
            group_msg = MagicMock(spec=OWNAutomationEvent)
            group_msg.where = "1"
            group_msg.is_group = True
            msg_handler(group_msg)

            area_msg = MagicMock(spec=OWNAutomationEvent)
            area_msg.where = "1"
            area_msg.is_area = True
            msg_handler(area_msg)

            gen_msg = MagicMock(spec=OWNAutomationEvent)
            gen_msg.where = "1"
            gen_msg.is_general = True
            msg_handler(gen_msg)

            # Case C: Valid new cover discovery (lines 104-124)
            valid_msg = OWNEvent.parse("*2*1*55##")
            valid_msg.handle_event = MagicMock()
            msg_handler(valid_msg)

            assert async_add_entities.called
            # Send same message again to hit `if unique_id not in known_covers:` False branch
            msg_handler(valid_msg)

    @pytest.mark.asyncio
    async def test_cover_async_added_to_hass(self, cover, mock_hass):
        cover.hass = mock_hass
        cover.async_on_remove = MagicMock()
        await cover.async_added_to_hass()
        assert cover.async_on_remove.call_count == 3


# ── Button Entities ────────────────────────────────────────────────────────

class TestButtonEntity:

    @pytest.fixture
    def enable_button(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.button import EnableCommandButtonEntity
        b = EnableCommandButtonEntity(
            hass=mock_hass,
            platform="button",
            name="Enable Button",
            device_id="25#24",
            who="25",
            where="24",
            interface="b",
            manufacturer="B",
            model="M",
            gateway=mock_gateway,
        )
        b.async_schedule_update_ha_state = MagicMock()
        return b

    @pytest.mark.asyncio
    async def test_async_press_enable(self, enable_button):
        await enable_button.async_press()
        enable_button._gateway_handler.send.assert_called_once()
        assert "*14*1*24#4#b##" in str(enable_button._gateway_handler.send.call_args[0][0])
