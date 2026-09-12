"""Tests for the MyHOME cover component."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntityFeature,
)
from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from OWNd.message import (
    OWNAutomationEvent,
    OWNEvent,
)

from custom_components.myhome.const import (
    CONF_ADVANCED_SHUTTER,
    CONF_BUS_INTERFACE,
    CONF_PLATFORMS,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.cover import (
    PLATFORM,
    MyHOMECover,
    async_setup_entry,
    async_unload_entry,
)


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.unique_id = "00:03:50:00:12:34"
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


async def test_cover_setup_restores_and_discovers(hass: HomeAssistant, mock_gateway):
    """Test cover platform setup restoring from registry and discovering new devices."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "33": {
                            CONF_WHERE: "33",
                            CONF_NAME: "Configured Cover 33",
                            CONF_ADVANCED_SHUTTER: True,
                        },
                        "33_dup": {
                            CONF_WHERE: "33",
                            CONF_NAME: "Configured Cover 33 Duplicate",
                        },
                        "34#4#02": {
                            CONF_WHERE: "34",
                            CONF_BUS_INTERFACE: "02",
                            CONF_NAME: "Interface Cover 34",
                        },
                    }
                },
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"

    # Mock entity registry restore check
    mock_er = MagicMock()
    reg_1 = MagicMock()
    reg_1.domain = PLATFORM
    reg_1.unique_id = f"{mac}-2-21"

    reg_2 = MagicMock()
    reg_2.domain = PLATFORM
    reg_2.unique_id = f"{mac}-2-22#4#01"

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_er), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[reg_1, reg_2]):

        added_entities = []

        def fake_add_entities(entities):
            added_entities.extend(entities)

        await async_setup_entry(hass, config_entry, fake_add_entities)

        # Restored (21, 22#4#01) + Configured from YAML (33, 34#4#02) = 4 covers
        assert len(added_entities) == 4

        # Test discovering a new cover via message dispatcher
        new_cover_msg = OWNEvent.parse("*2*1*41##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", new_cover_msg)
        assert len(added_entities) == 5

        # Test discovering cover with interface
        new_iface_msg = OWNEvent.parse("*2*1*42#4#02##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", new_iface_msg)
        assert len(added_entities) == 6

        # Sending message for existing cover triggers update signal rather than creating duplicate
        async_dispatcher_send(hass, f"myhome_message_{mac}", new_cover_msg)
        assert len(added_entities) == 6

        # Skip messages for group, area, general, or without where
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*1*#1##"))
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*1*1##")) # area 1
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*1*0##")) # general
        bad_msg = OWNEvent.parse("*2*1*21##")
        bad_msg._where = None
        async_dispatcher_send(hass, f"myhome_message_{mac}", bad_msg)
        assert len(added_entities) == 6

        # Unload
        assert await async_unload_entry(hass, config_entry) is True


