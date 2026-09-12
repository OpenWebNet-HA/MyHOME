"""Integration tests spanning all platforms to ensure setup, unload, and dispatcher wiring."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import CONF_ENTITY, DOMAIN

# List of platforms we want to ensure get loaded
PLATFORMS = ["light", "climate", "cover", "sensor", "binary_sensor", "switch", "media_player"]


@pytest.fixture
def mock_gateway_connection():
    with patch(
        "custom_components.myhome.gateway.OWNSession.test_connection",
        return_value={"Success": True, "Message": None}
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"
    ), patch(
        "custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"
    ):
        yield


async def test_platforms_setup_and_unload(hass: HomeAssistant, mock_gateway_connection):
    """Test all platforms are registered, set up, and cleanly unloaded."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.0.35",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:00:12:34",
            "ssdp_location": "http://192.168.0.35:49153/description.xml",
            "ssdp_st": "urn:schemas-upnp-org:device:Basic:1",
            "deviceType": "urn:schemas-upnp-org:device:Basic:1",
            "friendly_name": "MyHOME Gateway",
            "manufacturer": "BTicino",
            "manufacturerURL": "http://www.bticino.com",
            "name": "F454",
            "firmware": "2.0.0",
            "UDN": "uuid:12345678-1234-1234-1234-123456789012"
        },
        unique_id="00:03:50:00:12:34",
    )
    config_entry.add_to_hass(hass)

    # Note: custom component platforms must be registered in __init__.py dynamically
    # or exist in the platforms list if defined statically.
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    hass.data[DOMAIN][config_entry.data["mac"]][
        CONF_ENTITY
    ]._on_event_connection_state_change(True)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED

    # Ensure all platforms unload correctly
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_platform_dynamic_discovery(hass: HomeAssistant, mock_gateway_connection):
    """Test that incoming active discovery frames spawn entities properly."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.168.0.35",
            "port": 20000,
            "password": "pass",
            "mac": "00:03:50:00:12:34",
            "ssdp_location": "http://192.168.0.35:49153/description.xml",
            "ssdp_st": "urn:schemas-upnp-org:device:Basic:1",
            "deviceType": "urn:schemas-upnp-org:device:Basic:1",
            "friendly_name": "MyHOME Gateway",
            "manufacturer": "BTicino",
            "manufacturerURL": "http://www.bticino.com",
            "name": "F454",
            "firmware": "2.0.0",
            "UDN": "uuid:12345678-1234-1234-1234-123456789012"
        },
        unique_id="00:03:50:00:12:34",
    )
    config_entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    hass.data[DOMAIN][config_entry.data["mac"]][
        CONF_ENTITY
    ]._on_event_connection_state_change(True)
    await hass.async_block_till_done()

    # Dispatch a WHO=1 (Light) event
    light_msg = OWNEvent.parse("*1*1*21##")
    async_dispatcher_send(hass, f"myhome_message_{config_entry.data['mac']}", light_msg)
    await hass.async_block_till_done()

    # Dispatch a WHO=2 (Cover) event
    cover_msg = OWNEvent.parse("*2*0*31##")
    async_dispatcher_send(hass, f"myhome_message_{config_entry.data['mac']}", cover_msg)
    await hass.async_block_till_done()

    # Dispatch a WHO=4 (Heating) event
    climate_msg = OWNEvent.parse("*#4*0#1*0*0225##")
    async_dispatcher_send(hass, f"myhome_message_{config_entry.data['mac']}", climate_msg)
    await hass.async_block_till_done()

    # Dispatch a WHO=16 (Media Player) event
    media_msg = OWNEvent.parse("*16*0*1##")
    async_dispatcher_send(hass, f"myhome_message_{config_entry.data['mac']}", media_msg)
    await hass.async_block_till_done()

    # Entities should now be in the registry
    entity_registry = er.async_get(hass)

    # Check light entity exists
    light_entry = entity_registry.async_get("light.light_21")
    assert light_entry is not None

    # Send another status update to test the handle_event dispatcher
    light_off_msg = OWNEvent.parse("*1*0*21##")
    async_dispatcher_send(hass, f"myhome_message_{config_entry.data['mac']}", light_off_msg)
    await hass.async_block_till_done()

    # Check state updated
    state = hass.states.get("light.light_21")
    assert state.state == "off"

    # Clean up entry
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
