"""Tests for #624: CEN (WHO 15) 4-digit address (PL >= 10) replayed from authentic traces.

Traces contributed by @wave68runner on a shared MH200N + MyHomeServer1 bus.
Settles #624 with authentic on-wire physical captures of unit 0512 (A=5, PL=12).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PLATFORM,
    CONF_PORT,
    CONF_TYPE,
)
from homeassistant.core import HomeAssistant
from OWNd.message import OWNCENEvent, OWNCENPlusEvent, OWNMessage

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_FIRMWARE,
    CONF_LONG_PRESS,
    CONF_LONG_RELEASE,
    CONF_MANUFACTURER,
    CONF_MANUFACTURER_URL,
    CONF_SHORT_PRESS,
    CONF_SHORT_RELEASE,
    CONF_SSDP_LOCATION,
    CONF_SSDP_ST,
    CONF_UDN,
    DOMAIN,
)
from custom_components.myhome.device_trigger import (
    CONF_ADDRESS,
    CONF_OBJECT,
    CONF_SUBTYPE,
    _get_cen_address_from_device,
    _get_cen_info_from_device,
    async_attach_trigger,
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_624"
MH200N_TRACE_FILE = TRACES_DIR / "myhome_trace_MH200N_all_2026-10-05T20-50-47.json"
MHS1_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_all_2026-10-05T20-50-46.json"
TRACE_FILES = [MH200N_TRACE_FILE, MHS1_TRACE_FILE]


def _cen_frames(trace_file: Path) -> list[str]:
    data = json.loads(trace_file.read_text(encoding="utf-8"))
    return [f["raw"] for f in data["frames"] if f["raw"].startswith("*15*")]


@pytest.fixture
def gateway_handler() -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = "entry_624"
    entry.data = {
        CONF_HOST: "192.168.1.5",
        CONF_PORT: 20000,
        CONF_PASSWORD: "open",
        CONF_SSDP_LOCATION: "",
        CONF_SSDP_ST: "",
        CONF_DEVICE_TYPE: "Gateway",
        CONF_FRIENDLY_NAME: "GW",
        CONF_MANUFACTURER: "Bticino",
        CONF_MANUFACTURER_URL: "",
        CONF_NAME: "MYHOME",
        CONF_FIRMWARE: "1.0",
        CONF_MAC: "00:11:22:33:44:55",
        CONF_UDN: "1234",
    }
    hass = MagicMock()
    hass.data = {}
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.device_registry_id = "gateway_device"
    return handler


def test_trace_files_present_and_have_trailing_newline() -> None:
    """Ensure both trace fixture files exist, contain frames, and end with a trailing newline."""
    assert len(TRACE_FILES) == 2
    for tf in TRACE_FILES:
        assert tf.is_file(), f"Missing trace fixture: {tf}"
        raw_text = tf.read_text(encoding="utf-8")
        assert raw_text.endswith("\n"), f"File {tf.name} missing trailing newline"
        data = json.loads(raw_text)
        assert len(data.get("frames", [])) > 0


@pytest.mark.parametrize("trace_file", TRACE_FILES, ids=lambda p: p.name)
def test_cen_frames_parse_short_and_long_presses(trace_file: Path) -> None:
    """Validate that the CEN frames parse as short press, hold and release lifecycles."""
    frames = _cen_frames(trace_file)
    assert len(frames) == 6
    assert frames == [
        "*15*03*0512##",
        "*15*03#1*0512##",
        "*15*03*0512##",
        "*15*03#3*0512##",
        "*15*03#3*0512##",
        "*15*03#2*0512##",
    ]

    parsed = [OWNMessage.parse(f) for f in frames]
    for msg in parsed:
        assert isinstance(msg, OWNCENEvent)
        assert msg.push_button == 3
        assert msg.object == "0512"
        assert msg.where == "0512"

    # Short press sequence: press, then short release
    p1, r1 = parsed[0], parsed[1]
    assert p1.is_pressed and not p1.is_released_after_short_press
    assert r1.is_released_after_short_press and not r1.is_pressed

    # Long press sequence: press, 2 hold repetitions, then long release
    p2, h1, h2, r2 = parsed[2], parsed[3], parsed[4], parsed[5]
    assert p2.is_pressed
    assert h1.is_held and not h1.is_pressed
    assert h2.is_held and not h2.is_pressed
    assert r2.is_released_after_long_press and not r2.is_held


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_file", TRACE_FILES, ids=lambda p: p.name)
async def test_cen_trace_fires_myhome_cen_event(
    gateway_handler: MyHOMEGatewayHandler, trace_file: Path
) -> None:
    """Test replaying authentic traces fires myhome_cen_event with plain int object and wire string where."""
    messages = [OWNMessage.parse(f) for f in _cen_frames(trace_file)]

    with (
        patch("custom_components.myhome.gateway.OWNEventSession") as session_class,
        patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()),
    ):
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.get_next = AsyncMock(side_effect=[*messages, asyncio.CancelledError()])
        session_class.return_value = session
        gateway_handler.send_status_request = AsyncMock()
        with patch.object(gateway_handler.hass.bus, "async_fire") as fire:
            try:
                await gateway_handler.listening_loop()
            except asyncio.CancelledError:
                pass

    expected = {
        "object": 512,
        "pushbutton": 3,
        "where": "0512",
        "gateway_mac": gateway_handler.mac,
        "entry_id": "entry_624",
    }
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_SHORT_PRESS})
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_SHORT_RELEASE})
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_LONG_PRESS})
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_LONG_RELEASE})

    # Validate that payload uses plain int for object, standard string for where, and round-trips via JSON
    fired_payloads = [call[0][1] for call in fire.call_args_list if call[0][0] == "myhome_cen_event"]
    assert len(fired_payloads) == 6
    for payload in fired_payloads:
        assert type(payload["object"]) is int
        assert payload["object"] == 512
        assert payload["where"] == "0512"
        assert type(payload["where"]) is str
        assert payload["pushbutton"] == 3
        assert hash(payload["object"]) == hash(512)

        # External consumers (Node-RED, AppDaemon, websocket) see JSON serialization
        serialized = json.dumps(payload)
        restored = json.loads(serialized)
        assert type(restored["object"]) is int
        assert restored["object"] == 512
        assert restored["where"] == "0512"
        assert restored["object"] != "0512"


def test_cen_device_registry_new_unit_registration(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Ensure new CEN 4-digit unit registers wire identifier in the device registry."""
    mock_dr = MagicMock()
    mock_dr.async_get_device.return_value = None
    mock_dr.async_get_device_by_identifier.return_value = None
    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "0512")

    mock_dr.async_get_or_create.assert_called_once()
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    assert kwargs["name"] == "CEN Unit 0512"
    assert kwargs["identifiers"] == {(DOMAIN, f"{gateway_handler.mac}-15-0512")}
    assert kwargs["via_device_id"] == "gateway_device"


