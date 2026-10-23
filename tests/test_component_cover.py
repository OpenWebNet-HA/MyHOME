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
from custom_components.myhome.cover_motion import (
    ECHO_WINDOW,
    END_SETTLE_MARGIN,
    MOTOR_START_DELAY,
)
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

        # 100% is a plain UP too: a full run to the limit switch (#466).
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 100})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*2*1*22#4#02##"
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
            assert basic_cover.current_cover_position in (80, 82)

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
            assert basic_cover.current_cover_position in (18, 20)

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

    async def test_set_cover_position_end_stops_and_motor_floor(self, basic_cover, advanced_cover):
        """0%/100% are full runs; short moves are widened so the motor actually turns (#466)."""
        # 1. 0% and 100% on a timed cover are full runs with no timed stop, also
        # when the estimate already says so: the limit switch resets the drift.
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 100
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 100})
        basic_cover._gateway_handler.send.assert_awaited_once()
        assert str(basic_cover._gateway_handler.send.call_args[0][0]) == "*2*1*21##"
        basic_cover._cancel_stop_task()  # the settle timer, not a stop frame (see the settle test)

        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 0
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 0})
        basic_cover._gateway_handler.send.assert_awaited_once()
        assert str(basic_cover._gateway_handler.send.call_args[0][0]) == "*2*2*21##"
        basic_cover._cancel_stop_task()

        # 2. Same on an advanced cover that already reports the end.
        advanced_cover._gateway_handler.send.reset_mock()
        advanced_cover._attr_current_cover_position = 100
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 100})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert str(advanced_cover._gateway_handler.send.call_args[0][0]) == "*2*1*22#4#02##"

        # 3. Advanced cover: a 1% level change is widened to MIN_POSITION_DELTA.
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*40*001*0##"))
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 40})
        advanced_cover._gateway_handler.send.assert_not_awaited()

        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 41})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*42##"
        )

        advanced_cover._gateway_handler.send.reset_mock()
        advanced_cover._attr_current_cover_position = 40
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 39})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*38##"
        )

        # 3b. Rapid retarget before the actuator's first moving status arrives:
        # at 40, set 80 then 40 quickly -> must not drop the second call against stale 40.
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*40*001*0##"))
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 80})
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 40})
        assert [str(c.args[0]) for c in advanced_cover._gateway_handler.send.await_args_list] == [
            "*#2*22#4#02*#11#001*80##",
            "*#2*22#4#02*#11#001*40##",
        ]

        # 4. Widening that lands on an end becomes a full run to that end.
        advanced_cover._gateway_handler.send.reset_mock()
        advanced_cover._attr_current_cover_position = 2
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 1})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert str(advanced_cover._gateway_handler.send.call_args[0][0]) == "*2*2*22#4#02##"

        # 4b. While the actuator runs it reports the level it started from: a
        # retarget must go out as asked, not be widened or dropped against it.
        advanced_cover._gateway_handler.send.reset_mock()
        advanced_cover._attr_current_cover_position = 40
        advanced_cover._attr_is_opening = True  # running 40 -> 80
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 40})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*40##"
        )
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 41})
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*41##"
        )
        advanced_cover._attr_is_opening = False

        # 5. Unknown level (shutterLevel 255): nothing to widen from, the target goes as is.
        advanced_cover._gateway_handler.send.reset_mock()
        advanced_cover._attr_current_cover_position = None
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 41})
        advanced_cover._gateway_handler.send.assert_awaited_once()
        assert (
            str(advanced_cover._gateway_handler.send.call_args[0][0])
            == "*#2*22#4#02*#11#001*41##"
        )

        # 6. Timed cover: a 1% move on a 5 s cover is 0.05 s, stretched to
        # MIN_MOTOR_PULSE (0.4 s = 8% of travel). With stop latency fallback
        # (0.55 s), the motor runs 0.95 s total (19% of travel), booking 69%
        # (or 58% without stop latency fallback), not the 51% asked for.
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 50
        basic_cover._travel_time_up = 5.0
        slept_durations = []

        async def fake_sleep(duration):
            slept_durations.append(duration)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 51})
            assert basic_cover._stop_task is not None
            await basic_cover._stop_task

        assert any(0.39 <= d <= 0.41 for d in slept_durations), (
            f"Run duration must be clamped to MIN_MOTOR_PULSE, got: {slept_durations}"
        )
        assert basic_cover.current_cover_position in (58, 69)
        assert basic_cover._start_position in (58, 69)

        # 7. A stretched run that would reach an end is a full run to that end:
        # 98% -> 99% on a 25 s cover runs 0.4 s = 1.6%, i.e. to 100%.
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 98
        basic_cover._start_position = 98
        basic_cover._travel_time_up = 25.0
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 99})
        basic_cover._gateway_handler.send.assert_awaited_once()
        assert str(basic_cover._gateway_handler.send.call_args[0][0]) == "*2*1*21##"
        basic_cover._cancel_stop_task()

        basic_cover.handle_event(OWNEvent.parse("*2*0*21##"))
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 2
        basic_cover._start_position = 2
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 1})
        basic_cover._gateway_handler.send.assert_awaited_once()
        assert str(basic_cover._gateway_handler.send.call_args[0][0]) == "*2*2*21##"
        basic_cover._cancel_stop_task()

        # 8. Calibrating cover raises HomeAssistantError
        basic_cover._calibrating = True
        with pytest.raises(HomeAssistantError, match="is being calibrated"):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 60})
        basic_cover._calibrating = False

    async def test_full_run_settles_without_actuator_stop_status(self, basic_cover):
        """0%/100% settle on a timer when the actuator's stop status is late or never relayed (#466)."""
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 50
        basic_cover._start_position = 50
        slept: list[float] = []

        async def fake_sleep(duration):
            slept.append(duration)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 100})
            assert basic_cover.is_opening is True
            await basic_cover._stop_task

        # Only the direction frame: no stop is written, the motor ends on its limit switch.
        assert [str(c.args[0]) for c in basic_cover._gateway_handler.send.await_args_list] == ["*2*1*21##"]
        assert any(d > END_SETTLE_MARGIN for d in slept), slept
        assert basic_cover.is_opening is False
        assert basic_cover.is_closed is False
        assert basic_cover.current_cover_position == 100
        assert basic_cover._start_position == 100

        # Same towards 0%.
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 0})
            await basic_cover._stop_task
        assert basic_cover.is_closing is False
        assert basic_cover.is_closed is True
        assert basic_cover.current_cover_position == 0

        # The actuator's own stop status arriving first (or a newer command) wins.
        basic_cover._attr_current_cover_position = 50
        basic_cover._start_position = 50
        with patch("asyncio.sleep", side_effect=fake_sleep):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 100})
            task = basic_cover._stop_task
            basic_cover._run_generation += 1  # superseded
            await task
        assert basic_cover.is_opening is True  # not settled by the stale timer
        basic_cover._cancel_stop_task()

        # ... also when the stop status lands during the wait.
        async def sleep_and_bump(duration):
            basic_cover._run_generation += 1

        with patch("asyncio.sleep", side_effect=sleep_and_bump):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 100})
            await basic_cover._stop_task
        assert basic_cover.is_opening is True
        basic_cover._cancel_stop_task()

        # A direction frame that never reached the bus is logged, not raised from the task.
        with patch.object(
            basic_cover, "_await_motion_anchor", side_effect=HomeAssistantError("delivery failed")
        ):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 100})
            await basic_cover._stop_task

        # Cancelling the timer ends it quietly.
        await basic_cover.async_set_cover_position(**{ATTR_POSITION: 100})
        task = basic_cover._stop_task
        await asyncio.sleep(0)
        task.cancel()
        await task

    async def test_min_position_delta_per_cover(self, hass, mock_gateway, basic_cover, advanced_cover):
        """min_position_delta replaces the 2% floor of one advanced cover (discussion #770)."""
        assert advanced_cover.extra_state_attributes["min_position_delta"] == 2
        assert "min_position_delta" not in basic_cover.extra_state_attributes

        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            fine = MyHOMECover(
                hass=hass, name="Tall Shutter", entity_name="Tall Shutter", device_id="23#4#02",
                who="2", where="23", interface="02", advanced=True, manufacturer="BTicino",
                model="F401", gateway=mock_gateway, min_position_delta=1,
            )
        fine.hass = hass
        fine.entity_id = "cover.tall_shutter"
        fine.async_schedule_update_ha_state = MagicMock()
        assert fine.extra_state_attributes["min_position_delta"] == 1

        # At 0% (closed) a 1% change goes out as asked, not widened to 2%.
        fine.handle_event(OWNEvent.parse("*#2*23#4#02*10*10*0*001*15##"))
        mock_gateway.send.reset_mock()
        await fine.async_set_cover_position(**{ATTR_POSITION: 1})
        mock_gateway.send.assert_awaited_once()
        assert str(mock_gateway.send.call_args[0][0]) == "*#2*23#4#02*#11#001*1##"

        # F454 + F401 sequence (#770): start report keeps initial level (0%) while opening,
        # stop report settles at the reached 1% level.
        fine.handle_event(OWNEvent.parse("*#2*23#4#02*10*11*0*001*15##"))
        fine.handle_event(OWNEvent.parse("*2*1*23#4#02##"))
        assert fine.is_opening is True
        assert fine.current_cover_position == 0
        fine.handle_event(OWNEvent.parse("*#2*23#4#02*10*10*1*001*15##"))
        fine.handle_event(OWNEvent.parse("*2*0*23#4#02##"))
        assert fine.is_opening is False
        assert fine.current_cover_position == 1

        # The default cover still widens the same request.
        advanced_cover.handle_event(OWNEvent.parse("*#2*22#4#02*10*10*0*001*0##"))
        advanced_cover._gateway_handler.send.reset_mock()
        await advanced_cover.async_set_cover_position(**{ATTR_POSITION: 1})
        assert str(advanced_cover._gateway_handler.send.call_args[0][0]) == "*#2*22#4#02*#11#001*2##"

    def test_resolve_strategy_min_position_delta(self):
        """The factory hands min_position_delta to the native strategy on both routes."""
        from custom_components.myhome.cover_strategies import (
            NativePositionStrategy,
            resolve_cover_strategy,
        )

        for kwargs in ({"advanced": True}, {"strategy_name": "native"}):
            strategy = resolve_cover_strategy(min_position_delta=1, **kwargs)
            assert isinstance(strategy, NativePositionStrategy)
            assert strategy.min_position_delta == 1
            assert strategy.quantize_target(0, 1) == 1
        assert resolve_cover_strategy(advanced=True).min_position_delta == 2

    def test_quantize_position_delta(self):
        """Verify quantize_position_delta logic and edge cases."""
        from custom_components.myhome.cover_motion import (
            MIN_POSITION_DELTA,
            quantize_position_delta,
        )

        assert MIN_POSITION_DELTA == 2

        # None current position returns clamped target
        assert quantize_position_delta(None, 50) == 50
        assert quantize_position_delta(None, -10) == 0
        assert quantize_position_delta(None, 120) == 100

        # Exact match returns current position
        assert quantize_position_delta(50, 50) == 50

        # Sub-deadband deltas expand to min_delta
        assert quantize_position_delta(50, 51, min_delta=2) == 52
        assert quantize_position_delta(50, 49, min_delta=2) == 48

        # Delta already >= min_delta remains unquantized
        assert quantize_position_delta(50, 55, min_delta=2) == 55
        assert quantize_position_delta(50, 45, min_delta=2) == 45

        # Boundary clamping
        assert quantize_position_delta(99, 100, min_delta=2) == 100
        assert quantize_position_delta(1, 0, min_delta=2) == 0
        assert quantize_position_delta(2, 1, min_delta=2) == 0
        assert quantize_position_delta(50, 150, min_delta=2) == 100
        assert quantize_position_delta(50, -20, min_delta=2) == 0

    @pytest.mark.asyncio
    async def test_cover_edge_cases_generation_and_translation(self, basic_cover):
        """Test edge cases: is_translation, superseded generation in _on_written and _auto_stop."""
        # 1. A WHAT=1000 translation frame is ignored by handle_event.
        msg = OWNEvent.parse("*2*1000#12#10#001#1*21##")
        basic_cover.handle_event(msg)
        assert basic_cover.is_opening is False
        assert basic_cover.is_closing is False

        # 2. A write that lands after a newer command leaves the echo window and clock alone.
        basic_cover._echo_until = None
        fut = asyncio.get_running_loop().create_future()
        basic_cover._track_write(fut)
        basic_cover._run_generation += 1
        fut.set_result(100.0)
        await asyncio.sleep(0)
        assert basic_cover._echo_until is None
        assert basic_cover._move_start_time is None

        def sent_frames() -> list[str]:
            return [str(c.args[0]) for c in basic_cover._gateway_handler.send.await_args_list]

        # 3. _auto_stop superseded while waiting for the motor start: no stop frame.
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 50

        async def fake_await_and_bump(written):
            basic_cover._run_generation += 1
            return 100.0

        with patch.object(basic_cover, "_await_motion_anchor", side_effect=fake_await_and_bump):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 60})
            assert basic_cover._stop_task is not None
            await basic_cover._stop_task
        assert sent_frames() == ["*2*1*21##"]
        assert basic_cover._attr_current_cover_position == 50

        # 4. _auto_stop superseded during the run: no stop frame either.
        basic_cover._gateway_handler.send.reset_mock()
        basic_cover._attr_current_cover_position = 50

        async def fake_sleep_and_bump(duration):
            basic_cover._run_generation += 1

        with patch("asyncio.sleep", side_effect=fake_sleep_and_bump):
            await basic_cover.async_set_cover_position(**{ATTR_POSITION: 60})
            assert basic_cover._stop_task is not None
            await basic_cover._stop_task
        assert sent_frames() == ["*2*1*21##"]
        assert basic_cover._attr_current_cover_position == 50

    def test_compute_run_duration(self):
        """Verify compute_run_duration enforces the motor start-up floor."""
        from custom_components.myhome.cover_motion import (
            MIN_MOTOR_PULSE,
            compute_run_duration,
        )

        # Zero diff or invalid travel time returns 0.0
        assert compute_run_duration(0, 25.0) == 0.0
        assert compute_run_duration(5, 0.0) == 0.0
        assert compute_run_duration(5, -10.0) == 0.0

        # 1% on a 10 s, 20.4 s (the #466 F454 motor) or 15 s cover is shorter than the floor
        assert compute_run_duration(1, 10.0) == MIN_MOTOR_PULSE
        assert compute_run_duration(1, 20.4) == MIN_MOTOR_PULSE
        assert compute_run_duration(-1, 15.0) == MIN_MOTOR_PULSE

        # 2% on that 20.4 s motor (0.408 s) moved it and is not stretched
        assert compute_run_duration(2, 20.4) == pytest.approx(0.408)

        # 10% step on 20s cover: nominal is 2.0s -> returns nominal 2.0s
        assert compute_run_duration(10, 20.0) == 2.0
        assert compute_run_duration(-10, 20.0) == 2.0

        # Custom min_pulse parameter
        assert compute_run_duration(1, 20.0, min_pulse=0.5) == 0.5

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

        # A short 0.2s close from tilt 50 at position 0 turns slats by 10% (50 -> 40) without snapping
        tilt_cover._attr_current_cover_position = 0
        tilt_cover._attr_current_cover_tilt_position = 50
        tilt_cover._run_started_at = time.monotonic() - 0.2
        tilt_cover._attr_is_closing = True
        tilt_cover._attr_is_opening = False
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 40

        # A stop with nothing running (the relay of a slat pulse's stop) keeps
        # the slats of a lowered blind where they are
        tilt_cover._attr_current_cover_tilt_position = 50
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 50

        # A short 0.2s open from tilt 50 at position 100 turns slats by 10% (50 -> 60) without snapping
        tilt_cover._attr_current_cover_position = 100
        tilt_cover._attr_current_cover_tilt_position = 50
        tilt_cover._run_started_at = time.monotonic() - 0.2
        tilt_cover._attr_is_closing = False
        tilt_cover._attr_is_opening = True
        tilt_cover.handle_event(stop_msg)
        assert tilt_cover.current_cover_tilt_position == 60

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
        def _make(advanced=False, slat_tilt=True, **kwargs):
            with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
                cover = MyHOMECover(
                    hass=hass, name="Blind", entity_name="Blind", device_id="31", who="2", where="31",
                    interface=None, advanced=advanced, slat_tilt=slat_tilt, manufacturer="BTicino",
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
        await cover.async_set_cover_tilt_position(tilt_position=62)  # 240 ms (< default 0.4 s min_motor_pulse)
        mock_gateway.send.assert_not_called()
        assert cover.current_cover_tilt_position == 50
        await cover.async_set_cover_tilt_position(tilt_position=72)  # 440 ms (>= 0.4 s)
        assert mock_gateway.send.call_count == 1
        cover._cancel_stop_task()

        # Custom min_motor_pulse on the cover is honoured
        mock_gateway.send.reset_mock()
        cover._abort_tilt()
        cover._attr_current_cover_tilt_position = 50
        cover._min_motor_pulse = 0.2
        await cover.async_set_cover_tilt_position(tilt_position=56)  # 120 ms (< 0.2 s)
        mock_gateway.send.assert_not_called()
        await cover.async_set_cover_tilt_position(tilt_position=62)  # 240 ms (>= 0.2 s)
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
        cover._min_motor_pulse = 0.2
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
        await cover.async_set_cover_tilt_position(tilt_position=20)  # 0.4 s pulse, stop due ~0.95 s after write

        def supersede():
            cover._run_generation += 1

        asyncio.get_running_loop().call_later(0.1, supersede)
        await cover._stop_task
        assert self._sent_stops(mock_gateway) == []

    # ── Queued stops, retargets and overshoot ────────────────────────────

    @staticmethod
    def _directions(mock_gateway):
        return [str(c.args[0]) for c in mock_gateway.send.call_args_list if str(c.args[0]) != "*2*0*31##"]

    @pytest.mark.asyncio
    async def test_next_pulse_starts_from_the_angle_at_the_queued_stops_write(self, cover, mock_gateway):
        """The FIFO queue holds the stop: the next pulse waits for it and starts from the real angle."""
        self._write_each_send(mock_gateway, delays={"*2*0*31##": 1.0})
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=25)  # 0.5 s, but the stop leaves ~1 s in
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
            assert cover._pulse_stop_written is not None
            await cover.async_set_cover_tilt_position(tilt_position=0)
        assert cover._pulse_stop_written is None
        assert cover._tilt_direction == "close"
        assert 45 <= cover._tilt_initial_position <= 60  # the stop's real write, not the 25 % it was due at
        cover._cancel_stop_task()

    @pytest.mark.asyncio
    async def test_newer_tilt_target_replaces_one_still_waiting(self, cover, mock_gateway):
        self._write_each_send(mock_gateway, delays={"*2*0*31##": 0.3})
        cover._attr_current_cover_tilt_position = 50
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=75)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
            await asyncio.gather(
                cover.async_set_cover_tilt_position(tilt_position=0),
                cover.async_set_cover_tilt_position(tilt_position=100),
            )
        assert self._directions(mock_gateway) == ["*2*1*31##", "*2*1*31##"]  # 0 % was never sent
        assert cover._tilt_target_position == 100
        cover._cancel_stop_task()

    @pytest.mark.asyncio
    async def test_same_direction_retarget_moves_the_stop_and_sends_nothing(self, cover, mock_gateway):
        self._write_each_send(mock_gateway)
        cover._attr_current_cover_tilt_position = 0
        cover._slat_time = 2.0
        gate = asyncio.Event()
        real_sleep = asyncio.sleep

        async def fake_sleep(_delay):
            await gate.wait()

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await real_sleep(0)
            await cover.async_set_cover_tilt_position(tilt_position=80)  # dragged further open
            assert mock_gateway.send.call_count == 1
            assert cover._tilt_target_position == 80
            assert cover._tilt_duration == pytest.approx(1.6)
            gate.set()
            await cover._stop_task
        assert len(self._sent_stops(mock_gateway)) == 1
        assert cover.current_cover_tilt_position == 80

    @pytest.mark.parametrize(
        ("initial", "direction", "position", "overrun", "expected_position", "expected_tilt"),
        [
            (0, "open", 0, 2.3, 10, 100),     # 2.3 s past the slats' 2 s: 10 % of a 23 s (25 - 2) curtain travel up
            (100, "close", 50, 4.6, 30, 0),   # 4.6 s past: 20 % down
            (0, "open", 0, -0.5, 0, 75),      # stopped before the slats were turned: no curtain travel
        ],
    )
    def test_motor_time_past_a_full_rotation_moves_the_curtain(
        self, cover, initial, direction, position, overrun, expected_position, expected_tilt,
    ):
        start = time.monotonic()
        cover._slat_time = 2.0
        cover._attr_current_cover_position = cover._start_position = position
        cover._is_tilting = True
        cover._tilt_direction = direction
        cover._tilt_initial_position = initial
        cover._tilt_target_position = 100 if direction == "open" else 0
        cover._tilt_start_time = start
        cover._apply_tilt_stop(start + 2.0 + overrun)
        assert cover.current_cover_position == expected_position
        assert cover.current_cover_tilt_position == expected_tilt

    def test_advanced_blind_reports_its_own_level_after_an_overrun(self, make_cover):
        cover = make_cover(advanced=True)
        start = time.monotonic()
        cover._attr_current_cover_position = 0
        cover._is_tilting = True
        cover._tilt_direction = "open"
        cover._tilt_initial_position, cover._tilt_target_position = 0, 100
        cover._tilt_start_time = start
        cover._apply_tilt_stop(start + 5.0)
        assert cover.current_cover_position == 0
        assert cover.current_cover_tilt_position == 100

    @pytest.mark.asyncio
    async def test_tilt_request_cancelled_while_waiting_for_the_queued_stop(self, cover, mock_gateway):
        self._write_each_send(mock_gateway, delays={"*2*0*31##": 0.3})
        cover._attr_current_cover_tilt_position = 0
        real_sleep = asyncio.sleep
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
            request = asyncio.ensure_future(cover.async_set_cover_tilt_position(tilt_position=0))
            await real_sleep(0.05)  # the request now waits for the queued stop
            assert cover._pulse_stop_written is not None
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)
        assert request.cancelled()
        assert self._directions(mock_gateway) == ["*2*1*31##"]
        await asyncio.sleep(0.35)  # the queued stop still settles the pulse
        assert cover._is_tilting is False

    @pytest.mark.asyncio
    async def test_waiting_for_the_end_of_a_pulse(self, cover, mock_gateway):
        self._write_each_send(mock_gateway, delays={"*2*0*31##": 0.2})
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._async_wait_pulse_end()
        assert cover._is_tilting is False
        assert self._sent_stops(mock_gateway)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outcome", ["failed", "cancelled"])
    async def test_next_pulse_proceeds_when_the_queued_stop_never_leaves(self, cover, mock_gateway, outcome):
        loop = asyncio.get_running_loop()
        stop_fut = loop.create_future()

        async def send(cmd):
            if str(cmd) == "*2*0*31##":
                return stop_fut
            fut = loop.create_future()
            fut.set_result(time.monotonic())
            return fut

        mock_gateway.send.side_effect = send
        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_tilt_position(tilt_position=50)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
            if outcome == "failed":
                loop.call_soon(stop_fut.set_exception, OSError("connection lost"))
            else:
                loop.call_soon(stop_fut.cancel)
            await cover.async_set_cover_tilt_position(tilt_position=0)
        assert cover._pulse_stop_written is None
        assert self._directions(mock_gateway) == ["*2*1*31##", "*2*2*31##"]
        cover._cancel_stop_task()

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

    def test_keypad_run_turns_slats_before_moving_curtain(self, cover):
        """Review: a keypad run turns slats first at 100 % / slat_time before moving the curtain."""
        cover._slat_time = 2.0
        cover._travel_time_down = cover._travel_time_up = 25.0

        # 1. A 0.5 s close from position 50, tilt 50 turns slats 50 -> 25 (same as a pulse) and leaves curtain at 50
        t0 = 1000.0
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50
        with patch("time.monotonic", return_value=t0):
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
        with patch("time.monotonic", return_value=t0 + 0.5):
            assert cover.current_cover_position == 50
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 50
        assert cover.current_cover_tilt_position == 25

        # 2. A 1.5 s close from position 50, tilt 50 spends 1.0 s closing slats (50 -> 0) and 0.5 s moving curtain (50 -> 48)
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50
        with patch("time.monotonic", return_value=t0):
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
        with patch("time.monotonic", return_value=t0 + 1.0):
            assert cover.current_cover_position == 50  # slats just reached 0
        with patch("time.monotonic", return_value=t0 + 1.5):
            assert cover.current_cover_position == 48
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 48
        assert cover.current_cover_tilt_position == 0

        # 3. Direct reversal (close -> open -> close) settles both curtain and slats at the reversal point
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50
        with patch("time.monotonic", return_value=t0):
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
        with patch("time.monotonic", return_value=t0 + 0.5):
            cover.handle_event(OWNMessage.parse("*2*1*31##"))  # reverse to open at tilt 25, position 50
        assert cover._start_position == 50
        assert cover.current_cover_tilt_position == 25
        with patch("time.monotonic", return_value=t0 + 1.5):
            cover.handle_event(OWNMessage.parse("*2*2*31##"))  # 1.0 s open turned slats 25 -> 75, curtain still 50
        assert cover._start_position == 50
        assert cover.current_cover_tilt_position == 75

    @pytest.mark.asyncio
    async def test_set_cover_position_includes_slat_lead_time(self, cover, mock_gateway):
        """Review: async_set_cover_position adds slat lead time to run_duration and settles slats."""
        self._write_each_send(mock_gateway)
        cover._slat_time = 2.0
        cover._travel_time_down = cover._travel_time_up = 25.0
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await cover.async_set_cover_position(position=40)  # 1.0 s slat lead + 2.3 s curtain (10% of 23 s) = 3.3 s
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
            await cover._stop_task
        assert sleeps[0] == pytest.approx(3.3, abs=0.1)
        assert cover.current_cover_position == 40
        assert cover.current_cover_tilt_position == 0

    @pytest.mark.asyncio
    async def test_calibrate_preserves_stored_slat_time(self, cover, hass):
        """Review: async_calibrate must not drop a previously stored slat_time."""
        entry = self._wire_options(cover, hass, {"cover_travel_times": {"31": {"slat_time": 3.5}}})
        with (
            patch.object(cover, "_calibration_run", AsyncMock(side_effect=[20.0, 21.0, 22.0])),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            res = await cover.async_calibrate()
        assert res["down"] == 21.0
        assert res["up"] == 22.0
        assert res["slat_time"] == 3.5
        assert entry.options["cover_travel_times"]["31"]["slat_time"] == 3.5

    @pytest.mark.asyncio
    async def test_slat_time_rejected_without_slat_tilt(self, make_cover):
        """Review: slat_time is rejected on a cover without slat_tilt enabled."""
        shutter = make_cover(slat_tilt=False)
        with pytest.raises(ServiceValidationError, match="does not have slat_tilt enabled") as err:
            await shutter.async_set_travel_time(slat_time=2.5)
        assert err.value.translation_key == "slat_tilt_not_enabled"

    # ── Review 5562111186 regressions ────────────────────────────────────

    def test_keypad_full_measured_close_reaches_zero(self, cover):
        """Review #1: a keypad close lasting the measured 25 s from 100 % / tilt 100 settles at 0 %, not 8 %."""
        t0 = 1000.0
        cover._slat_time = 2.0
        cover._travel_time_down = cover._travel_time_up = 25.0
        cover._attr_current_cover_position = cover._start_position = 100
        cover._attr_current_cover_tilt_position = 100

        with patch("time.monotonic", return_value=t0):
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
        with patch("time.monotonic", return_value=t0 + 25.0):
            assert cover.current_cover_position == 0
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 0
        assert cover.current_cover_tilt_position == 0

        # Fallback when travel_time <= slat_time uses full travel_time without dividing by <= 0
        cover._travel_time_down = 2.0
        assert cover._curtain_travel_for(False) == 2.0

    @pytest.mark.asyncio
    async def test_run_to_end_settles_tilt_at_limit(self, cover, mock_gateway, hass):
        """Review #2: set_position(0) from tilt 100 settles at 0 % with tilt 0 (and 100 % with tilt 100)."""
        from custom_components.myhome.cover_scope import CoverScope, MyHOMEScopeCover

        self._write_each_send(mock_gateway)
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 100

        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_position(position=0)
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
            await cover._stop_task
        assert cover.current_cover_position == 0
        assert cover.current_cover_tilt_position == 0

        cover._attr_current_cover_tilt_position = 0
        with patch("asyncio.sleep", new=AsyncMock()):
            await cover.async_set_cover_position(position=100)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
            await cover._stop_task
        assert cover.current_cover_position == 100
        assert cover.current_cover_tilt_position == 100

        # A memberless scope cover with slat_tilt also settles tilt at the end of a timed run
        with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
            scope_cover = MyHOMEScopeCover(
                hass=hass,
                name="Group Blind",
                entity_name="Group Blind",
                device_id="#3",
                who="2",
                where="#3",
                interface=None,
                advanced=False,
                slat_tilt=True,
                manufacturer="BTicino",
                model="Venetian Blind",
                gateway=mock_gateway,
                scope=CoverScope.of("#3"),
            )
        scope_cover.entity_id = "cover.group_blind"
        scope_cover.hass = hass
        scope_cover.async_write_ha_state = MagicMock()
        scope_cover._attr_current_cover_position = scope_cover._start_position = 50
        scope_cover._attr_current_cover_tilt_position = 100
        scope_cover._attr_is_closing = True
        scope_cover._run_timeout_direction = "close"
        scope_cover._end_run(None)
        assert scope_cover.current_cover_position == 0
        assert scope_cover.current_cover_tilt_position == 0

    @pytest.mark.asyncio
    async def test_stop_status_reanchor_subtracts_slat_lead(self, cover, mock_gateway):
        """Review #3: the #707 stop-status re-anchor subtracts the slat lead and divides by curtain travel."""
        t0 = 1000.0
        # Scenario A: 50 -> 45 move from tilt 50 with travel 10 s (curtain travel 8 s, lead 1 s, curtain 0.4 s)
        cover._slat_time = 2.0
        cover._travel_time_down = cover._travel_time_up = 10.0
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50

        loop = asyncio.get_running_loop()
        write_times = iter([t0, t0 + 1.4])

        async def send_a(_cmd):
            fut = loop.create_future()
            fut.set_result(next(write_times))
            return fut

        mock_gateway.send.side_effect = send_a
        with patch("asyncio.sleep", new=AsyncMock()):
            with patch("time.monotonic", return_value=t0):
                await cover.async_set_cover_position(position=45)
                cover.handle_event(OWNMessage.parse("*2*2*31##"))
            with patch("time.monotonic", return_value=t0 + 1.4):
                await cover._stop_task
        assert cover.current_cover_position == 45
        assert cover.current_cover_tilt_position == 0

        # Relayed stop status shortly after stop write keeps position at 45 (not 34)
        with patch("time.monotonic", return_value=t0 + 1.42):
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 45
        assert cover.current_cover_tilt_position == 0

        # Scenario B: 1.5 s HA close from position 50, tilt 50 on a 25 s blind shows 48 after stop write and 48 after relayed stop
        cover._travel_time_down = cover._travel_time_up = 25.0
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50
        write_times_b = iter([t0, t0 + 1.5])

        async def send_b(_cmd):
            fut = loop.create_future()
            fut.set_result(next(write_times_b))
            return fut

        mock_gateway.send.side_effect = send_b
        with patch("time.monotonic", return_value=t0):
            await cover.async_close_cover()
            await asyncio.sleep(0)
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
        with patch("time.monotonic", return_value=t0 + 1.5):
            await cover.async_stop_cover()
            await asyncio.sleep(0)
        assert cover.current_cover_position == 48
        assert cover.current_cover_tilt_position == 0

        with patch("time.monotonic", return_value=t0 + 1.54):
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 48
        assert cover.current_cover_tilt_position == 0

        # Scenario C: HA open stopped during the slat phase (0.5 s from tilt 50) re-anchors tilt without moving curtain
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50
        write_times_c = iter([t0, t0 + 0.5])

        async def send_c(_cmd):
            fut = loop.create_future()
            fut.set_result(next(write_times_c))
            return fut

        mock_gateway.send.side_effect = send_c
        with patch("time.monotonic", return_value=t0):
            await cover.async_open_cover()
            await asyncio.sleep(0)
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
        with patch("time.monotonic", return_value=t0 + 0.5):
            await cover.async_stop_cover()
            await asyncio.sleep(0)
        assert cover.current_cover_position == 50
        assert cover.current_cover_tilt_position == 75

        with patch("time.monotonic", return_value=t0 + 0.54):
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 50
        assert cover.current_cover_tilt_position == 77

    @pytest.mark.asyncio
    async def test_mid_run_reversal_and_retarget_settle_slat_lead_and_auto_stop_duration(
        self, cover, mock_gateway
    ):
        """Mid-run HA retarget/reversal settles in-progress slat rotation, and _freeze_position clears _auto_stop_duration."""
        t0 = 1000.0
        loop = asyncio.get_running_loop()
        cover._slat_time = 2.0
        cover._travel_time_down = cover._travel_time_up = 25.0
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 50

        # 1. Start closing at t0; after 1.0 s (slats now at 0 %, curtain still at 50 %),
        # retarget in the same direction to 40 %. Slat lead must be 0.0 s (not 1.0 s again).
        write_times = iter([t0, t0 + 1.0, t0 + 3.3])

        async def send_seq(_cmd):
            fut = loop.create_future()
            fut.set_result(next(write_times))
            return fut

        mock_gateway.send.side_effect = send_seq
        with patch("time.monotonic", return_value=t0):
            await cover.async_close_cover()
            await asyncio.sleep(0)
            cover.handle_event(OWNMessage.parse("*2*2*31##"))

        now_box = [t0 + 1.0]
        sleep_calls: list[float] = []

        async def record_sleep(delay: float):
            sleep_calls.append(delay)
            now_box[0] += delay

        with (
            patch("asyncio.sleep", side_effect=record_sleep),
            patch("time.monotonic", side_effect=lambda: now_box[0]),
        ):
            await cover.async_set_cover_position(position=40)
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
            assert cover.current_cover_tilt_position == 0
            await cover._stop_task
        assert sleep_calls == [pytest.approx(2.3)]
        assert cover.current_cover_position == 40
        assert cover._auto_stop_duration is None

        # 2. Without a stop echo for step 1, an external keypad close (*2*2*31##) followed
        # 1.0 s later by a keypad open reversal (*2*1*31##) and an HA stop must not inherit
        # step 1's _auto_stop_duration, and must re-anchor lead from the reversed tilt.
        cover._attr_current_cover_position = cover._start_position = 50
        cover._attr_current_cover_tilt_position = 100
        with patch("time.monotonic", return_value=t0 + 10.0):
            cover.handle_event(OWNMessage.parse("*2*2*31##"))
        # 1.0 s into closing from tilt 100 -> slats are at 50 %, curtain at 50 %; reverse to open
        with patch("time.monotonic", return_value=t0 + 11.0):
            cover.handle_event(OWNMessage.parse("*2*1*31##"))
        assert cover.current_cover_tilt_position == 50
        assert cover._run_start_tilt == 50
        assert cover._run_lead == pytest.approx(1.0)

        write_times_stop = iter([t0 + 13.3])

        async def send_stop(_cmd):
            fut = loop.create_future()
            fut.set_result(next(write_times_stop))
            return fut

        mock_gateway.send.side_effect = send_stop
        with patch("time.monotonic", return_value=t0 + 13.3):
            await cover.async_stop_cover()
            await asyncio.sleep(0)
        assert cover.current_cover_position == 56
        assert cover.current_cover_tilt_position == 100

        with patch("time.monotonic", return_value=t0 + 13.34):
            cover.handle_event(OWNMessage.parse("*2*0*31##"))
        assert cover.current_cover_position == 56
        assert cover.current_cover_tilt_position == 100



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
async def test_timed_cover_auto_stop_reanchors_on_stop_status(hass, mock_gateway):
    """Test that a timed cover's auto-stop re-anchors final position and last_run on stop status echo (#705)."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover Reanchor",
            entity_name="Timed Cover Reanchor",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=20,
        )
        cover.entity_id = "cover.timed_reanchor"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 50
    cover._start_position = 50
    cover._travel_time_up = 20.0
    cover._travel_time_down = 20.0

    current_time = 100.0

    def mock_monotonic():
        return current_time

    # Run from 50 to 80 (delta = 30%, nominal duration = 6.0s on 20s travel)
    with patch("time.monotonic", side_effect=mock_monotonic):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await cover.async_set_cover_position(**{ATTR_POSITION: 80})
            assert cover._stop_task is not None
            # Simulate direction echo arriving at t=100.0
            cover.handle_event(OWNEvent.parse("*2*1*21##"))
            # Advance time to auto-stop trigger
            current_time = 106.0
            await cover._stop_task

        # Stop command written at 106.0: provisional freeze is 80%
        assert cover.is_opening is False
        assert cover.current_cover_position == 80

        # Now simulate gateway relaying the stop status at 106.5s (0.5s bus transit & relay release delay)
        current_time = 106.5
        cover.handle_event(OWNEvent.parse("*2*0*21##"))

        # Actual run: 106.5 - 100.0 = 6.5s. Delta: (6.5 / 20.0) * 100 = 32.5% -> 82% (round-to-even)
        assert cover.current_cover_position == 82
        assert cover.extra_state_attributes["last_run_seconds"] == 6.5
        assert cover.extra_state_attributes["last_run_direction"] == "open"
        assert cover._stopped_event.is_set()


@pytest.mark.asyncio
async def test_timed_cover_manual_stop_reanchors_on_stop_status(hass, mock_gateway):
    """Test that a manually stopped timed cover re-anchors on stop status echo (#705)."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover Manual Reanchor",
            entity_name="Timed Cover Manual Reanchor",
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
        cover.entity_id = "cover.timed_manual_reanchor"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 20
    cover._start_position = 20
    cover._travel_time_up = 25.0
    cover._travel_time_down = 25.0

    current_time = 100.0

    def mock_monotonic():
        return current_time

    with patch("time.monotonic", side_effect=mock_monotonic):
        await cover.async_open_cover()
        # Direction echo arrives at 100.0
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        assert cover.is_opening is True

        # Motor runs for 5.0 seconds before user clicks Stop
        current_time = 105.0
        await cover.async_stop_cover()

        # Provisional freeze at stop write (5.0s run -> 40%)
        assert cover.is_opening is False
        assert cover.current_cover_position == 40
        assert cover.extra_state_attributes["last_run_seconds"] == 5.0

        # Gateway relays stop status 0.6s later (105.6)
        current_time = 105.6
        cover.handle_event(OWNEvent.parse("*2*0*21##"))

        # Re-anchored: 105.6 - 100.0 = 5.6s. Delta: (5.6 / 25.0) * 100 = 22.4% -> 20 + 22.4 = 42.4% -> 42%
        assert cover.current_cover_position == 42
        assert cover.extra_state_attributes["last_run_seconds"] == 5.6
        assert cover.extra_state_attributes["last_run_direction"] == "open"


@pytest.mark.asyncio
async def test_timed_cover_preserves_write_freeze_when_no_stop_status(hass, mock_gateway):
    """Test that gateways that never relay stop status keep the write-time freeze (#705)."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover No Echo",
            entity_name="Timed Cover No Echo",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=20,
        )
        cover.entity_id = "cover.timed_no_echo"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 50
    cover._start_position = 50
    cover._travel_time_up = 20.0
    cover._travel_time_down = 20.0

    current_time = 100.0

    def mock_monotonic():
        return current_time

    with patch("time.monotonic", side_effect=mock_monotonic):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await cover.async_set_cover_position(**{ATTR_POSITION: 80})
            cover.handle_event(OWNEvent.parse("*2*1*21##"))
            current_time = 106.0
            await cover._stop_task

        # On gateways that never relay stop status, the provisional freeze at write is kept
        assert cover.current_cover_position == 80
        assert cover.is_opening is False

        # Advance past echo window (ECHO_WINDOW = 1.5s)
        current_time = 110.0
        assert not cover._in_echo_window(current_time)

        # Position remains preserved
        assert cover.current_cover_position == 80
        assert cover.is_opening is False


@pytest.mark.asyncio
async def test_timed_cover_general_stop_not_consumed_as_echo(hass, mock_gateway):
    """Test that general stop frame (*2*0*0##) is not treated as point-to-point stop echo (#705)."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover General Stop",
            entity_name="Timed Cover General Stop",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=20,
        )
        cover.entity_id = "cover.timed_gen_stop"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 50
    cover._start_position = 50
    cover._run_start_position = 50
    cover._run_started_at = 100.0
    cover._attr_is_opening = True
    cover._begin_command("stop")
    cover._pending_stop_context = {
        "run_start_position": 50,
        "run_started_at": 100.0,
        "is_opening": True,
        "is_closing": False,
        "auto_stop_duration": None,
    }

    # General stop frame arrives
    gen_stop = OWNEvent.parse("*2*0*0##")
    # _handle_echo must return False for general stop
    assert cover._handle_echo(gen_stop, 101.0) is False


@pytest.mark.asyncio
async def test_timed_cover_stop_reanchor_clamps_at_limits(hass, mock_gateway):
    """Test that stop status re-anchoring clamps at 0% and 100% (#705)."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover Limits",
            entity_name="Timed Cover Limits",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=20,
        )
        cover.entity_id = "cover.timed_limits"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 5
    cover._start_position = 5
    cover._travel_time_down = 20.0

    current_time = 100.0

    def mock_monotonic():
        return current_time

    with patch("time.monotonic", side_effect=mock_monotonic):
        await cover.async_close_cover()
        cover.handle_event(OWNEvent.parse("*2*2*21##"))
        assert cover.is_closing is True

        # Stop after 2 seconds (nominally 5% - (2/20)*100 = -5%)
        current_time = 102.0
        await cover.async_stop_cover()

        # Stop status arrives at 102.5s
        current_time = 102.5
        cover.handle_event(OWNEvent.parse("*2*0*21##"))

        assert cover.current_cover_position == 0
        assert cover.is_closed is True
        assert cover.extra_state_attributes["last_run_seconds"] == 2.5
        assert cover.extra_state_attributes["last_run_direction"] == "close"

        # Now test clamping at 100% when opening
        cover._attr_current_cover_position = 95
        cover._start_position = 95
        cover._travel_time_up = 20.0
        current_time = 200.0

        await cover.async_open_cover()
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        assert cover.is_opening is True

        # Stop after 2 seconds (nominally 95% + (2/20)*100 = 105%)
        current_time = 202.0
        await cover.async_stop_cover()

        # Stop status arrives at 202.5s
        current_time = 202.5
        cover.handle_event(OWNEvent.parse("*2*0*21##"))

        assert cover.current_cover_position == 100
        assert cover.is_closed is False
        assert cover.extra_state_attributes["last_run_seconds"] == 2.5
        assert cover.extra_state_attributes["last_run_direction"] == "open"


@pytest.mark.asyncio
async def test_timed_cover_stop_reanchor_with_firmware_oracle_translation_frames(
    hass, mock_gateway
):
    """Verify stop re-anchoring against immediate *2*1000#0*31## oracle translations (#705)."""
    import json
    from pathlib import Path

    oracle_path = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
    verdicts = json.loads(oracle_path.read_text(encoding="utf-8"))["verdicts"]

    # On F454, F459, F460, F461, MH202, MyHomeServer1, sending *2*0*31## immediately
    # emits *2*1000#0*31## (is_translation=True) before the physical actuator stops.
    stop_by_prod = {e["product"]: e for e in verdicts["*2*0*31##"]}
    for prod in ("F454", "F459", "F460", "F461", "MH202", "MyHomeServer1"):
        assert stop_by_prod[prod]["verdict"] == "out"
        assert stop_by_prod[prod]["emitted_own"] == ["*2*1000#0*31##"]

    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover Oracle Stop",
            entity_name="Timed Cover Oracle Stop",
            device_id="31",
            who="2",
            where="31",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=20,
        )
        cover.entity_id = "cover.timed_oracle_stop"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 50
    cover._start_position = 50
    cover._travel_time_up = 20.0

    current_time = 100.0

    def mock_monotonic():
        return current_time

    with patch("time.monotonic", side_effect=mock_monotonic):
        await cover.async_open_cover()
        # Replay F454 emitted_own on *2*1*31## (*2*1000#0*31##, *2*1000#1*31##)
        f454_up = next(e for e in verdicts["*2*1*31##"] if e["product"] == "F454")
        for raw_tr in f454_up["emitted_own"]:
            cover.handle_event(OWNEvent.parse(raw_tr))
        # Physical actuator direction echo starts the run at t=100.0
        cover.handle_event(OWNEvent.parse("*2*1*31##"))
        assert cover.is_opening is True

        # User presses STOP at t=104.0s (provisional position: 70%)
        current_time = 104.0
        await cover.async_stop_cover()
        assert cover.current_cover_position == 70
        assert cover._pending_stop_context is not None

        # Gateway immediately emits *2*1000#0*31## translation at t=104.02s:
        # must NOT consume _pending_stop_context!
        current_time = 104.02
        for raw_tr in stop_by_prod["F454"]["emitted_own"]:
            ev = OWNEvent.parse(raw_tr)
            assert ev.is_translation is True
            cover.handle_event(ev)
        assert cover._pending_stop_context is not None
        assert cover.current_cover_position == 70

        # Physical actuator stops and broadcasts *2*0*31## at t=105.0s (5.0s total run -> 75%)
        current_time = 105.0
        cover.handle_event(OWNEvent.parse("*2*0*31##"))
        assert cover._pending_stop_context is None
        assert cover.current_cover_position == 75
        assert cover.extra_state_attributes["last_run_seconds"] == 5.0


@pytest.mark.asyncio
async def test_timed_cover_stop_reanchoring_measured_hardware_case(hass, mock_gateway):
    """Verify stop status re-anchoring on Interstellar0verdrive's 15.2 s cases (#705 / #707).

    Case A (delayed stop status at t=1.41 s):
      - Travel time: 15.2 s, starting position 50%
      - t=0.00 s: UP command sent (*2*1*21##)
      - t=0.05 s: momentary stop echo from gateway arrives (*2*0*21##, must not cancel the run)
      - t=0.49 s: direction echo arrives (*2*1*21##, anchors motor start)
      - t=0.93 s: stop command written (*2*0*21##) -> provisional 0.44 s / 53%
      - t=1.41 s: stop status arrives (*2*0*21##) -> re-anchored to 0.92 s / 56%

    Case B (MyHomeServer1 measured 42 ms median stop status delay, #705 comment 6240346903):
      - Stop write at t=10.93 s after direction echo at t=10.49 s -> provisional 0.44 s / 53%
      - Stop status 42 ms later at t=10.972 s -> re-anchored to 0.48 s / 53%
    """
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=hass,
            name="Timed Cover Measured Trace",
            entity_name="Timed Cover Measured Trace",
            device_id="21",
            who="2",
            where="21",
            interface=None,
            advanced=False,
            manufacturer="BTicino",
            model="Shutter",
            gateway=mock_gateway,
            travel_time=15.2,
        )
        cover.entity_id = "cover.timed_measured_trace"
        cover.async_schedule_update_ha_state = MagicMock()

    cover._attr_current_cover_position = 50
    cover._start_position = 50
    cover._travel_time_up = 15.2
    cover._travel_time_down = 15.2

    current_time = 0.00

    def mock_monotonic():
        return current_time

    with patch("time.monotonic", side_effect=mock_monotonic):
        await cover.async_open_cover()
        assert cover.is_opening is True

        # t=0.05 s: momentary stop status echoed right after direction write
        current_time = 0.05
        cover.handle_event(OWNEvent.parse("*2*0*21##"))
        # Must be ignored: cover remains opening
        assert cover.is_opening is True

        # t=0.49 s: direction echo arrives, anchoring the run
        current_time = 0.49
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        assert cover.is_opening is True

        # t=0.93 s: stop command written
        current_time = 0.93
        await cover.async_stop_cover()
        assert cover.is_opening is False
        # Provisional freeze at stop write: 0.93 - 0.49 = 0.44 s -> 53%
        assert cover.current_cover_position == 53
        assert cover.extra_state_attributes["last_run_seconds"] == 0.44

        # t=1.41 s: stop status arrives
        current_time = 1.41
        cover.handle_event(OWNEvent.parse("*2*0*21##"))

        # Re-anchored to 1.41 - 0.49 = 0.92 s -> 50 + (0.92 / 15.2) * 100 = 56%
        assert cover.current_cover_position == 56
        assert cover.extra_state_attributes["last_run_seconds"] == 0.92
        assert cover.extra_state_attributes["last_run_direction"] == "open"
        assert cover._stopped_event.is_set()

        # Case B: MyHomeServer1 measured 42 ms median stop status delay (34-47 ms across 15 writes)
        cover._attr_current_cover_position = 50
        cover._start_position = 50
        current_time = 10.00
        await cover.async_open_cover()
        current_time = 10.49
        cover.handle_event(OWNEvent.parse("*2*1*21##"))
        current_time = 10.93
        await cover.async_stop_cover()
        assert cover.current_cover_position == 53
        assert cover.extra_state_attributes["last_run_seconds"] == 0.44

        current_time = 10.972  # +42 ms median stop status
        cover.handle_event(OWNEvent.parse("*2*0*21##"))
        assert cover.current_cover_position == 53
        assert cover.extra_state_attributes["last_run_seconds"] == 0.48


