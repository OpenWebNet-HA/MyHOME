"""Test the MyHOME climate component."""
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import UnitOfTemperature
from OWNd.message import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL,
    CLIMATE_MODE_HEAT,
    CLIMATE_MODE_OFF,
    MESSAGE_TYPE_ACTION,
    MESSAGE_TYPE_LOCAL_OFFSET,
    MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE,
    MESSAGE_TYPE_MAIN_HUMIDITY,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_MODE,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_TARGET_TEMPERATURE,
    OWNHeatingEvent,
)

from custom_components.myhome.climate import (
    MyHOMEClimate,
    async_setup_entry,
    async_unload_entry,
)


async def test_setup_and_unload_entry(hass):
    """Test setup and unload of the climate platform."""
    mock_gateway = MagicMock()

    hass.data = {
        "myhome": {
            "mac": {
                "platforms": {
                    "climate": {
                        "device_1": {
                            "who": "4",
                            "zone": "1",
                            "name": "Zone 1",
                            "heat": True,
                            "cool": True,
                            "fan": False,
                            "standalone": True,
                            "central": False,
                            "manufacturer": "BTicino",
                            "model": "F454",
                        }
                    }
                },
                "entity": mock_gateway,
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}

    async_add_entities = MagicMock()
    await async_setup_entry(hass, config_entry, async_add_entities)

    async_add_entities.assert_called_once()
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 1
    climate_entity = entities[0]

    assert isinstance(climate_entity, MyHOMEClimate)
    assert climate_entity.device_info["name"] == "Zone 1"

    # Test unload
    await async_unload_entry(hass, config_entry)
    assert "device_1" not in hass.data["myhome"]["mac"]["platforms"]["climate"]

async def test_climate_properties_and_hvac_modes(hass):
    """Test climate entity properties and set_hvac_mode."""
    gateway = MagicMock()
    gateway.send = AsyncMock()

    climate = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )

    assert climate.temperature_unit == UnitOfTemperature.CELSIUS
    assert HVACMode.AUTO in climate.hvac_modes
    assert HVACMode.HEAT in climate.hvac_modes
    assert HVACMode.COOL in climate.hvac_modes
    assert HVACMode.OFF in climate.hvac_modes

    # Test set_hvac_mode OFF
    await climate.async_set_hvac_mode(HVACMode.OFF)
    gateway.send.assert_called_once()
    assert str(gateway.send.call_args[0][0]) == "*4*303*1##"
    gateway.send.reset_mock()

    # Test set_hvac_mode AUTO
    await climate.async_set_hvac_mode(HVACMode.AUTO)
    gateway.send.assert_called_once()
    assert str(gateway.send.call_args[0][0]) == "*4*311*1##"
    gateway.send.reset_mock()

    # Test set_hvac_mode HEAT
    climate._target_temperature = 22.0
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    gateway.send.assert_called_once()
    # It sends set_temperature with mode HEAT
    assert str(gateway.send.call_args[0][0]) == "*#4*1*#14*0220*1##"
    gateway.send.reset_mock()

    # Test set_hvac_mode COOL
    await climate.async_set_hvac_mode(HVACMode.COOL)
    gateway.send.assert_called_once()
    # If _target_temperature is set, it will send the set_temperature command for COOL too.
    assert str(gateway.send.call_args[0][0]) == "*#4*1*#14*0220*2##"
    gateway.send.reset_mock()

async def test_climate_set_temperature(hass):
    """Test setting temperature."""
    gateway = MagicMock()
    gateway.send = AsyncMock()

    climate = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )

    # Set temperature when in HEAT mode
    climate._attr_hvac_mode = HVACMode.HEAT
    await climate.async_set_temperature(temperature=23.0)
    gateway.send.assert_called_once()
    assert str(gateway.send.call_args[0][0]) == "*#4*1*#14*0230*1##"
    gateway.send.reset_mock()

    # Set temperature when in COOL mode
    climate._attr_hvac_mode = HVACMode.COOL
    await climate.async_set_temperature(temperature=24.0)
    gateway.send.assert_called_once()
    assert str(gateway.send.call_args[0][0]) == "*#4*1*#14*0240*2##"
    gateway.send.reset_mock()

    # Set temperature when in AUTO mode
    climate._attr_hvac_mode = HVACMode.AUTO
    await climate.async_set_temperature(temperature=21.0)
    gateway.send.assert_called_once()
    assert str(gateway.send.call_args[0][0]) == "*#4*1*#14*0210*3##"
    gateway.send.reset_mock()

