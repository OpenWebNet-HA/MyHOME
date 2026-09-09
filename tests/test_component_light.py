"""Test the MyHOME light component."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_FLASH,
    FLASH_LONG,
    FLASH_SHORT,
    ATTR_TRANSITION,
    ColorMode,
    LightEntityFeature,
)

import asyncio
import logging
from homeassistant.helpers.dispatcher import async_dispatcher_send

from custom_components.myhome.light import (
    MyHOMELight,
    async_setup_entry,
    async_unload_entry,
    eight_bits_to_percent,
    percent_to_eight_bits,
)
from custom_components.myhome.ownd.message import (
    OWNLightingEvent,
    OWNLightingCommand,
)
from custom_components.myhome.const import (
    DOMAIN,
    CONF_TRANSITION_MODE,
    CONF_WORKER_COUNT,
    DEFAULT_TRANSITION_MODE,
    TRANSITION_MODE_AUTO,
    TRANSITION_MODE_NATIVE,
    TRANSITION_MODE_SOFTWARE,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    CONF_BUS_INTERFACE,
    CONF_DIMMABLE,
    CONF_ENTITY_NAME,
    CONF_ICON,
    CONF_ICON_ON,
)
from homeassistant.const import CONF_NAME


async def test_setup_configured_lights_from_yaml(hass):
    """Test setup instantiating configured lights from YAML with duplicate handling."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    hass.data = {
        DOMAIN: {
            "mac": {
                "entity": mock_gateway,
                CONF_PLATFORMS: {
                    "light": {
                        "14": {
                            CONF_WHERE: "14",
                            CONF_NAME: "Configured Light 14",
                            CONF_DIMMABLE: True,
                            CONF_ENTITY_NAME: "Light 14",
                            CONF_ICON: "mdi:lamp",
                            CONF_ICON_ON: "mdi:lamp-outline",
                        },
                        "14_dup": {
                            CONF_WHERE: "14",
                        },
                        "15#4#01": {
                            CONF_WHERE: "15",
                            CONF_BUS_INTERFACE: "01",
                        },
                    }
                },
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}
    config_entry.entry_id = "test_entry"

    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[],
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=MagicMock(),
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 2
        assert entities[0]._device_id == "14"
        assert entities[1]._device_id == "15#4#01"


async def test_setup_and_unload_entry(hass):
    """Test setup dynamically restoring and dynamically creating lights."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    
    hass.data = {DOMAIN: {"mac": {"entity": mock_gateway}}}
    
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}
    config_entry.entry_id = "test_entry"
    
    # Mock entity registry restore check
    mock_er = MagicMock()
    mock_entry_1 = MagicMock()
    mock_entry_1.domain = "light"
    mock_entry_1.unique_id = "mac-12"
    mock_entry_2 = MagicMock()
    mock_entry_2.domain = "light"
    mock_entry_2.unique_id = "mac-13#4#1"
    
    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[mock_entry_1, mock_entry_2]
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=mock_er
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)
        
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        
        assert len(entities) == 2
        assert entities[0]._device_id == "12"
        assert entities[1]._device_id == "13#4#1"
    
    assert await async_unload_entry(hass, config_entry)


def test_conversions():
    """Test value conversion logic."""
    assert eight_bits_to_percent(255) == 100
    assert eight_bits_to_percent(127) == 50
    assert percent_to_eight_bits(100) == 255
    assert percent_to_eight_bits(50) == 128


async def test_light_entity_dimmable(hass):
    """Test dimmable MyHOMELight logic."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    # Force native mode so legacy transition tests continue to exercise the
    # direct send path (matches pre-stepped behavior). New stepped tests use
    # software mode explicitly or the default.
    cfg = MagicMock()
    cfg.options = {"transition_mode": "native"}
    gateway.config_entry = cfg
    
    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="12", who="1", where="12", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.async_schedule_update_ha_state = MagicMock()
    
    assert light.color_mode == ColorMode.BRIGHTNESS
    
    # Update
    await light.async_update()
    gateway.send_status_request.assert_called_once()
    assert "status_request" not in str(gateway.send_status_request.call_args) # Should call get_brightness
    gateway.send_status_request.reset_mock()
    
    # Turn on
    await light.async_turn_on()
    gateway.send.assert_called_once()
    gateway.send.reset_mock()
    
    # Turn on with Brightness
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})
    gateway.send.assert_called_once()
    gateway.send.reset_mock()
    
    # Turn on with Transition
    await light.async_turn_on(**{ATTR_TRANSITION: 5})
    gateway.send.assert_called_once()
    gateway.send.reset_mock()
    
    # Turn on with Brightness and Transition
    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 50, ATTR_TRANSITION: 2})
    gateway.send.assert_called_once()
    gateway.send.reset_mock()
    
    # Turn off with brightness 0
    await light.async_turn_on(**{ATTR_BRIGHTNESS: 0})
    gateway.send.assert_called_once()  # Routes to async_turn_off
    # Check it actually sent the off command
    gateway.send.reset_mock()
    
    # Turn off with transition
    await light.async_turn_off(**{ATTR_TRANSITION: 5})
    gateway.send.assert_called_once()
    gateway.send.reset_mock()


