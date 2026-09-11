"""Test generalized state restoration across MyHOME entity types."""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate.const import HVACMode
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import State

from custom_components.myhome.binary_sensor import (
    MyHOMEAuxiliary,
    MyHOMEDryContact,
    MyHOMEMotionSensor,
)
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.sensor import (
    MyHOMEEnergySensor,
    MyHOMEIlluminanceSensor,
    MyHOMEPowerSensor,
    MyHOMETemperatureSensor,
)
from custom_components.myhome.switch import MyHOMESwitch


@pytest.fixture
def mock_gateway():
    """Create a mock gateway."""
    gw = MagicMock()
    gw.mac = "AA:BB:CC:DD:EE:FF"
    gw.log_id = "[Test Gateway]"
    gw.availability_signal = "myhome_avail_sig"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


@pytest.mark.asyncio
async def test_switch_state_restoration_on_and_off(hass, mock_gateway):
    """Test MyHOMESwitch restores previous ON/OFF state from HA storage."""
    # Test restoring 'on'
    switch_on = MyHOMESwitch(
        hass=hass,
        name="Switch 1",
        entity_name="Switch 1",
        icon=None,
        icon_on=None,
        device_id="11",
        who="1",
        where="11",
        interface=None,
        device_class=SwitchDeviceClass.SWITCH,
        manufacturer="BTicino",
        model="Relay",
        gateway=mock_gateway,
    )
    switch_on.async_get_last_state = AsyncMock(
        return_value=State("switch.switch_1", STATE_ON)
    )
    await switch_on.async_added_to_hass()
    assert switch_on.is_on is True

    # Test restoring 'off'
    switch_off = MyHOMESwitch(
        hass=hass,
        name="Switch 2",
        entity_name="Switch 2",
        icon=None,
        icon_on=None,
        device_id="12",
        who="1",
        where="12",
        interface=None,
        device_class=SwitchDeviceClass.SWITCH,
        manufacturer="BTicino",
        model="Relay",
        gateway=mock_gateway,
    )
    switch_off.async_get_last_state = AsyncMock(
        return_value=State("switch.switch_2", STATE_OFF)
    )
    await switch_off.async_added_to_hass()
    assert switch_off.is_on is False


