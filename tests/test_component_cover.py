"""Tests for the MyHOME cover component."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_CURRENT_TILT_POSITION,
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntityFeature,
)
from homeassistant.const import (
    CONF_NAME,
)
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAutomationEvent,
    OWNEvent,
    OWNMessage,
)

from custom_components.myhome.const import (
    CONF_ADVANCED_SHUTTER,
    CONF_BUS_INTERFACE,
    CONF_PLATFORMS,
    CONF_SLAT_TILT,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.cover import (
    PLATFORM,
    MyHOMECover,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.cover_motion import ECHO_WINDOW, MOTOR_START_DELAY
from custom_components.myhome.router import FrameRouter
from tests.conftest import attach_runtime


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

        attach_runtime(hass, config_entry)
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
        attach_runtime(hass, config_entry)
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
            cover.entity_id = "cover.cover"  # assigned by the registry in real Home Assistant
            cover.hass = hass
            cover.entity_id = cover.entity_id or "test.cover"
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
            cover.entity_id = "cover.cover"  # assigned by the registry in real Home Assistant
            cover.hass = hass
            cover.entity_id = cover.entity_id or "test.cover"
            cover.async_schedule_update_ha_state = MagicMock()
            return cover

    @pytest.fixture
    def tilt_cover(self, hass, mock_gateway):
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            cover = MyHOMECover(
                hass=hass,
                name="Venetian Blind",
                entity_name="Venetian Blind",
                device_id="31",
                who="2",
                where="31",
                interface=None,
                advanced=False,
                slat_tilt=True,
                manufacturer="BTicino",
                model="Venetian Blind",
                gateway=mock_gateway,
            )
            cover.entity_id = "cover.venetian_blind"
            cover.hass = hass
            cover.async_schedule_update_ha_state = MagicMock()
            cover.async_write_ha_state = MagicMock()
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
        assert basic_cover.async_on_remove.call_count == 1  # availability; frames come via the router

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

        # External stop event cancels stop task - but only once our own command's
        # echo window is over: the gateway relays a stop status right after our
        # direction frame (#302), which must not end the timed run.
        basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))
        basic_cover._attr_current_cover_position = 20
        basic_cover._start_position = 20
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 80})
            assert basic_cover._stop_task is not None
            basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))  # relayed echo
            assert basic_cover._stop_task is not None
            basic_cover.handle_event(OWNEvent.parse("*2*1*21##"))  # motor start echo
            assert basic_cover._stop_task is not None
            basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))  # genuine keypad stop
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
        msg_pos_no_closed.is_opening = False
        msg_pos_no_closed.is_closing = False
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

    @staticmethod
    def _unknown_level_events():
        """shutterLevel 255 as OWNd <= 2.0.0b8 reports it, and as later releases do."""
        passthrough = MagicMock(spec=OWNAutomationEvent)
        passthrough.current_position = 255
        passthrough.is_opening = False
        passthrough.is_closing = False
        passthrough.is_closed = False
        passthrough.human_readable_log = "opened at 255%"

        flagged = MagicMock(spec=OWNAutomationEvent)
        flagged.current_position = None
        flagged.is_position_unknown = True
        flagged.is_opening = False
        flagged.is_closing = False
        flagged.is_closed = None
        flagged._what = None
        flagged.human_readable_log = "stopped at an unknown position"
        return passthrough, flagged

    @pytest.mark.parametrize("style", [0, 1], ids=["ownd_b8_255", "ownd_flag"])
    def test_advanced_cover_level_255_is_unknown_position(self, advanced_cover, style):
        # Encyclopedia who-2-automation/dimensions.md: 255 = "Unknown position".
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*40*0*0##"))
        assert advanced_cover.current_cover_position == 40

        advanced_cover.handle_event(self._unknown_level_events()[style])

        assert advanced_cover.current_cover_position is None
        assert advanced_cover.is_closed is None
        assert advanced_cover.is_opening is False
        assert advanced_cover.is_closing is False

    def test_advanced_cover_moving_from_unknown_position(self, advanced_cover):
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*40*0*0##"))
        moving = self._unknown_level_events()[0]
        moving.is_opening = True
        advanced_cover.handle_event(moving)

        assert advanced_cover.current_cover_position is None
        assert advanced_cover.is_opening is True
        assert advanced_cover.is_closed is False

    def test_basic_cover_ignores_level_255(self, basic_cover):
        basic_cover.handle_event(OWNEvent.parse("*#2*21*10*10*30*0*0##"))
        assert basic_cover.current_cover_position == 30

        basic_cover.handle_event(self._unknown_level_events()[0])

        # A timed cover keeps its own estimate; 255 never becomes a level.
        assert basic_cover.current_cover_position == 30

    def test_advanced_cover_moving_status_wins_over_level(self, advanced_cover):
        # LN4661M2 on an MH201 (tests/fixtures/plants/mh201_physical_plant): the level
        # is not live while moving; it repeats the start position until the stop frame.
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*25*001*0##"))
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*12*25*001*0##"))

        assert advanced_cover.is_closing is True
        assert advanced_cover.is_opening is False
        assert advanced_cover.current_cover_position == 25

        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*0*001*0##"))

        assert advanced_cover.is_closing is False
        assert advanced_cover.current_cover_position == 0
        assert advanced_cover.is_closed is True

    def test_advanced_cover_opening_status_with_level(self, advanced_cover):
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*11*40*001*0##"))

        assert advanced_cover.is_opening is True
        assert advanced_cover.current_cover_position == 40
        assert advanced_cover.is_closed is False

    def test_basic_cover_moving_status_with_level_keeps_its_run(self, basic_cover):
        basic_cover.handle_event(OWNEvent.parse("*2*2*21##"))
        anchor = basic_cover._move_start_time
        assert basic_cover.is_closing is True

        # A stale level on a moving frame neither stops nor re-anchors the travel clock.
        basic_cover.handle_event(OWNEvent.parse("*#2*21*10*12*80*001*0##"))

        assert basic_cover.is_closing is True
        assert basic_cover._move_start_time == anchor

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
        cover.entity_id = "cover.cover"  # assigned by the registry in real Home Assistant
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

    def test_cover_slat_tilt_attributes_and_features(self, tilt_cover):
        """Test slat tilt attributes, device class, and supported features."""
        assert tilt_cover.device_class == CoverDeviceClass.BLIND
    def test_cover_slat_tilt_init(self, tilt_cover):
        """Test that slat_tilt: true initializes proper features and device class."""
        assert tilt_cover._slat_tilt is True
        assert tilt_cover.device_class == CoverDeviceClass.BLIND
        assert tilt_cover.supported_features & CoverEntityFeature.SET_TILT_POSITION
        assert tilt_cover.supported_features & CoverEntityFeature.OPEN_TILT
        assert tilt_cover.supported_features & CoverEntityFeature.CLOSE_TILT
        assert tilt_cover.supported_features & CoverEntityFeature.STOP_TILT
        assert tilt_cover.current_cover_tilt_position is None
        assert tilt_cover.extra_state_attributes.get("slat_tilt") is True
        assert tilt_cover.extra_state_attributes.get("slat_time") == 2.0

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_commands(self, tilt_cover, mock_gateway):
        """Test set_cover_tilt_position, open_cover_tilt, close_cover_tilt, and stop_cover_tilt."""
        fut = asyncio.Future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut

        with patch("asyncio.sleep", new_callable=AsyncMock):
            # 1. Set tilt position to 40% (starting from 0, diff=40 -> moves UP)
            tilt_cover._attr_current_cover_tilt_position = 0
            await tilt_cover.async_set_cover_tilt_position(tilt_position=40)
            assert mock_gateway.send.await_count == 1
            assert str(mock_gateway.send.call_args[0][0]) == "*2*1*31##"
            assert tilt_cover._is_tilting is True
            tilt_cover._motor_started.set()

            # Let auto-stop task complete
            if tilt_cover._stop_task:
                await tilt_cover._stop_task
            assert tilt_cover.current_cover_tilt_position == 40
            assert tilt_cover._is_tilting is False

            # 2. Open tilt (100%) (from 40, moves UP)
            mock_gateway.send.reset_mock()
            fut_up = asyncio.Future()
            fut_up.set_result(time.monotonic())
            mock_gateway.send.return_value = fut_up
            await tilt_cover.async_open_cover_tilt()
            assert str(mock_gateway.send.call_args[0][0]) == "*2*1*31##"
            assert tilt_cover._is_tilting is True
            tilt_cover._motor_started.set()
            if tilt_cover._stop_task:
                await tilt_cover._stop_task
            assert tilt_cover.current_cover_tilt_position == 100
            assert tilt_cover._is_tilting is False

            # 3. Close tilt (0%) (from 100, moves DOWN)
            mock_gateway.send.reset_mock()
            fut_down = asyncio.Future()
            fut_down.set_result(time.monotonic())
            mock_gateway.send.return_value = fut_down
            await tilt_cover.async_close_cover_tilt()
            assert str(mock_gateway.send.call_args[0][0]) == "*2*2*31##"
            assert tilt_cover._is_tilting is True
            tilt_cover._motor_started.set()
            if tilt_cover._stop_task:
                await tilt_cover._stop_task
            assert tilt_cover.current_cover_tilt_position == 0
            assert tilt_cover._is_tilting is False

            # 4. Stop tilt
            mock_gateway.send.reset_mock()
            fut_stop = asyncio.Future()
            fut_stop.set_result(time.monotonic())
            mock_gateway.send.return_value = fut_stop
            await tilt_cover.async_stop_cover_tilt()
            mock_gateway.send.assert_awaited_once()
            assert str(mock_gateway.send.call_args[0][0]) == "*2*0*31##"

            # 5. Call with missing tilt_position (no-op early return)
            mock_gateway.send.reset_mock()
            await tilt_cover.async_set_cover_tilt_position()
            mock_gateway.send.assert_not_called()

            # 6. Call with diff == 0 (no-op early return)
            tilt_cover._attr_current_cover_tilt_position = 40
            mock_gateway.send.reset_mock()
            await tilt_cover.async_set_cover_tilt_position(tilt_position=40)
            mock_gateway.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_calibrating_error(self, tilt_cover):
        """Test that tilt commands are rejected with HomeAssistantError while calibrating."""
        tilt_cover._calibrating = True

        with pytest.raises(HomeAssistantError, match="calibrated"):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)

        with pytest.raises(HomeAssistantError, match="calibrated"):
            await tilt_cover.async_open_cover_tilt()

        with pytest.raises(HomeAssistantError, match="calibrated"):
            await tilt_cover.async_close_cover_tilt()

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_delivery_failure(self, tilt_cover, mock_gateway):
        """Test that delivery failure in async_set_cover_tilt_position raises HomeAssistantError."""
        fut = asyncio.Future()
        fut.set_exception(Exception("Bus write error"))
        mock_gateway.send.return_value = fut

        with pytest.raises(HomeAssistantError, match="direction command delivery failed"):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=30)
        assert tilt_cover._is_tilting is False

    def test_cover_slat_tilt_stop_handling(self, tilt_cover):
        """Test that receiving a stop command while tilting resets _is_tilting and preserves position."""
        tilt_cover._is_tilting = True
        tilt_cover._attr_current_cover_position = 75
        tilt_cover._is_opening = False
        tilt_cover._is_closing = False

        msg = OWNMessage.parse("*2*0*31##")
        assert msg is not None
        tilt_cover.handle_event(msg)

        assert tilt_cover._is_tilting is False
        assert tilt_cover.current_cover_position == 75

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_async_update(self, tilt_cover, mock_gateway):
        """Test that async_update queries standard status and never sends invalid DIM 11 query."""
        mock_gateway.send_status_request.reset_mock()
        await tilt_cover.async_update()
        mock_gateway.send_status_request.assert_awaited_once()
        assert str(mock_gateway.send_status_request.call_args[0][0]) == "*#2*31##"

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_restore_state(self, tilt_cover, basic_cover):
        """Test that slat tilt position is restored from last state only if slat_tilt is enabled."""
        tilt_cover.async_get_last_state = AsyncMock(
            return_value=State(
                "cover.venetian_blind",
                "open",
                {ATTR_CURRENT_POSITION: 85, ATTR_CURRENT_TILT_POSITION: 35},
            )
        )
        await tilt_cover.async_added_to_hass()
        assert tilt_cover.current_cover_position == 85
        assert tilt_cover.current_cover_tilt_position == 35
        assert tilt_cover.device_class == CoverDeviceClass.BLIND

        # Test corrupt / non-numeric tilt attribute in last state
        tilt_cover.async_get_last_state = AsyncMock(
            return_value=State(
                "cover.venetian_blind",
                "open",
                {ATTR_CURRENT_POSITION: 85, ATTR_CURRENT_TILT_POSITION: "invalid_tilt"},
            )
        )
        tilt_cover._attr_current_cover_tilt_position = 0
        await tilt_cover.async_added_to_hass()
        assert tilt_cover.current_cover_tilt_position == 0

        # Test basic cover does NOT enable slat_tilt from restore
        basic_cover.async_get_last_state = AsyncMock(
            return_value=State(
                "cover.shutter",
                "open",
                {ATTR_CURRENT_POSITION: 85, ATTR_CURRENT_TILT_POSITION: 50},
            )
        )
        await basic_cover.async_added_to_hass()
        assert basic_cover._slat_tilt is False
        assert basic_cover.device_class == CoverDeviceClass.SHUTTER

    def test_cover_dimension11_updates_shutter_position(self, tilt_cover, basic_cover):
        """Dimension 11 frames (go-to-level writes and relays) never change state; DIM 10 does."""
        # Initial position 100
        tilt_cover._attr_current_cover_position = 100
        basic_cover._attr_current_cover_position = 20

        # 1. Dimension 11 command write (*#2*31*#11#001*50##) does not move the estimate
        msg1 = OWNMessage.parse("*#2*31*#11#001*50##")
        assert msg1 is not None
        tilt_cover.handle_event(msg1)
        assert tilt_cover.current_cover_position == 100

        # A plain stop does not snap to the unconfirmed target either
        stop_msg = OWNMessage.parse("*2*0*31##")
        assert stop_msg is not None
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_position == 100

        # 2. Dimension 11 bus event with selector (*#2*31*#11#001#1*40##) is ignored too
        msg2 = OWNMessage.parse("*#2*31*#11#001#1*40##")
        assert msg2 is not None
        tilt_cover.handle_event(msg2)
        assert tilt_cover.current_cover_position == 100

        # Dimension 10 stop status applies position
        dim10_stop = OWNMessage.parse("*#2*31*10*10*40*001*0##")
        assert dim10_stop is not None
        tilt_cover.handle_event(dim10_stop)
        assert tilt_cover.current_cover_position == 40

        # 3. A basic (non-tilt) cover does NOT snap on Dimension 11, does NOT turn into blind
        assert basic_cover._slat_tilt is False
        assert basic_cover.device_class == CoverDeviceClass.SHUTTER
        basic_cover.handle_event(msg2)
        assert basic_cover.current_cover_position == 20  # did not snap!
        basic_cover.handle_event(stop_msg)
        assert basic_cover.current_cover_position == 20  # plain stop does not snap
        # Confirmed via Dimension 10
        basic_cover.handle_event(dim10_stop)
        assert basic_cover.current_cover_position == 40
        assert basic_cover._slat_tilt is False
        assert basic_cover.device_class == CoverDeviceClass.SHUTTER

        # 4. Out of bounds or invalid values are ignored safely
        msg_inv = MagicMock()
        msg_inv.is_translation = False
        msg_inv.dimension = 11
        msg_inv.dimension_value = ["invalid"]
        tilt_cover.handle_event(msg_inv)
        assert tilt_cover.current_cover_position == 40

    def test_cover_slat_tilt_movement_frames_preserve_state(self, tilt_cover):
        """Test that actuator movement frames during tilt pulse do NOT clear _is_tilting or anchor linear travel."""
        tilt_cover._is_tilting = True
        tilt_cover._tilt_direction = "open"
        tilt_cover._tilt_start_time = time.monotonic()
        tilt_cover._tilt_duration = 2.0
        tilt_cover._tilt_initial_position = 0
        tilt_cover._tilt_target_position = 100
        tilt_cover._attr_current_cover_position = 60
        tilt_cover._run_started_at = None

        # Actuator movement frame in tilt direction (*2*1*31##)
        open_msg = OWNMessage.parse("*2*1*31##")
        assert open_msg is not None
        tilt_cover.handle_event(open_msg)

        assert tilt_cover._is_tilting is True
        assert tilt_cover._run_started_at is None
        assert tilt_cover.current_cover_position == 60

        # Dimension 10 movement frame (*#2*31*10*11*30*001*0##)
        dim10_move = OWNMessage.parse("*#2*31*10*11*30*001*0##")
        assert dim10_move is not None
        tilt_cover.handle_event(dim10_move)

        assert tilt_cover._is_tilting is True
        assert tilt_cover._run_started_at is None
        assert tilt_cover.current_cover_position == 60

        # Stop frame ends tilt without corrupting linear position
        stop_msg = OWNMessage.parse("*2*0*31##")
        assert stop_msg is not None
        tilt_cover.handle_event(stop_msg)

        assert tilt_cover._is_tilting is False
        assert tilt_cover.current_cover_position == 60

    def test_tilt_movement_opposite_direction_and_endpoints(self, tilt_cover):
        """Test opposite direction movement frames during tilt and 0/100 endpoint tilt updates."""
        # 1. Closing frame matching tilt direction "close"
        tilt_cover._is_tilting = True
        tilt_cover._tilt_direction = "close"
        tilt_cover._tilt_start_time = time.monotonic()
        tilt_cover._tilt_duration = 2.0
        tilt_cover._tilt_initial_position = 100
        tilt_cover._tilt_target_position = 50
        close_msg = OWNMessage.parse("*2*2*31##")
        assert close_msg is not None
        tilt_cover.handle_event(close_msg)
        assert tilt_cover._is_tilting is True
        assert tilt_cover._attr_is_closing is True
        assert tilt_cover._attr_is_opening is False

        # 2. Opposite direction: open arrives while tilting "close"
        mock_task = MagicMock()
        mock_task.done.return_value = False
        tilt_cover._stop_task = mock_task
        gen_before = tilt_cover._run_generation
        open_msg = OWNMessage.parse("*2*1*31##")
        assert open_msg is not None
        tilt_cover.handle_event(open_msg)
        assert tilt_cover._is_tilting is False
        mock_task.cancel.assert_called_once()
        assert tilt_cover._run_generation > gen_before

        # 3. Opposite direction: close arrives while tilting "open"
        tilt_cover._is_tilting = True
        tilt_cover._tilt_direction = "open"
        tilt_cover._tilt_start_time = time.monotonic()
        tilt_cover._tilt_duration = 2.0
        tilt_cover._tilt_initial_position = 0
        tilt_cover._tilt_target_position = 50
        tilt_cover.handle_event(close_msg)
        assert tilt_cover._is_tilting is False

        # 4. Status frame reporting position 0 and 100 updates tilt to 0 and 100
        dim10_pos0 = OWNMessage.parse("*#2*31*10*10*0*001*0##")
        assert dim10_pos0 is not None
        tilt_cover.handle_event(dim10_pos0)
        assert tilt_cover.current_cover_tilt_position == 0

        dim10_pos100 = OWNMessage.parse("*#2*31*10*10*100*001*0##")
        assert dim10_pos100 is not None
        tilt_cover.handle_event(dim10_pos100)
        assert tilt_cover.current_cover_tilt_position == 100

        # 5. Curtain travel updates tilt model
        stop_msg = OWNMessage.parse("*2*0*31##")
        assert stop_msg is not None

        # Closing travel >= slat_time rotates slats to 0
        tilt_cover._attr_current_cover_position = 40
        tilt_cover._attr_current_cover_tilt_position = 100
        tilt_cover._slat_time = 2.0
        tilt_cover._run_started_at = time.monotonic() - 3.0
        tilt_cover._attr_is_closing = True
        tilt_cover._attr_is_opening = False
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 0

        # Opening travel >= slat_time rotates slats to 100
        tilt_cover._attr_current_cover_position = 60
        tilt_cover._attr_current_cover_tilt_position = 0
        tilt_cover._slat_time = 2.0
        tilt_cover._run_started_at = time.monotonic() - 3.0
        tilt_cover._attr_is_closing = False
        tilt_cover._attr_is_opening = True
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 100

        # Partial travel (1s of 2s slat_time while opening from 0) updates tilt proportionally to 50
        tilt_cover._attr_current_cover_position = 50
        tilt_cover._attr_current_cover_tilt_position = 0
        tilt_cover._slat_time = 2.0
        tilt_cover._run_started_at = time.monotonic() - 1.0
        tilt_cover._attr_is_closing = False
        tilt_cover._attr_is_opening = True
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 50

        # Partial travel (1s of 2s slat_time while closing from 100) updates tilt proportionally to 50
        tilt_cover._attr_current_cover_position = 50
        tilt_cover._attr_current_cover_tilt_position = 100
        tilt_cover._slat_time = 2.0
        tilt_cover._run_started_at = time.monotonic() - 1.0
        tilt_cover._attr_is_closing = True
        tilt_cover._attr_is_opening = False
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 50

        # A short run that ends at position 0 forces tilt to 0
        tilt_cover._attr_current_cover_position = 0
        tilt_cover._attr_current_cover_tilt_position = 50
        tilt_cover._run_started_at = time.monotonic() - 0.2
        tilt_cover._attr_is_closing = True
        tilt_cover._attr_is_opening = False
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 0

        # A stop with nothing running (the relay of a slat pulse's stop) keeps
        # the slats of a lowered blind where they are
        tilt_cover._attr_current_cover_tilt_position = 50
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 50

        # A short run that ends at position 100 forces tilt to 100
        tilt_cover._attr_current_cover_position = 100
        tilt_cover._attr_current_cover_tilt_position = 50
        tilt_cover._run_started_at = time.monotonic() - 0.2
        tilt_cover._attr_is_closing = False
        tilt_cover._attr_is_opening = True
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 100

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_manual_stop_interpolates_angle(self, tilt_cover, mock_gateway):
        """Test that manual stop mid-tilt interpolates angle proportionally."""
        fut = asyncio.Future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut

        tilt_cover._attr_current_cover_tilt_position = 0
        tilt_cover._slat_time = 2.0

        sleep_gate = asyncio.Event()
        with patch("asyncio.sleep", side_effect=lambda s: sleep_gate.wait()):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=100)
            assert tilt_cover._is_tilting is True
            tilt_cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
            start_ts = tilt_cover._tilt_start_time

            # Simulate stopping 1.0 second into a 2.0s pulse (50% progress)
            with patch("time.monotonic", return_value=start_ts + 1.0):
                await tilt_cover.async_stop_cover_tilt()

            assert tilt_cover._is_tilting is False
            assert tilt_cover.current_cover_tilt_position == 50

    @pytest.mark.asyncio
    async def test_cover_slat_time_service(self, tilt_cover, basic_cover):
        """Test setting slat_time via async_set_travel_time and range validation."""
        # 1. Setting slat_time alone on a tilt cover preserves calibration source (e.g. measured)
        tilt_cover._calibration_source = "measured"
        res = await tilt_cover.async_set_travel_time(slat_time=3.5)
        assert res["slat_time"] == 3.5
        assert res["source"] == "measured"
        assert tilt_cover._calibration_source == "measured"
        assert tilt_cover._slat_time == 3.5
        assert tilt_cover.extra_state_attributes["slat_time"] == 3.5

        # 2. Out of range slat_time rejected with ServiceValidationError
        with pytest.raises(ServiceValidationError):
            await tilt_cover.async_set_travel_time(slat_time=0.2)

        with pytest.raises(ServiceValidationError):
            await tilt_cover.async_set_travel_time(slat_time=12.0)

        # 3. Missing both travel_time and slat_time raises ServiceValidationError
        with pytest.raises(ServiceValidationError):
            await tilt_cover.async_set_travel_time()

        # 4. Advanced cover rejects travel time calibration
        basic_cover._advanced = True
        with pytest.raises(HomeAssistantError, match="reports its position"):
            await basic_cover.async_set_travel_time(travel_time=30)

    @pytest.mark.asyncio
    async def test_cover_slat_tilt_auto_stop_branches(self, tilt_cover, mock_gateway):
        """Test auto-stop cancellation, error handling, pending write, and superseding branches."""
        fut = asyncio.Future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut

        tilt_cover._attr_current_cover_tilt_position = 0

        # Branch 1: Superseded before sleep
        tilt_cover._run_generation = 1
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)
            tilt_cover._run_generation = 99  # simulate superseding command
            if tilt_cover._stop_task:
                await tilt_cover._stop_task

        # Branch 2: Superseded during sleep
        tilt_cover._attr_current_cover_tilt_position = 0
        async def bump_gen_sleep(delay, *args, **kwargs):
            tilt_cover._run_generation += 1

        with patch("asyncio.sleep", side_effect=bump_gen_sleep):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)
            if tilt_cover._stop_task:
                await tilt_cover._stop_task

        # Branch 3: Pending write future resolves with timestamp
        pending_fut = asyncio.Future()
        mock_gateway.send.return_value = pending_fut
        tilt_cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)
            pending_fut.set_result(time.monotonic())
            if tilt_cover._stop_task:
                await tilt_cover._stop_task
        # No direction status: the stop waits in real time until write + delay + pulse,
        # and whatever the loop adds on top is real slat travel.
        assert tilt_cover.current_cover_tilt_position == pytest.approx(50, abs=6)

        # Branch 4: Pending write future raises TimeoutError/Exception
        err_fut = asyncio.Future()
        mock_gateway.send.return_value = err_fut
        tilt_cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)
            err_fut.set_exception(TimeoutError())
            if tilt_cover._stop_task:
                await tilt_cover._stop_task
            _ = err_fut.exception()

        # Branch 5: HomeAssistantError during auto-stop
        mock_gateway.send.return_value = fut
        tilt_cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new_callable=AsyncMock), \
             patch.object(tilt_cover, "async_stop_cover", side_effect=HomeAssistantError("gateway fail")):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)
            if tilt_cover._stop_task:
                await tilt_cover._stop_task
            assert tilt_cover._is_tilting is False

        # Branch 6: CancelledError during auto-stop
        tilt_cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            await tilt_cover.async_set_cover_tilt_position(tilt_position=50)
            if tilt_cover._stop_task:
                await tilt_cover._stop_task


class TestTiltAuditRegressions:
    """Regressions for the PR #508 audit (findings 1-4 and 7)."""

    @pytest.fixture
    def make_cover(self, hass, mock_gateway):
        def _make(advanced=False, **kwargs):
            with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
                cover = MyHOMECover(
                    hass=hass, name="Blind", entity_name="Blind", device_id="31", who="2", where="31",
                    interface=None, advanced=advanced, slat_tilt=True, manufacturer="BTicino",
                    model="Venetian Blind", gateway=mock_gateway, **kwargs,
                )
            cover.entity_id = "cover.blind"
            cover.hass = hass
            cover.async_write_ha_state = MagicMock()
            return cover

        return _make

    @pytest.fixture
    def cover(self, make_cover):
        return make_cover()

    @staticmethod
    def _wire_options(cover, hass, options):
        entry = MagicMock()
        entry.options = options
        cover._gateway_handler.config_entry = entry
        hass.config_entries.async_update_entry = MagicMock(
            side_effect=lambda e, options: setattr(e, "options", options)
        )
        return entry

    def test_dimension11_leaves_no_dead_state(self, cover):
        """Finding 1: DIM 11 must not leave unconsumed state behind."""
        assert not hasattr(cover, "_pending_target_position")
        cover._attr_current_cover_position = 70
        cover.handle_event(OWNMessage.parse("*#2*31*#11#001*50##"))
        assert cover.current_cover_position == 70
        assert not hasattr(cover, "_pending_target_position")

    @pytest.mark.asyncio
    async def test_slat_time_only_does_not_store_travel_calibration(self, cover, hass):
        """Finding 2: a slat-only call must not turn the defaults into a stored calibration."""
        entry = self._wire_options(cover, hass, {})
        await cover.async_set_travel_time(slat_time=3.5)
        stored = entry.options["cover_travel_times"]["31"]
        assert stored == {"slat_time": 3.5}

    @pytest.mark.asyncio
    async def test_slat_time_only_keeps_existing_travel_record(self, cover, hass):
        entry = self._wire_options(
            cover, hass, {"cover_travel_times": {"31": {"down": 20.0, "up": 22.0, "source": "measured"}}}
        )
        await cover.async_set_travel_time(slat_time=1.5)
        assert entry.options["cover_travel_times"]["31"] == {
            "down": 20.0, "up": 22.0, "source": "measured", "slat_time": 1.5,
        }

    @pytest.mark.asyncio
    async def test_travel_time_call_stores_slat_time_with_it(self, cover, hass):
        entry = self._wire_options(cover, hass, {})
        await cover.async_set_travel_time(travel_time=30, slat_time=2.5)
        stored = entry.options["cover_travel_times"]["31"]
        assert stored["down"] == 30 and stored["slat_time"] == 2.5

    def test_stored_slat_time_is_restored(self, make_cover):
        """Finding 2: the service-set slat time survives a restart."""
        cover = make_cover(calibration={"slat_time": 3.25})
        assert cover._slat_time == 3.25
        assert cover._calibration_source == "default"  # a slat-only record is no travel calibration

    def test_stored_slat_time_is_clamped_and_garbage_ignored(self, make_cover):
        assert make_cover(calibration={"slat_time": 99})._slat_time == 10.0
        assert make_cover(calibration={"slat_time": "x"})._slat_time == 2.0

    @pytest.mark.asyncio
    async def test_slat_time_service_allowed_on_advanced_cover(self, make_cover):
        """Finding 7: advanced covers can still set the slat time, but not travel times."""
        cover = make_cover(advanced=True)
        res = await cover.async_set_travel_time(slat_time=2.5)
        assert res["slat_time"] == 2.5
        with pytest.raises(HomeAssistantError, match="reports its position"):
            await cover.async_set_travel_time(travel_time=30)
        with pytest.raises(HomeAssistantError, match="reports its position"):
            await cover.async_set_travel_time()

    @pytest.mark.asyncio
    async def test_status_report_during_tilt_pulse_keeps_auto_stop(self, cover, mock_gateway):
        """Finding 3: a position report mid-pulse must not cancel the stop."""
        fut = asyncio.Future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut
        cover._attr_current_cover_tilt_position = 0
        gate = asyncio.Event()
        real_sleep = asyncio.sleep
        async def fake_sleep(_delay):
            await gate.wait()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            task = cover._stop_task
            assert task is not None
            await real_sleep(0)
            cover.handle_event(OWNMessage.parse("*#2*31*10*10*40*001*0##"))
            assert cover._stop_task is task
            assert not task.cancelled()
            assert cover._is_tilting is True
            gate.set()
            await task
        assert any(str(c.args[0]) == "*2*0*31##" for c in mock_gateway.send.call_args_list)
        assert cover._is_tilting is False

    @pytest.mark.asyncio
    async def test_status_report_without_tilt_still_cancels_stop_task(self, make_cover):
        """The guard is tilt-only: ordinary runs keep their behaviour."""
        cover = make_cover()
        task = asyncio.ensure_future(asyncio.sleep(10))
        cover._stop_task = task
        cover.handle_event(OWNMessage.parse("*#2*31*10*10*40*001*0##"))
        await asyncio.sleep(0)
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_superseded_tilt_task_never_sends_stale_stop(self, cover, mock_gateway):
        """Finding 4: cancelling the task while it awaits the write ends it quietly."""
        pending = asyncio.Future()
        mock_gateway.send.return_value = pending
        cover._attr_current_cover_tilt_position = 0
        await cover.async_set_cover_tilt_position(tilt_position=50)
        task = cover._stop_task
        await asyncio.sleep(0)  # the task is now waiting for the write
        sent = mock_gateway.send.call_count
        cover._cancel_stop_task()
        await asyncio.sleep(0)
        assert task.done()
        pending.set_result(time.monotonic())
        await asyncio.sleep(0.01)
        assert mock_gateway.send.call_count == sent  # no stop frame

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("queue flushed")])
    async def test_failed_tilt_write_aborts_without_moving_angle(self, cover, mock_gateway, failure):
        """Finding 4: a pulse that never reached the bus changes nothing and sends no stop."""
        pending = asyncio.Future()
        mock_gateway.send.return_value = pending
        cover._attr_current_cover_tilt_position = 20
        await cover.async_set_cover_tilt_position(tilt_position=80)
        task = cover._stop_task
        await asyncio.sleep(0)
        sent = mock_gateway.send.call_count
        pending.set_exception(failure)
        await task
        assert cover._is_tilting is False
        assert cover._attr_is_opening is False
        assert cover.current_cover_tilt_position == 20
        assert mock_gateway.send.call_count == sent

    @pytest.mark.asyncio
    async def test_cancelled_tilt_write_aborts(self, cover, mock_gateway):
        pending = asyncio.Future()
        mock_gateway.send.return_value = pending
        cover._attr_current_cover_tilt_position = 20
        await cover.async_set_cover_tilt_position(tilt_position=80)
        task = cover._stop_task
        await asyncio.sleep(0)
        pending.cancel()
        await task
        assert cover._is_tilting is False
        assert cover.current_cover_tilt_position == 20

    @pytest.mark.asyncio
    async def test_setting_current_tilt_mid_pulse_stops_the_motor(self, cover, mock_gateway):
        """Setting the angle the running pulse has just reached must stop the motor, not strand it."""
        fut = asyncio.Future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        gate = asyncio.Event()
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):
            await gate.wait()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=100)
            task = cover._stop_task
            cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
            start = cover._tilt_start_time
            with patch("time.monotonic", return_value=start + 1.0):  # half way: 50 %
                await cover.async_set_cover_tilt_position(tilt_position=50)
            assert cover._is_tilting is False
            assert cover.current_cover_tilt_position == 50
            assert any(str(c.args[0]) == "*2*0*31##" for c in mock_gateway.send.call_args_list)
            gate.set()
            await real_sleep(0)
            assert task.cancelled()  # the stop cancelled the superseded auto-stop itself

    def test_interpolated_tilt_without_a_running_pulse_is_the_last_angle(self, cover):
        """A second command can arrive before the first pulse has a start time."""
        cover._attr_current_cover_tilt_position = 35
        assert cover._interpolated_tilt(time.monotonic()) == 35

    @pytest.mark.asyncio
    async def test_new_tilt_target_mid_pulse_starts_from_interpolated_angle(self, cover, mock_gateway):
        fut = asyncio.Future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        gate = asyncio.Event()
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):
            await gate.wait()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=100)
            first = cover._stop_task
            cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
            start = cover._tilt_start_time
            with patch("time.monotonic", return_value=start + 1.0):
                await cover.async_set_cover_tilt_position(tilt_position=25)
            assert cover._tilt_initial_position == 50  # not the stale 0
            assert cover._tilt_direction == "close"
            assert cover._tilt_duration == pytest.approx(0.5)
            await real_sleep(0)
            assert first.done()  # the superseded auto-stop is gone
            gate.set()
            await cover._stop_task

    # ── Second audit of PR #508 ──────────────────────────────────────────

    @staticmethod
    def _sent_stops(mock_gateway):
        return [c for c in mock_gateway.send.call_args_list if str(c.args[0]) == "*2*0*31##"]

    @staticmethod
    def _written_now(mock_gateway):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(time.monotonic())
        mock_gateway.send.return_value = fut
        return fut

    @pytest.mark.asyncio
    @pytest.mark.parametrize("advanced", [False, True])
    async def test_relayed_stop_before_direction_status_is_an_echo(self, make_cover, mock_gateway, advanced):
        """The stop status the gateway relays before the direction status must not end the pulse."""
        cover = make_cover(advanced=advanced)
        self._written_now(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        gate = asyncio.Event()
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):
            await gate.wait()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            task = cover._stop_task
            await real_sleep(0)
            cover.handle_event(OWNMessage.parse("*2*0*31##"))  # relayed stop status
            assert cover._stop_task is task and not task.done()
            assert cover._is_tilting is True
            cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
            gate.set()
            await task
        assert self._sent_stops(mock_gateway)
        assert cover._is_tilting is False
        assert cover.current_cover_tilt_position == 50

    @pytest.mark.asyncio
    async def test_own_stop_relay_on_a_lowered_blind_keeps_the_slats(self, cover, mock_gateway):
        """Blind fully down, slats tilted open: the relayed stop of the pulse must not close them."""
        self._written_now(mock_gateway)
        cover._attr_current_cover_position = 0
        cover._start_position = 0
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
            await cover._stop_task
        assert self._sent_stops(mock_gateway)
        assert cover.current_cover_tilt_position == 50
        cover.handle_event(OWNMessage.parse("*2*0*31##"))  # the gateway relays our stop
        assert cover.current_cover_tilt_position == 50
        assert cover.current_cover_position == 0

    def test_advanced_level_report_turns_the_slats_only_at_a_new_end_stop(self, make_cover):
        cover = make_cover(advanced=True)
        cover._attr_current_cover_position = 0
        cover._attr_current_cover_tilt_position = 60
        cover.handle_event(OWNMessage.parse("*#2*31*10*10*0*001*0##"))  # the same level again
        assert cover.current_cover_tilt_position == 60
        cover._attr_current_cover_position = 40
        cover.handle_event(OWNMessage.parse("*#2*31*10*10*0*001*0##"))  # the curtain came down
        assert cover.current_cover_tilt_position == 0

    @pytest.mark.asyncio
    async def test_tilt_is_refused_while_the_curtain_runs(self, cover, mock_gateway):
        cover._attr_current_cover_position = 100
        cover.handle_event(OWNMessage.parse("*2*2*31##"))  # keypad close run
        with pytest.raises(HomeAssistantError, match="is moving") as err:
            await cover.async_set_cover_tilt_position(tilt_position=50)
        assert err.value.translation_key == "cover_busy_moving"
        mock_gateway.send.assert_not_called()
        assert cover.is_closing is True

    @pytest.mark.asyncio
    async def test_curtain_command_mid_pulse_settles_the_angle(self, cover, mock_gateway):
        self._written_now(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        await cover.async_set_cover_tilt_position(tilt_position=100)
        pulse = cover._stop_task
        cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
        start = cover._tilt_start_time
        with patch("time.monotonic", return_value=start + 1.0):
            await cover.async_close_cover()
        assert cover._is_tilting is False
        assert cover.current_cover_tilt_position == 50
        assert cover.is_closing is True
        await asyncio.sleep(0)
        assert pulse.cancelled()
        cover._cancel_stop_task()

    @pytest.mark.asyncio
    async def test_tilt_step_below_the_minimum_pulse_sends_nothing(self, cover, mock_gateway):
        cover._attr_current_cover_tilt_position = 50
        cover._slat_time = 2.0
        await cover.async_set_cover_tilt_position(tilt_position=52)  # a 40 ms pulse
        mock_gateway.send.assert_not_called()
        assert cover.current_cover_tilt_position == 50
        await cover.async_set_cover_tilt_position(tilt_position=56)  # 120 ms
        assert mock_gateway.send.call_count == 1
        cover._cancel_stop_task()

    @pytest.mark.asyncio
    async def test_pulse_is_timed_from_the_direction_status(self, cover, mock_gateway):
        """The motor starts ~0.55 s after the write: a pulse timed from the write loses that."""
        write_ts = time.monotonic()
        fut = asyncio.get_running_loop().create_future()
        fut.set_result(write_ts)
        mock_gateway.send.return_value = fut
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=50)  # 1.0 s of slat travel
            assert cover._tilt_start_time is None  # nothing turns before the write
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            assert cover._tilt_start_time >= write_ts
            await cover._stop_task
        assert sleeps[0] == pytest.approx(1.0, abs=0.1)
        assert self._sent_stops(mock_gateway)

    @pytest.mark.asyncio
    async def test_pulse_without_direction_status_stops_on_time(self, cover, mock_gateway):
        """A gateway that never relays the direction status: the stop is due at write + delay + pulse.

        Waiting the whole echo window for that status would stretch a 0.2 s pulse to ~1 s.
        """
        self._written_now(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        started = time.monotonic()
        await cover.async_set_cover_tilt_position(tilt_position=10)  # 0.2 s of slat travel
        await cover._stop_task
        elapsed = time.monotonic() - started
        assert MOTOR_START_DELAY + 0.2 - 0.05 <= elapsed < ECHO_WINDOW
        assert self._sent_stops(mock_gateway)
        assert cover.current_cover_tilt_position == pytest.approx(10, abs=6)

    # ── Third audit of PR #508 ───────────────────────────────────────────

    @staticmethod
    def _write_each_send(mock_gateway, delays=None):
        """Every send gets its own delivery future, written now or after ``delays[frame]`` seconds."""
        loop = asyncio.get_running_loop()

        async def send(cmd):
            fut = loop.create_future()
            delay = (delays or {}).get(str(cmd), 0.0)
            if delay:
                loop.call_later(delay, lambda: fut.done() or fut.set_result(time.monotonic()))
            else:
                fut.set_result(time.monotonic())
            return fut

        mock_gateway.send.side_effect = send

    @pytest.mark.asyncio
    async def test_set_position_to_the_current_level_mid_pulse_stops_the_motor(self, cover, mock_gateway):
        """A 'close blinds' automation on a lowered blind whose slats are turning."""
        self._write_each_send(mock_gateway)
        cover._attr_current_cover_position = 0
        cover._start_position = 0
        cover._attr_current_cover_tilt_position = 0
        gate = asyncio.Event()
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):
            await gate.wait()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await real_sleep(0)
            await cover.async_set_cover_position(position=0)
            gate.set()
            await real_sleep(0)
        assert self._sent_stops(mock_gateway)
        assert cover._is_tilting is False
        assert cover.current_cover_position == 0

    @pytest.mark.asyncio
    async def test_set_position_to_the_level_a_run_is_passing_stops_it(self, cover, mock_gateway):
        self._write_each_send(mock_gateway)
        cover._attr_current_cover_position = 100
        cover._start_position = 100
        cover.handle_event(OWNMessage.parse("*2*2*31##"))  # keypad close run
        cover._move_start_time = cover._run_started_at = time.monotonic() - 10  # ~60 %
        await cover.async_set_cover_position(position=cover.current_cover_position)
        assert self._sent_stops(mock_gateway)

    @pytest.mark.asyncio
    async def test_level_command_takes_the_motor_over_from_a_pulse(self, make_cover, mock_gateway):
        cover = make_cover(advanced=True)
        self._write_each_send(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            pulse = cover._stop_task
            await cover.async_set_cover_position(position=60)
            await asyncio.gather(pulse, return_exceptions=True)
        sent = [str(c.args[0]) for c in mock_gateway.send.call_args_list]
        assert sent[-1] != "*2*0*31##"  # the pulse's auto-stop must not halt the level move
        assert self._sent_stops(mock_gateway) == []
        assert cover._is_tilting is False

    @pytest.mark.asyncio
    async def test_late_stop_write_counts_the_extra_slat_travel(self, cover, mock_gateway):
        """The FIFO queue writes the stop late: the slats turn on until it reaches the bus."""
        self._write_each_send(mock_gateway, delays={"*2*0*31##": 0.5})
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        await cover.async_set_cover_tilt_position(tilt_position=25)  # 0.5 s
        cover.handle_event(OWNMessage.parse("*2*1*31##"))
        await cover._stop_task
        assert cover._is_tilting is True  # the motor still runs until the stop is written
        await asyncio.sleep(0.6)
        assert cover._is_tilting is False
        assert 45 <= cover.current_cover_tilt_position <= 65  # ~1.0 s of travel, not 25 %

    @pytest.mark.asyncio
    async def test_stop_status_before_our_late_stop_is_written_settles_the_pulse(self, cover, mock_gateway):
        self._write_each_send(mock_gateway, delays={"*2*0*31##": 0.3})
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
        assert cover._is_tilting is True
        cover.handle_event(OWNMessage.parse("*2*0*31##"))  # a keypad stop beats our queued one
        settled = cover.current_cover_tilt_position
        assert cover._is_tilting is False
        await asyncio.sleep(0.4)  # our stop is written now: it must not settle again
        assert cover.current_cover_tilt_position == settled

    @pytest.mark.asyncio
    async def test_undelivered_pulse_stop_is_logged_and_settled(self, cover, mock_gateway, caplog):
        loop = asyncio.get_running_loop()

        async def send(cmd):
            fut = loop.create_future()
            if str(cmd) == "*2*0*31##":
                loop.call_soon(fut.set_exception, OSError("connection lost"))
            else:
                fut.set_result(time.monotonic())
            return fut

        mock_gateway.send.side_effect = send
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
        await asyncio.sleep(0)
        assert cover._is_tilting is False
        assert "was not delivered; the motor may still run" in caplog.text

    @pytest.mark.asyncio
    async def test_pulse_superseded_while_waiting_for_the_motor_start_sends_no_stop(self, cover, mock_gateway):
        self._write_each_send(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        await cover.async_set_cover_tilt_position(tilt_position=10)  # stop due ~0.75 s after the write

        def supersede():
            cover._run_generation += 1

        asyncio.get_running_loop().call_later(0.1, supersede)
        await cover._stop_task
        assert self._sent_stops(mock_gateway) == []

    @pytest.mark.asyncio
    async def test_keypad_press_after_a_stopped_pulse_is_not_an_echo(self, cover, mock_gateway):
        self._write_each_send(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            await cover.async_stop_cover_tilt()  # before any direction status
        assert cover._is_tilting is False
        cover.handle_event(OWNMessage.parse("*2*1*31##"))  # keypad UP right after
        assert cover.is_opening is True

    @pytest.mark.asyncio
    async def test_reset_on_advanced_blind_restores_the_slat_time(self, make_cover, hass):
        cover = make_cover(advanced=True)
        entry = self._wire_options(cover, hass, {"cover_travel_times": {"31": {"slat_time": 4.0}}})
        cover._slat_time = 4.0
        with patch.object(cover, "_device_config", return_value={"slat_time": 1.5}):
            await cover.async_reset_travel_time()
        assert cover._slat_time == 1.5
        assert "31" not in entry.options["cover_travel_times"]
        assert "travel_time" not in cover._attr_extra_state_attributes

    @pytest.mark.asyncio
    async def test_pulse_superseded_during_its_run_sends_no_stop(self, cover, mock_gateway):
        self._written_now(mock_gateway)
        cover._attr_current_cover_tilt_position = 0

        async def superseding_sleep(_delay):
            cover._run_generation += 1  # a newer command took over while the pulse ran

        with patch("asyncio.sleep", side_effect=superseding_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))  # motor start
            await cover._stop_task
        assert self._sent_stops(mock_gateway) == []

    @pytest.mark.asyncio
    async def test_stop_queued_before_a_new_pulse_does_not_settle_it(self, cover, mock_gateway):
        cover._is_tilting = True
        cover._tilt_start_time = time.monotonic()
        cover._tilt_initial_position, cover._tilt_target_position, cover._tilt_duration = 0, 50, 1.0

        async def send(_cmd):
            # A new pulse is started while this stop is being queued.
            cover._begin_command("open")
            cover._tilt_initial_position, cover._tilt_target_position = 50, 100
            return None

        mock_gateway.send.side_effect = send
        await cover.async_stop_cover()
        assert cover._is_tilting is True
        assert cover._tilt_start_time is not None

    @pytest.mark.asyncio
    async def test_failed_tilt_write_on_advanced_cover_adds_no_travel_attributes(self, make_cover, mock_gateway):
        cover = make_cover(advanced=True)
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(OSError("connection lost"))
        mock_gateway.send.return_value = fut
        cover._attr_current_cover_tilt_position = 0
        with pytest.raises(HomeAssistantError):
            await cover.async_set_cover_tilt_position(tilt_position=50)
        assert cover._is_tilting is False
        assert not cover.is_opening
        assert cover.current_cover_tilt_position == 0
        assert "travel_time" not in cover._attr_extra_state_attributes

    @pytest.mark.asyncio
    async def test_reset_restores_the_configured_slat_time(self, cover, hass):
        entry = self._wire_options(cover, hass, {"cover_travel_times": {"31": {"slat_time": 4.0}}})
        cover._slat_time = 4.0
        with patch.object(cover, "_device_config", return_value={"slat_time": 1.5}):
            await cover.async_reset_travel_time()
        assert cover._slat_time == 1.5
        assert cover._attr_extra_state_attributes["slat_time"] == 1.5
        assert "31" not in entry.options["cover_travel_times"]

    def test_non_numeric_slat_time_is_a_schema_error(self):
        """Finding 7: a bad slat_time is rejected as a schema error, never a bare ValueError."""
        from voluptuous import Invalid

        from custom_components.myhome.validate import cover_schema

        with pytest.raises(Invalid):
            cover_schema({"c": {"where": "31", "slat_tilt": True, "slat_time": "fast"}})


async def test_cover_general_commands_update_all_covers(hass: HomeAssistant, mock_gateway):
    """Test that general cover events (*2*1*0##, *2*2*0##, *2*0*0##) update all covers."""

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
        cover1.entity_id = "cover.cover1"  # assigned by the registry in real Home Assistant
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
        cover2.entity_id = "cover.cover2"  # assigned by the registry in real Home Assistant

    cover1.hass = hass
    cover1.entity_id = cover1.entity_id or "test.cover1"
    cover2.hass = hass
    cover2.entity_id = cover2.entity_id or "test.cover2"
    cover1.async_schedule_update_ha_state = MagicMock()
    cover2.async_schedule_update_ha_state = MagicMock()

    await cover1.async_added_to_hass()
    await cover2.async_added_to_hass()
    # The cover platform subscribes every cover under "general" (see cover.async_setup_entry)
    router = FrameRouter()
    for cover in (cover1, cover2):
        router.subscribe("2", [cover._where, "general"], cover.handle_event)

    cover1._attr_current_cover_position = 50
    cover1._start_position = 50
    cover2._attr_current_cover_position = 50
    cover2._start_position = 50

    # 1. General Open (*2*1*0##)
    msg_open = OWNEvent.parse("*2*1*0##")
    with patch("time.monotonic", return_value=1000.0):
        router.publish("2", ["general"], msg_open)

    assert cover1.is_opening is True
    assert cover1.is_closing is False
    assert cover2.is_opening is True
    assert cover2.is_closing is False

    # 2. General Stop (*2*0*0##) after 5 seconds (5s / 25s * 100 = 20% increase -> 70%)
    with patch("time.monotonic", return_value=1005.0):
        msg_stop = OWNEvent.parse("*2*0*0##")
        router.publish("2", ["general"], msg_stop)

    assert cover1.is_opening is False
    assert cover1.is_closing is False
    assert cover2.is_opening is False
    assert cover2.is_closing is False
    assert cover1.current_cover_position == 70
    assert cover2.current_cover_position == 70

    # 3. General Close (*2*2*0##)
    msg_close = OWNEvent.parse("*2*2*0##")
    with patch("time.monotonic", return_value=2000.0):
        router.publish("2", ["general"], msg_close)

    assert cover1.is_closing is True
    assert cover1.is_opening is False
    assert cover2.is_closing is True
    assert cover2.is_opening is False

    # 4. General Stop (*2*0*0##) after 5 seconds (70% - 20% = 50%)
    with patch("time.monotonic", return_value=2005.0):
        router.publish("2", ["general"], msg_stop)

    assert cover1.is_closing is False
    assert cover2.is_closing is False
    assert cover1.current_cover_position == 50
    assert cover2.current_cover_position == 50

    # 5. Verify individual commands only affect the target cover
    msg_single_open = OWNEvent.parse("*2*1*21##")
    router.publish("2", ["21"], msg_single_open)
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
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, lambda entities: None)

    dispatched = []

    @callback
    def on_general_event(msg):
        dispatched.append(msg)

    config_entry.runtime_data.router.subscribe("2", ["general"], on_general_event)

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
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, fake_add_entities)

    assert len(added_entities) == 2
    cover1, cover2 = added_entities[0], added_entities[1]
    cover1.hass = hass
    cover1.entity_id = cover1.entity_id or "test.cover1"
    cover2.hass = hass
    cover2.entity_id = cover2.entity_id or "test.cover2"
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
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)

    assert len(added) == 1
    assert added[0]._advanced is True


