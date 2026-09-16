"""Test the MyHOME button component."""
from unittest.mock import AsyncMock, MagicMock

from custom_components.myhome.button import (
    DisableCommandButtonEntity,
    EnableCommandButtonEntity,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.myhome.const import DOMAIN
from tests.conftest import attach_runtime, bind_entity


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
    attach_runtime(hass, config_entry)
    await async_setup_entry(hass, config_entry, async_add_entities)

    async_add_entities.assert_called_once()
    from custom_components.myhome.button import CalibrateAllCoversButtonEntity

    all_entities = async_add_entities.call_args[0][0]
    # The gateway always gets one "Calibrate all covers" button
    assert sum(isinstance(e, CalibrateAllCoversButtonEntity) for e in all_entities) == 1
    entities = [e for e in all_entities if not isinstance(e, CalibrateAllCoversButtonEntity)]

    # Each device gets 1 disable and 1 enable button
    assert len(entities) == 4
    entity_ids = [e._display_name for e in entities]
    assert entity_ids == [
        "Light 12 Lock",
        "Light 12 Unlock",
        "Light 13 Lock",
        "Light 13 Unlock",
    ]

    # Test unload
    attach_runtime(hass, config_entry)
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

    assert btn1._display_name == "Device Lock"
    assert btn1.translation_key == "lock"
    assert btn1.unique_id == "mac-1-device_1-disable"
    bind_entity(hass, btn1, "mac", mock_gateway)
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

    assert btn1._display_name == "Device Unlock"
    assert btn1.translation_key == "unlock"
    assert btn1.unique_id == "mac-1-device_1-enable"
    bind_entity(hass, btn1, "mac", mock_gateway)

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

    attach_runtime(hass, config_entry)
    assert await async_setup_entry(hass, config_entry, MagicMock()) is True
    attach_runtime(hass, config_entry)
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

    bind_entity(hass, dis_btn, "mac", mock_gateway)
    bind_entity(hass, en_btn, "mac", mock_gateway)
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

    # 2. Entities without a platform / runtime data must not raise
    dis_btn.platform = None
    en_btn.platform.config_entry.runtime_data = None
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
    attach_runtime(hass, config_entry)
    assert await async_setup_entry(hass, config_entry, MagicMock()) is True
    attach_runtime(hass, config_entry)
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
    attach_runtime(hass, config_entry)
    await async_setup_entry(hass, config_entry, lambda ents: added_entities.extend(ents))
    # Area button with '#' ignored; only the gateway's "Calibrate all covers" button remains
    from custom_components.myhome.button import CalibrateAllCoversButtonEntity

    assert [type(e) for e in added_entities] == [CalibrateAllCoversButtonEntity]

    # Test dynamic device listener
    from homeassistant.helpers.dispatcher import async_dispatcher_send
    async_dispatcher_send(
        hass,
        "myhome_new_device_mac1",
        {"who": "1", "where": "21", "name": "Light 21", "device_id": "21"},
    )
    assert len(added_entities) == 3  # calibrate-all + lock + unlock

    # Send duplicate device event (triggers line 58 return [])
    async_dispatcher_send(
        hass,
        "myhome_new_device_mac1",
        {"who": "1", "where": "21", "name": "Light 21", "device_id": "21"},
    )
    assert len(added_entities) == 3


async def test_button_entity_id_sanitization_issue_347(hass):
    """Device names with apostrophes and accents never yield an invalid entity id (Issue #347).

    The integration no longer sets ``entity_id`` itself: the buttons carry
    ``has_entity_name`` and a translation key, and Home Assistant derives the id
    from the slugified "<device name> <entity name>". The expected ids below are
    what that slug produces.
    """
    from homeassistant.core import valid_entity_id
    from homeassistant.util import slugify

    mock_gateway = MagicMock()
    mock_gateway.mac = "00:03:50:81:17:76"

    test_cases = [
        ("Chambre d'amis Groupe de volets", "11", "button.chambre_d_amis_groupe_de_volets_lock", "button.chambre_d_amis_groupe_de_volets_unlock"),
        ("Chambre d'amis Volets arrière", "12", "button.chambre_d_amis_volets_arriere_lock", "button.chambre_d_amis_volets_arriere_unlock"),
        ("Chambre d'amis Volets avant", "13", "button.chambre_d_amis_volets_avant_lock", "button.chambre_d_amis_volets_avant_unlock"),
        ("L'Éclairage Salon", "14", "button.l_eclairage_salon_lock", "button.l_eclairage_salon_unlock"),
        ("Salon / Salle à manger", "15", "button.salon_salle_a_manger_lock", "button.salon_salle_a_manger_unlock"),
    ]

    for name, where, expected_lock, expected_unlock in test_cases:
        lock_btn = DisableCommandButtonEntity(
            hass=hass,
            platform="button",
            name=name,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            manufacturer="BTicino",
            model="Actuator",
            gateway=mock_gateway,
        )
        unlock_btn = EnableCommandButtonEntity(
            hass=hass,
            platform="button",
            name=name,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            manufacturer="BTicino",
            model="Actuator",
            gateway=mock_gateway,
        )

        # No invented entity id: Home Assistant slugifies the friendly name it builds
        assert lock_btn.entity_id is None and unlock_btn.entity_id is None
        assert lock_btn._device_name == name and lock_btn._display_name == f"{name} Lock"
        assert unlock_btn._display_name == f"{name} Unlock"
        assert f"button.{slugify(lock_btn._display_name)}" == expected_lock
        assert f"button.{slugify(unlock_btn._display_name)}" == expected_unlock
        assert valid_entity_id(expected_lock) and valid_entity_id(expected_unlock)

    # Also verify through the dispatcher / platform setup
    hass.data = {
        DOMAIN: {
            "mac_issue_347": {
                "platforms": {"button": {}},
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.data = {"mac": "mac_issue_347"}
    added_buttons = []
    runtime = attach_runtime(hass, config_entry)
    await async_setup_entry(hass, config_entry, lambda ents: added_buttons.extend(ents))

    from homeassistant.helpers.dispatcher import async_dispatcher_send
    async_dispatcher_send(
        hass,
        f"myhome_new_device_{runtime.mac}",
        {
            "who": "2",
            "where": "31",
            "name": "Chambre d'amis Groupe de volets",
            "device_id": "31",
        },
    )
    # Besides lock/unlock, a cover gets a calibration button and the gateway a "calibrate all"
    lock_unlock = [b for b in added_buttons if isinstance(b, (DisableCommandButtonEntity, EnableCommandButtonEntity))]
    assert len(lock_unlock) == 2
    assert [b.entity_id for b in lock_unlock] == [None, None]
    assert [b._display_name for b in lock_unlock] == [
        "Chambre d'amis Groupe de volets Lock",
        "Chambre d'amis Groupe de volets Unlock",
    ]
    assert [f"button.{slugify(b._display_name)}" for b in lock_unlock] == [
        "button.chambre_d_amis_groupe_de_volets_lock",
        "button.chambre_d_amis_groupe_de_volets_unlock",
    ]

