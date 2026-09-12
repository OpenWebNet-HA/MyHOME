from unittest.mock import AsyncMock, MagicMock, patch

import homeassistant.helpers.entity_platform as entity_platform
import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    CONF_MAC,
    CONF_NAME,
)
from homeassistant.core import HomeAssistant

from custom_components.myhome.const import (
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITIES,
    CONF_ENTITY,
    CONF_MANUFACTURER,
    CONF_PLATFORMS,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
)
from custom_components.myhome.sensor import (
    MyHOMEIlluminanceSensor,
    MyHOMEPowerSensor,
    MyHOMETemperatureSensor,
    async_setup_entry,
    async_unload_entry,
)


@pytest.fixture
def mock_config_entry():
    entry = MagicMock()
    entry.data = {
        CONF_MAC: "00:11:22:33:44:55",
    }
    return entry

@pytest.fixture
def mock_hass():
    hass = MagicMock(spec=HomeAssistant)

    # Setup standard mocked data structure for the gateway MAC
    hass.data = {
        DOMAIN: {
            "00:11:22:33:44:55": {
                CONF_PLATFORMS: {
                    "sensor": {
                        "sensor_power_1": {
                            CONF_DEVICE_CLASS: SensorDeviceClass.POWER,
                            CONF_ENTITIES: {SensorDeviceClass.POWER: {}},
                            CONF_WHO: "18",
                            CONF_WHERE: "51",
                            CONF_NAME: "Power Sensor",
                            CONF_MANUFACTURER: "Bticino",
                            CONF_DEVICE_MODEL: "Meter",
                        },
                        "sensor_energy_1": {
                            CONF_DEVICE_CLASS: SensorDeviceClass.ENERGY,
                            CONF_ENTITIES: {"total-energy": {}},
                            CONF_WHO: "18",
                            CONF_WHERE: "51",
                            CONF_NAME: "Energy Sensor",
                            CONF_MANUFACTURER: "Bticino",
                            CONF_DEVICE_MODEL: "Meter",
                        }
                    }
                },
                CONF_ENTITY: MagicMock() # Mock Gateway
            }
        }
    }
    return hass

@pytest.mark.asyncio
async def test_async_setup_entry_power_migration(mock_hass, mock_config_entry):
    mock_add_entities = MagicMock()

    platform_token = entity_platform.current_platform.set(MagicMock())
    try:
        with patch("custom_components.myhome.sensor.er.async_get") as mock_er_get:
            # Mock entity registry
            mock_registry = MagicMock()
            mock_registry.async_get_entity_id.return_value = "sensor.some_legacy_id"
            mock_er_get.return_value = mock_registry

            await async_setup_entry(mock_hass, mock_config_entry, mock_add_entities)

            # Registry update should be called because we returned an existing legacy ID
            mock_registry.async_update_entity.assert_called_once_with(
                entity_id="sensor.some_legacy_id",
                new_unique_id=f"sensor_power_1-{SensorDeviceClass.POWER}"
            )

            # We should have added two entities: 1 Power, 1 Energy
            assert mock_add_entities.call_count == 1
            sensors_added = mock_add_entities.call_args[0][0]
            assert len(sensors_added) == 2

            power_sens = [s for s in sensors_added if isinstance(s, MyHOMEPowerSensor)]
            assert len(power_sens) == 1
            assert power_sens[0].name == "Power Sensor Power"
    finally:
        entity_platform.current_platform.reset(platform_token)

@pytest.mark.asyncio
async def test_async_setup_entry_no_migration(mock_hass, mock_config_entry):
    mock_add_entities = MagicMock()

    platform_token = entity_platform.current_platform.set(MagicMock())
    try:
        with patch("custom_components.myhome.sensor.er.async_get") as mock_er_get:
            mock_registry = MagicMock()
            # No legacy entity id exists
            mock_registry.async_get_entity_id.return_value = None
            mock_er_get.return_value = mock_registry

            await async_setup_entry(mock_hass, mock_config_entry, mock_add_entities)

            # Registry update should NOT be called
            mock_registry.async_update_entity.assert_not_called()

            assert mock_add_entities.call_count == 1
            sensors_added = mock_add_entities.call_args[0][0]
            assert len(sensors_added) == 2
    finally:
        entity_platform.current_platform.reset(platform_token)

@pytest.mark.asyncio
async def test_async_setup_entry_no_platform_data(mock_hass, mock_config_entry):
    # Rip out the "sensor" platform from hass data
    del mock_hass.data[DOMAIN]["00:11:22:33:44:55"][CONF_PLATFORMS]["sensor"]

    mock_add_entities = MagicMock()

    result = await async_setup_entry(mock_hass, mock_config_entry, mock_add_entities)

    assert result is True
    mock_add_entities.assert_not_called()


