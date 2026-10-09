"""Tests for Door Entry (WHO=6) doorbell events and gateway handling."""
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNEvent

try:
    from OWNd.message import OWNDoorEntryEvent
except ImportError:  # pragma: no cover
    from custom_components.myhome.gateway_events import OWNDoorEntryEvent
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.myhome.gateway import MyHOMEGatewayHandler


async def test_gateway_fires_doorbell_events(hass: HomeAssistant):
    """Test that MyHOMEGatewayHandler._process_message fires myhome_doorbell_event on calls and chimes."""
    config_entry = MagicMock()
    config_entry.data = {
        "host": "192.168.1.100",
        "port": 20000,
        "password": "open",
        "mac": "00:03:50:00:77:77",
    }
    config_entry.options = {}
    config_entry.entry_id = "test_doorbell_gw_entry"

    gateway = MyHOMEGatewayHandler(hass, config_entry)
    gateway.generate_events = False

    captured_events = async_capture_events(hass, "myhome_doorbell_event")

    dispatcher_payloads = []

    def handle_dispatcher(payload):
        dispatcher_payloads.append(payload)

    unsub = async_dispatcher_connect(
        hass, f"myhome_doorbell_event_{gateway.mac}", handle_dispatcher
    )

    try:
        # 1. Incoming call from entrance panel 1 (*6*6*1##)
        call_event = OWNEvent.parse("*6*6*1##")
        assert isinstance(call_event, (OWNDoorEntryEvent, OWNEvent))
        await gateway._process_message(call_event)

        assert len(captured_events) == 1
        assert captured_events[0].data == {
            "where": "1",
            "event": "call",
            "is_broadcast": False,
            "gateway_mac": gateway.mac,
            "entry_id": config_entry.entry_id,
        }
        assert len(dispatcher_payloads) == 1
        assert dispatcher_payloads[0] == captured_events[0].data

        # 2. General / broadcast call (*6*6*4100##)
        bcast_event = OWNEvent.parse("*6*6*4100##")
        assert isinstance(bcast_event, (OWNDoorEntryEvent, OWNEvent))
        await gateway._process_message(bcast_event)

        assert len(captured_events) == 2
        assert captured_events[1].data == {
            "where": "4100",
            "event": "broadcast_call",
            "is_broadcast": True,
            "gateway_mac": gateway.mac,
            "entry_id": config_entry.entry_id,
        }
        assert len(dispatcher_payloads) == 2

        # 3. Chime event (*6*20*1##)
        chime_event = OWNEvent.parse("*6*20*1##")
        assert isinstance(chime_event, (OWNDoorEntryEvent, OWNEvent))
        await gateway._process_message(chime_event)

        assert len(captured_events) == 3
        assert captured_events[2].data == {
            "where": "1",
            "event": "chime",
            "is_broadcast": False,
            "gateway_mac": gateway.mac,
            "entry_id": config_entry.entry_id,
        }
        assert len(dispatcher_payloads) == 3

        # 4. Lock open event (*6*10*1##) -> should NOT fire myhome_doorbell_event
        lock_event = OWNEvent.parse("*6*10*1##")
        assert isinstance(lock_event, (OWNDoorEntryEvent, OWNEvent))
        await gateway._process_message(lock_event)

        # No new events should have been captured
        assert len(captured_events) == 3
        assert len(dispatcher_payloads) == 3
    finally:
        unsub()


async def test_gateway_logs_door_entry_events(hass: HomeAssistant):
    """Test that human-readable logs are emitted for door entry events."""
    config_entry = MagicMock()
    config_entry.data = {
        "host": "192.168.1.100",
        "port": 20000,
        "password": "open",
        "mac": "00:03:50:00:77:77",
    }
    config_entry.options = {}
    config_entry.entry_id = "test_doorbell_gw_entry"

    gateway = MyHOMEGatewayHandler(hass, config_entry)

    call_event = OWNEvent.parse("*6*6*1##")
    with patch("custom_components.myhome.gateway.LOGGER.debug") as mock_debug:
        await gateway._process_message(call_event)
        mock_debug.assert_called_with(
            "%s %s",
            gateway.log_id,
            call_event.human_readable_log,
        )