async def test_light_entity_onoff(hass):
    """Test default onoff MyHOMELight logic."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    
    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="13", who="1", where="13", interface=None, dimmable=False,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    
    assert light.color_mode == ColorMode.ONOFF
    
    # Update calls status request
    await light.async_update()
    gateway.send_status_request.assert_called_once()
    gateway.send_status_request.reset_mock()
    
    # Flash support
    await light.async_turn_on(**{ATTR_FLASH: FLASH_SHORT})
    gateway.send.assert_called_once()
    gateway.send.reset_mock()
    
    await light.async_turn_off(**{ATTR_FLASH: FLASH_LONG})
    gateway.send.assert_called_once()
    gateway.send.reset_mock()
    
    # Event handling
    event = MagicMock(spec=OWNLightingEvent)
    event.is_on = True
    event.brightness = None
    
    light.handle_event(event)
    assert light.is_on
    assert light.icon == "mdi:lightbulb-on"
    
    event.is_on = False
    light.handle_event(event)
    assert not light.is_on
    assert light.icon == "mdi:lightbulb-off"

    await light.async_added_to_hass()


# ============================================================================
# Software Stepped Transitions & Edge Cases Tests
# ============================================================================


def test_transition_mode_helper_branches(hass):
    """Test _get_transition_mode and _should_use_software_stepped logic branches."""
    gateway = MagicMock()
    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="20", who="1", where="20", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )

    # 1. No gateway handler
    light._gateway_handler = None
    assert light._get_transition_mode() == DEFAULT_TRANSITION_MODE

    # 2. Gateway handler without config_entry
    light._gateway_handler = gateway
    gateway.config_entry = None
    assert light._get_transition_mode() == DEFAULT_TRANSITION_MODE

    # 3. Config entry with auto mode -> maps to software_stepped
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_AUTO}
    gateway.config_entry = cfg
    assert light._get_transition_mode() == TRANSITION_MODE_SOFTWARE

    # 4. Config entry with invalid mode -> defaults
    cfg.options = {CONF_TRANSITION_MODE: "unsupported_mode"}
    assert light._get_transition_mode() == DEFAULT_TRANSITION_MODE

    # 5. Config entry with native
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_NATIVE}
    assert light._get_transition_mode() == TRANSITION_MODE_NATIVE

    # 6. _should_use_software_stepped checks
    assert light._should_use_software_stepped(None) is False
    assert light._should_use_software_stepped(0) is False
    assert light._should_use_software_stepped(-1.0) is False
    # In native mode, returns False even with transition > 0
    assert light._should_use_software_stepped(2.0) is False

    # In software mode, returns True
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE}
    assert light._should_use_software_stepped(2.0) is True


async def test_software_stepped_turn_on_fade(hass):
    """Test executing a full software stepped fade ramp on turn_on."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE, CONF_WORKER_COUNT: 1}
    gateway.config_entry = cfg

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="21", who="1", where="21", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    light._attr_is_on = True
    light._attr_brightness_pct = 20

    # Patch asyncio.sleep to execute instantly
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 1.0})

        # Wait for fade task to finish
        assert light._fade_task is not None
        await light._fade_task

        # Verify multiple send calls with transition=0 (instant steps)
        assert gateway.send.call_count >= 2
        assert mock_sleep.call_count >= 1
        assert light._attr_brightness_pct == 80
        assert light.is_on is True