@pytest.mark.asyncio
async def test_cover_custom_min_motor_pulse(hass, mock_gateway):
    """Test timed cover behaves properly with custom min_motor_pulse configuration."""
    from tests.conftest import bind_entity

    cover_custom = MyHOMECover(
        hass=hass,
        name="Custom Floor Cover",
        entity_name=None,
        device_id="25",
        who="2",
        where="25",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=mock_gateway,
        travel_time=5,
        min_motor_pulse=0.6,
    )
    bind_entity(hass, cover_custom)
    assert cover_custom.extra_state_attributes["min_motor_pulse"] == 0.6

    # 1% on a 5.0s cover (nominal 0.05s) clamps to 0.6s (12% of travel -> ends at 62%)
    cover_custom._gateway_handler.send.reset_mock()
    cover_custom._attr_current_cover_position = 50
    cover_custom._travel_time_up = 5.0
    slept_durations = []

    async def fake_sleep(duration):
        slept_durations.append(duration)

    with patch("asyncio.sleep", side_effect=fake_sleep):
        await cover_custom.async_set_cover_position(**{ATTR_POSITION: 51})
        assert cover_custom._stop_task is not None
        await cover_custom._stop_task

    assert any(0.59 <= d <= 0.61 for d in slept_durations), f"Expected 0.6s clamp, got: {slept_durations}"
    assert cover_custom.current_cover_position == 62

    # Cover with min_motor_pulse = 0.0 (disabled floor)
    cover_zero = MyHOMECover(
        hass=hass,
        name="Zero Floor Cover",
        entity_name=None,
        device_id="26",
        who="2",
        where="26",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=mock_gateway,
        travel_time=5,
        min_motor_pulse=0.0,
    )
    bind_entity(hass, cover_zero)
    assert cover_zero.extra_state_attributes["min_motor_pulse"] == 0.0

    cover_zero._gateway_handler.send.reset_mock()
    cover_zero._attr_current_cover_position = 50
    cover_zero._travel_time_up = 5.0
    slept_zero = []

    async def fake_sleep_zero(duration):
        slept_zero.append(duration)

    with patch("asyncio.sleep", side_effect=fake_sleep_zero):
        await cover_zero.async_set_cover_position(**{ATTR_POSITION: 51})
        assert cover_zero._stop_task is not None
        await cover_zero._stop_task

    assert any(0.04 <= d <= 0.06 for d in slept_zero), f"Expected nominal 0.05s, got: {slept_zero}"
    assert cover_zero.current_cover_position == 51


def test_cover_validate_min_motor_pulse():
    """Verify voluptuous cover_schema validation for min_motor_pulse."""
    import voluptuous as vol

    from custom_components.myhome.validate import cover_schema

    # Valid values
    valid_cfg = {
        "kitchen": {
            "where": "21",
            "name": "Kitchen Shutter",
            "min_motor_pulse": 0.5,
        }
    }
    validated = cover_schema(valid_cfg)
    assert validated["2-21"]["min_motor_pulse"] == 0.5

    # Boundary 0.0 and 5.0
    assert cover_schema({"k": {"where": "21", "name": "K", "min_motor_pulse": 0.0}})["2-21"]["min_motor_pulse"] == 0.0
    assert cover_schema({"k": {"where": "21", "name": "K", "min_motor_pulse": 5.0}})["2-21"]["min_motor_pulse"] == 5.0

    # Negative value rejected
    with pytest.raises(vol.Invalid):
        cover_schema({"k": {"where": "21", "name": "K", "min_motor_pulse": -0.1}})

    # Exceeding maximum rejected
    with pytest.raises(vol.Invalid):
        cover_schema({"k": {"where": "21", "name": "K", "min_motor_pulse": 5.1}})


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
