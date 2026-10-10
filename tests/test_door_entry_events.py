"""Tests for Door Entry (WHO=6) doorbell events and gateway handling."""
from unittest.mock import MagicMock, PropertyMock, patch

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNEvent

try:
    from OWNd.message import OWNDoorEntryEvent
except ImportError:  # pragma: no cover
    from custom_components.myhome.gateway_events import OWNDoorEntryEvent
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_events import GatewayEventDispatcher


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

    @callback
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

        # 5. WHO 8 Video Intercom Call (*8*1#1#4*74##) -> should fire myhome_doorbell_event
        who8_call_event = OWNEvent.parse("*8*1#1#4*74##")
        await gateway._process_message(who8_call_event)

        assert len(captured_events) == 4
        assert captured_events[3].data == {
            "where": "74",
            "event": "call",
            "is_broadcast": False,
            "gateway_mac": gateway.mac,
            "entry_id": config_entry.entry_id,
        }
        assert len(dispatcher_payloads) == 4
        assert dispatcher_payloads[3] == captured_events[3].data

        # 6. Internal handset call and pager broadcast are not doorbell rings
        for raw in ("*8*1#6#2#11*16##", "*8*1#14#2#11*4##"):
            await gateway._process_message(OWNEvent.parse(raw))
        assert len(captured_events) == 4
        assert len(dispatcher_payloads) == 4
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


def _doorbell_gateway(hass: HomeAssistant) -> MyHOMEGatewayHandler:
    config_entry = MagicMock()
    config_entry.data = {"host": "192.168.1.100", "port": 20000, "password": "open", "mac": "00:03:50:00:77:78"}
    config_entry.options = {}
    config_entry.entry_id = "test_doorbell_kinds_entry"
    gateway = MyHOMEGatewayHandler(hass, config_entry)
    gateway.generate_events = False
    return gateway


async def test_doorbell_only_for_entrance_panel_calls(hass: HomeAssistant):
    """WHO 8 WHAT 1 starts any call: only the entrance panels (kinds 1-4) ring the door."""
    gateway = _doorbell_gateway(hass)
    captured = async_capture_events(hass, "myhome_doorbell_event")

    # Handset to handset (kind 6) and a pager broadcast (kind 14) are not a visitor
    for frame in ("*8*1#6#2*74##", "*8*1#6#4#73*74##", "*8*1#14#2*4##"):
        await gateway._process_message(OWNEvent.parse(frame))
    # A call whose kind cannot be read fails closed
    for frame in ("*8*1*74##", "*8*1#x#4*74##"):
        message = OWNEvent.parse(frame)
        if message is not None:
            await gateway._process_message(message)
    assert captured == []

    for kind in (1, 2, 3, 4):
        await gateway._process_message(OWNEvent.parse(f"*8*1#{kind}#4*74##"))
    assert [event.data["event"] for event in captured] == ["call"] * 4
    assert {event.data["where"] for event in captured} == {"74"}


def test_unreadable_call_kind_is_none():
    """A call kind that is not a number (from the library or the raw parameters) reads as None."""
    assert GatewayEventDispatcher._intercom_call_kind(MagicMock(call_kind="x")) is None
    assert GatewayEventDispatcher._intercom_call_kind(MagicMock(call_kind=None, _what_param=["x"])) is None
    assert GatewayEventDispatcher._intercom_call_kind(MagicMock(call_kind=None, _what_param=[])) is None


async def test_doorbell_ignores_library_call_flags_for_who8(hass: HomeAssistant):
    """Older OWNd flags every WHO 8 WHAT 1 as an incoming call; the call kind still decides."""
    gateway = _doorbell_gateway(hass)
    captured = async_capture_events(hass, "myhome_doorbell_event")

    internal = OWNEvent.parse("*8*1#6#2*74##")
    internal.is_call = True  # type: ignore[attr-defined]
    internal.is_incoming_call = True  # type: ignore[attr-defined]
    await gateway._process_message(internal)
    assert captured == []


async def test_doorbell_fires_once_on_a_shared_bus(hass: HomeAssistant):
    """Only the gateway that owns the subsystem announces the ring; the other one stays silent."""
    gateway = _doorbell_gateway(hass)
    captured = async_capture_events(hass, "myhome_doorbell_event")
    dispatched: list[dict] = []

    @callback
    def record(payload):
        dispatched.append(payload)

    unsub = async_dispatcher_connect(hass, f"myhome_doorbell_event_{gateway.mac}", record)

    try:
        with patch.object(MyHOMEGatewayHandler, "delegated_away_whos", new_callable=PropertyMock, return_value={6, 8}):
            await gateway._process_message(OWNEvent.parse("*8*1#1#4*74##"))
            await gateway._process_message(OWNEvent.parse("*6*6*1##"))
        assert captured == []
        assert dispatched == []

        with patch.object(MyHOMEGatewayHandler, "is_standby", new_callable=PropertyMock, return_value=True):
            await gateway._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        assert captured == []

        await gateway._process_message(OWNEvent.parse("*8*1#1#4*74##"))
        assert len(captured) == 1
        assert len(dispatched) == 1
    finally:
        unsub()