@pytest.mark.asyncio
async def test_async_setup_entry_temperature_illuminance_and_legacy_power(mock_hass, mock_config_entry):
    mock_hass.data[DOMAIN]["00:11:22:33:44:55"][CONF_PLATFORMS]["sensor"] = {
        "sensor_temp_1": {
            CONF_DEVICE_CLASS: SensorDeviceClass.TEMPERATURE,
            CONF_WHO: "4",
            CONF_WHERE: "51",
            CONF_NAME: "Temp Sensor",
            CONF_MANUFACTURER: "Bticino",
            CONF_DEVICE_MODEL: "Meter",
        },
        "sensor_lux_1": {
            CONF_DEVICE_CLASS: SensorDeviceClass.ILLUMINANCE,
            CONF_WHO: "1",
            CONF_WHERE: "12",
            CONF_NAME: "Lux Sensor",
            CONF_MANUFACTURER: "Bticino",
            CONF_DEVICE_MODEL: "Meter",
        },
        "sensor_power_legacy": {
            CONF_DEVICE_CLASS: SensorDeviceClass.POWER,
            CONF_ENTITIES: {"power": {}},
            CONF_WHO: "18",
            CONF_WHERE: "51",
            CONF_NAME: "Legacy Power Sensor",
            CONF_MANUFACTURER: "Bticino",
            CONF_DEVICE_MODEL: "Meter",
        },
    }

    mock_platform = MagicMock()
    platform_token = entity_platform.current_platform.set(mock_platform)
    try:
        with patch("custom_components.myhome.sensor.er.async_get") as mock_er_get:
            mock_registry = MagicMock()
            mock_registry.async_get_entity_id.return_value = None
            mock_er_get.return_value = mock_registry

            mock_add_entities = MagicMock()
            await async_setup_entry(mock_hass, mock_config_entry, mock_add_entities)

            mock_add_entities.assert_called_once()
            added = mock_add_entities.call_args[0][0]
            assert len(added) == 3
            assert any(isinstance(s, MyHOMETemperatureSensor) for s in added)
            assert any(isinstance(s, MyHOMEIlluminanceSensor) for s in added)
            assert any(isinstance(s, MyHOMEPowerSensor) for s in added)

            mock_platform.async_register_entity_service.assert_called_once()
    finally:
        entity_platform.current_platform.reset(platform_token)


@pytest.mark.asyncio
async def test_async_unload_entry(mock_hass, mock_config_entry):

    # 1. Unload when platform present
    assert "sensor_power_1" in mock_hass.data[DOMAIN]["00:11:22:33:44:55"][CONF_PLATFORMS]["sensor"]
    await async_unload_entry(mock_hass, mock_config_entry)
    assert len(mock_hass.data[DOMAIN]["00:11:22:33:44:55"][CONF_PLATFORMS]["sensor"]) == 0

    # 2. Unload when platform absent
    del mock_hass.data[DOMAIN]["00:11:22:33:44:55"][CONF_PLATFORMS]["sensor"]
    result = await async_unload_entry(mock_hass, mock_config_entry)
    assert result is True