class TestMyHOMECoverEntity:
    """Test MyHOMECover entity methods and features."""

    @pytest.fixture
    def basic_cover(self, hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            cover = MyHOMECover(
                hass=hass,
                name="Basic Shutter",
                entity_name="Basic Shutter",
                device_id="21",
                who="2",
                where="21",
                interface=None,
                advanced=False,
                manufacturer="BTicino",
                model="Shutter",
                gateway=mock_gateway,
            )
            cover.hass = hass
            cover.async_schedule_update_ha_state = MagicMock()
            return cover

    @pytest.fixture
    def advanced_cover(self, hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            cover = MyHOMECover(
                hass=hass,
                name="Advanced Shutter",
                entity_name="Advanced Shutter",
                device_id="22#4#02",
                who="2",
                where="22",
                interface="02",
                advanced=True,
                manufacturer="BTicino",
                model="Advanced Shutter",
                gateway=mock_gateway,
            )
            cover.hass = hass
            cover.async_schedule_update_ha_state = MagicMock()
            return cover

    def test_cover_attributes(self, basic_cover, advanced_cover):
        assert basic_cover.device_class == CoverDeviceClass.SHUTTER
        assert basic_cover.supported_features == (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        assert basic_cover.extra_state_attributes["A"] == "2"
        assert basic_cover.extra_state_attributes["PL"] == "1"
        assert basic_cover.extra_state_attributes["travel_time"] == 25
        assert "Int" not in basic_cover.extra_state_attributes

        assert advanced_cover.supported_features == (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.STOP
            | CoverEntityFeature.SET_POSITION
        )
        assert advanced_cover.extra_state_attributes["Int"] == "02"

        # When current_cover_position is None, is_closed falls back to _attr_is_closed
        basic_cover._attr_current_cover_position = None
        basic_cover._attr_is_closed = True
        assert basic_cover.is_closed is True

    async def test_async_lifecycle_and_update(self, basic_cover, hass):
        basic_cover.async_on_remove = MagicMock()
        await basic_cover.async_added_to_hass()
        assert basic_cover.async_on_remove.call_count == 3

        basic_cover._gateway_handler.send_status_request.assert_awaited_once()
        assert str(basic_cover._gateway_handler.send_status_request.call_args[0][0]) == "*#2*21##"

        basic_cover._gateway_handler.send_status_request.reset_mock()
        await basic_cover.async_update()
        basic_cover._gateway_handler.send_status_request.assert_awaited_once()

    async def test_cover_commands(self, basic_cover, advanced_cover):
        await basic_cover.async_open_cover()
        basic_cover._gateway_handler.send.assert_awaited()

        basic_cover._gateway_handler.send.reset_mock()
        await basic_cover.async_close_cover()
        basic_cover._gateway_handler.send.assert_awaited()

        basic_cover._gateway_handler.send.reset_mock()
        await basic_cover.async_stop_cover()
        basic_cover._gateway_handler.send.assert_awaited()

        # Set position
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 45})
        advanced_cover._gateway_handler.send.assert_awaited()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*45##"
        )

        # MH201 rejects the calibrated level command at 0%; use plain DOWN.
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 0})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*2*2*22#4#02##"
        )

        # 100% remains a calibrated level command because MH201 accepts it.
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 100})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*100##"
        )

        # Set position without ATTR_POSITION kwarg
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position()
        advanced_cover._gateway_handler.send.assert_not_called()

        # Virtual travel time positioning for basic cover
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 50
        # Set to 50 (same) -> no-op
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 50})
        basic_cover._gateway_handler.send.assert_not_called()

        # Set to 80 (open) with gateway echo resilience
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 80})
            assert basic_cover.is_opening is True
            assert basic_cover._stop_task is not None
            # Simulate OpenWebNet gateway echoing the open command *2*1*21##
            basic_cover.handle_event(OWNEvent.parse("*2*1*21##"))
            # Crucial: _stop_task MUST NOT be cancelled by gateway echo
            assert basic_cover._stop_task is not None
            # Await the stop task
            await basic_cover._stop_task
            assert basic_cover.is_opening is False
            assert basic_cover.current_cover_position == 80

        # Set to 20 (close) with gateway echo resilience
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 20})
            assert basic_cover.is_closing is True
            assert basic_cover._stop_task is not None
            # Simulate OpenWebNet gateway echoing the close command *2*2*21##
            basic_cover.handle_event(OWNEvent.parse("*2*2*21##"))
            # Crucial: _stop_task MUST NOT be cancelled by gateway echo
            assert basic_cover._stop_task is not None
            await basic_cover._stop_task
            assert basic_cover.is_closing is False
            assert basic_cover.current_cover_position == 20

        # Direction reversal 1: Opening cover receives external closing event
        basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))
        basic_cover._attr_current_cover_position = 20
        basic_cover._start_position = 20
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 80})
            assert basic_cover.is_opening is True
            assert basic_cover._stop_task is not None
            # External close event arrives (reversal)
            basic_cover.handle_event(OWNEvent.parse("*2*2*21##"))
            assert basic_cover._stop_task is None
            assert basic_cover.is_opening is False
            assert basic_cover.is_closing is True

        # Direction reversal 2: Closing cover receives external opening event
        basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))
        basic_cover._attr_current_cover_position = 80
        basic_cover._start_position = 80
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 20})
            assert basic_cover.is_closing is True
            assert basic_cover._stop_task is not None
            # External open event arrives (reversal)
            basic_cover.handle_event(OWNEvent.parse("*2*1*21##"))
            assert basic_cover._stop_task is None
            assert basic_cover.is_opening is True
            assert basic_cover.is_closing is False

        # External stop event cancels stop task
        basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))
        basic_cover._attr_current_cover_position = 20
        basic_cover._start_position = 20
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 80})
            assert basic_cover._stop_task is not None
            basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))
            assert basic_cover._stop_task is None
            assert basic_cover.is_opening is False

        # Test active stop task manual cancellation
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 80})
        task = basic_cover._stop_task
        assert task is not None
        await asyncio.sleep(0)
        basic_cover._cancel_stop_task()
        assert basic_cover._stop_task is None
        await asyncio.sleep(0)

        # Unload / remove from hass cleans up tasks
        await basic_cover.async_will_remove_from_hass()
        assert basic_cover._stop_task is None

    def test_handle_event(self, basic_cover):
        # Opening event
        msg_opening = OWNEvent.parse("*2*1*21##")
        basic_cover.handle_event(msg_opening)
        assert basic_cover.is_opening is True
        assert basic_cover.is_closing is False

        # Moving position interpolation
        with patch("time.monotonic", return_value=basic_cover._move_start_time + 12.5):
            # After 12.5s out of 25s full travel time from 50%, position should advance ~50%
            pos = basic_cover.current_cover_position
            assert pos >= 90

        # Stop event
        msg_stop = OWNEvent.parse("*2*0*21##")
        basic_cover.handle_event(msg_stop)
        assert basic_cover.is_opening is False
        assert basic_cover.is_closing is False

        # Closing event
        msg_closing = OWNEvent.parse("*2*2*21##")
        basic_cover.handle_event(msg_closing)
        assert basic_cover.is_opening is False
        assert basic_cover.is_closing is True

        # Moving closing interpolation
        with patch("time.monotonic", return_value=basic_cover._move_start_time + 12.5):
            pos = basic_cover.current_cover_position
            assert pos <= 60

        basic_cover.handle_event(msg_stop)
        assert basic_cover.is_closing is False

        # Position event
        msg_pos = OWNEvent.parse("*#2*21*10*10*0*0*0##")
        basic_cover.handle_event(msg_pos)
        assert basic_cover.is_closed is True
        assert basic_cover.current_cover_position == 0

        # Position event without is_closed
        msg_pos_no_closed = MagicMock(spec=OWNAutomationEvent)
        msg_pos_no_closed.current_position = 0
        msg_pos_no_closed.is_closed = None
        msg_pos_no_closed.human_readable_log = "Pos no closed"
        basic_cover.handle_event(msg_pos_no_closed)
        assert basic_cover.is_closed is True

        # Stop event with is_closed reported
        msg_stopped_with_closed = MagicMock(spec=OWNAutomationEvent)
        msg_stopped_with_closed.current_position = None
        msg_stopped_with_closed.is_opening = False
        msg_stopped_with_closed.is_closing = False
        msg_stopped_with_closed.is_closed = True
        msg_stopped_with_closed.human_readable_log = "Stopped with closed"
        basic_cover.handle_event(msg_stopped_with_closed)
        assert basic_cover.is_closed is True

    @pytest.mark.asyncio
    async def test_advanced_cover_async_update(self, hass: HomeAssistant, mock_gateway):
        cover = MyHOMECover(
            hass=hass,
            name="Advanced Cover",
            entity_name="Advanced Cover",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=True,
            manufacturer="BTicino",
            model="F401",
            gateway=mock_gateway,
        )
        await cover.async_update()
        mock_gateway.send_status_request.assert_awaited_once()
        assert str(mock_gateway.send_status_request.call_args[0][0]) == "*#2*21*10##"

        # Test RuntimeError exception safety in handle_event
        cover.async_schedule_update_ha_state = MagicMock(side_effect=RuntimeError("Loop closing"))
        cover.handle_event(OWNEvent.parse("*2*0*21##"))

        # Advanced cover does not use travel time estimation on move/stop
        cover._attr_current_cover_position = 70
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        assert cover.is_opening is True
        assert cover._move_start_time is None

        # Stop does not overwrite exact position with travel time math
        cover.handle_event(OWNEvent.parse("*2*0*21##"))
        assert cover.is_opening is False
        assert cover.current_cover_position == 70

    @pytest.mark.asyncio
    async def test_restore_entity_position(self, basic_cover):
        """Test position restoration from RestoreEntity when current_position is restored."""
        # 1. Restored to 75%
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "open", {ATTR_CURRENT_POSITION: 75})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 75
        assert basic_cover._start_position == 75
        assert basic_cover.is_closed is False

        # 2. Restored to 0% (closed)
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "closed", {ATTR_CURRENT_POSITION: 0})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 0
        assert basic_cover._start_position == 0
        assert basic_cover.is_closed is True

        # 3. Restored to 100% (open)
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "open", {ATTR_CURRENT_POSITION: 100})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 100
        assert basic_cover._start_position == 100
        assert basic_cover.is_closed is False

        # 4. Restored from float value 42.6 -> 43%
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "open", {ATTR_CURRENT_POSITION: 42.6})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 43
        assert basic_cover._start_position == 43
        assert basic_cover.is_closed is False

        # 5. Invalid position attribute with state 'open' -> falls back to 100%
        basic_cover._attr_current_cover_position = 50
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "open", {ATTR_CURRENT_POSITION: "invalid"})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 100
        assert basic_cover._start_position == 100
        assert basic_cover.is_closed is False

        # 6. Invalid position attribute with state 'closed' -> falls back to 0%
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "closed", {ATTR_CURRENT_POSITION: "invalid"})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 0
        assert basic_cover._start_position == 0
        assert basic_cover.is_closed is True

        # 7. Invalid position attribute with state 'unknown' -> retains default 50%
        basic_cover._attr_current_cover_position = 50
        basic_cover._start_position = 50
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "unknown", {ATTR_CURRENT_POSITION: "invalid"})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 50
        assert basic_cover._start_position == 50

    @pytest.mark.asyncio
    async def test_restore_entity_fallback_from_state(self, basic_cover):
        """Test fallback restoration when state.state is 'closed' or 'open' without position."""
        # 1. State 'closed' -> position 0%, is_closed True
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "closed", {})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 0
        assert basic_cover._start_position == 0
        assert basic_cover.is_closed is True

        # 2. State 'open' -> position 100%, is_closed False
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "open", {})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 100
        assert basic_cover._start_position == 100
        assert basic_cover.is_closed is False

        # 3. State 'unknown' -> position remains unchanged (default 50)
        basic_cover._attr_current_cover_position = 50
        basic_cover._start_position = 50
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.basic_shutter", "unknown", {})
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 50
        assert basic_cover._start_position == 50
        assert basic_cover.is_closed is False

    @pytest.mark.asyncio
    async def test_restore_none_or_advanced_cover(self, basic_cover, advanced_cover):
        """Test fallback to 50% when no last state exists and verify advanced covers do not restore."""
        # 1. No last state (None) -> remains default 50%
        basic_cover._attr_current_cover_position = 50
        basic_cover._start_position = 50
        basic_cover.async_get_last_state = AsyncMock(return_value=None)
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 50
        assert basic_cover._start_position == 50
        assert basic_cover.is_closed is False

        # 2. Exception in async_get_last_state -> handled gracefully
        basic_cover._attr_current_cover_position = 50
        basic_cover.async_get_last_state = AsyncMock(side_effect=RuntimeError("Storage failure"))
        await basic_cover.async_added_to_hass()
        assert basic_cover.current_cover_position == 50

        # 3. Advanced cover does not restore stale state and requests live hardware status
        advanced_cover._attr_current_cover_position = 50
        advanced_cover.async_get_last_state = AsyncMock(
            return_value=State("cover.advanced_shutter", "open", {ATTR_CURRENT_POSITION: 80})
        )
        advanced_cover._gateway_handler.send_status_request.reset_mock()
        await advanced_cover.async_added_to_hass()
        advanced_cover.async_get_last_state.assert_not_called()
        advanced_cover._gateway_handler.send_status_request.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send_status_request.call_args[0][0])
            == "*#2*22#4#02*10##"
        )
        assert advanced_cover._attr_current_cover_position == 50


