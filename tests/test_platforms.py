"""Tests for MyHOME HA platform entities using lightweight mocking.

Strategy: We mock the absolute minimum of the HA framework (hass object,
gateway handler, dispatcher) to test entity construction, state handling,
and command generation. This avoids requiring a full HA test harness while
still covering the entity logic.

References:
- https://developers.home-assistant.io/docs/development_testing
- https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import (
    OWNEvent,
)

# ── Fixtures ───────────────────────────────────────────────────────────────

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


# ── MyHOMEEntity Base ─────────────────────────────────────────────────────

class TestMyHOMEEntity:
    """Test the shared base entity class."""

    def test_entity_construction(self, mock_hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            from custom_components.myhome.myhome_device import MyHOMEEntity
            entity = MyHOMEEntity(
                hass=mock_hass,
                name="Test Device",
                platform="light",
                device_id="21",
                who="1",
                where="21",
                manufacturer="BTicino",
                model="Test",
                gateway=mock_gateway,
            )
            assert entity._attr_unique_id == f"{mock_gateway.mac}-1-21"
            assert entity._attr_should_poll is False
            assert entity._attr_has_entity_name is False
            assert entity._manufacturer == "BTicino"

    def test_entity_default_manufacturer(self, mock_hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            from custom_components.myhome.myhome_device import MyHOMEEntity
            entity = MyHOMEEntity(
                hass=mock_hass,
                name="Test Device",
                platform="light",
                device_id="21",
                who="1",
                where="21",
                manufacturer=None,
                model="Test",
                gateway=mock_gateway,
            )
            assert entity._manufacturer == "BTicino S.p.A."

    def test_entity_device_info(self, mock_hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            from custom_components.myhome.myhome_device import MyHOMEEntity
            entity = MyHOMEEntity(
                hass=mock_hass,
                name="Test Device",
                platform="light",
                device_id="21",
                who="1",
                where="21",
                manufacturer="BTicino",
                model="F411/4",
                gateway=mock_gateway,
            )
            assert "identifiers" in entity._attr_device_info
            assert entity._attr_device_info["model"] == "F411/4"
            assert entity._attr_device_info["name"] == "Test Device"

    async def test_entity_lifecycle_hooks(self, mock_hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            from custom_components.myhome.myhome_device import MyHOMEEntity
            mock_gateway.available = True
            mock_gateway.availability_signal = "myhome_test_availability"
            entity = MyHOMEEntity(
                hass=mock_hass,
                name="Test Device",
                platform="light",
                device_id="21",
                who="1",
                where="21",
                manufacturer="BTicino",
                model="F411/4",
                gateway=mock_gateway,
            )
            entity.async_update = AsyncMock()
            entity.async_on_remove = MagicMock()
            entity.async_write_ha_state = MagicMock()
            with patch(
                "custom_components.myhome.myhome_device.async_dispatcher_connect",
                return_value=MagicMock(),
            ) as connect:
                await entity.async_added_to_hass()
                entity._register_availability_listener()

            entity.async_update.assert_awaited_once()
            assert entity.available is True
            connect.assert_called_once_with(
                mock_hass,
                mock_gateway.availability_signal,
                entity._handle_availability_update,
            )
            entity._handle_availability_update()
            entity.async_write_ha_state.assert_called_once()
            await entity.async_will_remove_from_hass()

    def test_availability_listener_waits_for_hass(self, mock_gateway):
        """Do not subscribe before Home Assistant has been assigned to the entity."""
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            from custom_components.myhome.myhome_device import MyHOMEEntity
            entity = MyHOMEEntity(
                hass=None,
                name="Test Device",
                platform="light",
                device_id="21",
                who="1",
                where="21",
                manufacturer="BTicino",
                model="Test",
                gateway=mock_gateway,
            )

            with patch(
                "custom_components.myhome.myhome_device.async_dispatcher_connect"
            ) as connect:
                entity._register_availability_listener()

            connect.assert_not_called()
            assert entity._availability_listener_registered is False

    async def test_entity_via_device_id_annotation(self, mock_hass, mock_gateway):
        from homeassistant.helpers.device_registry import DeviceInfo
        with patch.dict(DeviceInfo.__annotations__, {"via_device_id": str}):
            with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
                from custom_components.myhome.myhome_device import MyHOMEEntity
                mock_gateway.device_registry_id = "mock_reg_id"
                entity = MyHOMEEntity(
                    hass=mock_hass,
                    name="Test Device",
                    platform="light",
                    device_id="21",
                    who="1",
                    where="21",
                    manufacturer="BTicino",
                    model="F411/4",
                    gateway=mock_gateway,
                )
                mock_gateway.unique_id = "mock_gw_unique"
                assert entity._attr_device_info.get("via_device_id") == "mock_reg_id"
                assert entity.via_device_id == "mock_gw_unique"


# ── MediaPlayer Entity ─────────────────────────────────────────────────────

class TestMediaPlayerEntity:
    """Test MyHOMEMediaPlayer entity logic."""

    @pytest.fixture
    def player(self, mock_hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):

            from custom_components.myhome.media_player import MyHOMEMediaPlayer

            p = MyHOMEMediaPlayer(
                hass=mock_hass,
                name="Audio Zone 1",
                entity_name="Audio Zone 1",
                device_id="1#16",
                who="16",
                where="1",
                manufacturer="BTicino",
                model="Audio System",
                gateway=mock_gateway,
            )
            # Override the schedule_update method to avoid HA internals
            p.async_schedule_update_ha_state = MagicMock()
            return p

    def test_initial_state(self, player):
        from homeassistant.components.media_player import MediaPlayerState
        assert player._attr_state == MediaPlayerState.OFF

    def test_source_list(self, player):
        assert len(player._attr_source_list) == 5
        assert "Source 1" in player._attr_source_list

    def test_handle_event_on(self, player):
        from homeassistant.components.media_player import MediaPlayerState
        msg = OWNEvent.parse("*16*0*1##")
        player.handle_event(msg)
        assert player._attr_state == MediaPlayerState.ON
        player.async_schedule_update_ha_state.assert_called()

    def test_handle_event_off(self, player):
        from homeassistant.components.media_player import MediaPlayerState
        msg = OWNEvent.parse("*16*10*1##")
        player.handle_event(msg)
        assert player._attr_state == MediaPlayerState.OFF

    def test_handle_event_source_0_routing(self, player):
        """Routing events update the source label but do NOT force state to ON."""
        from homeassistant.components.media_player import MediaPlayerState
        msg = OWNEvent.parse("*16*3*101##")
        player.handle_event(msg)
        # Routing events only update the source label, NOT the state
        assert player._attr_state == MediaPlayerState.OFF
        assert player._attr_source == "Source 0"
        player.async_schedule_update_ha_state.assert_called()

    def test_handle_event_source_1_routing(self, player):
        """Routing events update the source label but do NOT force state to ON."""
        from homeassistant.components.media_player import MediaPlayerState
        msg = OWNEvent.parse("*16*3*111##")
        player.handle_event(msg)
        assert player._attr_state == MediaPlayerState.OFF
        assert player._attr_source == "Source 1"
        player.async_schedule_update_ha_state.assert_called()

    def test_routing_event_does_not_resurrect_off_zone(self, player):
        """Regression: F441M re-broadcasts routing for ALL zones when ANY zone
        changes source.  A zone that was turned OFF must stay OFF when it
        receives a stale routing event from the matrix."""
        from homeassistant.components.media_player import MediaPlayerState
        # 1. Turn on the zone via an ON event
        on_msg = OWNEvent.parse("*16*0*1##")
        player.handle_event(on_msg)
        assert player._attr_state == MediaPlayerState.ON

        # 2. Turn off the zone via an OFF event
        off_msg = OWNEvent.parse("*16*10*1##")
        player.handle_event(off_msg)
        assert player._attr_state == MediaPlayerState.OFF

        # 3. Another zone turns on, causing the matrix to re-broadcast
        #    this zone's routing info — must NOT resurrect the state
        routing_msg = OWNEvent.parse("*16*3*111##")
        player.handle_event(routing_msg)
        assert player._attr_state == MediaPlayerState.OFF
        assert player._attr_source == "Source 1"

    @pytest.mark.asyncio
    async def test_turn_on(self, player):
        await player.async_turn_on()
        assert player._gateway_handler.send.call_count == 2

    @pytest.mark.asyncio
    async def test_turn_off(self, player):
        from homeassistant.components.media_player import MediaPlayerState
        await player.async_turn_off()
        player._gateway_handler.send.assert_called_once()
        # Turn-off must optimistically set state to OFF immediately
        assert player._attr_state == MediaPlayerState.OFF

    @pytest.mark.asyncio
    async def test_volume_up(self, player):
        await player.async_volume_up()
        player._gateway_handler.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_volume_down(self, player):
        await player.async_volume_down()
        player._gateway_handler.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_select_source(self, player):
        """Source selection is a no-op (MH200 startup scenario handles routing)."""
        await player.async_select_source("Source 3")
        player._gateway_handler.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_select_source_invalid(self, player):
        """Invalid source should also be a no-op."""
        await player.async_select_source("Invalid Source")
        player._gateway_handler.send.assert_not_called()

    def test_handle_event_volume(self, player):
        msg = OWNEvent.parse("*#16*1*1*15##")
        player.handle_event(msg)
        assert player._attr_volume_level == pytest.approx(15 / 31.0)
        player.async_schedule_update_ha_state.assert_called()

    @pytest.mark.asyncio
    async def test_async_set_volume_level(self, player):
        await player.async_set_volume_level(0.50)
        # 0.50 * 31.0 = 15.5 -> round to 16
        args, _ = player._gateway_handler.send.call_args
        command = args[0]
        assert str(command) == "*#16*1*#1*16##"

    @pytest.mark.asyncio
    async def test_async_mute_volume(self, player):
        player._attr_volume_level = 0.50
        await player.async_mute_volume(True)
        # Should set volume to 0
        args, _ = player._gateway_handler.send.call_args
        command = args[0]
        assert str(command) == "*#16*1*#1*0##"
        assert player._attr_is_volume_muted is True
        assert player._pre_mute_volume == 0.50

        await player.async_mute_volume(False)
        # Should restore volume to 16 HW units (round(0.50 * 31))
        args, _ = player._gateway_handler.send.call_args
        command = args[0]
        assert str(command) == "*#16*1*#1*16##"
        assert player._attr_is_volume_muted is False

    @pytest.mark.asyncio
    async def test_async_update(self, player):
        await player.async_update()
        player._gateway_handler.send_status_request.assert_called_once()


# ── Platform Setup ─────────────────────────────────────────────────────────

class TestMediaPlayerPlatformSetup:
    """Test the async_setup_entry and async_unload_entry functions."""

    @pytest.mark.asyncio
    async def test_unload_entry(self):
        from custom_components.myhome.media_player import async_unload_entry
        result = await async_unload_entry(MagicMock(), MagicMock())
        assert result is True


# ── Platform Import Smoke Test ─────────────────────────────────────────────

class TestPlatformImportCleanliness:
    """Smoke test ensuring every integration platform module can be imported cleanly.

    Catches upstream HA breaking changes (e.g. deprecated/removed constants, changed module
    paths) immediately at import time across all supported platforms.
    """

    def test_all_platforms_import_cleanly(self):
        """Test importing each platform in PLATFORMS without ImportError or syntax issues."""
        import importlib
        import os

        import custom_components

        repo_cc = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "custom_components"))
        if repo_cc not in custom_components.__path__:
            custom_components.__path__.insert(0, repo_cc)

        from custom_components.myhome import PLATFORMS

        failed_imports = {}
        for platform in PLATFORMS:
            try:
                mod = importlib.import_module(f"custom_components.myhome.{platform}")
                assert hasattr(mod, "async_setup_entry"), f"{platform} missing async_setup_entry"
            except Exception as exc:
                failed_imports[platform] = str(exc)

        assert not failed_imports, f"Platform imports failed: {failed_imports}"

    def test_diagnostics_platform_imports_cleanly(self):
        """Test importing the diagnostics platform module."""
        import importlib
        import os

        import custom_components

        repo_cc = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "custom_components"))
        if repo_cc not in custom_components.__path__:
            custom_components.__path__.insert(0, repo_cc)

        mod = importlib.import_module("custom_components.myhome.diagnostics")
        assert hasattr(mod, "async_get_config_entry_diagnostics")