def test_cen_device_registry_aliases_existing_normalized_device(
    gateway_handler: MyHOMEGatewayHandler,
) -> None:
    """Ensure when only normalized entry exists ({mac}-15-512), wire ID is aliased and name preserved."""
    existing_norm_dev = MagicMock()
    existing_norm_dev.name = "My Custom Button"

    def _get_device(*, identifiers):
        ident = next(iter(identifiers))
        if ident == (DOMAIN, f"{gateway_handler.mac}-15-512"):
            return existing_norm_dev
        return None

    mock_dr = MagicMock()
    mock_dr.async_get_device.side_effect = _get_device
    mock_dr.async_get_device_by_identifier.side_effect = lambda ident, **kw: _get_device(identifiers={ident})

    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "0512")

    mock_dr.async_get_or_create.assert_called_once()
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    # Preserves existing name and aliases both identifiers onto the single entry
    assert kwargs["name"] == "My Custom Button"
    assert kwargs["identifiers"] == {
        (DOMAIN, f"{gateway_handler.mac}-15-0512"),
        (DOMAIN, f"{gateway_handler.mac}-15-512"),
    }


def test_cen_device_registry_two_existing_entries_does_not_raise(
    gateway_handler: MyHOMEGatewayHandler,
) -> None:
    """Ensure when two separate devices already exist, async_get_or_create is not called with multiple identifiers."""
    dev_wire = MagicMock(id="dev_wire_id")
    dev_wire.name = "CEN Unit 0512"
    dev_norm = MagicMock(id="dev_norm_id")
    dev_norm.name = "CEN Unit 512"

    def _get_device(*, identifiers):
        ident = next(iter(identifiers))
        if ident == (DOMAIN, f"{gateway_handler.mac}-15-0512"):
            return dev_wire
        if ident == (DOMAIN, f"{gateway_handler.mac}-15-512"):
            return dev_norm
        return None

    mock_dr = MagicMock()
    mock_dr.async_get_device.side_effect = _get_device
    mock_dr.async_get_device_by_identifier.side_effect = lambda ident, **kw: _get_device(identifiers={ident})

    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "0512")

    mock_dr.async_get_or_create.assert_called_once()
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    # Must only pass wire identifier to avoid multiple device exception
    assert kwargs["identifiers"] == {(DOMAIN, f"{gateway_handler.mac}-15-0512")}
    assert kwargs["name"] == "CEN Unit 0512"


