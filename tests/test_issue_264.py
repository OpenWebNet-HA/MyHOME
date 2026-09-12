"""Unit tests for Issue #264: Temperature probe auto-discovery and dimension 15 support."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    CONF_MAC,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from OWNd.message import (
    MESSAGE_TYPE_SECONDARY_TEMPERATURE,
    OWNEvent,
    OWNHeatingEvent,
)

from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.sensor import (
    MyHOMETemperatureSensor,
    async_setup_entry,
)


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[Test Gateway]"
    gateway.send_status_request = AsyncMock()
    return gateway


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {
        CONF_MAC: "00:11:22:33:44:55",
    }
    return entry


@pytest.mark.asyncio
async def test_probe_dimension_15_discovery_and_update(mock_gateway, mock_config_entry):
    """Test that *#4*100*15*1*0200*0001## auto-discovers a temperature probe entity and updates on next frame."""
    mac = mock_gateway.mac
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"sensor": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    dispatch_callbacks = []

    def mock_dispatcher_connect(hass, signal, target):
        if signal == f"myhome_message_{mac}":
            dispatch_callbacks.append(target)
        return MagicMock()

    with patch("custom_components.myhome.sensor.async_dispatcher_connect", side_effect=mock_dispatcher_connect), \
         patch("custom_components.myhome.sensor.er.async_get", return_value=MagicMock()), \
         patch("custom_components.myhome.sensor.er.async_entries_for_config_entry", return_value=[]):
        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    assert len(discovered_entities) == 0
    assert len(dispatch_callbacks) == 1
    handle_message = dispatch_callbacks[0]

    # 1. Dispatch probe 100 frame 1 from Issue #264: *#4*100*15*1*0200*0001##
    msg1 = OWNEvent.parse("*#4*100*15*1*0200*0001##")
    handle_message(msg1)

    assert len(discovered_entities) == 1
    probe_sensor: MyHOMETemperatureSensor = discovered_entities[0]
    assert probe_sensor._where == "100"
    assert probe_sensor._device_id == "100"
    assert probe_sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert probe_sensor.native_value == 20.0
    assert probe_sensor.name == "Probe 100 Temperature"
    assert probe_sensor.unique_id == f"{mac}-100-temperature"

    # 2. Dispatch subsequent update: *#4*100*15*1*0196*0001## (19.6°C)
    msg2 = OWNEvent.parse("*#4*100*15*1*0196*0001##")
    handle_message(msg2)

    # Should not create duplicate entity
    assert len(discovered_entities) == 1
    assert probe_sensor.native_value == 19.6

    # 3. Dispatch negative temperature probe reading: *#4*100*15*1*1050*0001## (-5.0°C)
    msg_neg = OWNEvent.parse("*#4*100*15*1*1050*0001##")
    handle_message(msg_neg)
    assert len(discovered_entities) == 1
    assert probe_sensor.native_value == -5.0


@pytest.mark.asyncio
async def test_probe_dimension_0_discovery(mock_gateway, mock_config_entry):
    """Test that probe address >= 100 reporting via dimension 0 also auto-discovers."""
    mac = mock_gateway.mac
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"sensor": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    dispatch_callbacks = []

    def mock_dispatcher_connect(hass, signal, target):
        if signal == f"myhome_message_{mac}":
            dispatch_callbacks.append(target)
        return MagicMock()

    with patch("custom_components.myhome.sensor.async_dispatcher_connect", side_effect=mock_dispatcher_connect), \
         patch("custom_components.myhome.sensor.er.async_get", return_value=MagicMock()), \
         patch("custom_components.myhome.sensor.er.async_entries_for_config_entry", return_value=[]):
        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    handle_message = dispatch_callbacks[0]

    # Secondary probe 105 reporting 21.5°C via dimension 0
    msg = OWNEvent.parse("*#4*105*0*0215##")
    handle_message(msg)

    assert len(discovered_entities) == 1
    sensor = discovered_entities[0]
    assert sensor._where == "105"
    assert sensor.native_value == 21.5
    assert sensor.name == "Probe 105 Temperature"


