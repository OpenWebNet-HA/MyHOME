"""Test the MyHOME binary sensor component."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.myhome.binary_sensor import (
    MyHOMEDryContact,
    MyHOMEAuxiliary,
    MyHOMEMotionSensor,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.ownd.message import (
    OWNDryContactEvent,
    OWNAuxEvent,
    OWNLightingEvent,
    MESSAGE_TYPE_MOTION,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    MESSAGE_TYPE_MOTION_TIMEOUT,
)
from custom_components.myhome.const import DOMAIN

async def test_setup_and_unload_entry(hass):
    """Test setup and unload of the binary_sensor platform."""
    mock_gateway = MagicMock()
    
    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "binary_sensor": {
                        "device_1": {
                            "who": "25",
                            "where": "12",
                            "name": "Dry Contact",
                            "entity_name": "Contact 1",
                            "inverted": False,
                            "class": BinarySensorDeviceClass.WINDOW,
                            "manufacturer": "B",
                            "model": "M",
                        },
                        "device_2": {
                            "who": "9",
                            "where": "13",
                            "name": "Aux",
                            "entity_name": "Aux 1",
                            "inverted": True,
                            "class": BinarySensorDeviceClass.DOOR,
                            "manufacturer": "B",
                            "model": "M",
                        },
                        "device_3": {
                            "who": "1",
                            "where": "14",
                            "name": "Motion",
                            "entity_name": "Motion 1",
                            "inverted": False,
                            "class": BinarySensorDeviceClass.MOTION,
                            "manufacturer": "B",
                            "model": "M",
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
    
    assert len(entities) == 3
    assert isinstance(entities[0], MyHOMEDryContact)
    assert isinstance(entities[1], MyHOMEAuxiliary)
    assert isinstance(entities[2], MyHOMEMotionSensor)
    
    # Test unload
    await async_unload_entry(hass, config_entry)
    assert "device_1" not in hass.data[DOMAIN]["mac"]["platforms"]["binary_sensor"]

async def test_dry_contact(hass):
    """Test MyHOMEDryContact logic."""
    gateway = MagicMock()
    gateway.send_status_request = AsyncMock()
    
    hass.data = {DOMAIN: {"mac": {"platforms": {"binary_sensor": {"device_1": {"entities": {}}}}}}}
    
    sensor = MyHOMEDryContact(
        hass=hass,
        name="Device",
        entity_name="Sensor 1",
        device_id="device_1",
        who="25",
        where="12",
        inverted=False,
        device_class=BinarySensorDeviceClass.WINDOW,
        manufacturer="M",
        model="M",
        gateway=gateway,
    )
    
    sensor.async_schedule_update_ha_state = MagicMock()
    
    assert sensor.name == "Sensor 1"
    assert sensor.device_class == BinarySensorDeviceClass.WINDOW
    assert not sensor.is_on
    
    # Update state via method
    await sensor.async_update()
    gateway.send_status_request.assert_called_once()
    
    # Handle on event
    event = MagicMock(spec=OWNDryContactEvent)
    event.is_on = True
    sensor.handle_event(event)
    assert sensor.is_on
    
    # Handle off event
    event.is_on = False
    sensor.handle_event(event)
    assert not sensor.is_on
    
    # Test lifecycle
    gateway.mac = "mac"
    await sensor.async_added_to_hass()
    await sensor.async_will_remove_from_hass()

async def test_dry_contact_inverted(hass):
    """Test MyHOMEDryContact logic when inverted."""
    sensor = MyHOMEDryContact(
        hass=hass, name="Device", entity_name="Sensor", device_id="D1",
        who="25", where="12", inverted=True, device_class=BinarySensorDeviceClass.WINDOW,
        manufacturer="M", model="M", gateway=MagicMock(),
    )
    sensor.async_schedule_update_ha_state = MagicMock()
    # Default is false
    event = MagicMock(spec=OWNDryContactEvent)
    event.is_on = True
    sensor.handle_event(event)
    # Since inverted is True, True != True returns False
    assert not sensor.is_on
    
    event.is_on = False
    sensor.handle_event(event)
    assert sensor.is_on

async def test_auxiliary_sensor(hass):
    """Test MyHOMEAuxiliary logic."""
    gateway = MagicMock()
    gateway.mac = "mac"
    hass.data = {DOMAIN: {"mac": {"platforms": {"binary_sensor": {"device_1": {"entities": {}}}}}}}
    
    sensor = MyHOMEAuxiliary(
        hass=hass, name="Device", entity_name="Sensor", device_id="device_1",
        who="9", where="12", inverted=False, device_class=BinarySensorDeviceClass.DOOR,
        manufacturer="M", model="M", gateway=gateway,
    )
    sensor.async_schedule_update_ha_state = MagicMock()
    
    # Update does nothing for AUX
    await sensor.async_update()
    
    event = MagicMock(spec=OWNDryContactEvent)
    event.is_on = True
    sensor.handle_event(event)
    assert sensor.is_on
    
    # Lifecycle
    await sensor.async_added_to_hass()
    await sensor.async_will_remove_from_hass()

async def test_motion_sensor(hass):
    """Test MyHOMEMotionSensor logic."""
    gateway = MagicMock()
    gateway.mac = "mac"
    gateway.send_status_request = AsyncMock()
    
    hass.data = {DOMAIN: {"mac": {"platforms": {"binary_sensor": {"device_1": {"entities": {}}}}}}}
    
    sensor = MyHOMEMotionSensor(
        hass=hass, name="Device", entity_name="Sensor", device_id="device_1",
        who="1", where="12", inverted=False, device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="M", model="M", gateway=gateway,
    )
    sensor.async_write_ha_state = MagicMock()
    
    # Init tests calls to gateway
    sensor.async_get_last_state = AsyncMock(return_value=None)
    await sensor.async_added_to_hass()
    assert gateway.send_status_request.call_count == 2
    
    # Test motion event
    event = MagicMock(spec=OWNLightingEvent)
    event.message_type = MESSAGE_TYPE_MOTION
    event.motion = True
    sensor.handle_event(event)
    assert sensor.is_on
    
    # Test timeout event
    event.message_type = MESSAGE_TYPE_MOTION_TIMEOUT
    event.motion_timeout = timedelta(seconds=60)
    sensor.handle_event(event)
    assert sensor._timeout == timedelta(seconds=75)
    
    # Test PIR sensitivity event
    event.message_type = MESSAGE_TYPE_PIR_SENSITIVITY
    event.pir_sensitivity = 2 # 0: low, 1: medium, 2: high
    sensor.handle_event(event)
    assert sensor.extra_state_attributes["Sensitivity"] == "high"
    
    # Test unrelated event
    event.message_type = "unrelated"
    assert sensor.handle_event(event) is True
    
    # Test async_will_remove_from_hass
    await sensor.async_will_remove_from_hass()


async def test_binary_sensor_platform_not_in_platforms(hass):
    """Test setup and unload when binary_sensor is not in platforms."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {},
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac"}

    assert await async_setup_entry(hass, config_entry, MagicMock()) is True
    assert await async_unload_entry(hass, config_entry) is True