def test_cen_device_registry_routed_unit_does_not_steal_base_unit(
    gateway_handler: MyHOMEGatewayHandler,
) -> None:
    """Ensure a routed unit like 36#4#01 never strips routing into base identifier 36."""
    mock_dr = MagicMock()
    mock_dr.async_get_device.return_value = None
    mock_dr.async_get_device_by_identifier.return_value = None

    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        gateway_handler._ensure_cen_device(15, "36#4#01")
        # Base unit 36 arriving after routed unit
        gateway_handler._ensure_cen_device(15, "36")

    assert mock_dr.async_get_or_create.call_count == 2
    first_call_identifiers = mock_dr.async_get_or_create.call_args_list[0].kwargs["identifiers"]
    second_call_identifiers = mock_dr.async_get_or_create.call_args_list[1].kwargs["identifiers"]

    assert first_call_identifiers == {(DOMAIN, f"{gateway_handler.mac}-15-36#4#01")}
    assert second_call_identifiers == {(DOMAIN, f"{gateway_handler.mac}-15-36")}


def test_helper_cen_address_extraction_is_deterministic() -> None:
    """Test _get_cen_address_from_device returns wire address deterministically regardless of set order."""
    device = MagicMock()
    # Test with both set iteration orders
    device.identifiers = {
        (DOMAIN, "00:03:50:aa:bb:cc-15-0512"),
        (DOMAIN, "00:03:50:aa:bb:cc-15-512"),
    }
    assert _get_cen_address_from_device(device) == "0512"

    is_valid, addr = _get_cen_info_from_device(device)
    assert is_valid is True
    assert addr == 512

    # Routed identifier with #4#01
    routed_dev = MagicMock()
    routed_dev.identifiers = {(DOMAIN, "00:03:50:aa:bb:cc-15-36#4#01")}
    assert _get_cen_address_from_device(routed_dev) == "36#4#01"
    is_valid, addr = _get_cen_info_from_device(routed_dev)
    assert is_valid is True
    assert addr == 36


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger_config",
    [
        # 1. Device trigger matching by device_id (infers "0512" from identifiers)
        {CONF_PLATFORM: "device", CONF_DEVICE_ID: "dev_cen_0512", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3"},
        # 2. Explicit integer address 512
        {CONF_PLATFORM: "device", CONF_DEVICE_ID: "dev_cen_0512", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_ADDRESS: 512},
        # 3. Explicit wire string address "0512"
        {CONF_PLATFORM: "device", CONF_DEVICE_ID: "dev_cen_0512", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_ADDRESS: "0512"},
        # 4. Explicit string decimal address "512"
        {CONF_PLATFORM: "device", CONF_DEVICE_ID: "dev_cen_0512", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_ADDRESS: "512"},
        # 5. Using CONF_OBJECT integer 512
        {CONF_PLATFORM: "device", CONF_DEVICE_ID: "dev_cen_0512", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_OBJECT: 512},
        # 6. Using CONF_OBJECT string "0512"
        {CONF_PLATFORM: "device", CONF_DEVICE_ID: "dev_cen_0512", CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_OBJECT: "0512"},
        # 7. Bare trigger without device_id with address "0512"
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_ADDRESS: "0512"},
        # 8. Bare trigger without device_id with address 512
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_TYPE: CONF_SHORT_PRESS, CONF_SUBTYPE: "button_3", CONF_ADDRESS: 512},
    ],
)
async def test_device_trigger_matching_tolerance(
    hass: HomeAssistant, trigger_config: dict
) -> None:
    """Validate that device triggers match under integer, wire string, and device_id variations."""
    mock_device = MagicMock()
    mock_device.identifiers = {
        (DOMAIN, "00:11:22:33:44:55-15-0512"),
        (DOMAIN, "00:11:22:33:44:55-15-512"),
    }
    mock_device.connections = set()
    mock_registry = MagicMock()
    mock_registry.async_get.return_value = mock_device

    action = AsyncMock()
    full_config = {CONF_DOMAIN: DOMAIN, **trigger_config}

    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_registry):
        unsub = await async_attach_trigger(hass, full_config, action, {"name": "test_trig"})

    payload = {
        "event": CONF_SHORT_PRESS,
        "pushbutton": 3,
        "object": 512,
        "where": "0512",
        "gateway_mac": "00:11:22:33:44:55",
    }
    hass.bus.async_fire("myhome_cen_event", payload)
    await hass.async_block_till_done()

    action.assert_called_once()
    unsub()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatched_address", [513, "0513", 5120])