async def test_climate_handle_events(hass):
    """Test event handling."""
    gateway = MagicMock()
    gateway.log_id = "test"

    climate = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )
    climate.async_schedule_update_ha_state = MagicMock()

    # Event: MAIN_TEMPERATURE
    event = MagicMock(spec=OWNHeatingEvent)
    event.message_type = MESSAGE_TYPE_MAIN_TEMPERATURE
    event.main_temperature = 22.5
    climate.handle_event(event)
    assert climate.current_temperature == 22.5

    # Event: MAIN_HUMIDITY
    event.message_type = MESSAGE_TYPE_MAIN_HUMIDITY
    event.main_humidity = 55.0
    climate.handle_event(event)
    assert climate.current_humidity == 55.0

    # Event: TARGET_TEMPERATURE
    event.message_type = MESSAGE_TYPE_TARGET_TEMPERATURE
    event.set_temperature = 21.0
    climate.handle_event(event)
    assert climate.target_temperature == 21.0

    # Event: LOCAL_OFFSET
    event.message_type = MESSAGE_TYPE_LOCAL_OFFSET
    event.local_offset = 1
    climate.handle_event(event)
    assert climate._local_target_temperature == 22.0
    assert climate.target_temperature == 22.0

    # Event: LOCAL_TARGET_TEMPERATURE
    event.message_type = MESSAGE_TYPE_LOCAL_TARGET_TEMPERATURE
    event.local_set_temperature = 23.0
    climate.handle_event(event)
    assert climate._target_temperature == 22.0 # local_set - local_offset (23-1)

    # Event: MODE (HEAT)
    event.message_type = MESSAGE_TYPE_MODE
    event.mode = CLIMATE_MODE_HEAT
    climate.handle_event(event)
    assert climate.hvac_mode == HVACMode.HEAT

    # Event: MODE (OFF)
    event.mode = CLIMATE_MODE_OFF
    climate.handle_event(event)
    assert climate.hvac_mode == HVACMode.OFF
    assert climate.hvac_action == HVACAction.OFF

    # Event: MODE_TARGET (AUTO with set temp)
    event.message_type = MESSAGE_TYPE_MODE_TARGET
    event.mode = CLIMATE_MODE_AUTO
    event.set_temperature = 22.5
    climate.handle_event(event)
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate._target_temperature == 22.5

    # Event: ACTION
    event.message_type = MESSAGE_TYPE_ACTION
    event.is_active.return_value = True
    event.is_heating.return_value = True
    climate.handle_event(event)
    assert climate.hvac_action == HVACAction.HEATING

async def test_climate_async_update(hass):
    """Test async update."""
    gateway = MagicMock()
    gateway.send_status_request = AsyncMock()

    climate = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )

    await climate.async_update()
    gateway.send_status_request.assert_called_once()


