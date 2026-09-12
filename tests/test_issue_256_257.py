"""World-class test suite for Issue #256 (F422 interface naming disambiguation) and Issue #257 (Climate auto-discovery & state restore)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.const import CONF_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAutomationEvent,
    OWNHeatingEvent,
    OWNLightingEvent,
)

from custom_components.myhome.climate import (
    MyHOMEClimate,
)
from custom_components.myhome.climate import (
    async_setup_entry as async_setup_climate_entry,
)
from custom_components.myhome.climate import (
    async_unload_entry as async_unload_climate_entry,
)
from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.cover import (
    async_setup_entry as async_setup_cover_entry,
)
from custom_components.myhome.light import (
    async_setup_entry as async_setup_light_entry,
)
from custom_components.myhome.switch import (
    async_setup_entry as async_setup_switch_entry,
)


@pytest.fixture
def mock_gateway():
    """Mock gateway handler."""
    gateway = MagicMock()
    gateway.mac = "00:11:22:33:44:55"
    gateway.log_id = "[GW]"
    gateway.unique_id = "001122334455"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    return gateway


# ============================================================================
# Issue #257: Auto-discovery & Restore for WHO=4 Climate Zones
# ============================================================================


async def test_climate_auto_discovery_from_temperature_event(hass, mock_gateway):
    """Test dynamic discovery of climate zone from temperature frame (*#4*2*0*0230##)."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)
    assert len(added_entities) == 0

    # Simulate temperature event: *#4*2*0*0230## (Zone 2, temp 23.0°C)
    event = OWNHeatingEvent("*#4*2*0*0230##")
    assert event.zone == 2
    assert event.main_temperature == 23.0

    async_dispatcher_send(hass, f"myhome_message_{mac}", event)

    assert len(added_entities) == 1
    climate_entity = added_entities[0]
    assert isinstance(climate_entity, MyHOMEClimate)
    assert climate_entity.name == "Climate Zone 2"
    assert climate_entity._where == "2"
    assert climate_entity.current_temperature == 23.0


async def test_climate_auto_discovery_from_humidity_event(hass, mock_gateway):
    """Test dynamic discovery of climate zone from humidity frame (*#4*3*60*55##)."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # Simulate humidity event: *#4*3*60*55## (Zone 3, humidity 55%)
    event = OWNHeatingEvent("*#4*3*60*55##")
    assert event.zone == 3
    assert event.main_humidity == 55.0

    async_dispatcher_send(hass, f"myhome_message_{mac}", event)

    assert len(added_entities) == 1
    climate_entity = added_entities[0]
    assert climate_entity.name == "Climate Zone 3"
    assert climate_entity._where == "3"
    assert climate_entity.current_humidity == 55.0


async def test_climate_auto_discovery_from_heating_call_event(hass, mock_gateway):
    """Test dynamic discovery from zone heating call frame (*4*4001#2*0#3##)."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # Frame *4*4001#2*0#3##: heating call where zone 2 calls master zone 3
    event = OWNHeatingEvent("*4*4001#2*0#3##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", event)

    assert len(added_entities) >= 1
    zones = [e._where for e in added_entities]
    assert "2" in zones or "3" in zones


async def test_climate_auto_discovery_deduplication(hass, mock_gateway):
    """Test that multiple events for the same zone do not spawn duplicate entities."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # Event 1: 21.0°C
    event1 = OWNHeatingEvent("*#4*5*0*0210##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", event1)
    assert len(added_entities) == 1
    assert added_entities[0].current_temperature == 21.0

    await added_entities[0].async_added_to_hass()

    # Event 2: 22.5°C for same zone
    event2 = OWNHeatingEvent("*#4*5*0*0225##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", event2)
    assert len(added_entities) == 1
    assert added_entities[0].current_temperature == 22.5


async def test_climate_auto_discovery_ignores_broadcast(hass, mock_gateway):
    """Test that general broadcast frames with where=0 and no calling zones are ignored."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # General OFF broadcast: *4*303*0##
    event = OWNHeatingEvent("*4*303*0##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", event)

    # Must NOT create a ghost entity for zone 0
    assert len(added_entities) == 0


async def test_climate_central_unit_auto_discovery(hass, mock_gateway):
    """Test auto-discovery of the central unit (#0)."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    event = OWNHeatingEvent("*#4*#0*0*0215##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", event)

    assert len(added_entities) == 1
    central_entity = added_entities[0]
    assert central_entity.name == "Climate Zone 0"
    assert central_entity.entity_id == "climate.climate_zone_0"
    assert central_entity._central is True
    assert central_entity._standalone is False


async def test_climate_restore_from_entity_registry(hass, mock_gateway):
    """Test that previously discovered climate entities are restored from EntityRegistry."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    mock_registry_entry = MagicMock()
    mock_registry_entry.domain = "climate"
    mock_registry_entry.unique_id = f"{mock_gateway.mac}-4-02"

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    with (
        patch("homeassistant.helpers.entity_registry.async_get"),
        patch(
            "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
            return_value=[mock_registry_entry],
        ),
    ):
        await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    assert len(added_entities) == 1
    restored = added_entities[0]
    assert restored._where == "02"
    assert restored.name == "Climate Zone 02"


async def test_climate_state_restoration(hass, mock_gateway):
    """Test that MyHOMEClimate restores state via async_get_last_state."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
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

    mock_state = MagicMock()
    mock_state.state = HVACMode.HEAT
    mock_state.attributes = {"temperature": 21.5}

    with patch.object(climate, "async_get_last_state", new=AsyncMock(return_value=mock_state)):
        await climate.async_added_to_hass()

    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.target_temperature == 21.5


async def test_climate_state_restoration_cool_mode(hass, mock_gateway):
    """Test that MyHOMEClimate restores COOL mode and target temperature."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id="2",
        who="4",
        where="2",
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

    mock_state = MagicMock()
    mock_state.state = HVACMode.COOL
    mock_state.attributes = {"temperature": 24.0}

    with patch.object(climate, "async_get_last_state", new=AsyncMock(return_value=mock_state)):
        await climate.async_added_to_hass()

    assert climate.hvac_mode == HVACMode.COOL
    assert climate.target_temperature == 24.0


# ============================================================================
# Issue #256: F422 Interface Device Naming Disambiguation & Routing
# ============================================================================


async def test_light_f422_interface_naming(hass, mock_gateway):
    """Test that lights behind F422 interfaces get disambiguated names (Light 02 vs Light 02I02)."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "light": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_light_entry(hass, config_entry, mock_add_entities)

    # 1. Main bus light at address 02: *1*1*02##
    main_light_msg = OWNLightingEvent("*1*1*02##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", main_light_msg)

    assert len(added_entities) == 1
    assert added_entities[0].name == "Light 02"
    assert added_entities[0].entity_id == "light.light_02"

    # 2. Light behind F422 interface 02 at address 02: *1*1*02#4#02##
    interface_light_msg = OWNLightingEvent("*1*1*02#4#02##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", interface_light_msg)

    assert len(added_entities) == 2
    assert added_entities[1].name == "Light 02I02"
    assert added_entities[1].entity_id == "light.light_02i02"

    # 3. Extended address light behind interface 02: *1*1*0010#4#02##
    ext_light_msg = OWNLightingEvent("*1*1*0010#4#02##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", ext_light_msg)

    assert len(added_entities) == 3
    assert added_entities[2].name == "Light 0010I02"
    assert added_entities[2].entity_id == "light.light_0010i02"


async def test_cover_f422_interface_naming(hass, mock_gateway):
    """Test that covers behind F422 interfaces get disambiguated names (Cover 10 vs Cover 10I02)."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "cover": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_cover_entry(hass, config_entry, mock_add_entities)

    # Main bus cover: *2*1*10##
    main_cover_msg = OWNAutomationEvent("*2*1*10##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", main_cover_msg)

    assert len(added_entities) == 1
    assert added_entities[0].name == "Cover 10"

    # Cover behind F422 interface 02: *2*1*10#4#02##
    int_cover_msg = OWNAutomationEvent("*2*1*10#4#02##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", int_cover_msg)

    assert len(added_entities) == 2
    assert added_entities[1].name == "Cover 10I02"


async def test_switch_f422_interface_naming(hass, mock_gateway):
    """Test that switches configured behind F422 interfaces receive disambiguated names."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "switch": {
                        "sw_main": {
                            "where": "02",
                            "name": None,
                        },
                        "sw_interface": {
                            "where": "02",
                            "bus_interface": "02",
                            "name": None,
                        },
                    },
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_switch_entry(hass, config_entry, mock_add_entities)

    assert len(added_entities) == 2
    names = [e.name for e in added_entities]
    assert "Switch 02" in names
    assert "Switch 02I02" in names


async def test_climate_f422_interface_naming(hass, mock_gateway):
    """Test that climate zones behind F422 interfaces receive disambiguated names."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "climate": {},
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_climate_entry(hass, config_entry, mock_add_entities)

    # Frame with interface: *#4*2#4#02*0*0230##
    event = OWNHeatingEvent("*#4*2#4#02*0*0230##")
    async_dispatcher_send(hass, f"myhome_message_{mac}", event)

    assert len(added_entities) == 1
    assert added_entities[0].name == "Climate Zone 2I02"
    assert added_entities[0]._interface == "02"


async def test_climate_f422_interface_command_routing(hass, mock_gateway):
    """Test that climate entity behind F422 routes status requests with interface syntax (#4#<interface>)."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id="2#4#02",
        who="4",
        where="2",
        interface="02",
        heating=True,
        cooling=True,
        fan=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    climate.hass = hass

    # Test status query routes with full interface address: *#4*2#4#02##
    await climate.async_update()
    mock_gateway.send_status_request.assert_called()
    sent_status = mock_gateway.send_status_request.call_args[0][0]
    assert "2#4#02" in str(sent_status)

    # Test set mode executes successfully for zone 2
    await climate.async_set_hvac_mode(HVACMode.OFF)
    mock_gateway.send.assert_called()
    sent_command = mock_gateway.send.call_args[0][0]
    assert str(sent_command) == "*4*303*2##"

    # Test set temperature executes successfully
    await climate.async_set_temperature(temperature=22.0)
    sent_command = mock_gateway.send.call_args[0][0]
    assert "*#4*2*#14*0220*" in str(sent_command)

    # Test set fan mode executes successfully
    await climate.async_set_fan_mode("high")
    sent_command = mock_gateway.send.call_args[0][0]
    assert str(sent_command) == "*#4*2*#11*3##"


async def test_f422_custom_names_override(hass, mock_gateway):
    """Test that custom configured names take precedence over default suffix names."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        "myhome": {
            mac: {
                "platforms": {
                    "light": {
                        "custom_light": {
                            "where": "02",
                            "interface": "02",
                            "name": "Custom Island Light",
                        }
                    },
                },
                "entity": mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    await async_setup_light_entry(hass, config_entry, mock_add_entities)

    assert len(added_entities) == 1
    assert added_entities[0].name == "Custom Island Light"
    assert added_entities[0].entity_id == "light.custom_island_light"


async def test_f422_extra_state_attributes(hass, mock_gateway):
    """Test that devices behind F422 interfaces expose the Int attribute."""
    climate_int = MyHOMEClimate(
        hass=hass,
        name="Zone 2I02",
        device_id="2#4#02",
        who="4",
        where="2",
        interface="02",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    assert climate_int.extra_state_attributes.get("Int") == "02"

    climate_main = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id="2",
        who="4",
        where="2",
        interface=None,
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    assert "Int" not in climate_main.extra_state_attributes


async def test_cover_async_setup_entry_registry_error(hass, mock_gateway):
    """Test cover async_setup_entry gracefully handles entity registry exception."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"cover": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}
    with patch(
        "homeassistant.helpers.entity_registry.async_get", side_effect=Exception("registry error")
    ):
        added = []
        await async_setup_cover_entry(hass, config_entry, added.extend)
        assert len(added) == 0


async def test_climate_async_setup_entry_missing_mac(hass):
    """Test climate async_setup_entry returns True when MAC is missing or unknown."""
    config_entry = MagicMock()
    config_entry.data = {}
    assert await async_setup_climate_entry(hass, config_entry, MagicMock()) is True


async def test_climate_async_setup_entry_missing_gateway(hass):
    """Test climate async_setup_entry returns True when gateway entity is missing."""
    mac = "00:11:22:33:44:55"
    hass.data = {DOMAIN: {mac: {CONF_PLATFORMS: {"climate": {}}, CONF_ENTITY: None}}}
    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: mac}
    assert await async_setup_climate_entry(hass, config_entry, MagicMock()) is True


async def test_climate_async_setup_entry_registry_error(hass, mock_gateway):
    """Test climate async_setup_entry handles entity registry exception."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"climate": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}
    with patch("homeassistant.helpers.entity_registry.async_get", side_effect=Exception("boom")):
        added = []
        assert await async_setup_climate_entry(hass, config_entry, added.extend) is True


async def test_climate_async_setup_entry_restores_interface_from_registry(hass, mock_gateway):
    """Test climate async_setup_entry restores interface entity from registry."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"climate": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    mock_entry = MagicMock()
    mock_entry.domain = "climate"
    mock_entry.unique_id = f"{mac}-4-02#4#02"

    with (
        patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()),
        patch(
            "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
            return_value=[mock_entry],
        ),
    ):
        added = []
        await async_setup_climate_entry(hass, config_entry, added.extend)
        assert len(added) == 1
        assert added[0]._interface == "02"
        assert added[0].name == "Climate Zone 02I02"


async def test_climate_async_setup_entry_duplicate_configured(hass, mock_gateway):
    """Test climate async_setup_entry skips duplicate configured entries."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {
                    "climate": {
                        "zone_1": {"where": "1"},
                        "zone_1_dup": {"where": "1"},
                    }
                },
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added = []
    await async_setup_climate_entry(hass, config_entry, added.extend)
    assert len(added) == 1


async def test_climate_discovery_what_param_and_invalid_params(hass, mock_gateway):
    """Test climate auto discovery handles what_param and invalid param parsing."""
    mac = "00:11:22:33:44:55"
    hass.data = {
        DOMAIN: {
            mac: {
                CONF_PLATFORMS: {"climate": {}},
                CONF_ENTITY: mock_gateway,
            }
        }
    }
    config_entry = MagicMock()
    config_entry.entry_id = "test_entry"
    config_entry.data = {CONF_MAC: mac}

    added = []
    await async_setup_climate_entry(hass, config_entry, added.extend)

    # 1. Message with what=4001 and valid what_param
    msg1 = OWNHeatingEvent("*4*4001*0#3##")
    msg1.what = 4001
    msg1._what_param = ["3"]
    async_dispatcher_send(hass, f"myhome_message_{mac}", msg1)
    assert any(e._where == "3" for e in added)

    # 2. Message with what=4001 and non-numeric what_param (hits ValueError exception)
    msg2 = OWNHeatingEvent("*4*4001*0#3##")
    msg2.what = 4001
    msg2._what_param = ["invalid"]
    async_dispatcher_send(hass, f"myhome_message_{mac}", msg2)

    # 3. Message with where_param != "4" and valid int
    msg3 = OWNHeatingEvent("*4*0*0##")
    msg3._where_param = ["6"]
    async_dispatcher_send(hass, f"myhome_message_{mac}", msg3)
    assert any(e._where == "6" for e in added)

    # 4. Message with non-numeric where_param (hits ValueError exception)
    msg4 = OWNHeatingEvent("*4*0*0##")
    msg4._where_param = ["not_a_number"]
    async_dispatcher_send(hass, f"myhome_message_{mac}", msg4)


async def test_climate_async_unload_entry_missing_mac(hass):
    """Test climate async_unload_entry handles missing mac."""
    config_entry = MagicMock()
    config_entry.data = {}
    assert await async_unload_climate_entry(hass, config_entry) is True


async def test_climate_restore_invalid_target_temp_and_exception(hass, mock_gateway):
    """Test climate entity state restore handles invalid target temp and exceptions."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 1",
        device_id="1",
        who="4",
        where="1",
        interface=None,
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )

    # 1. Non-numeric temperature attribute
    mock_state = MagicMock()
    mock_state.state = HVACMode.HEAT
    mock_state.attributes = {"temperature": "not_a_float"}
    with patch.object(climate, "async_get_last_state", new=AsyncMock(return_value=mock_state)):
        await climate.async_added_to_hass()
    assert climate.hvac_mode == HVACMode.HEAT

    # 2. async_get_last_state raises exception
    with patch.object(
        climate, "async_get_last_state", new=AsyncMock(side_effect=Exception("state error"))
    ):
        await climate.async_added_to_hass()


async def test_climate_async_added_to_hass_with_interface(hass, mock_gateway):
    """Test climate async_added_to_hass registers listener for full_where when interface is present."""
    climate = MyHOMEClimate(
        hass=hass,
        name="Zone 2I02",
        device_id="2#4#02",
        who="4",
        where="2",
        interface="02",
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=mock_gateway,
    )
    await climate.async_added_to_hass()
    mock_gateway.send_status_request.assert_called()