async def test_binary_sensor_lifecycle_edge_cases(hass):
    """Test exception handling and missing CONF_ENTITIES dict across all binary sensor entities."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"

    # Setup device_dict without "entities" key to trigger line 234 and line 314
    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "binary_sensor": {
                        "d1": {},
                        "a1": {},
                        "m1": {},
                    }
                }
            }
        }
    }

    dry = MyHOMEDryContact(
        hass=hass, name="D", entity_name="D", device_id="d1",
        who="25", where="12", inverted=False, device_class=BinarySensorDeviceClass.WINDOW,
        manufacturer="M", model="M", gateway=mock_gateway,
    )
    aux = MyHOMEAuxiliary(
        hass=hass, name="A", entity_name="A", device_id="a1",
        who="9", where="13", inverted=False, device_class=BinarySensorDeviceClass.DOOR,
        manufacturer="M", model="M", gateway=mock_gateway,
    )
    motion = MyHOMEMotionSensor(
        hass=hass, name="M", entity_name="M", device_id="m1",
        who="1", where="14", inverted=False, device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="M", model="M", gateway=mock_gateway,
    )
    motion.async_get_last_state = AsyncMock(return_value=None)
    mock_gateway.send_status_request = AsyncMock()

    # Trigger async_added_to_hass with device_dict lacking CONF_ENTITIES
    await dry.async_added_to_hass()
    await aux.async_added_to_hass()
    await motion.async_added_to_hass()

    assert "window" in hass.data[DOMAIN]["mac"]["platforms"]["binary_sensor"]["d1"]["entities"]
    assert "door" in hass.data[DOMAIN]["mac"]["platforms"]["binary_sensor"]["a1"]["entities"]
    assert "motion" in hass.data[DOMAIN]["mac"]["platforms"]["binary_sensor"]["m1"]["entities"]

    await dry.async_will_remove_from_hass()
    await aux.async_will_remove_from_hass()
    await motion.async_will_remove_from_hass()

    # Trigger KeyError branches when hass.data is empty
    hass.data = {}
    await dry.async_added_to_hass()
    await dry.async_will_remove_from_hass()
    await aux.async_added_to_hass()
    await aux.async_will_remove_from_hass()
    await motion.async_added_to_hass()
    await motion.async_will_remove_from_hass()


async def test_motion_sensor_restore_state_and_timeout_expiration(hass):
    """Test motion sensor state restore from last state and timeout expiration in async_update."""
    from datetime import datetime, timezone
    from homeassistant.const import STATE_ON

    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    mock_gateway.send_status_request = AsyncMock()

    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "binary_sensor": {
                        "m1": {"entities": {}}
                    }
                }
            }
        }
    }

    motion = MyHOMEMotionSensor(
        hass=hass, name="M", entity_name="M", device_id="m1",
        who="1", where="14", inverted=False, device_class=BinarySensorDeviceClass.MOTION,
        manufacturer="M", model="M", gateway=mock_gateway,
    )
    motion.async_schedule_update_ha_state = MagicMock()

    # 1. Test restore state (lines 322-323)
    mock_state = MagicMock()
    mock_state.state = STATE_ON
    # Past timestamp older than timeout (e.g. 500 seconds ago)
    past_time = datetime.now(timezone.utc) - timedelta(seconds=500)
    mock_state.last_updated = past_time
    motion.async_get_last_state = AsyncMock(return_value=mock_state)

    await motion.async_added_to_hass()
    # During async_added_to_hass, it calls async_update(), which notices timeout expired!
    # lines 341-343: _attr_is_on becomes False, _last_updated updated, async_schedule_update_ha_state called
    assert motion._attr_is_on is False
    motion.async_schedule_update_ha_state.assert_called()


async def test_binary_sensor_dispatcher_and_discovery(hass):
    """Test dynamic discovery and forwarding of binary sensor bus events."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from custom_components.myhome.ownd.message import OWNEvent

    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:00:25:25"
    mock_gateway.send_status_request = AsyncMock()

    hass.data = {
        DOMAIN: {
            mock_gateway.mac: {
                "platforms": {},
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mock_gateway.mac}

    added = []

    def fake_add(entities):
        added.extend(entities)

    assert await async_setup_entry(hass, config_entry, fake_add) is True

    # Discover new dry contact
    dry_msg = OWNEvent.parse("*25*31#1*99##")
    async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", dry_msg)
    assert len(added) == 1
    assert added[0]._where == "99"

    # Forward Aux event
    aux_msg = OWNEvent.parse("*9*1*1##")
    async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", aux_msg)

    # Forward motion lighting event with dimension
    light_msg = OWNEvent.parse("*#1*12*1##")
    async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", light_msg)

    # Test async_added_to_hass
    sensor = added[0]
    sensor.async_on_remove = MagicMock()
    await sensor.async_added_to_hass()
    mock_gateway.send_status_request.assert_awaited()


async def test_binary_sensor_entity_registry_and_motion_discovery(hass):
    """Test binary sensor registry restoration and dynamic motion sensor discovery."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    from custom_components.myhome.ownd.message import OWNEvent

    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:00:11:22"
    mock_gateway.send_status_request = AsyncMock()

    # Configure a sensor that is already in registry to hit the continue branch
    hass.data = {
        DOMAIN: {
            mock_gateway.mac: {
                "platforms": {
                    "binary_sensor": {
                        "reg_mot": {
                            "who": "1",
                            "where": "41",
                            "name": "Restored Motion",
                        }
                    }
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": mock_gateway.mac}
    config_entry.entry_id = "test_bs_reg_entry"

    entry_motion = MagicMock()
    entry_motion.domain = "binary_sensor"
    entry_motion.unique_id = f"{mock_gateway.mac}-1-41-motion"
    entry_motion.original_device_class = BinarySensorDeviceClass.MOTION

    entry_dry = MagicMock()
    entry_dry.domain = "binary_sensor"
    entry_dry.unique_id = f"{mock_gateway.mac}-25-42"
    entry_dry.original_device_class = BinarySensorDeviceClass.OPENING

    entry_aux = MagicMock()
    entry_aux.domain = "binary_sensor"
    entry_aux.unique_id = f"{mock_gateway.mac}-9-43"
    entry_aux.original_device_class = None

    entry_aux2 = MagicMock()
    entry_aux2.domain = "binary_sensor"
    entry_aux2.unique_id = f"{mock_gateway.mac}-9-44"
    entry_aux2.original_device_class = BinarySensorDeviceClass.CONNECTIVITY

    mock_er = MagicMock()

    with patch(
        "custom_components.myhome.binary_sensor.er.async_entries_for_config_entry",
        return_value=[entry_motion, entry_dry, entry_aux, entry_aux2],
    ), patch(
        "custom_components.myhome.binary_sensor.er.async_get",
        return_value=mock_er,
    ):
        added = []
        def fake_add(entities):
            added.extend(entities)

        assert await async_setup_entry(hass, config_entry, fake_add) is True
        # 4 entities restored from registry; configured 41 is skipped via continue
        assert len(added) == 4
        assert isinstance(added[0], MyHOMEMotionSensor)
        assert isinstance(added[1], MyHOMEDryContact)
        assert isinstance(added[2], MyHOMEAuxiliary)
        assert isinstance(added[3], MyHOMEAuxiliary)

        # Dynamic motion discovery via *1*34*51##
        motion_msg = OWNEvent.parse("*1*34*51##")
        async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", motion_msg)
        await hass.async_block_till_done()

        # Should discover new motion sensor for 51
        assert len(added) == 5
        assert isinstance(added[4], MyHOMEMotionSensor)
        assert added[4]._where == "51"

        # Sending another motion message for 51 should not add duplicate
        async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", motion_msg)
        await hass.async_block_till_done()
        assert len(added) == 5

        # Dynamic motion discovery with interface *1*34*52#4#01##
        motion_interface = OWNEvent.parse("*1*34*52#4#01##")
        async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", motion_interface)
        await hass.async_block_till_done()
        assert len(added) == 6
        assert added[5]._where == "52"

        # Motion message with hyphenated WHERE to hit clean_where != where
        hyphen_msg = MagicMock(spec=OWNLightingEvent)
        hyphen_msg.where = "sub-53"
        hyphen_msg.message_type = "motion_detected"
        hyphen_msg.motion = True
        hyphen_msg.human_readable_log = "motion on sub-53"
        async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", hyphen_msg)
        await hass.async_block_till_done()
        assert len(added) == 7
        assert added[6]._where == "sub-53"

        # Message with dimension 5 (sensitivity) and dimension 7 (timeout)
        dim5_msg = OWNEvent.parse("*#1*51*5*2##")
        async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", dim5_msg)
        await hass.async_block_till_done()

        dim7_msg = OWNEvent.parse("*#1*51*7*0*15*0##")
        async_dispatcher_send(hass, f"myhome_message_{mock_gateway.mac}", dim7_msg)
        await hass.async_block_till_done()


async def test_binary_sensor_registry_exception(hass):
    """Test registry exception fallback in binary_sensor async_setup_entry."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac_bs_err"
    hass.data = {
        DOMAIN: {
            "mac_bs_err": {
                "platforms": {},
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac_bs_err"}
    config_entry.entry_id = "test_bs_err"

    with patch(
        "custom_components.myhome.binary_sensor.er.async_get",
        side_effect=Exception("ER error"),
    ):
        added = []
        assert await async_setup_entry(hass, config_entry, lambda e: added.extend(e)) is True