@pytest.mark.asyncio
async def test_actuators_and_valves_do_not_discover_temperature_sensor(mock_gateway, mock_config_entry):
    """Test that actuator (dim 20) and valve (dim 19) frames for address >= 100 do not create sensors."""
    mac = mock_gateway.mac
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"sensor": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    dispatch_callbacks = []

    def mock_dispatcher_connect(hass, signal, target):
        if signal == f"myhome_message_{mac}":
            dispatch_callbacks.append(target)
        return MagicMock()

    with patch("custom_components.myhome.sensor.async_dispatcher_connect", side_effect=mock_dispatcher_connect), \
         patch("custom_components.myhome.sensor.er.async_get", return_value=MagicMock()), \
         patch("custom_components.myhome.sensor.er.async_entries_for_config_entry", return_value=[]):
        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    handle_message = dispatch_callbacks[0]

    # Actuator status: *#4*100#1*20*1##
    actuator_msg = OWNHeatingEvent("*#4*100#1*20*1##")
    handle_message(actuator_msg)
    assert len(discovered_entities) == 0

    # Valve status: *#4*100*19*0*0##
    valve_msg = OWNHeatingEvent("*#4*100*19*0*0##")
    handle_message(valve_msg)
    assert len(discovered_entities) == 0

    # Fan status: *#4*100*11*1##
    fan_msg = OWNHeatingEvent("*#4*100*11*1##")
    handle_message(fan_msg)
    assert len(discovered_entities) == 0


@pytest.mark.asyncio
async def test_temperature_registry_restoration(mock_gateway, mock_config_entry):
    """Test that previously discovered temperature probe entries in the entity registry are restored."""
    mac = mock_gateway.mac
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"sensor": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    discovered_entities = []

    def mock_add_entities(entities):
        discovered_entities.extend(entities)

    reg_entry1 = MagicMock()
    reg_entry1.domain = "sensor"
    reg_entry1.unique_id = f"{mac}-100-temperature"
    reg_entry1.entity_id = "sensor.custom_probe_name"
    reg_entry1.original_device_class = SensorDeviceClass.TEMPERATURE

    # Second entry is duplicate for same address, should be skipped
    reg_entry2 = MagicMock()
    reg_entry2.domain = "sensor"
    reg_entry2.unique_id = f"{mac}-100-temperature"
    reg_entry2.entity_id = "sensor.custom_probe_name_duplicate"
    reg_entry2.original_device_class = SensorDeviceClass.TEMPERATURE

    with patch("custom_components.myhome.sensor.er.async_get", return_value=MagicMock()), \
         patch("custom_components.myhome.sensor.er.async_entries_for_config_entry", return_value=[reg_entry1, reg_entry2]), \
         patch("custom_components.myhome.sensor.async_dispatcher_connect", return_value=MagicMock()):
        await async_setup_entry(hass, mock_config_entry, mock_add_entities)

    assert len(discovered_entities) == 1
    sensor = discovered_entities[0]
    assert sensor.entity_id == "sensor.custom_probe_name"
    assert sensor._where == "100"