async def test_setup_and_unload_entry_platform_not_configured(hass):
    """Test setup and unload when climate platform is not configured."""
    hass.data = {
        "myhome": {
            "mac": {
                "platforms": {},
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}

    assert await async_setup_entry(hass, config_entry, MagicMock()) is True
    assert await async_unload_entry(hass, config_entry) is True


async def test_climate_edge_cases_and_properties(hass):
    """Test target_temperature fallback, None target temperature in set_hvac_mode, and default kwargs."""
    gateway = MagicMock()
    gateway.send = AsyncMock()

    climate = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )

    # target_temperature fallback when _local_target_temperature is None
    climate._local_target_temperature = None
    climate._target_temperature = 21.5
    assert climate.target_temperature == 21.5

    # async_set_hvac_mode when _target_temperature is None does not send command for HEAT or COOL
    climate._target_temperature = None
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_set_hvac_mode(HVACMode.COOL)
    gateway.send.assert_not_called()

    # async_set_temperature without temperature kwarg uses _local_target_temperature
    climate._local_target_temperature = 22.0
    climate._local_offset = 1.0
    climate._attr_hvac_mode = HVACMode.HEAT
    await climate.async_set_temperature()
    gateway.send.assert_called_once()
    assert str(gateway.send.call_args[0][0]) == "*#4*1*#14*0210*1##"


async def test_climate_handle_events_mode_and_target_transitions(hass):
    """Test mode and mode_target event branches with idle transitions."""
    gateway = MagicMock()
    gateway.log_id = "test"

    climate = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )
    climate.async_schedule_update_ha_state = MagicMock()

    # MESSAGE_TYPE_LOCAL_OFFSET when _target_temperature is None
    climate._target_temperature = None
    event_offset = MagicMock(spec=OWNHeatingEvent)
    event_offset.message_type = MESSAGE_TYPE_LOCAL_OFFSET
    event_offset.local_offset = 2
    climate.handle_event(event_offset)
    assert climate._local_offset == 2
    assert climate._local_target_temperature is None

    # MESSAGE_TYPE_MODE transitions when action was OFF
    event_mode = MagicMock(spec=OWNHeatingEvent)
    event_mode.message_type = MESSAGE_TYPE_MODE

    # AUTO transition
    climate._attr_hvac_action = HVACAction.OFF
    event_mode.mode = CLIMATE_MODE_AUTO
    climate.handle_event(event_mode)
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate.hvac_action == HVACAction.IDLE

    # COOL transition
    climate._attr_hvac_action = HVACAction.OFF
    event_mode.mode = CLIMATE_MODE_COOL
    climate.handle_event(event_mode)
    assert climate.hvac_mode == HVACMode.COOL
    assert climate.hvac_action == HVACAction.IDLE

    # HEAT transition
    climate._attr_hvac_action = HVACAction.OFF
    event_mode.mode = CLIMATE_MODE_HEAT
    climate.handle_event(event_mode)
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.hvac_action == HVACAction.IDLE

    # MESSAGE_TYPE_MODE_TARGET transitions
    event_target = MagicMock(spec=OWNHeatingEvent)
    event_target.message_type = MESSAGE_TYPE_MODE_TARGET
    event_target.set_temperature = 20.0

    # AUTO with action OFF
    climate._attr_hvac_action = HVACAction.OFF
    event_target.mode = CLIMATE_MODE_AUTO
    climate.handle_event(event_target)
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate.hvac_action == HVACAction.IDLE

    # COOL with action OFF
    climate._attr_hvac_action = HVACAction.OFF
    event_target.mode = CLIMATE_MODE_COOL
    climate.handle_event(event_target)
    assert climate.hvac_mode == HVACMode.COOL
    assert climate.hvac_action == HVACAction.IDLE

    # HEAT with action OFF
    climate._attr_hvac_action = HVACAction.OFF
    event_target.mode = CLIMATE_MODE_HEAT
    climate.handle_event(event_target)
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.hvac_action == HVACAction.IDLE

    # OFF mode
    event_target.mode = CLIMATE_MODE_OFF
    climate.handle_event(event_target)
    assert climate.hvac_mode == HVACMode.OFF
    assert climate.hvac_action == HVACAction.OFF


async def test_climate_handle_events_action_variations_and_runtime_error(hass):
    """Test action variations across single/dual heating/cooling and runtime error handling."""
    gateway = MagicMock()
    gateway.log_id = "test"

    # Dual heating + cooling
    climate_dual = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_1",
        who="4",
        where="1",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )
    climate_dual.async_schedule_update_ha_state = MagicMock()

    event = MagicMock(spec=OWNHeatingEvent)
    event.message_type = MESSAGE_TYPE_ACTION
    event.is_active.return_value = True
    event.is_heating.return_value = False
    event.is_cooling.return_value = True
    climate_dual.handle_event(event)
    assert climate_dual.hvac_action == HVACAction.COOLING

    # Inactive while mode is OFF
    event.is_active.return_value = False
    climate_dual._attr_hvac_mode = HVACMode.OFF
    climate_dual.handle_event(event)
    assert climate_dual.hvac_action == HVACAction.OFF

    # Inactive while mode is AUTO -> IDLE
    climate_dual._attr_hvac_mode = HVACMode.AUTO
    climate_dual.handle_event(event)
    assert climate_dual.hvac_action == HVACAction.IDLE

    # Heating only
    climate_heat = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_2",
        who="4",
        where="2",
        heating=True,
        cooling=False,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )
    climate_heat.async_schedule_update_ha_state = MagicMock()
    event.is_active.return_value = True
    climate_heat.handle_event(event)
    assert climate_heat.hvac_action == HVACAction.HEATING

    # Cooling only
    climate_cool = MyHOMEClimate(
        hass=hass,
        name="Thermostat",
        device_id="device_3",
        who="4",
        where="3",
        heating=False,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="B",
        model="M",
        gateway=gateway,
    )
    climate_cool.async_schedule_update_ha_state = MagicMock()
    event.is_active.return_value = True
    climate_cool.handle_event(event)
    assert climate_cool.hvac_action == HVACAction.COOLING

    # Test RuntimeError catch in async_schedule_update_ha_state
    climate_cool.async_schedule_update_ha_state.side_effect = RuntimeError("State update error")
    climate_cool.handle_event(event)  # Should not raise