async def test_device_trigger_negative_cases(
    hass: HomeAssistant, mismatched_address: int | str
) -> None:
    """Ensure triggers with non-matching addresses (513, 0513, 5120) do not fire."""
    action = AsyncMock()
    config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_3",
        CONF_ADDRESS: mismatched_address,
    }
    unsub = await async_attach_trigger(hass, config, action, {"name": "negative_test"})

    payload = {
        "event": CONF_SHORT_PRESS,
        "pushbutton": 3,
        "object": 512,
        "where": "0512",
        "gateway_mac": "00:11:22:33:44:55",
    }
    hass.bus.async_fire("myhome_cen_event", payload)
    await hass.async_block_till_done()

    action.assert_not_called()
    unsub()


@pytest.mark.asyncio
async def test_bare_trigger_does_not_cross_match_cen_and_cenplus(hass: HomeAssistant) -> None:
    """Ensure a bare trigger for CEN address 21 does not fire on a CEN+ event with wire where 21."""
    action_cen = AsyncMock()
    config_cen = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_1",
        CONF_ADDRESS: 21,
    }
    unsub = await async_attach_trigger(hass, config_cen, action_cen, {"name": "bare_cen_21"})

    # CEN event for unit 21 (*15*01*21##)
    cen_payload = {
        "event": CONF_SHORT_PRESS,
        "pushbutton": 1,
        "object": 21,
        "where": "21",
        "gateway_mac": "00:11:22:33:44:55",
    }

    # Firing CEN event matches
    hass.bus.async_fire("myhome_cen_event", cen_payload)
    await hass.async_block_till_done()
    assert action_cen.call_count == 1

    # Firing CEN+ event for scenario object 2 whose where happens to carry "21"
    # must NOT match the trigger for address 21 via int_where.
    action_cen.reset_mock()
    cenplus_non_21_payload = {
        "event": CONF_SHORT_PRESS,
        "pushbutton": 1,
        "object": 2,
        "where": "21",
        "gateway_mac": "00:11:22:33:44:55",
    }
    hass.bus.async_fire("myhome_cenplus_event", cenplus_non_21_payload)
    await hass.async_block_till_done()
    action_cen.assert_not_called()

    unsub()


@pytest.mark.asyncio
async def test_cen_bus_routing_and_non_int_no_value_error(hass: HomeAssistant) -> None:
    """Ensure frames with bus routing tags or non-int objects do not crash and match triggers."""
    entry = MagicMock()
    entry.entry_id = "entry_robustness"
    entry.data = {
        CONF_HOST: "192.168.1.5",
        CONF_PORT: 20000,
        CONF_PASSWORD: "open",
        CONF_NAME: "MYHOME",
        CONF_MAC: "00:11:22:33:44:55",
    }
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.device_registry_id = "gateway_device"

    # Bus-routed frame: *15*06*36#4#01##
    msg = OWNMessage.parse("*15*06*36#4#01##")
    assert isinstance(msg, OWNCENEvent)

    events: list[dict] = []
    hass.bus.async_listen("myhome_cen_event", lambda ev: events.append(ev.data))

    with patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()):
        await handler._event_dispatcher.process_message(msg)
    await hass.async_block_till_done()

    assert len(events) == 1
    payload = events[0]
    assert type(payload["object"]) is int
    assert payload["object"] == 36
    assert payload["where"] == "36"
    assert payload["pushbutton"] == 6


