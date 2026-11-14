"""Tests for #612 / #654: MH200N authentic startup sweep, CEN pushbutton, and Scenario 1 trace.

Authentic on-wire bus trace contributed by @Depechie on #612 (comment 6046447690).
Verifies:
- Elimination of motion sensor area sweeps (#614 fix verified against physical MH200N capture)
- Deprecation-free device registry lookup via async_get_device_by_identifier
- Full handling of WHO 17 (OWNSceneEvent) firing myhome_scene_event
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_PASSWORD,
    CONF_PORT,
)
from OWNd.message import OWNCENEvent, OWNLightingEvent, OWNMessage, OWNSceneEvent

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.gateway import MyHOMEGatewayHandler
from custom_components.myhome.gateway_events import GatewayEventDispatcher

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_612"
TRACE_FILE = TRACES_DIR / "myhome_trace_MH200N_startup_cen_scene_2026-10-07T20-38-24.json"


@pytest.fixture
def gateway_handler() -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = "entry_612"
    entry.data = {
        CONF_HOST: "10.10.13.148",
        CONF_PORT: 20000,
        CONF_PASSWORD: "open",
        CONF_MAC: "00:03:50:01:75:8b",
    }
    hass = MagicMock()
    hass.data = {}
    handler = MyHOMEGatewayHandler(hass, entry)
    handler.device_registry_id = "gateway_device_612"
    return handler


def test_trace_file_present_and_has_trailing_newline() -> None:
    """Ensure the trace fixture file exists, contains frames, and ends with a trailing newline."""
    assert TRACE_FILE.is_file(), f"Missing trace fixture: {TRACE_FILE}"
    raw_text = TRACE_FILE.read_text(encoding="utf-8")
    assert raw_text.endswith("\n"), "Trace file missing trailing newline"
    data = json.loads(raw_text)
    assert len(data.get("frames", [])) == 193


@pytest.mark.asyncio
async def test_trace_and_motion_frames_do_not_trigger_area_sweeps(
    gateway_handler: MyHOMEGatewayHandler,
) -> None:
    """Empirically verify PR #614 in code: frames never schedule motion sensor area sweeps (*#1*00## or *#1*1##)."""
    data = json.loads(TRACE_FILE.read_text(encoding="utf-8"))
    frames = [f["raw"] for f in data["frames"]]

    # 1. Verify authentic capture contains no unsolicited area sweeps
    assert "*#1*00##" not in frames
    assert "*#1*1##" not in frames
    assert "*#1*2##" not in frames

    # 2. Verify initial discovery sweep *#1*0## is present in the capture
    initial_sweeps = [f for f in frames if f == "*#1*0##"]
    assert len(initial_sweeps) == 1

    # 3. Empirically verify PR #614 execution in code:
    # Process startup discovery frames, PIR dimension frames, and motion detection frames.
    # Gateway handler must NEVER schedule or send area sweeps (*#1*00##, *#1*1##, etc.).
    gateway_handler.send_status_request = AsyncMock()
    with patch.object(gateway_handler, "_known_light_areas", return_value=["1", "00"]):
        # Replay the first 25 startup frames from the capture (containing *#1*0##, datetime, and status frames)
        for frame_str in frames[:25]:
            await gateway_handler._process_message(OWNMessage.parse(frame_str))

        # Replay motion detection and PIR dimension frames from issue #612
        for motion_frame in ["*1*34*1##", "*1*34*00##", "*#1*0*7*0*1*21##", "*#1*0*5*3##"]:
            await gateway_handler._process_message(OWNMessage.parse(motion_frame))

    # Assert no broadcast resync sweeps were scheduled
    assert not gateway_handler._resync_timers
    # Assert send_status_request was never called with any area sweep frame
    sent_requests = [str(call[0][0]) for call in gateway_handler.send_status_request.call_args_list]
    assert "*#1*00##" not in sent_requests
    assert "*#1*1##" not in sent_requests


