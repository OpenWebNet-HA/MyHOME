"""Test the MyHOME button component."""
from unittest.mock import AsyncMock, MagicMock

from custom_components.myhome.button import (
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.const import DOMAIN


async def test_setup_and_unload_entry(hass):
    """Test setup and unload of the button platform."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"

    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "button": {
                        "device_1": {
                            "who": "1",
                            "where": "12",
                            "name": "Light 12",
                            "manufacturer": "BTicino",
                            "model": "F411",
                            "entities": {}
                        },
                        "device_2": {
                            "who": "1",
                            "where": "13",
                            "interface": "2", # test with bus interface
                            "name": "Light 13",
                            "manufacturer": "BTicino",
                            "model": "F411",
                            "entities": {}
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

    # Each device gets 1 disable and 1 enable button
    assert len(entities) == 4
    entity_ids = [e.entity_id for e in entities]
    assert entity_ids == [
        "button.light_12_lock",
        "button.light_12_unlock",
        "button.light_13_lock",
        "button.light_13_unlock",
    ]

    # Test unload
    await async_unload_entry(hass, config_entry)
    assert "device_1" not in hass.data[DOMAIN]["mac"]["platforms"]["button"]
    assert "device_2" not in hass.data[DOMAIN]["mac"]["platforms"]["button"]


async def test_disable_button_entity(hass):
    """Test DisableCommandButtonEntity logic."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    mock_gateway.send = AsyncMock()

    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "button": {
                        "device_1": {
                            "entities": {}
                        }
                    }
                }
            }
        }
    }

    # Test without interface
    btn1 = DisableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Device",
        device_id="device_1",
        who="1",
        where="12",
        interface=None,
        manufacturer="B",
        model="A",
        gateway=mock_gateway,
    )

    assert btn1.name == "Lock"
    assert btn1.entity_id == "button.device_lock"
    assert btn1.unique_id == "mac-1-device_1-disable"
    assert btn1.extra_state_attributes["A"] == "1"
    assert btn1.extra_state_attributes["PL"] == "2"
    assert "Int" not in btn1.extra_state_attributes

    await btn1.async_press()
    mock_gateway.send.assert_called_once_with("*14*0*12##")
    mock_gateway.send.reset_mock()

    # Test with interface
    btn2 = DisableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Device",
        device_id="device_1",
        who="1",
        where="13",
        interface="2",
        manufacturer="B",
        model="A",
        gateway=mock_gateway,
    )
    assert btn2.extra_state_attributes["A"] == "1"
    assert btn2.extra_state_attributes["PL"] == "3"
    assert btn2.extra_state_attributes["Int"] == "2"

    await btn2.async_press()
    mock_gateway.send.assert_called_once_with("*14*0*13#4#2##")

    # Test lifecycle
    await btn1.async_added_to_hass()
    assert hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]["disable"] == btn1

    await btn1.async_will_remove_from_hass()
    assert "disable" not in hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]


async def test_enable_button_entity(hass):
    """Test EnableCommandButtonEntity logic."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"
    mock_gateway.send = AsyncMock()

    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "button": {
                        "device_1": {
                            "entities": {}
                        }
                    }
                }
            }
        }
    }

    btn1 = EnableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Device",
        device_id="device_1",
        who="1",
        where="12",
        interface=None,
        manufacturer="B",
        model="A",
        gateway=mock_gateway,
    )

    assert btn1.name == "Unlock"
    assert btn1.entity_id == "button.device_unlock"
    assert btn1.unique_id == "mac-1-device_1-enable"

    await btn1.async_press()
    mock_gateway.send.assert_called_once_with("*14*1*12##")

    # Test lifecycle
    await btn1.async_added_to_hass()
    assert hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]["enable"] == btn1

    await btn1.async_will_remove_from_hass()
    assert "enable" not in hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]


async def test_button_platform_not_in_platforms(hass):
    """Test setup and unload when button platform is not configured."""
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


async def test_button_entities_lifecycle_edge_cases(hass):
    """Test exception branches and missing entities dict in button lifecycle."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac"

    # 1. Test when device_dict has no "entities" key (triggers device_dict[CONF_ENTITIES] = {})
    hass.data = {
        DOMAIN: {
            "mac": {
                "platforms": {
                    "button": {
                        "device_1": {}
                    }
                }
            }
        }
    }

    dis_btn = DisableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Device",
        device_id="device_1",
        who="1",
        where="12",
        interface=None,
        manufacturer="B",
        model="A",
        gateway=mock_gateway,
    )
    en_btn = EnableCommandButtonEntity(
        hass=hass,
        platform="button",
        name="Device",
        device_id="device_1",
        who="1",
        where="12",
        interface=None,
        manufacturer="B",
        model="A",
        gateway=mock_gateway,
    )

    await dis_btn.async_added_to_hass()
    assert "disable" in hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]

    # Delete entities so en_btn also triggers line 218
    del hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]
    await en_btn.async_added_to_hass()
    assert "enable" in hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]

    await dis_btn.async_will_remove_from_hass()
    await en_btn.async_will_remove_from_hass()
    assert "disable" not in hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]
    assert "enable" not in hass.data[DOMAIN]["mac"]["platforms"]["button"]["device_1"]["entities"]

    # 2. Test when hass.data raises KeyError/TypeError
    hass.data = {}
    # Both add and remove should gracefully swallow KeyError without raising
    await dis_btn.async_added_to_hass()
    await dis_btn.async_will_remove_from_hass()
    await en_btn.async_added_to_hass()
    await en_btn.async_will_remove_from_hass()


async def test_button_additional_edge_coverage(hass):
    """Test button edge cases: missing mac in hass.data, invalid where."""
    config_entry = MagicMock()
    config_entry.data = {"mac": "nonexistent"}

    # mac not in hass.data
    hass.data = {}
    assert await async_setup_entry(hass, config_entry, MagicMock()) is True
    assert await async_unload_entry(hass, config_entry) is True

    # configured button with where starting with '#' (should return empty list)
    mock_gateway = MagicMock()
    mock_gateway.mac = "mac1"
    hass.data = {
        DOMAIN: {
            "mac1": {
                "platforms": {
                    "button": {
                        "area_device": {
                            "who": "1",
                            "where": "#1",
                        }
                    }
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry.data = {"mac": "mac1"}
    added_entities = []
    await async_setup_entry(hass, config_entry, lambda ents: added_entities.extend(ents))
    # Area button with '#' ignored
    assert len(added_entities) == 0

    # Test dynamic device listener
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    async_dispatcher_send(
        hass,
        "myhome_new_device_mac1",
        {"who": "1", "where": "21", "name": "Light 21", "device_id": "21"},
    )
    assert len(added_entities) == 2  # lock + unlock

    # Send duplicate device event (triggers line 58 return [])
    async_dispatcher_send(
        hass,
        "myhome_new_device_mac1",
        {"who": "1", "where": "21", "name": "Light 21", "device_id": "21"},
    )
    assert len(added_entities) == 2