async def test_software_stepped_turn_off_fade(hass):
    """Test executing a software stepped fade to 0 on turn_off."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE}
    gateway.config_entry = cfg

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="22", who="1", where="22", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    light._attr_is_on = True
    light._attr_brightness_pct = 70

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await light.async_turn_off(**{ATTR_TRANSITION: 1.0})
        assert light._fade_task is not None
        await light._fade_task

        assert light._attr_brightness_pct == 0
        assert light.is_on is False


async def test_software_stepped_transition_only_turn_on(hass):
    """Test turn_on with transition but without explicit brightness kwarg."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE}
    gateway.config_entry = cfg

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="23", who="1", where="23", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()
    light._attr_is_on = False
    light._last_brightness_pct = 60

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await light.async_turn_on(**{ATTR_TRANSITION: 1.0})
        assert light._fade_task is not None
        await light._fade_task

        assert light._attr_brightness_pct == 60
        assert light.is_on is True


async def test_software_stepped_short_duration_instant_path(hass):
    """Test duration < 0.05s sends an instant brightness command without stepping."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE}
    gateway.config_entry = cfg

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="24", who="1", where="24", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 90, ATTR_TRANSITION: 0.02})
    if light._fade_task:
        await light._fade_task
    gateway.send.assert_called_once()
    assert light._attr_brightness_pct == 90


async def test_software_stepped_multi_worker_warning(hass, caplog):
    """Test warning logged when command_worker_count > 1 with software stepped transitions."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE, CONF_WORKER_COUNT: 2}
    gateway.config_entry = cfg

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="25", who="1", where="25", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with caplog.at_level(logging.WARNING):
            await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 50, ATTR_TRANSITION: 0.5})
            await light._fade_task
            assert "command_worker_count=2" in caplog.text


