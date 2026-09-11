"""Test suite for Issue #283: WHO=1 Command Translation (WHAT=1000) frame filtering.

Verifies that Command Translation frames (e.g. *1*1000#1*53## and *1*1000#0*53##)
are never forwarded to mutate entity state to unknown or trigger dynamic discovery,
while still being preserved for the Bus Monitor and myhome_message_event bus.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.switch import SwitchDeviceClass
from homeassistant.const import CONF_MAC
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNMessage

from custom_components.myhome.const import (
    CONF_ENTITY,
    CONF_PLATFORMS,
    DOMAIN,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.light import (
    MyHOMELight,
)
from custom_components.myhome.light import (
    async_setup_entry as async_setup_light,
)
from custom_components.myhome.switch import MyHOMESwitch


@pytest.mark.asyncio
async def test_light_state_preserved_on_translation_frames(hass):
    """Test that light state is not altered to unknown by ON or OFF translation frames."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:11:22:33:44:55"
    mock_gateway.send = AsyncMock()
    mock_gateway.send_status_request = AsyncMock()
    mock_gateway.log_id = "TEST_GW"

    light = MyHOMELight(
        hass=hass,
        name="Light 53",
        entity_name="Light 53",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="53",
        who="1",
        where="53",
        interface=None,
        dimmable=False,
        manufacturer="BTicino",
        model="Light",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    # Initial state: Turn OFF via actuator status
    off_msg = OWNMessage.parse("*1*0*53##")
    light.handle_event(off_msg)
    assert light.is_on is False

    # Receive ON translation frame: entity must remain OFF (not None/unknown, not ON)
    on_trans_msg = OWNMessage.parse("*1*1000#1*53##")
    assert on_trans_msg.is_translation is True
    assert on_trans_msg.is_on is None
    light.handle_event(on_trans_msg)
    assert light.is_on is False

    # Receive actual actuator ON frame: entity becomes ON
    on_msg = OWNMessage.parse("*1*1*53##")
    light.handle_event(on_msg)
    assert light.is_on is True

    # Receive OFF translation frame: entity must remain ON (not None/unknown, not OFF)
    off_trans_msg = OWNMessage.parse("*1*1000#0*53##")
    assert off_trans_msg.is_translation is True
    assert off_trans_msg.is_on is None
    light.handle_event(off_trans_msg)
    assert light.is_on is True

    # Receive actual actuator OFF frame: entity becomes OFF
    light.handle_event(off_msg)
    assert light.is_on is False


@pytest.mark.asyncio
async def test_switch_state_preserved_on_translation_frames(hass):
    """Test that configured switch state is not altered to unknown by translation frames."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:11:22:33:44:55"
    mock_gateway.send = AsyncMock()
    mock_gateway.send_status_request = AsyncMock()
    mock_gateway.log_id = "TEST_GW"

    switch = MyHOMESwitch(
        hass=hass,
        name="Switch 53",
        entity_name="Switch 53",
        icon="mdi:toggle-switch",
        icon_on="mdi:toggle-switch-off",
        device_id="53",
        who="1",
        where="53",
        interface=None,
        device_class=SwitchDeviceClass.SWITCH,
        manufacturer="BTicino",
        model="Switch",
        gateway=mock_gateway,
    )
    switch.hass = hass
    switch.async_schedule_update_ha_state = MagicMock()

    # Initial state: Turn OFF
    off_msg = OWNMessage.parse("*1*0*53##")
    switch.handle_event(off_msg)
    assert switch.is_on is False

    # Receive ON translation frame -> remains OFF
    on_trans_msg = OWNMessage.parse("*1*1000#1*53##")
    switch.handle_event(on_trans_msg)
    assert switch.is_on is False

    # Receive actual actuator ON frame -> becomes ON
    on_msg = OWNMessage.parse("*1*1*53##")
    switch.handle_event(on_msg)
    assert switch.is_on is True

    # Receive OFF translation frame -> remains ON
    off_trans_msg = OWNMessage.parse("*1*1000#0*53##")
    switch.handle_event(off_trans_msg)
    assert switch.is_on is True

    # Receive actual actuator OFF frame -> becomes OFF
    switch.handle_event(off_msg)
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_dynamic_discovery_rejects_command_translation(hass):
    """Test that translation frames for unknown addresses do NOT trigger dynamic discovery."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "test_mac_discovery"
    hass.data = {
        DOMAIN: {
            "test_mac_discovery": {
                CONF_ENTITY: mock_gateway,
                CONF_PLATFORMS: {},
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: "test_mac_discovery"}
    config_entry.entry_id = "entry_discovery_test"

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    with patch("custom_components.myhome.light.er.async_entries_for_config_entry", return_value=[]), \
         patch("custom_components.myhome.light.er.async_get"):
        await async_setup_light(hass, config_entry, mock_add_entities)

    dispatcher_signal = f"myhome_message_{config_entry.data[CONF_MAC]}"

    # Send ON translation frame for unconfigured address 99
    trans_frame = OWNMessage.parse("*1*1000#1*99##")
    async_dispatcher_send(hass, dispatcher_signal, trans_frame)

    # Must NOT create a light entity
    assert len(added_entities) == 0

    # Send actual actuator state frame for address 99
    actuator_frame = OWNMessage.parse("*1*1*99##")
    async_dispatcher_send(hass, dispatcher_signal, actuator_frame)

    # Must create exactly 1 light entity with state ON
    assert len(added_entities) == 1
    assert added_entities[0]._where == "99"
    assert added_entities[0].is_on is True


@pytest.mark.asyncio
async def test_gateway_bus_monitor_and_message_event_retain_translation(hass):
    """Test that command translation frames are still recorded in BusMonitor and fire myhome_message_event."""
    gateway = MagicMock()
    gateway.host = "192.168.1.50"
    gateway.port = 20000

    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: "00:11:22:33:44:55"}
    config_entry.options = {"generate_events": True}

    handler = MyHOMEGatewayHandler(hass, gateway, config_entry)
    handler.gateway.host = "192.168.1.50"
    handler.generate_events = True

    fired_events = []

    def _event_listener(event):
        fired_events.append(event)

    hass.bus.async_listen("myhome_message_event", _event_listener)

    trans_msg = OWNMessage.parse("*1*1000#1*53##")

    with patch("custom_components.myhome.gateway.OWNEventSession") as mock_session_class:
        mock_session = MagicMock()
        mock_session.connect = AsyncMock(return_value={"Success": True})
        mock_session.get_next = AsyncMock(side_effect=[trans_msg, asyncio.CancelledError()])
        mock_session_class.return_value = mock_session

        try:
            await handler.listening_loop()
        except asyncio.CancelledError:
            pass

    # Verify event was fired on Home Assistant event bus
    assert len(fired_events) == 1
    event_data = fired_events[0].data
    assert event_data.get("message") == "*1*1000#1*53##"
    assert event_data.get("gateway") == "192.168.1.50"
    assert event_data.get("who") == 1
    assert event_data.get("where") == "53"

    # Verify recorded in BusMonitor
    recent = handler.bus_monitor.get_recent_frames(limit=10)
    assert len(recent) >= 1
    assert any(f["raw"] == "*1*1000#1*53##" and f["direction"] == "rx" for f in recent)


@pytest.mark.asyncio
async def test_cover_state_preserved_on_translation_frames(hass):
    """Test that cover state and position are not altered by translation frames."""
    from custom_components.myhome.cover import MyHOMECover

    mock_gateway = MagicMock()
    mock_gateway.mac = "00:11:22:33:44:55"
    mock_gateway.send = AsyncMock()
    mock_gateway.send_status_request = AsyncMock()
    mock_gateway.log_id = "TEST_GW"

    cover = MyHOMECover(
        hass=hass,
        name="Cover 21",
        entity_name="Cover 21",
        device_id="21",
        who="2",
        where="21",
        interface=None,
        advanced=False,
        manufacturer="BTicino",
        model="Shutter",
        gateway=mock_gateway,
    )
    cover.hass = hass
    cover.async_write_ha_state = MagicMock()

    # Initial state: Open cover (position 100)
    cover._attr_current_cover_position = 100
    cover._attr_is_closed = False

    # Receive DOWN translation frame: *2*1000#2*21##
    down_trans = OWNMessage.parse("*2*1000#2*21##")
    assert down_trans.is_translation is True
    cover.handle_event(down_trans)

    # Position and closed state must remain untouched
    assert cover.current_cover_position == 100
    assert cover.is_closed is False

    # Receive UP translation frame: *2*1000#1*21##
    up_trans = OWNMessage.parse("*2*1000#1*21##")
    assert up_trans.is_translation is True
    cover.handle_event(up_trans)

    assert cover.current_cover_position == 100
    assert cover.is_closed is False


@pytest.mark.asyncio
async def test_cover_dynamic_discovery_rejects_command_translation(hass):
    """Test that cover dynamic discovery rejects translation frames."""
    from custom_components.myhome.cover import async_setup_entry as async_setup_cover

    mock_gateway = MagicMock()
    mock_gateway.mac = "test_mac_cover"
    hass.data = {
        DOMAIN: {
            "test_mac_cover": {
                CONF_ENTITY: mock_gateway,
                CONF_PLATFORMS: {},
            }
        }
    }

    config_entry = MagicMock()
    config_entry.data = {CONF_MAC: "test_mac_cover"}
    config_entry.entry_id = "entry_cover_test"

    added_entities = []

    def mock_add_entities(entities):
        added_entities.extend(entities)

    with patch("custom_components.myhome.cover.er.async_entries_for_config_entry", return_value=[]), \
         patch("custom_components.myhome.cover.er.async_get"):
        await async_setup_cover(hass, config_entry, mock_add_entities)

    dispatcher_signal = f"myhome_message_{config_entry.data[CONF_MAC]}"

    # Send DOWN translation frame for unconfigured address 88
    trans_frame = OWNMessage.parse("*2*1000#2*88##")
    async_dispatcher_send(hass, dispatcher_signal, trans_frame)

    assert len(added_entities) == 0

    # Send actual movement event
    actuator_frame = OWNMessage.parse("*2*2*88##")
    async_dispatcher_send(hass, dispatcher_signal, actuator_frame)

    assert len(added_entities) == 1
    assert added_entities[0]._where == "88"


@pytest.mark.asyncio
async def test_light_active_fade_not_interrupted_by_translation(hass):
    """Test that an active software fade on a light is not cancelled by a translation frame."""
    mock_gateway = MagicMock()
    mock_gateway.mac = "00:11:22:33:44:55"
    mock_gateway.send = AsyncMock()
    mock_gateway.log_id = "TEST_GW"

    light = MyHOMELight(
        hass=hass,
        name="Dimmer 33",
        entity_name="Dimmer 33",
        icon="mdi:lightbulb",
        icon_on="mdi:lightbulb-on",
        device_id="33",
        who="1",
        where="33",
        interface=None,
        dimmable=True,
        manufacturer="BTicino",
        model="Dimmer",
        gateway=mock_gateway,
    )
    light.hass = hass
    light.async_schedule_update_ha_state = MagicMock()

    mock_fade_task = MagicMock()
    mock_fade_task.done.return_value = False
    light._fade_task = mock_fade_task

    trans_msg = OWNMessage.parse("*1*1000#0*33##")
    light.handle_event(trans_msg)

    # Active fade task must NOT be cancelled
    mock_fade_task.cancel.assert_not_called()