def test_trace_cen_and_scene_events_parse() -> None:
    """Validate that the trace's CEN and Scene frames parse with accurate properties."""
    cen_frame = "*15*03*61##"
    scene_start_frame = "*17*1*1##"
    scene_stop_frame = "*17*2*1##"

    cen_msg = OWNMessage.parse(cen_frame)
    assert isinstance(cen_msg, OWNCENEvent)
    assert cen_msg.object == "61"
    assert cen_msg.push_button == 3
    assert cen_msg.is_pressed is True

    scene_start = OWNMessage.parse(scene_start_frame)
    assert isinstance(scene_start, OWNSceneEvent)
    assert scene_start.scenario == "1"
    assert scene_start.state == 1
    assert scene_start.is_on is True
    assert scene_start.is_enabled is None
    assert scene_start.human_readable_log == "Scene 1 is started."

    scene_stop = OWNMessage.parse(scene_stop_frame)
    assert isinstance(scene_stop, OWNSceneEvent)
    assert scene_stop.scenario == "1"
    assert scene_stop.state == 2
    assert scene_stop.is_on is False
    assert scene_stop.is_enabled is None
    assert scene_stop.human_readable_log == "Scene 1 is stopped."


@pytest.mark.asyncio
async def test_trace_replay_cen_and_scene_events(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Replay authentic trace and assert ordering, debouncing, and state progression."""
    data = json.loads(TRACE_FILE.read_text(encoding="utf-8"))
    raw_frames = [f["raw"] for f in data["frames"]]
    messages = [OWNMessage.parse(f) for f in raw_frames]

    mock_dr = MagicMock()
    mock_dr.async_get_device.return_value = None
    mock_dr.async_get_device_by_identifier.return_value = None

    timeline: list[tuple[str, Any]] = []

    def record_fire(event: str, payload: dict[str, Any] | None = None) -> None:
        timeline.append(("bus_event", (event, dict(payload or {}))))

    def record_dispatcher(hass: Any, signal: str, msg: Any = None) -> None:
        timeline.append(("dispatcher", (signal, msg)))

    with (
        patch("custom_components.myhome.gateway.OWNEventSession") as session_class,
        patch("homeassistant.helpers.device_registry.async_get", return_value=mock_dr),
        patch("custom_components.myhome.gateway.async_dispatcher_send", side_effect=record_dispatcher),
        patch("custom_components.myhome.gateway_events.async_dispatcher_send", side_effect=record_dispatcher),
    ):
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.get_next = AsyncMock(side_effect=[*messages, asyncio.CancelledError()])
        session_class.return_value = session
        gateway_handler.send_status_request = AsyncMock()

        with patch.object(gateway_handler.hass.bus, "async_fire", side_effect=record_fire):
            try:
                await gateway_handler.listening_loop()
            except asyncio.CancelledError:
                pass

    # 1. Assert strict chronological ordering:
    # CEN press (*15*03*61##) -> Scene 1 start (*17*1*1##) -> Actuators ON (25, 74, 26) -> Scene 1 stop (*17*2*1##) -> Actuator 22 ON
    cen_press_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "bus_event"
        and payload[0] == "myhome_cen_event"
        and payload[1].get("object") == 61
        and payload[1].get("pushbutton") == 3
    )
    scene_start_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "bus_event"
        and payload[0] == "myhome_scene_event"
        and payload[1].get("scenario") == 1
        and payload[1].get("is_on") is True
    )
    light_25_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "dispatcher"
        and isinstance(payload[1], OWNLightingEvent)
        and payload[1].where == "25"
        and payload[1].is_on is True
    )
    light_74_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "dispatcher"
        and isinstance(payload[1], OWNLightingEvent)
        and payload[1].where == "74"
        and payload[1].is_on is True
    )
    light_26_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "dispatcher"
        and isinstance(payload[1], OWNLightingEvent)
        and payload[1].where == "26"
        and payload[1].is_on is True
    )
    scene_stop_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "bus_event"
        and payload[0] == "myhome_scene_event"
        and payload[1].get("scenario") == 1
        and payload[1].get("is_on") is False
    )
    light_22_idx = next(
        i
        for i, (kind, payload) in enumerate(timeline)
        if kind == "dispatcher"
        and isinstance(payload[1], OWNLightingEvent)
        and payload[1].where == "22"
        and payload[1].is_on is True
    )

    assert cen_press_idx < scene_start_idx
    assert scene_start_idx < light_25_idx < scene_stop_idx
    assert scene_start_idx < light_74_idx < scene_stop_idx
    assert scene_start_idx < light_26_idx < scene_stop_idx
    assert scene_stop_idx < light_22_idx

    # 2. Assert state progression:
    # Lights 25, 74, 26 become active during Scenario 1 execution, and light 22 becomes active after scenario stop.
    active_lights_during_scene: set[str] = set()
    active_lights_after_scene: set[str] = set()
    for idx, (kind, payload) in enumerate(timeline):
        if kind == "dispatcher" and isinstance(payload[1], OWNLightingEvent) and payload[1].is_on is True:
            where = str(payload[1].where)
            if scene_start_idx < idx < scene_stop_idx:
                active_lights_during_scene.add(where)
            elif idx > scene_stop_idx:
                active_lights_after_scene.add(where)

    assert {"25", "74", "26"}.issubset(active_lights_during_scene)
    assert "22" in active_lights_after_scene

    # 3. Assert debouncing and deduplication:
    # CEN unit 61 is registered exactly once in the device registry despite multiple queries and status frames.
    mock_dr.async_get_or_create.assert_called_once()
    kwargs = mock_dr.async_get_or_create.call_args.kwargs
    assert kwargs["name"] == "CEN Unit 61"
    assert kwargs["identifiers"] == {(DOMAIN, f"{gateway_handler.mac}-15-61")}
    assert kwargs["via_device_id"] == "gateway_device_612"

    # Deprecation-free device registry lookup via async_get_device_by_identifier
    mock_dr.async_get_device_by_identifier.assert_any_call(
        (DOMAIN, f"{gateway_handler.mac}-15-61"),
        config_entry_id="entry_612",
    )
    mock_dr.async_get_device.assert_not_called()

    # 4. Assert that replaying the authentic trace does not trigger motion sensor area sweeps
    assert not gateway_handler._resync_timers
    sent_requests = [str(call[0][0]) for call in gateway_handler.send_status_request.call_args_list]
    assert "*#1*00##" not in sent_requests
    assert "*#1*1##" not in sent_requests


@pytest.mark.asyncio
async def test_scene_event_enabled_and_disabled_states(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Test scenario enable (3) and disable (4) states."""
    scene_enabled = OWNMessage.parse("*17*3*2##")
    scene_disabled = OWNMessage.parse("*17*4*2##")

    assert isinstance(scene_enabled, OWNSceneEvent)
    assert scene_enabled.state == 3
    assert scene_enabled.is_enabled is True
    assert scene_enabled.is_on is None
    assert scene_enabled.human_readable_log == "Scene 2 is enabled."

    assert isinstance(scene_disabled, OWNSceneEvent)
    assert scene_disabled.state == 4
    assert scene_disabled.is_enabled is False
    assert scene_disabled.is_on is None
    assert scene_disabled.human_readable_log == "Scene 2 is disabled."

    with patch.object(gateway_handler.hass.bus, "async_fire") as fire:
        await gateway_handler._event_dispatcher.process_message(scene_enabled)
        await gateway_handler._event_dispatcher.process_message(scene_disabled)

    fire.assert_any_call(
        "myhome_scene_event",
        {
            "scenario": 2,
            "where": "2",
            "state": 3,
            "is_on": None,
            "is_enabled": True,
            "gateway_mac": gateway_handler.mac,
            "entry_id": "entry_612",
        },
    )
    fire.assert_any_call(
        "myhome_scene_event",
        {
            "scenario": 2,
            "where": "2",
            "state": 4,
            "is_on": None,
            "is_enabled": False,
            "gateway_mac": gateway_handler.mac,
            "entry_id": "entry_612",
        },
    )


@pytest.mark.asyncio
async def test_scene_event_non_integer_scenario(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Test scenario event with non-integer scenario identifier degrades gracefully."""
    mock_scene = MagicMock(spec=OWNSceneEvent)
    mock_scene.scenario = "A1"
    mock_scene.where = "A1"
    mock_scene.state = 1
    mock_scene.is_on = True
    mock_scene.is_enabled = None
    mock_scene.human_readable_log = "Scene A1 is started."

    with patch.object(gateway_handler.hass.bus, "async_fire") as fire:
        await gateway_handler._event_dispatcher.process_message(mock_scene)

    fire.assert_called_once_with(
        "myhome_scene_event",
        {
            "scenario": "A1",
            "where": "A1",
            "state": 1,
            "is_on": True,
            "is_enabled": None,
            "gateway_mac": gateway_handler.mac,
            "entry_id": "entry_612",
        },
    )

@pytest.mark.asyncio
async def test_scene_event_standby_failover() -> None:
    """Test standby gateway failover behavior for scene events."""
    mock_handler = MagicMock()
    mock_handler.is_standby = True
    mock_handler.is_secondary = False
    mock_handler.generate_events = False
    mock_handler.mac = "00:03:50:01:75:8b"
    mock_handler.log_id = "[Standby GW]"
    mock_handler.config_entry.entry_id = "standby_entry_id"
    primary_gw = MagicMock()
    primary_gw.mac = "00:03:50:99:99:99"
    primary_gw.config_entry.entry_id = "primary_entry_id"
    primary_gw.is_connected = False
    mock_handler._get_primary_gateway.return_value = primary_gw
    mock_handler._profile_supports_who.return_value = True

    dispatcher = GatewayEventDispatcher(mock_handler)
    scene_msg = OWNMessage.parse("*17*1*5##")

    with patch.object(mock_handler.hass.bus, "async_fire") as fire:
        # Primary is offline and profile supports WHO 17 -> failover active, uses primary MAC/entry
        await dispatcher.process_message(scene_msg)

    fire.assert_called_once_with(
        "myhome_scene_event",
        {
            "scenario": 5,
            "where": "5",
            "state": 1,
            "is_on": True,
            "is_enabled": None,
            "gateway_mac": "00:03:50:99:99:99",
            "entry_id": "primary_entry_id",
        },
    )

    # Primary is online -> standby does not fire
    primary_gw.is_connected = True
    fire.reset_mock()
    await dispatcher.process_message(scene_msg)
    fire.assert_not_called()


@pytest.mark.asyncio
async def test_scene_event_delegated_away() -> None:
    """Test that when WHO 17 is delegated away, scene events are not fired."""
    mock_handler = MagicMock()
    mock_handler.is_standby = False
    mock_handler.is_secondary = False
    mock_handler.delegated_away_whos = {17}
    dispatcher = GatewayEventDispatcher(mock_handler)
    scene_msg = OWNMessage.parse("*17*1*1##")

    with patch.object(mock_handler.hass.bus, "async_fire") as fire:
        await dispatcher.process_message(scene_msg)

    fire.assert_not_called()


@pytest.mark.asyncio
async def test_scene_event_without_config_entry(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Test scene event handling when gateway_handler has no config_entry."""
    gateway_handler.config_entry = None
    scene_msg = OWNMessage.parse("*17*1*1##")

    with patch.object(gateway_handler.hass.bus, "async_fire") as fire:
        await gateway_handler._event_dispatcher.process_message(scene_msg)

    fire.assert_called_once_with(
        "myhome_scene_event",
        {
            "scenario": 1,
            "where": "1",
            "state": 1,
            "is_on": True,
            "is_enabled": None,
            "gateway_mac": gateway_handler.mac,
        },
    )


def test_cen_device_registry_fallback_without_async_get_device_by_identifier(
    gateway_handler: MyHOMEGatewayHandler,
) -> None:
    """Ensure older core versions without async_get_device_by_identifier fallback to async_get_device."""

    class LegacyRegistry:
        def __init__(self) -> None:
            self.async_get_device = MagicMock(return_value=None)
            self.async_get_or_create = MagicMock()

    legacy_dr = LegacyRegistry()
    assert not hasattr(legacy_dr, "async_get_device_by_identifier")

    with patch("homeassistant.helpers.device_registry.async_get", return_value=legacy_dr):
        gateway_handler._ensure_cen_device(15, "0512")

    legacy_dr.async_get_device.assert_called()
    legacy_dr.async_get_or_create.assert_called_once()


def test_cen_device_registry_strict_signature(
    gateway_handler: MyHOMEGatewayHandler,
) -> None:
    """Ensure async_get_device_by_identifier matches the exact Home Assistant Core signature.

    HA Core 2026.9+ DeviceRegistry.async_get_device_by_identifier signature:
    (self, identifier: tuple[str, str], config_entry_id: str) -> DeviceEntry | None.
    A strict stub class prevents MagicMock keyword-argument blindness.
    """

    class StrictDeviceRegistry:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, str], str]] = []
            self.async_get_or_create = MagicMock()

        def async_get_device_by_identifier(
            self, identifier: tuple[str, str], config_entry_id: str
        ) -> Any | None:
            self.calls.append((identifier, config_entry_id))
            return None

    strict_dr = StrictDeviceRegistry()

    with patch("homeassistant.helpers.device_registry.async_get", return_value=strict_dr):
        # Numeric object with leading zero: checks both wire format and normalized format
        gateway_handler._ensure_cen_device(15, "0512")

    assert strict_dr.calls == [
        ((DOMAIN, f"{gateway_handler.mac}-15-0512"), "entry_612"),
        ((DOMAIN, f"{gateway_handler.mac}-15-512"), "entry_612"),
    ]
    strict_dr.async_get_or_create.assert_called_once()