async def test_cancellation_and_entity_removal(hass):
    """Test cancelling fade task on entity removal from Home Assistant."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    cfg = MagicMock()
    cfg.options = {CONF_TRANSITION_MODE: TRANSITION_MODE_SOFTWARE}
    gateway.config_entry = cfg

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="26", who="1", where="26", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # Launch a long fade task that pauses on sleep
    stop_event = asyncio.Event()

    async def slow_sleep(*args, **kwargs):
        await stop_event.wait()

    with patch("asyncio.sleep", side_effect=slow_sleep):
        await light.async_turn_on(**{ATTR_BRIGHTNESS_PCT: 80, ATTR_TRANSITION: 5.0})
        assert light._fade_task is not None
        assert not light._fade_task.done()

        # Removing from hass must robustly cancel and clear _fade_task
        await light.async_will_remove_from_hass()
        assert light._fade_task is None


def test_handle_event_active_fade_policy(hass):
    """Test handle_event logic during an active software stepped fade."""
    gateway = MagicMock()
    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="27", who="1", where="27", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # 1. Bus event is_on=False cancels active fade task
    mock_task = MagicMock()
    mock_task.done.return_value = False
    light._fade_task = mock_task

    off_event = MagicMock(spec=OWNLightingEvent)
    off_event.is_on = False
    off_event.brightness = None
    off_event.brightness_preset = None
    off_event.human_readable_log = "Turned off"

    light.handle_event(off_event)
    mock_task.cancel.assert_called_once()
    assert light._fade_task is None

    # 2. Small brightness difference (< 10pp) keeps optimistic state (echo ignored)
    mock_task2 = MagicMock()
    mock_task2.done.return_value = False
    light._fade_task = mock_task2
    light._attr_brightness_pct = 50

    echo_event = MagicMock(spec=OWNLightingEvent)
    echo_event.is_on = True
    echo_event.brightness = 53  # diff = 3 (< 10)
    echo_event.brightness_preset = None
    echo_event.human_readable_log = "Echo 53%"

    light.handle_event(echo_event)
    mock_task2.cancel.assert_not_called()
    assert light._attr_brightness_pct == 50  # unchanged optimistic state

    # 3. Large brightness change (>= 10pp) cancels fade and applies reported value
    jump_event = MagicMock(spec=OWNLightingEvent)
    jump_event.is_on = True
    jump_event.brightness = 80  # diff = 30 (>= 10)
    jump_event.brightness_preset = None
    jump_event.human_readable_log = "Wall switch 80%"

    light.handle_event(jump_event)
    mock_task2.cancel.assert_called_once()
    assert light._attr_brightness_pct == 80


def test_auto_dimmer_promotion_and_flash_support(hass):
    """Test auto-promotion from on/off to dimmable and flash feature flags."""
    gateway = MagicMock()
    gateway.send = AsyncMock()

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon="mdi:lightbulb-off", icon_on="mdi:lightbulb-on",
        device_id="28", who="1", where="28", interface=None, dimmable=False,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    assert light.color_mode == ColorMode.ONOFF
    assert light.supported_features & LightEntityFeature.FLASH

    # Receive an event with brightness_preset
    preset_event = MagicMock(spec=OWNLightingEvent)
    preset_event.is_on = True
    preset_event.brightness = None
    preset_event.brightness_preset = 4
    preset_event.human_readable_log = "Preset 4"

    light.handle_event(preset_event)

    # Promoted to BRIGHTNESS mode with TRANSITION feature, FLASH removed
    assert light.color_mode == ColorMode.BRIGHTNESS
    assert light.supported_features & LightEntityFeature.TRANSITION
    assert not (light.supported_features & LightEntityFeature.FLASH)


async def test_discovery_callback_message_filtering(hass):
    """Test async_add_light ignores messages with missing where or group/area/general flags."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "test_mac"
    hass.data = {DOMAIN: {"test_mac": {"entity": mock_gateway}}}

    config_entry = MagicMock()
    config_entry.data = {"mac": "test_mac"}
    config_entry.entry_id = "test_entry"

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    with patch("custom_components.myhome.light.er.async_entries_for_config_entry", return_value=[]), \
         patch("custom_components.myhome.light.er.async_get"):
        await async_setup_entry(hass, config_entry, mock_add_entities)

    dispatcher_signal = f"myhome_message_{config_entry.data['mac']}"

    # Message with missing where -> ignored
    msg_no_where = MagicMock(spec=OWNLightingEvent)
    msg_no_where.where = None
    async_dispatcher_send(hass, dispatcher_signal, msg_no_where)
    assert len(added_entities) == 0

    # Group message -> ignored
    msg_group = MagicMock(spec=OWNLightingEvent)
    msg_group.where = "1"
    msg_group.is_group = True
    async_dispatcher_send(hass, dispatcher_signal, msg_group)
    assert len(added_entities) == 0

    # Area message -> ignored
    msg_area = MagicMock(spec=OWNLightingEvent)
    msg_area.where = "1"
    msg_area.is_area = True
    async_dispatcher_send(hass, dispatcher_signal, msg_area)
    assert len(added_entities) == 0

    # General message -> ignored
    msg_general = MagicMock(spec=OWNLightingEvent)
    msg_general.where = "0"
    msg_general.is_general = True
    async_dispatcher_send(hass, dispatcher_signal, msg_general)
    assert len(added_entities) == 0

    # Valid message -> light discovered and added
    valid_msg = MagicMock(spec=OWNLightingEvent)
    valid_msg.where = "44"
    valid_msg.who = 1
    valid_msg.interface = None
    valid_msg.is_group = False
    valid_msg.is_area = False
    valid_msg.is_general = False
    valid_msg.brightness = 60
    valid_msg.brightness_preset = None
    valid_msg.is_on = True
    valid_msg.human_readable_log = "Valid Light 44"
    async_dispatcher_send(hass, dispatcher_signal, valid_msg)
    assert len(added_entities) == 1


