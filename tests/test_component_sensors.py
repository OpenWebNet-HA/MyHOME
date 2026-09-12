from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import UnitOfPower, UnitOfTemperature
from OWNd.message import (
    MESSAGE_TYPE_ACTIVE_POWER,
    MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION,
    MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION,
    MESSAGE_TYPE_ENERGY_TOTALIZER,
    MESSAGE_TYPE_ILLUMINANCE,
    MESSAGE_TYPE_MAIN_TEMPERATURE,
    MESSAGE_TYPE_SECONDARY_TEMPERATURE,
)

from custom_components.myhome.binary_sensor import (
    MyHOMEAuxiliary,
    MyHOMEMotionSensor,
)
from custom_components.myhome.sensor import (
    MyHOMEEnergySensor,
    MyHOMEIlluminanceSensor,
    MyHOMEPowerSensor,
    MyHOMETemperatureSensor,
)


@pytest.fixture
def mock_gateway():
    gateway = MagicMock()
    gateway.mac = "01:02:03:04:05:06"
    gateway.log_id = "[Test Gateway]"
    return gateway

@pytest.fixture
def mock_hass():
    hass = MagicMock()
    hass.data = {"myhome": {"01:02:03:04:05:06": {"platforms": {"sensor": {}, "binary_sensor": {}}}}}
    return hass