async def test_cover_general_commands_update_all_covers(hass: HomeAssistant, mock_gateway):
    """Test that general cover events (*2*1*0##, *2*2*0##, *2*0*0##) update all covers."""
    mac = mock_gateway.mac

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover1 = MyHOMECover(
            hass=hass,
            name="Cover 21",
            entity_name="Cover 21",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=25,
        )
        cover2 = MyHOMECover(
            hass=hass,
            name="Cover 22",
            entity_name="Cover 22",
            device_id="22",
            who="2",
            where="22",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=25,
        )

    cover1.hass = hass
    cover2.hass = hass
    cover1.async_schedule_update_ha_state = MagicMock()
    cover2.async_schedule_update_ha_state = MagicMock()

    await cover1.async_added_to_hass()
    await cover2.async_added_to_hass()

    cover1._attr_current_cover_position = 50
    cover1._start_position = 50
    cover2._attr_current_cover_position = 50
    cover2._start_position = 50

    # 1. General Open (*2*1*0##)
    msg_open = OWNEvent.parse("*2*1*0##")
    with patch("time.monotonic", return_value=1000.0):
        async_dispatcher_send(hass, f"myhome_update_{mac}_2_general", msg_open)

    assert cover1.is_opening is True
    assert cover1.is_closing is False
    assert cover2.is_opening is True
    assert cover2.is_closing is False

    # 2. General Stop (*2*0*0##) after 5 seconds (5s / 25s * 100 = 20% increase -> 70%)
    with patch("time.monotonic", return_value=1005.0):
        msg_stop = OWNEvent.parse("*2*0*0##")
        async_dispatcher_send(hass, f"myhome_update_{mac}_2_general", msg_stop)

    assert cover1.is_opening is False
    assert cover1.is_closing is False
    assert cover2.is_opening is False
    assert cover2.is_closing is False
    assert cover1.current_cover_position == 70
    assert cover2.current_cover_position == 70

    # 3. General Close (*2*2*0##)
    msg_close = OWNEvent.parse("*2*2*0##")
    with patch("time.monotonic", return_value=2000.0):
        async_dispatcher_send(hass, f"myhome_update_{mac}_2_general", msg_close)

    assert cover1.is_closing is True
    assert cover1.is_opening is False
    assert cover2.is_closing is True
    assert cover2.is_opening is False

    # 4. General Stop (*2*0*0##) after 5 seconds (70% - 20% = 50%)
    with patch("time.monotonic", return_value=2005.0):
        async_dispatcher_send(hass, f"myhome_update_{mac}_2_general", msg_stop)

    assert cover1.is_closing is False
    assert cover2.is_closing is False
    assert cover1.current_cover_position == 50
    assert cover2.current_cover_position == 50

    # 5. Verify individual commands only affect the target cover
    msg_single_open = OWNEvent.parse("*2*1*21##")
    async_dispatcher_send(hass, f"myhome_update_{mac}_2_21", msg_single_open)
    assert cover1.is_opening is True
    assert cover2.is_opening is False