@pytest.mark.asyncio
async def test_cover_slat_tilt_setup_entry_config(hass, mock_gateway):
    """Test that CONF_SLAT_TILT configuration enables slat tilt on setup."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {
                        "41": {
                            CONF_WHERE: "41",
                            CONF_NAME: "Blind 41",
                            CONF_SLAT_TILT: True,
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
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)

    assert len(added) == 1
    assert added[0]._slat_tilt is True
    assert added[0].device_class == CoverDeviceClass.BLIND
    assert added[0].supported_features & CoverEntityFeature.SET_TILT_POSITION


@pytest.mark.asyncio
async def test_yaml_cover_restores_a_service_set_slat_time(hass, mock_gateway):
    """A slat time stored by myhome.set_cover_travel_time wins over myhome.yaml after a restart."""
    mac = mock_gateway.mac
    hass.data = {
        DOMAIN: {
            mac: {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    PLATFORM: {"41": {CONF_WHERE: "41", CONF_NAME: "Blind 41", CONF_SLAT_TILT: True, "slat_time": 2.0}},
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"
    config_entry.options = {"cover_travel_times": {"41": {"down": 40.0, "up": 40.0, "slat_time": 3.5}}}

    added = []
    with patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
         patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()):
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)

    assert len(added) == 1
    assert added[0]._slat_time == 3.5
    # yaml covers keep their configured travel time
    assert added[0]._travel_time_down == 25.0
    assert added[0]._calibration_source == "default"
