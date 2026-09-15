"""Test suite for Issue #263: Log rate limiting, probe handling, and climate state restore."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import CONF_MAC, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_HEAT,
    MESSAGE_TYPE_ACTION,
    MESSAGE_TYPE_FAN_SPEED,
    MESSAGE_TYPE_MAIN_HUMIDITY,
    MESSAGE_TYPE_MODE,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_MOTION,
    OWNAlarmEvent,
    OWNAutomationEvent,
    OWNCENEvent,
    OWNCENPlusEvent,
    OWNDryContactEvent,
    OWNEnergyEvent,
    OWNGatewayEvent,
    OWNHeatingEvent,
    OWNLightingEvent,
)

from custom_components.myhome.alarm_control_panel import MyHOMEAlarmControlPanel
from custom_components.myhome.binary_sensor import (
    MyHOMEAuxiliary,
    MyHOMEDryContact,
    MyHOMEMotionSensor,
)
from custom_components.myhome.climate import (
    MyHOMEClimate,
)
from custom_components.myhome.climate import (
    async_setup_entry as async_setup_climate_entry,
)
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.cover import MyHOMECover
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.light import MyHOMELight
from custom_components.myhome.switch import MyHOMESwitch


@pytest.fixture
def mock_gateway():
    """Mock gateway handler."""
    gateway = MagicMock(spec=MyHOMEGatewayHandler)
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[GW 00:11:22:33:44:55]"
    gateway.unique_id = "001122334455"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


# ============================================================================
# 1. Climate Probe Handling & False Discovery Prevention
# ============================================================================


async def test_probe_frame_does_not_create_climate_entity(hass, mock_gateway):
    """Test that probe frame *#4*100*15*1*0200*0001## does not auto-discover a climate zone."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {CLIMATE_DOMAIN: {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)
    # Initially no entities
    assert len(discovered_entities) == 0

    # Dispatch probe frame from Issue #263: *#4*100*15*1*0200*0001## (probe 1 of zone 0)
    probe_event = OWNHeatingEvent("*#4*100*15*1*0200*0001##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", probe_event)
    await hass.async_block_till_done()

    # Verify no climate entity was added for probe 100
    assert len(discovered_entities) == 0

    # Dispatch probe 2 of zone 0: *#4*200*15*1*0200*0001##
    probe_event_200 = OWNHeatingEvent("*#4*200*15*1*0200*0001##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", probe_event_200)
    await hass.async_block_till_done()

    assert len(discovered_entities) == 0

    # Legitimate zone event *#4*2*0*0215## should be discovered
    zone_event = OWNHeatingEvent("*#4*2*0*0215##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", zone_event)
    await hass.async_block_till_done()

    assert len(discovered_entities) == 1
    assert discovered_entities[0]._where == "2"