async def test_climate_fan_mode_and_attributes(hass):
    """Test fancoil fan modes, attributes, and status query on added to hass."""
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:11:22"
    gateway.log_id = "[Test Gateway]"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()

    climate_fancoil = MyHOMEClimate(
        hass=hass,
        name="Fancoil",
        device_id="device_fan",
        who="4",
        where="5",
        heating=True,
        cooling=True,
        fan=True,
        standalone=False,
        central=False,
        manufacturer="BTicino",
        model="Fancoil Unit",
        gateway=gateway,
    )
    climate_fancoil.hass = hass
    climate_fancoil.async_schedule_update_ha_state = MagicMock()

    assert climate_fancoil.supported_features & ClimateEntityFeature.FAN_MODE
    assert climate_fancoil.fan_modes == ["auto", "low", "medium", "high"]
    assert climate_fancoil.fan_mode == "auto"
    assert climate_fancoil.extra_state_attributes["local_offset"] == 0
    assert climate_fancoil.extra_state_attributes["fan_mode"] == "auto"

    # Test setting fan modes: low (1), medium (2), high (3), auto (0)
    await climate_fancoil.async_set_fan_mode("low")
    assert climate_fancoil.fan_mode == "low"
    assert str(gateway.send.call_args[0][0]) == "*#4*#5*#11*1##"

    await climate_fancoil.async_set_fan_mode("medium")
    assert climate_fancoil.fan_mode == "medium"
    assert str(gateway.send.call_args[0][0]) == "*#4*#5*#11*2##"

    await climate_fancoil.async_set_fan_mode("high")
    assert climate_fancoil.fan_mode == "high"
    assert str(gateway.send.call_args[0][0]) == "*#4*#5*#11*3##"

    await climate_fancoil.async_set_fan_mode("auto")
    assert climate_fancoil.fan_mode == "auto"
    assert str(gateway.send.call_args[0][0]) == "*#4*#5*#11*0##"

    # Unknown fan mode
    gateway.send.reset_mock()
    await climate_fancoil.async_set_fan_mode("turbo")
    gateway.send.assert_not_called()

    # Event handling for fan speeds
    from OWNd.message import MESSAGE_TYPE_FAN_SPEED
    event = MagicMock()
    event.message_type = MESSAGE_TYPE_FAN_SPEED
    event.human_readable_log = "Fan speed event"

    event.fan_speed = 1
    climate_fancoil.handle_event(event)
    assert climate_fancoil.fan_mode == "low"

    event.fan_speed = 2
    climate_fancoil.handle_event(event)
    assert climate_fancoil.fan_mode == "medium"

    event.fan_speed = 3
    climate_fancoil.handle_event(event)
    assert climate_fancoil.fan_mode == "high"

    event.fan_speed = 0
    climate_fancoil.handle_event(event)
    assert climate_fancoil.fan_mode == "auto"

    # Test async_added_to_hass immediately sends status request
    climate_fancoil.async_on_remove = MagicMock()
    await climate_fancoil.async_added_to_hass()
    gateway.send_status_request.assert_awaited_once()
    assert str(gateway.send_status_request.call_args[0][0]) == "*#4*5##"