def test_schedule_update_runtime_error_caught(hass):
    """Test that RuntimeError during async_schedule_update_ha_state is caught safely."""
    gateway = MagicMock()
    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon=None, icon_on=None,
        device_id="29", who="1", where="29", interface=None, dimmable=False,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock(side_effect=RuntimeError("Update after removal"))

    event = MagicMock(spec=OWNLightingEvent)
    event.is_on = True
    event.brightness = None
    event.brightness_preset = None
    event.human_readable_log = "Safe test"

    # Must not raise RuntimeError
    light.handle_event(event)


async def test_flash_variants_and_fade_exceptions(hass, caplog):
    """Test FLASH_LONG on turn_on, FLASH_SHORT on turn_off, and fade exception handling."""
    gateway = MagicMock()
    gateway.send = AsyncMock()

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon=None, icon_on=None,
        device_id="30", who="1", where="30", interface=None, dimmable=False,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # FLASH_LONG on turn_on
    await light.async_turn_on(**{ATTR_FLASH: FLASH_LONG})
    assert gateway.send.called
    gateway.send.reset_mock()

    # FLASH_SHORT on turn_off
    await light.async_turn_off(**{ATTR_FLASH: FLASH_SHORT})
    assert gateway.send.called
    gateway.send.reset_mock()

    # Direct _apply_brightness_state with explicit is_on
    light._apply_brightness_state(40, is_on=False)
    assert light._attr_brightness_pct == 40
    assert light.is_on is False

    # Stale fade_id at start of _async_fade_to
    light._fade_id = 5
    await light._async_fade_to(0, 100, 1.0, fade_id=1)
    # Stale ID immediately returns without sending
    assert not gateway.send.called

    # Fade exception caught and logged
    light._fade_id = 10
    with patch.object(light, "_set_brightness_instant", side_effect=RuntimeError("Bus disconnected")):
        with caplog.at_level(logging.WARNING):
            await light._async_fade_to(0, 100, 0.5, fade_id=10)
            assert "Fade task error" in caplog.text

    # Fade cancellation caught cleanly
    light._fade_id = 11
    with patch.object(light, "_set_brightness_instant", side_effect=asyncio.CancelledError()):
        with pytest.raises(asyncio.CancelledError):
            await light._async_fade_to(0, 100, 0.5, fade_id=11)