@pytest.mark.asyncio
async def test_async_setup_entry_illuminance_registry_and_discovery(hass, mock_config_entry):
    """Test illuminance sensor registry restoration, duplicate skipping, and dynamic discovery."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from OWNd.message import OWNEnergyEvent, OWNEvent, OWNLightingEvent

    mac = mock_config_entry.data[CONF_MAC]
    mock_gateway = MagicMock()
    mock_gateway.mac = mac
    mock_gateway.log_id = "[Test Gateway]"
    mock_gateway.send_status_request = AsyncMock()

    cfg_lux = {
        CONF_DEVICE_CLASS: SensorDeviceClass.ILLUMINANCE,
        CONF_WHO: "1",
        CONF_WHERE: "12",
        CONF_NAME: "Configured Lux",
        CONF_MANUFACTURER: "Bticino",
        CONF_DEVICE_MODEL: "Sensor",
    }
    # Configure an illuminance sensor and an alias pointing to the same dict to hit line 101
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {
                    "sensor": {
                        "sensor_lux_reg": cfg_lux,
                        "12": cfg_lux,
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }

    entry_illum = MagicMock()
    entry_illum.domain = "sensor"
    entry_illum.unique_id = f"{mac}-1-12-illuminance"
    entry_illum.original_device_class = SensorDeviceClass.ILLUMINANCE

    entry_illum_disc = MagicMock()
    entry_illum_disc.domain = "sensor"
    entry_illum_disc.unique_id = f"{mac}-1-14-illuminance"
    entry_illum_disc.original_device_class = SensorDeviceClass.ILLUMINANCE
    entry_illum_disc.entity_id = "sensor.illuminance_14"

    entry_energy_pwr = MagicMock()
    entry_energy_pwr.domain = "sensor"
    entry_energy_pwr.unique_id = f"{mac}-18-51-power"
    entry_energy_pwr.entity_id = "sensor.meter_51_power"

    entry_energy_tot = MagicMock()
    entry_energy_tot.domain = "sensor"
    entry_energy_tot.unique_id = f"{mac}-18-51-total-energy"
    entry_energy_tot.entity_id = "sensor.meter_51_energy"

    mock_registry = MagicMock()
    mock_registry.async_get_entity_id.return_value = None

    entry_other_domain = MagicMock()
    entry_other_domain.domain = "light"
    entry_other_domain.unique_id = f"{mac}-1-12"

    entry_invalid_energy = MagicMock()
    entry_invalid_energy.domain = "sensor"
    entry_invalid_energy.unique_id = f"{mac}-18-invalid"

    with patch(
        "custom_components.myhome.sensor.er.async_entries_for_config_entry",
        return_value=[entry_other_domain, entry_invalid_energy, entry_illum, entry_illum_disc, entry_energy_pwr, entry_energy_tot],
    ), patch(
        "custom_components.myhome.sensor.er.async_get",
        return_value=mock_registry,
    ):
        added = []
        def fake_add(entities):
            added.extend(entities)

        assert await async_setup_entry(hass, mock_config_entry, fake_add) is True
        # 4 entities added: 1 configured lux 12, 1 restored lux 14, 1 restored power 51, 1 restored total-energy 51
        assert len(added) == 4
        assert isinstance(added[0], MyHOMEIlluminanceSensor)
        assert added[0]._where == "12"
        assert isinstance(added[1], MyHOMEIlluminanceSensor)
        assert added[1]._where == "14"

        # Dynamic discovery via *#1*21*6*500##
        illum_msg = OWNEvent.parse("*#1*21*6*500##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", illum_msg)
        await hass.async_block_till_done()
        assert len(added) == 5
        assert isinstance(added[4], MyHOMEIlluminanceSensor)
        assert added[4]._where == "21"
        assert added[4]._attr_native_value == 500

        # Hook up entity to hass dispatcher
        added[4].hass = hass
        added[4].async_on_remove = MagicMock()
        await added[4].async_added_to_hass()

        # Subsequent message for 21 should update value without re-adding
        illum_msg_2 = OWNEvent.parse("*#1*21*6*600##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", illum_msg_2)
        await hass.async_block_till_done()
        assert len(added) == 5
        assert added[4]._attr_native_value == 600

        # Message with hyphenated WHERE to hit clean_where != where (line 276)
        hyphen_msg = MagicMock(spec=OWNLightingEvent)
        hyphen_msg.who = "1"
        hyphen_msg.where = "sub-22"
        hyphen_msg.message_type = "illuminance_value"
        hyphen_msg.dimension = 6
        hyphen_msg.illuminance = 450
        hyphen_msg.human_readable_log = "illuminance on sub-22"
        async_dispatcher_send(hass, f"myhome_message_{mac}", hyphen_msg)
        await hass.async_block_till_done()
        assert len(added) == 6
        assert added[5]._where == "sub-22"
        assert added[5]._attr_native_value == 450

        # Dynamic energy sensor discovery (lines 231-256, 328)
        energy_pwr_msg = OWNEnergyEvent.parse("*#18*71*113*1500##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", energy_pwr_msg)
        await hass.async_block_till_done()
        assert len(added) == 7
        assert added[6]._where == "71"
        assert added[6]._attr_device_class == SensorDeviceClass.POWER

        # Dynamic totalizer energy sensor discovery
        energy_tot_msg = MagicMock(spec=OWNEnergyEvent)
        energy_tot_msg.who = "18"
        energy_tot_msg.where = "71"
        energy_tot_msg.message_type = "energy_totalizer"
        energy_tot_msg.human_readable_log = "energy totalizer"
        async_dispatcher_send(hass, f"myhome_message_{mac}", energy_tot_msg)
        await hass.async_block_till_done()
        assert len(added) == 8
        assert added[7]._where == "71"
        assert added[7]._attr_device_class == SensorDeviceClass.ENERGY

        # Message that is not energy/heating/lighting (line 315)
        async_dispatcher_send(hass, f"myhome_message_{mac}", "invalid_msg")
        await hass.async_block_till_done()
        assert len(added) == 8

        # Message without message_type and not dimension 6 (line 318)
        empty_msg = MagicMock(spec=OWNLightingEvent)
        empty_msg.who = "1"
        empty_msg.where = "99"
        empty_msg.message_type = None
        empty_msg.dimension = 1
        async_dispatcher_send(hass, f"myhome_message_{mac}", empty_msg)
        await hass.async_block_till_done()
        assert len(added) == 8


@pytest.mark.asyncio
async def test_sensor_setup_registry_exception(mock_hass, mock_config_entry):
    """Test registry exception fallback in sensor async_setup_entry."""
    with patch(
        "custom_components.myhome.sensor.er.async_get",
        side_effect=Exception("ER error"),
    ):
        added = []
        assert await async_setup_entry(mock_hass, mock_config_entry, lambda e: added.extend(e)) is True


@pytest.mark.asyncio
async def test_illuminance_sensor_zero_padded_where_and_deduplication(hass: HomeAssistant):
    """Test illuminance sensor handling 4-digit WHO 1 frames (*#1*0015*6*33338##) and deduplication."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from OWNd.message import OWNEvent

    mac = "00:03:50:00:15:15"
    mock_gateway = MagicMock()
    mock_gateway.mac = mac
    mock_gateway.send_status_request = AsyncMock()

    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {
                    "sensor": {
                        "light_sensor_0015": {
                            CONF_DEVICE_CLASS: SensorDeviceClass.ILLUMINANCE,
                            CONF_WHO: "1",
                            CONF_WHERE: "0015",
                            CONF_NAME: "Illuminance 0015",
                            CONF_MANUFACTURER: "BTicino",
                            CONF_DEVICE_MODEL: "Light Sensor",
                        }
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: mac}
    config_entry.entry_id = "test_ill_0015"

    # Registry with a duplicate legacy format entry for 0015
    entry_dup = MagicMock()
    entry_dup.domain = "sensor"
    entry_dup.entity_id = "sensor.illuminance_0015_legacy"
    entry_dup.unique_id = f"{mac}-1-0015-illuminance"
    entry_dup.original_device_class = SensorDeviceClass.ILLUMINANCE

    mock_er = MagicMock()

    with patch(
        "custom_components.myhome.sensor.er.async_entries_for_config_entry",
        return_value=[entry_dup],
    ), patch(
        "custom_components.myhome.sensor.er.async_get",
        return_value=mock_er,
    ):
        added = []
        assert await async_setup_entry(hass, config_entry, lambda e: added.extend(e)) is True
        mock_er.async_remove.assert_called_with("sensor.illuminance_0015_legacy")
        assert len(added) == 1
        sensor = added[0]
        assert sensor._where == "0015"

        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()
        await sensor.async_added_to_hass()

        # Send Legrand 048834 frame: *#1*0015*6*33338##
        msg = OWNEvent.parse("*#1*0015*6*33338##")
        async_dispatcher_send(hass, f"myhome_message_{mac}", msg)
        await hass.async_block_till_done()

        assert sensor._attr_native_value == 33338
        sensor.async_write_ha_state.assert_called()


@pytest.mark.asyncio
async def test_async_setup_entry_illuminance_deduplication_exception_and_padded_where():
    """Test deduplication exception handling and padded WHERE listener registration in sensor.py."""
    mac = "00:11:22:33:44:66"
    hass = MagicMock(spec=HomeAssistant)
    mock_gateway = MagicMock()
    mock_gateway.mac = mac
    mock_gateway.send_status_request = AsyncMock()
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {
                    "sensor": {
                        "sensor_021": {
                            CONF_DEVICE_CLASS: SensorDeviceClass.ILLUMINANCE,
                            CONF_ENTITIES: {SensorDeviceClass.ILLUMINANCE: {}},
                            CONF_WHO: "1",
                            CONF_WHERE: "021",
                            CONF_NAME: "Illuminance 021",
                            CONF_MANUFACTURER: "BTicino",
                            CONF_DEVICE_MODEL: "Light Sensor",
                        }
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: mac}
    config_entry.entry_id = "test_ill_021"

    entry_dup = MagicMock()
    entry_dup.domain = "sensor"
    entry_dup.entity_id = "sensor.illuminance_021_legacy"
    entry_dup.unique_id = f"{mac}-1-021-illuminance"
    entry_dup.original_device_class = SensorDeviceClass.ILLUMINANCE

    mock_er = MagicMock()
    mock_er.async_remove.side_effect = RuntimeError("Removal failed")

    with patch(
        "custom_components.myhome.sensor.er.async_entries_for_config_entry",
        return_value=[entry_dup],
    ), patch(
        "custom_components.myhome.sensor.er.async_get",
        return_value=mock_er,
    ):
        added = []
        assert await async_setup_entry(hass, config_entry, lambda e: added.extend(e)) is True
        assert len(added) == 1
        sensor = added[0]
        assert sensor._where == "021"

        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()
        sensor.async_on_remove = MagicMock()
        await sensor.async_added_to_hass()

