"""Tests for MyHOME HA platform entities handle_event methods using lightweight mocking."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import (
    HVACMode,
)
from OWNd.message import (
    OWNEvent,
)


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

@pytest.fixture
def mock_entity_base_init():
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield

# ── Climate Entity ─────────────────────────────────────────────────────────

class TestClimateEntity:

    @pytest.fixture
    def climate(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.climate import MyHOMEClimate
        c = MyHOMEClimate(
            hass=mock_hass,
            name="Climate 1",
            device_id="4#01",
            who="4",
            where="01",
            heating=True,
            cooling=True,
            fan=False,
            standalone=False,
            central=False,
            manufacturer="BTicino",
            model="Thermostat",
            gateway=mock_gateway,
        )
        c.async_schedule_update_ha_state = MagicMock()
        return c

    def test_handle_event_mode_off(self, climate):
        msg = OWNEvent.parse("*4*303*01##")
        climate.handle_event(msg)
        assert climate._attr_hvac_mode == HVACMode.OFF
        climate.async_schedule_update_ha_state.assert_called()

    def test_handle_event_mode_heat(self, climate):
        msg = OWNEvent.parse("*4*1*01##")
        climate.handle_event(msg)
        assert climate._attr_hvac_mode == HVACMode.HEAT

    def test_handle_event_mode_cool(self, climate):
        msg = OWNEvent.parse("*4*0*01##")
        climate.handle_event(msg)
        assert climate._attr_hvac_mode == HVACMode.COOL

    def test_handle_event_main_temperature(self, climate):
        msg = OWNEvent.parse("*#4*01*0*0225##")
        climate.handle_event(msg)
        assert climate._attr_current_temperature == 22.5

    def test_handle_event_target_temperature(self, climate):
        msg = OWNEvent.parse("*#4*01*14*0210##")
        climate.handle_event(msg)
        assert climate.target_temperature == 21.0

    @pytest.mark.asyncio
    async def test_async_set_temperature(self, climate):
        await climate.async_set_temperature(temperature=23.5)
        climate._gateway_handler.send.assert_called_once()
        sent_cmd = climate._gateway_handler.send.call_args[0][0]
        assert "0235" in str(sent_cmd)

    @pytest.mark.asyncio
    async def test_async_set_hvac_mode(self, climate):
        climate._target_temperature = 22.0
        await climate.async_set_hvac_mode(HVACMode.HEAT)
        climate._gateway_handler.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_update(self, climate):
        await climate.async_update()
        climate._gateway_handler.send_status_request.assert_called()

# ── Binary Sensor Entities ──────────────────────────────────────────────────

class TestBinarySensorEntity:

    @pytest.fixture
    def dry_contact(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.binary_sensor import MyHOMEDryContact
        s = MyHOMEDryContact(
            hass=mock_hass,
            name="Door",
            entity_name="Door",
            device_id="25#31",
            who="25",
            where="31",
            device_class="door",
            inverted=False,
            manufacturer="B",
            model="M",
            gateway=mock_gateway,
        )
        s.async_schedule_update_ha_state = MagicMock()
        return s

    def test_dry_contact_handle_event(self, dry_contact):
        msg = MagicMock(is_on=True, human_readable_log="o")
        dry_contact.handle_event(msg)
        assert dry_contact._attr_is_on is True

        msg2 = MagicMock(is_on=False, human_readable_log="c")
        dry_contact.handle_event(msg2)
        assert dry_contact._attr_is_on is False

# ── Sensor Entities ────────────────────────────────────────────────────────

class TestSensorEntity:

    @pytest.fixture
    def power_sensor(self, mock_hass, mock_gateway, mock_entity_base_init):
        from custom_components.myhome.sensor import MyHOMEPowerSensor
        s = MyHOMEPowerSensor(
            hass=mock_hass,
            name="Power",
            device_id="18#51",
            who="18",
            where="51",
            device_class="power",
            manufacturer="B",
            model="M",
            gateway=mock_gateway,
        )
        s.async_schedule_update_ha_state = MagicMock()
        return s

    def test_power_sensor_handle_event(self, power_sensor):
        msg = MagicMock()
        msg.message_type = "active_power"
        msg.active_power = 113.0
        msg.human_readable_log = "mock"
        power_sensor.handle_event(msg)
        assert power_sensor._attr_native_value == 113.0

# ── Gateway Connection ──────────────────────────────────────────────────────

class TestGatewayConnection:
    @pytest.mark.asyncio
    async def test_test_connection_dns_failure(self, mock_gateway):
        from OWNd.connection import OWNSession
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError()):
            mock_gateway.address = "invalid_host"
            mock_gateway.port = 20000
            session = OWNSession(gateway=mock_gateway, logger=MagicMock())
            response = await session.test_connection()
            assert response == {
                "Success": False,
                "Message": "connection_error",
            }