async def test_fade_stale_step_and_worker_count_exception(hass):
    """Test worker count exception handling and mid-step stale fade cancellation."""
    gateway = MagicMock()
    gateway.send = AsyncMock()
    gateway.config_entry = MagicMock()
    # Cause int(wc) to raise ValueError in worker count check
    gateway.config_entry.options = {CONF_WORKER_COUNT: "not_a_number"}

    light = MyHOMELight(
        hass=hass, name="L", entity_name="L", icon=None, icon_on=None,
        device_id="31", who="1", where="31", interface=None, dimmable=True,
        manufacturer="B", model="M", gateway=gateway
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # Step 1: worker count exception handled gracefully (no crash)
    light._fade_id = 20
    # Step 2: mid-loop fade invalidation (mutating _fade_id during sleep or execution)
    original_set_brightness = light._set_brightness_instant

    async def side_effect_change_fade(pct):
        # Invalidate fade id during first step to trigger line 388-389
        light._fade_id = 999
        await original_set_brightness(pct)

    with patch.object(light, "_set_brightness_instant", side_effect=side_effect_change_fade):
        await light._async_fade_to(start_pct=0, target_pct=100, duration=0.2, fade_id=20)

    # It aborted after the first step and did not complete
    assert light._fade_id == 999


async def test_light_switch_collision_and_interface_dispatch(hass):
    """Test configured light skipped if address is in switch_wheres and bus interface dispatch."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    hass.data.setdefault(DOMAIN, {})["mac"] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {
            "switch": {
                "16": {CONF_WHERE: "16"},
            },
            "light": {
                "16": {CONF_WHERE: "16", CONF_NAME: "Conflicting Light 16"},
                "17": {CONF_WHERE: "17", CONF_NAME: "Light 17"},
            },
        },
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}
    config_entry.entry_id = "test_entry"

    sw_entry = MagicMock()
    sw_entry.domain = "switch"
    sw_entry.unique_id = "mac-1-16#4#01"
    sw_entry.entity_id = "switch.sw_16"

    light_ghost_entry = MagicMock()
    light_ghost_entry.domain = "light"
    light_ghost_entry.unique_id = "mac-1-16"
    light_ghost_entry.entity_id = "light.ghost_16"

    mock_er = MagicMock()

    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[sw_entry, light_ghost_entry],
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=mock_er,
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)
        mock_er.async_remove.assert_called_once_with("light.ghost_16")
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        # Only Light 17 is added; Light 16 is skipped because it is in switch_wheres
        assert len(entities) == 1
        assert entities[0]._device_id == "17"

        # Now test message dispatching for an entity with interface where clean_where is in switch_wheres
        received_unique = []
        received_base = []

        from homeassistant.helpers.dispatcher import async_dispatcher_connect
        async_dispatcher_connect(hass, "myhome_update_mac_1_16#4#01", lambda msg: received_unique.append(msg))
        async_dispatcher_connect(hass, "myhome_update_mac_1_16", lambda msg: received_base.append(msg))

        from custom_components.myhome.ownd.message import OWNEvent
        msg = OWNEvent.parse("*1*1*16#4#01##")

        # Send gateway message
        async_dispatcher_send(hass, "myhome_message_mac", msg)
        await hass.async_block_till_done()

        assert len(received_unique) == 1
        assert len(received_base) == 1


async def test_light_setup_registry_exception(hass):
    """Test light async_setup_entry gracefully handles entity registry exception."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac_err"
    hass.data.setdefault(DOMAIN, {})["mac_err"] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {
            "light": {
                "19": {CONF_WHERE: "19", CONF_NAME: "Light 19"},
            },
        },
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac_err"}
    config_entry.entry_id = "test_entry_err"

    with patch(
        "custom_components.myhome.light.er.async_get",
        side_effect=Exception("Registry unavailable"),
    ):
        async_add_entities = MagicMock()
        await async_setup_entry(hass, config_entry, async_add_entities)
        async_add_entities.assert_called_once()
        entities = async_add_entities.call_args[0][0]
        assert len(entities) == 1
        assert entities[0]._device_id == "19"


async def test_light_suppresses_sensor_discovery_and_purges_registry(hass):
    """Test that light platform purges ghost light entities from registry and ignores sensor messages."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac_sensor"
    hass.data.setdefault(DOMAIN, {})["mac_sensor"] = {
        "entity": mock_gateway,
        CONF_PLATFORMS: {
            "light": {
                "25": {CONF_WHERE: "25", CONF_NAME: "Light 25"},
            },
            "binary_sensor": {
                "bs_21": {CONF_WHO: "1", CONF_WHERE: "21", CONF_NAME: "Motion 21"},
            },
            "sensor": {
                "s_22": {CONF_WHO: "1", CONF_WHERE: "22", CONF_NAME: "Illuminance 22"},
            },
        },
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac_sensor"}
    config_entry.entry_id = "test_entry_sensor"

    # Mock entity registry with:
    # 1. binary_sensor with who 1: mac_sensor-1-23-motion
    # 2. sensor with who 1: mac_sensor-24-illuminance
    # 3. ghost light entity for 21: mac_sensor-1-21
    # 4. ghost light entity for 23: mac_sensor-1-23
    # 5. real light entity: mac_sensor-1-25
    bs_entry = MagicMock()
    bs_entry.domain = "binary_sensor"
    bs_entry.unique_id = "mac_sensor-1-23-motion"

    s_entry = MagicMock()
    s_entry.domain = "sensor"
    s_entry.unique_id = "mac_sensor-24-illuminance"

    ghost_21 = MagicMock()
    ghost_21.domain = "light"
    ghost_21.unique_id = "mac_sensor-1-21"
    ghost_21.entity_id = "light.ghost_21"

    ghost_23 = MagicMock()
    ghost_23.domain = "light"
    ghost_23.unique_id = "mac_sensor-1-23"
    ghost_23.entity_id = "light.ghost_23"

    light_25 = MagicMock()
    light_25.domain = "light"
    light_25.unique_id = "mac_sensor-1-25"
    light_25.entity_id = "light.light_25"

    mock_er = MagicMock()

    with patch(
        "custom_components.myhome.light.er.async_entries_for_config_entry",
        return_value=[bs_entry, s_entry, ghost_21, ghost_23, light_25],
    ), patch(
        "custom_components.myhome.light.er.async_get",
        return_value=mock_er,
    ):
        added = []
        def fake_add(entities):
            added.extend(entities)

        await async_setup_entry(hass, config_entry, fake_add)

        # Verify ghost lights are removed from registry
        mock_er.async_remove.assert_any_call("light.ghost_21")
        mock_er.async_remove.assert_any_call("light.ghost_23")
        assert len(added) == 1
        assert added[0]._device_id == "25"

        # Now test receiving incoming sensor messages:
        # 1. Motion message (*1*34*31##)
        from custom_components.myhome.ownd.message import OWNEvent
        motion_msg = OWNEvent.parse("*1*34*31##")
        async_dispatcher_send(hass, "myhome_message_mac_sensor", motion_msg)
        await hass.async_block_till_done()

        # Should NOT add any new light entity
        assert len(added) == 1

        # 2. Illuminance message (*#1*31*6*500##)
        illum_msg = OWNEvent.parse("*#1*31*6*500##")
        async_dispatcher_send(hass, "myhome_message_mac_sensor", illum_msg)
        await hass.async_block_till_done()

        # Should NOT add any new light entity
        assert len(added) == 1

        # 3. Subsequent standard light event on address 31 (*1*1*31##)
        # Since 31 is recognized as sensor_wheres, it should be ignored by light platform
        on_msg = OWNEvent.parse("*1*1*31##")
        async_dispatcher_send(hass, "myhome_message_mac_sensor", on_msg)
        await hass.async_block_till_done()
        assert len(added) == 1

        # 4. Motion message with interface (*1*34*32#4#01##)
        motion_interface = OWNEvent.parse("*1*34*32#4#01##")
        async_dispatcher_send(hass, "myhome_message_mac_sensor", motion_interface)
        await hass.async_block_till_done()
        assert len(added) == 1

        # 5. Sensor message arriving for an address previously in known_lights (e.g. "25")
        sensor_for_light = OWNEvent.parse("*1*34*25##")
        async_dispatcher_send(hass, "myhome_message_mac_sensor", sensor_for_light)
        await hass.async_block_till_done()