@pytest.mark.asyncio
async def test_climate_state_restoration(hass, mock_gateway):
    """Test MyHOMEClimate restores HVAC mode and target temperature."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Living Room",
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
    last_state = State(
        "climate.living_room",
        HVACMode.HEAT,
        {"temperature": 21.5},
    )
    climate.async_get_last_state = AsyncMock(return_value=last_state)
    await climate.async_added_to_hass()

    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.target_temperature == 21.5


@pytest.mark.asyncio
async def test_binary_sensor_state_restoration(hass, mock_gateway):
    """Test dry contact, auxiliary, and motion sensors restore previous states."""
    # 1. Dry contact
    dry = MyHOMEDryContact(
        hass=hass,
        name="Window",
        entity_name="Window",
        device_id="25-1",
        who="25",
        where="1",
        inverted=False,
        device_class=BinarySensorDeviceClass.WINDOW,
        manufacturer="BTicino",
        model="Dry Contact",
        gateway=mock_gateway,
    )
    dry.async_get_last_state = AsyncMock(
        return_value=State("binary_sensor.window", STATE_ON)
    )
    await dry.async_added_to_hass()
    assert dry.is_on is True

    # 2. Auxiliary
    aux = MyHOMEAuxiliary(
        hass=hass,
        name="Aux Sensor",
        entity_name="Aux Sensor",
        device_id="9-1",
        who="9",
        where="1",
        inverted=False,
        device_class=None,
        manufacturer="BTicino",
        model="Aux",
        gateway=mock_gateway,
    )
    aux.async_get_last_state = AsyncMock(
        return_value=State("binary_sensor.aux_sensor", STATE_ON)
    )
    await aux.async_added_to_hass()
    assert aux.is_on is True

    # 3. Motion Sensor
    motion = MyHOMEMotionSensor(
        hass=hass,
        name="Motion",
        entity_name="Motion",
        device_id="1-15",
        who="1",
        where="15",
        inverted=False,
        device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="BTicino",
        model="PIR",
        gateway=mock_gateway,
    )
    motion.async_get_last_state = AsyncMock(
        return_value=State("binary_sensor.motion", STATE_ON)
    )
    await motion.async_added_to_hass()
    assert motion.is_on is True

    # 4. Binary sensor off state
    dry_off = MyHOMEDryContact(
        hass=hass,
        name="Window 2",
        entity_name="Window 2",
        device_id="25-2",
        who="25",
        where="2",
        inverted=False,
        device_class=BinarySensorDeviceClass.WINDOW,
        manufacturer="BTicino",
        model="Dry Contact",
        gateway=mock_gateway,
    )
    dry_off.async_get_last_state = AsyncMock(
        return_value=State("binary_sensor.window_2", STATE_OFF)
    )
    await dry_off.async_added_to_hass()
    assert dry_off.is_on is False


@pytest.mark.asyncio
async def test_sensors_state_restoration(hass, mock_gateway):
    """Test numeric values are properly restored for energy, power, temperature, and illuminance sensors."""
    # 1. Energy sensor (total increasing)
    energy = MyHOMEEnergySensor(
        hass=hass,
        name="Main Meter",
        device_id="18-1",
        who="18",
        where="1",
        entity_specific_id="total-energy",
        device_class="energy",
        manufacturer="BTicino",
        model="Energy Meter",
        gateway=mock_gateway,
    )
    energy.async_get_last_state = AsyncMock(
        return_value=State("sensor.main_meter_energy", "12345.6")
    )
    await energy.async_added_to_hass()
    assert energy.native_value == 12345.6
    assert isinstance(energy.native_value, float)

    # 2. Power sensor (measurement)
    power = MyHOMEPowerSensor(
        hass=hass,
        name="Main Meter",
        device_id="18-1",
        who="18",
        where="1",
        device_class="power",
        manufacturer="BTicino",
        model="Energy Meter",
        gateway=mock_gateway,
    )
    power.async_get_last_state = AsyncMock(
        return_value=State("sensor.main_meter_power", "450.2")
    )
    await power.async_added_to_hass()
    assert power.native_value == 450.2

    # 3. Temperature sensor
    temp = MyHOMETemperatureSensor(
        hass=hass,
        name="Zone 1 Temp",
        device_id="4-1",
        who="4",
        where="1",
        device_class="temperature",
        manufacturer="BTicino",
        model="Probe",
        gateway=mock_gateway,
    )
    temp.async_get_last_state = AsyncMock(
        return_value=State("sensor.zone_1_temp_temperature", "22.4")
    )
    await temp.async_added_to_hass()
    assert temp.native_value == 22.4

    # 4. Illuminance sensor
    lux = MyHOMEIlluminanceSensor(
        hass=hass,
        name="Sensor Lux",
        device_id="1-15",
        who="1",
        where="15",
        device_class="illuminance",
        manufacturer="BTicino",
        model="PIR Lux",
        gateway=mock_gateway,
    )
    lux.async_get_last_state = AsyncMock(
        return_value=State("sensor.sensor_lux_illuminance", "350")
    )
    await lux.async_added_to_hass()
    assert lux.native_value == 350.0

    # 5. Non-numeric sensor state fallback
    str_sensor = MyHOMEIlluminanceSensor(
        hass=hass,
        name="Sensor Status",
        device_id="1-16",
        who="1",
        where="16",
        device_class="illuminance",
        manufacturer="BTicino",
        model="PIR Lux",
        gateway=mock_gateway,
    )
    str_sensor.async_get_last_state = AsyncMock(
        return_value=State("sensor.sensor_status", "calibrating")
    )
    await str_sensor.async_added_to_hass()
    assert str_sensor.native_value == "calibrating"