@pytest.mark.asyncio
async def test_temperature_sensor_async_update_and_events(mock_gateway):
    """Test MyHOMETemperatureSensor async_update and event handling."""
    hass = MagicMock()
    sensor = MyHOMETemperatureSensor(
        hass=hass,
        name="Probe 100",
        device_id="100",
        who="4",
        where="100",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Temperature Probe",
        gateway=mock_gateway,
    )

    # async_update for probe >= 100
    await sensor.async_update()
    mock_gateway.send_status_request.assert_called_once()
    called_cmd = mock_gateway.send_status_request.call_args[0][0]
    assert str(called_cmd) in ("*#4*100*15##", "*#4*100*0##")

    # async_update for zone < 100
    sensor_zone = MyHOMETemperatureSensor(
        hass=hass,
        name="Zone 2",
        device_id="2",
        who="4",
        where="2",
        device_class=SensorDeviceClass.TEMPERATURE,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=mock_gateway,
    )
    mock_gateway.send_status_request.reset_mock()
    await sensor_zone.async_update()
    mock_gateway.send_status_request.assert_called_once()
    assert str(mock_gateway.send_status_request.call_args[0][0]) == "*#4*2*0##"

    # Event handling with dimension 15 fallback (positive)
    mock_event = MagicMock(spec=OWNHeatingEvent)
    mock_event.message_type = None
    mock_event.dimension = 15
    mock_event.dimension_value = ["1", "0205", "0001"]
    mock_event.human_readable_log = "Probe reporting 20.5"

    sensor.async_schedule_update_ha_state = MagicMock()
    sensor.handle_event(mock_event)
    assert sensor._attr_native_value == 20.5
    sensor.async_schedule_update_ha_state.assert_called_once()

    # Event handling with dimension 15 fallback (negative)
    mock_event_neg = MagicMock(spec=OWNHeatingEvent)
    mock_event_neg.message_type = None
    mock_event_neg.dimension = 15
    mock_event_neg.dimension_value = ["1", "1025", "0001"]
    mock_event_neg.human_readable_log = "Probe reporting -2.5"
    sensor.handle_event(mock_event_neg)
    assert sensor._attr_native_value == -2.5

    # Event handling with dimension 15 fallback (invalid value does not crash)
    mock_event_invalid = MagicMock(spec=OWNHeatingEvent)
    mock_event_invalid.message_type = None
    mock_event_invalid.dimension = 15
    mock_event_invalid.dimension_value = ["1", "invalid", "0001"]
    sensor.handle_event(mock_event_invalid)
    assert sensor._attr_native_value == -2.5

    # Event handling with secondary_temperature as float
    mock_event_sec_float = MagicMock(spec=OWNHeatingEvent)
    mock_event_sec_float.message_type = MESSAGE_TYPE_SECONDARY_TEMPERATURE
    mock_event_sec_float.secondary_temperature = 18.5
    sensor.handle_event(mock_event_sec_float)
    assert sensor._attr_native_value == 18.5

    # Event handling with probe_temperature attribute and no secondary_temperature list
    class MockProbeEvent:
        message_type = MESSAGE_TYPE_SECONDARY_TEMPERATURE
        secondary_temperature = None
        probe_temperature = 23.4
        human_readable_log = "Probe reporting 23.4"

    sensor.handle_event(MockProbeEvent())
    assert sensor._attr_native_value == 23.4

    # Event handling with dimension 0 fallback (positive and negative)
    mock_dim0_pos = MagicMock(spec=OWNHeatingEvent)
    mock_dim0_pos.message_type = None
    mock_dim0_pos.dimension = 0
    mock_dim0_pos.dimension_value = ["0215"]
    mock_dim0_pos.human_readable_log = "Dim 0 reporting 21.5"
    sensor.handle_event(mock_dim0_pos)
    assert sensor._attr_native_value == 21.5

    mock_dim0_neg = MagicMock(spec=OWNHeatingEvent)
    mock_dim0_neg.message_type = None
    mock_dim0_neg.dimension = 0
    mock_dim0_neg.dimension_value = ["1045"]
    mock_dim0_neg.human_readable_log = "Dim 0 reporting -4.5"
    sensor.handle_event(mock_dim0_neg)
    assert sensor._attr_native_value == -4.5

    mock_dim0_invalid = MagicMock(spec=OWNHeatingEvent)
    mock_dim0_invalid.message_type = None
    mock_dim0_invalid.dimension = 0
    mock_dim0_invalid.dimension_value = ["invalid"]
    sensor.handle_event(mock_dim0_invalid)
    assert sensor._attr_native_value == -4.5

    # Unhandled dimension returns True
    unhandled = MagicMock(spec=OWNHeatingEvent)
    unhandled.message_type = "other"
    unhandled.dimension = 20
    assert sensor.handle_event(unhandled) is True