@pytest.mark.asyncio
async def test_cen_and_cenplus_non_integer_fallback(hass: HomeAssistant) -> None:
    """Ensure non-integer CEN and CEN+ messages fall back safely without raising."""
    entry = MagicMock()
    entry.entry_id = "entry_fallback"
    entry.data = {
        CONF_HOST: "192.168.1.5",
        CONF_PORT: 20000,
        CONF_PASSWORD: "open",
        CONF_NAME: "MYHOME",
        CONF_MAC: "00:11:22:33:44:55",
    }
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.device_registry_id = "gateway_device"

    cen_events: list[dict] = []
    cenplus_events: list[dict] = []
    hass.bus.async_listen("myhome_cen_event", lambda ev: cen_events.append(ev.data))
    hass.bus.async_listen("myhome_cenplus_event", lambda ev: cenplus_events.append(ev.data))

    # Mock non-integer CEN message
    cen_msg = MagicMock(spec=OWNCENEvent)
    cen_msg.object = "not_an_int"
    cen_msg.push_button = "not_an_int_pb"
    cen_msg.is_pressed = True
    cen_msg.is_released_after_short_press = False
    cen_msg.is_held = False
    cen_msg.is_released_after_long_press = False
    cen_msg.human_readable_log = "mock non-int CEN"

    # Mock non-integer CEN+ message
    cenplus_msg = MagicMock(spec=OWNCENPlusEvent)
    cenplus_msg.object = "not_an_int_plus"
    cenplus_msg.push_button = "not_an_int_plus_pb"
    cenplus_msg.is_short_pressed = True
    cenplus_msg.is_held = False
    cenplus_msg.is_still_held = False
    cenplus_msg.is_released = False
    cenplus_msg.is_slowly_turned_cw = False
    cenplus_msg.is_quickly_turned_cw = False
    cenplus_msg.is_slowly_turned_ccw = False
    cenplus_msg.is_quickly_turned_ccw = False
    cenplus_msg.human_readable_log = "mock non-int CEN+"

    mock_dr = MagicMock()
    mock_dr.async_get_device.return_value = None
    mock_dr.async_get_device_by_identifier.return_value = None
    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        await handler._event_dispatcher.process_message(cen_msg)
        await handler._event_dispatcher.process_message(cenplus_msg)
    await hass.async_block_till_done()

    assert len(cen_events) == 1
    assert cen_events[0]["object"] == "not_an_int"
    assert cen_events[0]["pushbutton"] == "not_an_int_pb"

    assert len(cenplus_events) == 1
    assert cenplus_events[0]["object"] == "not_an_int_plus"
    assert cenplus_events[0]["pushbutton"] == "not_an_int_plus_pb"


@pytest.mark.asyncio
async def test_cenplus_trigger_non_integer_event_handling(hass: HomeAssistant) -> None:
    """Ensure CEN+ trigger handles malformed/non-integer event object safely without raising."""
    action = AsyncMock()
    trigger_config = {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: "mock_cenplus_device",
        CONF_TYPE: CONF_SHORT_PRESS,
        CONF_SUBTYPE: "button_1",
        CONF_ADDRESS: "12",
    }

    mock_device = MagicMock()
    mock_device.identifiers = {(DOMAIN, "00:11:22:33:44:55-25-12")}
    mock_dr = MagicMock()
    mock_dr.async_get.return_value = mock_device

    with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr):
        unsub = await async_attach_trigger(
            hass,
            trigger_config,
            action,
            {"trigger": trigger_config},
        )

    # Fire malformed event with non-integer object and where
    hass.bus.async_fire(
        "myhome_cenplus_event",
        {
            "object": "not_an_int",
            "pushbutton": 1,
            "event": CONF_SHORT_PRESS,
            "where": "not_an_int",
        },
    )
    await hass.async_block_till_done()

    action.assert_not_called()
    unsub()