async def test_cover_setup_dispatches_general_messages_from_gateway(hass: HomeAssistant, mock_gateway):
    """Test that incoming gateway messages for general cover (WHERE=0) dispatch to all covers."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {PLATFORM: {}},
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]):
        await async_setup_entry(hass, config_entry, lambda entities: None)

    dispatched = []

    @callback
    def on_general_event(msg):
        dispatched.append(msg)

    async_dispatcher_connect(hass, f"myhome_update_{mac}_2_general", on_general_event)

    # Dispatch general open from gateway
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*1*0##"))
    assert len(dispatched) == 1
    assert dispatched[0].is_general is True
    assert dispatched[0].is_opening is True

    # Dispatch general close from gateway
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*2*0##"))
    assert len(dispatched) == 2
    assert dispatched[1].is_general is True
    assert dispatched[1].is_closing is True

    # Dispatch general stop from gateway
    async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*0*0##"))
    assert len(dispatched) == 3
    assert dispatched[2].is_general is True
    assert dispatched[2].is_opening is False
    assert dispatched[2].is_closing is False


async def test_cover_gateway_general_message_updates_all_active_entities(hass: HomeAssistant, mock_gateway):
    """Test full end-to-end path: gateway general messages update live cover entities."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "21": {CONF_WHERE: "21", CONF_NAME: "Cover 21"},
                        "22": {CONF_WHERE: "22", CONF_NAME: "Cover 22"},
                    }
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"

    added_entities = []

    def fake_add_entities(entities):
        added_entities.extend(entities)

    with patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()), \
         patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]):
        await async_setup_entry(hass, config_entry, fake_add_entities)

    assert len(added_entities) == 2
    cover1, cover2 = added_entities[0], added_entities[1]
    cover1.hass = hass
    cover2.hass = hass
    cover1.async_schedule_update_ha_state = MagicMock()
    cover2.async_schedule_update_ha_state = MagicMock()

    await cover1.async_added_to_hass()
    await cover2.async_added_to_hass()

    cover1._attr_current_cover_position = 50
    cover1._start_position = 50
    cover2._attr_current_cover_position = 50
    cover2._start_position = 50

    # 1. Gateway receives general open (*2*1*0##)
    with patch("time.monotonic", return_value=1000.0):
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*1*0##"))
    assert cover1.is_opening is True
    assert cover2.is_opening is True

    # 2. Gateway receives general stop (*2*0*0##) after 5 seconds
    with patch("time.monotonic", return_value=1005.0):
        async_dispatcher_send(hass, f"myhome_message_{mac}", OWNEvent.parse("*2*0*0##"))

    assert cover1.is_opening is False
    assert cover2.is_opening is False
    assert cover1.current_cover_position == 70
    assert cover2.current_cover_position == 70


@pytest.mark.asyncio
async def test_cover_advanced_shutter_key_precedence(hass, mock_gateway):
    """Test that CONF_ADVANCED_SHUTTER ('advanced') takes precedence over legacy 'advanced_shutter'."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "35": {
                            CONF_WHERE: "35",
                            CONF_NAME: "Cover 35",
                            CONF_ADVANCED_SHUTTER: True,
                            "advanced_shutter": False,
                        },
                    }
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"

    added = []
    with patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
         patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()):
        await async_setup_entry(hass, config_entry, added.extend)

    assert len(added) == 1
    assert added[0]._advanced is True