class TestSensorsCoverage:

    @pytest.mark.asyncio
    async def test_power_sensor(self, mock_hass, mock_gateway):
        mock_gateway.send = AsyncMock()
        sensor = MyHOMEPowerSensor(
            hass=mock_hass,
            name="Test Pwr",
            device_id="sensor_pwr",
            who="18",
            where="51",
            device_class="power",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor.name == "Test Pwr Power"
        assert sensor.unique_id == "01:02:03:04:05:06-sensor_pwr-power"
        assert sensor.native_unit_of_measurement == UnitOfPower.WATT
        assert sensor.extra_state_attributes == {"Sensor": "(5)1"}

        # async_added_to_hass with valid device dict
        device_dict = {}
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_pwr"] = device_dict
        await sensor.async_added_to_hass()
        assert device_dict["entities"]["power"] is sensor

        # async_will_remove_from_hass
        await sensor.async_will_remove_from_hass()
        assert "power" not in device_dict["entities"]

        # async_added_to_hass and async_will_remove_from_hass with missing/invalid device dict (exception safety)
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_pwr"] = None
        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        # async_update is a no-op
        await sensor.async_update()

        # start_sending_instant_power
        await sensor.start_sending_instant_power(120)
        mock_gateway.send.assert_called_once()

        # handle_event: unhandled type returns True
        unhandled_msg = MagicMock()
        unhandled_msg.message_type = "other_type"
        assert sensor.handle_event(unhandled_msg) is True

        # handle_event: active power
        sensor.async_schedule_update_ha_state = MagicMock()
        power_msg = MagicMock()
        power_msg.message_type = MESSAGE_TYPE_ACTIVE_POWER
        power_msg.active_power = 320.5
        power_msg.human_readable_log = "mock active power"
        sensor.handle_event(power_msg)
        assert sensor._attr_native_value == 320.5
        sensor.async_schedule_update_ha_state.assert_called_once()

        # handle_event: RuntimeError safely caught
        sensor.async_schedule_update_ha_state.side_effect = RuntimeError("state update failed")
        sensor.handle_event(power_msg)

    @pytest.mark.asyncio
    async def test_energy_sensor(self, mock_hass, mock_gateway):
        mock_gateway.send_status_request = AsyncMock()

        # 1. Total energy
        sensor_total = MyHOMEEnergySensor(
            hass=mock_hass,
            name="Test En",
            device_id="sensor_energy",
            who="18",
            where="51",
            entity_specific_id="total-energy",
            device_class="energy",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor_total.name == "Test En Energy"
        assert sensor_total.entity_registry_enabled_default is True

        # 2. Daily energy
        sensor_daily = MyHOMEEnergySensor(
            hass=mock_hass,
            name="Test En",
            device_id="sensor_energy",
            who="18",
            where="51",
            entity_specific_id="daily-energy",
            device_class="energy",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor_daily.name == "Test En Energy (today)"
        assert sensor_daily.entity_registry_enabled_default is False

        # 3. Monthly energy
        sensor_monthly = MyHOMEEnergySensor(
            hass=mock_hass,
            name="Test En",
            device_id="sensor_energy",
            who="18",
            where="51",
            entity_specific_id="monthly-energy",
            device_class="energy",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor_monthly.name == "Test En Energy (current month)"
        assert sensor_monthly.entity_registry_enabled_default is False

        # 4. Custom entity_specific_id
        sensor_custom = MyHOMEEnergySensor(
            hass=mock_hass,
            name="Test En",
            device_id="sensor_energy",
            who="18",
            where="51",
            entity_specific_id="custom_energy",
            device_class="energy",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor_custom.name == "Test En Custom energy"
        assert sensor_custom.entity_registry_enabled_default is True

        # async_added_to_hass and async_will_remove_from_hass
        device_dict = {}
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_energy"] = device_dict
        await sensor_total.async_added_to_hass()
        assert device_dict["entities"]["total-energy"] is sensor_total

        await sensor_total.async_will_remove_from_hass()
        assert "total-energy" not in device_dict["entities"]

        # Error handling during add/remove
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_energy"] = None
        await sensor_total.async_added_to_hass()
        await sensor_total.async_will_remove_from_hass()

        # async_update for each energy type
        mock_gateway.send_status_request.reset_mock()
        await sensor_total.async_update()
        assert mock_gateway.send_status_request.call_count == 1

        await sensor_monthly.async_update()
        assert mock_gateway.send_status_request.call_count == 2

        await sensor_daily.async_update()
        assert mock_gateway.send_status_request.call_count == 3

        # handle_event: unhandled type returns True
        unhandled_msg = MagicMock()
        unhandled_msg.message_type = "other_type"
        assert sensor_total.handle_event(unhandled_msg) is True

        # handle_event: totalizer
        sensor_total.async_schedule_update_ha_state = MagicMock()
        msg_tot = MagicMock(message_type=MESSAGE_TYPE_ENERGY_TOTALIZER, total_consumption=1500.0, human_readable_log="mock energy")
        sensor_total.handle_event(msg_tot)
        assert sensor_total._attr_native_value == 1500.0

        # handle_event: monthly
        sensor_monthly.async_schedule_update_ha_state = MagicMock()
        msg_month = MagicMock(message_type=MESSAGE_TYPE_CURRENT_MONTH_CONSUMPTION, current_month_partial_consumption=250.0, human_readable_log="mock month")
        sensor_monthly.handle_event(msg_month)
        assert sensor_monthly._attr_native_value == 250.0

        # handle_event: daily
        sensor_daily.async_schedule_update_ha_state = MagicMock()
        msg_day = MagicMock(message_type=MESSAGE_TYPE_CURRENT_DAY_CONSUMPTION, current_day_partial_consumption=15.0, human_readable_log="mock day")
        sensor_daily.handle_event(msg_day)
        assert sensor_daily._attr_native_value == 15.0

        # handle_event: RuntimeError caught safely
        sensor_daily.async_schedule_update_ha_state.side_effect = RuntimeError("state error")
        sensor_daily.handle_event(msg_day)

    @pytest.mark.asyncio
    async def test_temperature_sensor(self, mock_hass, mock_gateway):
        mock_gateway.send_status_request = AsyncMock()
        sensor = MyHOMETemperatureSensor(
            hass=mock_hass,
            name="Test Temp",
            device_id="sensor_temp",
            who="4",
            where="51",
            device_class="temperature",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor.name == "Test Temp Temperature"
        assert sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert sensor.extra_state_attributes == {"Sensor": "(5)1"}

        # async_added_to_hass & async_will_remove_from_hass
        device_dict = {}
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_temp"] = device_dict
        await sensor.async_added_to_hass()
        assert device_dict["entities"]["temperature"] is sensor
        await sensor.async_will_remove_from_hass()
        assert "temperature" not in device_dict["entities"]

        # Error safety
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_temp"] = None
        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        # async_update
        mock_gateway.send_status_request.reset_mock()
        await sensor.async_update()
        mock_gateway.send_status_request.assert_called_once()

        # handle_event: unhandled type returns True
        unhandled_msg = MagicMock()
        unhandled_msg.message_type = "other_type"
        assert sensor.handle_event(unhandled_msg) is True

        # handle_event: main temperature
        sensor.async_schedule_update_ha_state = MagicMock()
        msg_main = MagicMock(message_type=MESSAGE_TYPE_MAIN_TEMPERATURE, main_temperature=22.5, human_readable_log="mock temp")
        sensor.handle_event(msg_main)
        assert sensor._attr_native_value == 22.5

        # handle_event: main temperature RuntimeError
        sensor.async_schedule_update_ha_state.side_effect = RuntimeError("update error")
        sensor.handle_event(msg_main)

        # handle_event: secondary temperature
        sensor.async_schedule_update_ha_state = MagicMock()
        msg_sec = MagicMock(message_type=MESSAGE_TYPE_SECONDARY_TEMPERATURE, secondary_temperature=[None, 19.8], human_readable_log="mock sec temp")
        sensor.handle_event(msg_sec)
        assert sensor._attr_native_value == 19.8

        # handle_event: secondary temperature RuntimeError
        sensor.async_schedule_update_ha_state.side_effect = RuntimeError("update error")
        sensor.handle_event(msg_sec)

    @pytest.mark.asyncio
    async def test_illuminance_sensor(self, mock_hass, mock_gateway):
        mock_gateway.send_status_request = AsyncMock()
        sensor = MyHOMEIlluminanceSensor(
            hass=mock_hass,
            name="Test Illum",
            device_id="sensor_lux",
            who="1",
            where="12",
            device_class="illuminance",
            manufacturer="Bticino",
            model="Meter",
            gateway=mock_gateway,
        )
        assert sensor.name == "Test Illum Illuminance"
        assert sensor.extra_state_attributes == {"A": "1", "PL": "2"}

        # async_added_to_hass & async_will_remove_from_hass
        device_dict = {}
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_lux"] = device_dict
        await sensor.async_added_to_hass()
        assert device_dict["entities"]["illuminance"] is sensor
        await sensor.async_will_remove_from_hass()
        assert "illuminance" not in device_dict["entities"]

        # Error safety
        mock_hass.data["myhome"]["01:02:03:04:05:06"]["platforms"]["sensor"]["sensor_lux"] = None
        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        # async_update
        mock_gateway.send_status_request.reset_mock()
        await sensor.async_update()
        mock_gateway.send_status_request.assert_called_once()

        # handle_event: unhandled type returns True
        unhandled_msg = MagicMock()
        unhandled_msg.message_type = "other_type"
        assert sensor.handle_event(unhandled_msg) is True

        # handle_event: illuminance value
        sensor.async_schedule_update_ha_state = MagicMock()
        msg_lux = MagicMock(message_type=MESSAGE_TYPE_ILLUMINANCE, illuminance=450, human_readable_log="mock lux")
        sensor.handle_event(msg_lux)
        assert sensor._attr_native_value == 450

        # handle_event: RuntimeError safely caught
        sensor.async_schedule_update_ha_state.side_effect = RuntimeError("update error")
        sensor.handle_event(msg_lux)


class TestBinarySensorsCoverage:

    def test_auxiliary(self, mock_hass, mock_gateway):
        sensor = MyHOMEAuxiliary(
            hass=mock_hass,
            name="Test Aux",
            entity_name="test_aux",
            device_id="aux_1",
            who="25",
            where="51",
            inverted=False,
            device_class="window",
            manufacturer="Bticino",
            model="Sensor",
            gateway=mock_gateway
        )
        sensor.async_schedule_update_ha_state = MagicMock()
        msg = MagicMock(is_on=True, human_readable_log="o")
        sensor.handle_event(msg)
        assert sensor._attr_is_on is True

    def test_motion(self, mock_hass, mock_gateway):
        sensor = MyHOMEMotionSensor(
            hass=mock_hass,
            name="Test Motion",
            entity_name="test_mot",
            device_id="mot_1",
            who="25",
            where="51",
            inverted=False,
            device_class="motion",
            manufacturer="Bticino",
            model="Sensor",
            gateway=mock_gateway
        )
        sensor.async_schedule_update_ha_state = MagicMock()
        sensor.async_write_ha_state = MagicMock()
        msg = MagicMock()
        msg.message_type = "motion_detected"
        msg.motion = True
        msg.human_readable_log = "m"
        sensor.handle_event(msg)
        assert sensor._attr_is_on is True