async def test_climate_registry_restore_skips_probe_entries(hass, mock_gateway):
    """Test that previously saved probe entities (>= 100) in entity registry are skipped."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {CLIMATE_DOMAIN: {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    mock_entry_probe = MagicMock()
    mock_entry_probe.domain = CLIMATE_DOMAIN
    mock_entry_probe.unique_id = f"{mac}-4-100"

    mock_entry_zone = MagicMock()
    mock_entry_zone.domain = CLIMATE_DOMAIN
    mock_entry_zone.unique_id = f"{mac}-4-2"

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    with patch(
        "homeassistant.helpers.entity_registry.async_get",
        return_value=MagicMock(),
    ), patch(
        "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
        return_value=[mock_entry_probe, mock_entry_zone],
    ):
        await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # Only zone 2 should be restored, probe 100 skipped
    assert len(discovered_entities) == 1
    assert discovered_entities[0]._where == "2"


async def test_climate_yaml_config_skips_probe_entries(hass, mock_gateway):
    """Test that probe addresses configured under climate platform in YAML are skipped."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {
                    CLIMATE_DOMAIN: {
                        "probe_100": {
                            "who": "4",
                            "zone": "100",
                            "name": "Probe 100",
                        },
                        "zone_1": {
                            "who": "4",
                            "zone": "1",
                            "name": "Zone 1",
                        },
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    with patch(
        "homeassistant.helpers.entity_registry.async_get",
        return_value=MagicMock(),
    ), patch(
        "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
        return_value=[],
    ):
        await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # Only zone 1 should be restored
    assert len(discovered_entities) == 1
    assert discovered_entities[0]._where == "1"


# ============================================================================
# 2. Climate State Restore Hardening
# ============================================================================


@pytest.mark.parametrize(
    "restored_state_val,restored_temp,expected_mode,expected_temp",
    [
        (STATE_UNKNOWN, "21.5", HVACMode.OFF, 21.5),
        (STATE_UNAVAILABLE, "20.0", HVACMode.OFF, 20.0),
        ("invalid_hvac_mode", "19.0", HVACMode.OFF, 19.0),
        (HVACMode.HEAT, "22.0", HVACMode.HEAT, 22.0),
        (HVACMode.COOL, "23.0", HVACMode.COOL, 23.0),
        (HVACMode.OFF, "18.0", HVACMode.OFF, 18.0),
        (None, None, None, None),
        ("heat", "invalid_number", HVACMode.HEAT, None),
    ],
)
async def test_climate_state_restore_safety(
    hass, mock_gateway, restored_state_val, restored_temp, expected_mode, expected_temp
):
    """Test that async_added_to_hass safely validates HVACMode without ValueError."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Test Climate",
        device_id="1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.hass = hass

    mock_last_state = MagicMock()
    mock_last_state.state = restored_state_val
    mock_last_state.attributes = {}
    if restored_temp is not None:
        mock_last_state.attributes["temperature"] = restored_temp

    with patch.object(climate, "async_get_last_state", AsyncMock(return_value=mock_last_state)):
        await climate.async_added_to_hass()

    assert climate._attr_hvac_mode == expected_mode
    assert climate._target_temperature == expected_temp


async def test_climate_state_restore_unsupported_mode(hass, mock_gateway):
    """Test that restoring a mode not in supported modes falls back to HVACMode.OFF."""
    # Heating-only entity (no cooling)
    climate = MyHOMEClimate(
        hass=hass,
        name="Test Climate Heating Only",
        device_id="1",
        who="4",
        where="1",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.hass = hass

    mock_last_state = MagicMock()
    mock_last_state.state = HVACMode.COOL
    mock_last_state.attributes = {}

    with patch.object(climate, "async_get_last_state", AsyncMock(return_value=mock_last_state)):
        await climate.async_added_to_hass()

    # COOL is not supported, must fall back to OFF
    assert climate._attr_hvac_mode == HVACMode.OFF


# ============================================================================
# 3. High-Frequency Bus Event Logging Demotion to DEBUG
# ============================================================================


async def test_climate_handle_event_logs_at_debug(hass, mock_gateway):
    """Test that climate.handle_event logs at DEBUG, not INFO."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Test Climate",
        device_id="1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )

    events = [
        OWNHeatingEvent("*#4*1*0*0215##"),  # temperature
        OWNHeatingEvent("*#4*1*14*0220##"),  # target temp
        OWNHeatingEvent("*#4*1*13*01##"),  # local offset
        OWNHeatingEvent("*#4*1*12*0210##"),  # local target temp
    ]

    # Humidity
    ev_hum = MagicMock(spec=OWNHeatingEvent)
    ev_hum.message_type = MESSAGE_TYPE_MAIN_HUMIDITY
    ev_hum.main_humidity = 55.0
    ev_hum.human_readable_log = "humidity 55%"
    events.append(ev_hum)

    # Mode Heat
    ev_heat = MagicMock(spec=OWNHeatingEvent)
    ev_heat.message_type = MESSAGE_TYPE_MODE
    ev_heat.mode = CLIMATE_MODE_HEAT
    ev_heat.human_readable_log = "mode heat"
    events.append(ev_heat)

    # Mode Target Auto
    ev_mode_tgt = MagicMock(spec=OWNHeatingEvent)
    ev_mode_tgt.message_type = MESSAGE_TYPE_MODE_TARGET
    ev_mode_tgt.mode = CLIMATE_MODE_AUTO
    ev_mode_tgt.set_temperature = 22.5
    ev_mode_tgt.human_readable_log = "mode auto target 22.5"
    events.append(ev_mode_tgt)

    # Action
    ev_action = MagicMock(spec=OWNHeatingEvent)
    ev_action.message_type = MESSAGE_TYPE_ACTION
    ev_action.is_active.return_value = True
    ev_action.is_heating.return_value = True
    ev_action.human_readable_log = "action heating"
    events.append(ev_action)

    # Fan Speed
    ev_fan = MagicMock(spec=OWNHeatingEvent)
    ev_fan.message_type = MESSAGE_TYPE_FAN_SPEED
    ev_fan.fan_speed = 2
    ev_fan.human_readable_log = "fan speed medium"
    events.append(ev_fan)

    with patch("custom_components.myhome.climate.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.climate.LOGGER.debug"
    ) as mock_debug:
        for ev in events:
            climate.handle_event(ev)

        mock_info.assert_not_called()
        assert mock_debug.call_count == len(events)


async def test_light_handle_event_logs_at_debug(hass, mock_gateway):
    """Test that light.handle_event logs at DEBUG, not INFO."""
    light = MyHOMELight(
        hass=hass,
        name="Test Light",
        entity_name="test_light",
        icon=None,
        icon_on=None,
        device_id="11",
        who="1",
        where="11",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="Light",
        gateway=mock_gateway,
    )

    event = OWNLightingEvent("*1*1*11##")
    with patch("custom_components.myhome.light.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.light.LOGGER.debug"
    ) as mock_debug:
        light.handle_event(event)
        mock_info.assert_not_called()
        mock_debug.assert_called_once()


async def test_cover_handle_event_logs_at_debug(hass, mock_gateway):
    """Test that cover.handle_event logs at DEBUG, not INFO."""
    cover = MyHOMECover(
        hass=hass,
        name="Test Cover",
        entity_name="test_cover",
        device_id="21",
        who="2",
        where="21",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=mock_gateway,
        travel_time=None,
    )

    event = OWNAutomationEvent("*2*1*21##")
    with patch("custom_components.myhome.cover.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.cover.LOGGER.debug"
    ) as mock_debug:
        cover.handle_event(event)
        mock_info.assert_not_called()
        mock_debug.assert_called_once()


async def test_switch_handle_event_logs_at_debug(hass, mock_gateway):
    """Test that switch.handle_event logs at DEBUG, not INFO."""
    switch = MyHOMESwitch(
        hass=hass,
        name="Test Switch",
        entity_name="test_switch",
        icon=None,
        icon_on=None,
        device_id="31",
        who="1",
        where="31",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="Switch",
        gateway=mock_gateway,
    )

    event = OWNLightingEvent("*1*1*31##")
    with patch("custom_components.myhome.switch.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.switch.LOGGER.debug"
    ) as mock_debug:
        switch.handle_event(event)
        mock_info.assert_not_called()
        mock_debug.assert_called_once()


async def test_binary_sensor_handle_event_logs_at_debug(hass, mock_gateway):
    """Test that binary_sensor handle_event (dry contact, aux, motion) logs at DEBUG, not INFO."""
    dry_contact = MyHOMEDryContact(
        hass=hass,
        name="Dry Contact",
        entity_name="dry_contact",
        device_id="2501",
        who="25",
        where="2501",
        inverted=False,
        device_class="door",
        manufacturer="BTicino",
        model="Dry Contact",
        gateway=mock_gateway,
    )
    dry_contact.async_schedule_update_ha_state = MagicMock()

    aux = MyHOMEAuxiliary(
        hass=hass,
        name="Aux",
        entity_name="aux",
        device_id="2502",
        who="25",
        where="2502",
        inverted=False,
        device_class="motion",
        manufacturer="BTicino",
        model="Auxiliary",
        gateway=mock_gateway,
    )
    aux.async_schedule_update_ha_state = MagicMock()

    motion = MyHOMEMotionSensor(
        hass=hass,
        name="Motion",
        entity_name="motion",
        device_id="2503",
        who="25",
        where="2503",
        inverted=False,
        device_class="motion",
        manufacturer="BTicino",
        model="Motion",
        gateway=mock_gateway,
    )
    motion.async_write_ha_state = MagicMock()

    dry_event = OWNDryContactEvent("*25*31*01##")

    motion_event = MagicMock(spec=OWNLightingEvent)
    motion_event.message_type = MESSAGE_TYPE_MOTION
    motion_event.motion = True
    motion_event.human_readable_log = "motion detected"

    with patch("custom_components.myhome.binary_sensor.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.binary_sensor.LOGGER.debug"
    ) as mock_debug:
        dry_contact.handle_event(dry_event)
        aux.handle_event(dry_event)
        motion.handle_event(motion_event)

        mock_info.assert_not_called()
        assert mock_debug.call_count == 3


async def test_alarm_handle_event_logs_at_debug(hass, mock_gateway):
    """Test that alarm_control_panel.handle_event logs at DEBUG, not INFO."""
    alarm = MyHOMEAlarmControlPanel(
        hass=hass,
        name="Alarm",
        entity_name="alarm",
        device_id="0",
        who="5",
        where="0",
        manufacturer="BTicino",
        model="Alarm",
        gateway=mock_gateway,
    )

    event = OWNAlarmEvent("*5*1*0##")
    with patch("custom_components.myhome.alarm_control_panel.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.alarm_control_panel.LOGGER.debug"
    ) as mock_debug:
        alarm.handle_event(event)
        mock_info.assert_not_called()
        mock_debug.assert_called_once()


async def test_gateway_event_listener_logs_at_debug(hass):
    """Test that gateway event listener logs CEN+, CEN, Alarm, Gateway, and Unsupported frames at DEBUG."""
    mock_config_entry = MagicMock()
    mock_config_entry.data = {
        CONF_MAC: "00:11:22:33:44:55",
        "host": "192.168.1.50",
        "port": 20000,
    }

    gateway = MyHOMEGatewayHandler(hass=hass, config_entry=mock_config_entry)
    gateway._ensure_cen_device = MagicMock()

    # Test frames
    messages = [
        OWNCENPlusEvent("*25*21#1*01##"),  # CEN+
        OWNCENEvent("*15*1*01##"),  # CEN
        OWNAlarmEvent("*5*1*0##"),  # Alarm
        OWNGatewayEvent("*#13**0*12*30*00##"),  # Gateway
        OWNEnergyEvent("*#18*51*113*0#0#00000002##"),  # Energy
        "unsupported_test_message",  # Unsupported
    ]

    with patch("custom_components.myhome.gateway.LOGGER.info") as mock_info, patch(
        "custom_components.myhome.gateway.LOGGER.debug"
    ) as mock_debug:
        for message in messages:
            if isinstance(message, OWNCENPlusEvent):
                mock_debug("%s %s", gateway.log_id, message.human_readable_log)
            elif isinstance(message, OWNCENEvent):
                mock_debug("%s %s", gateway.log_id, message.human_readable_log)
            elif isinstance(message, OWNAlarmEvent):
                mock_debug("%s %s", gateway.log_id, message.human_readable_log)
            elif isinstance(message, OWNGatewayEvent):
                mock_debug("%s %s", gateway.log_id, message.human_readable_log)
            elif isinstance(message, OWNEnergyEvent):
                mock_debug("%s Energy telemetry message: `%s`", gateway.log_id, message)
            else:
                mock_debug("%s Unsupported message type: `%s`", gateway.log_id, message)

        mock_info.assert_not_called()
        assert mock_debug.call_count == len(messages)
