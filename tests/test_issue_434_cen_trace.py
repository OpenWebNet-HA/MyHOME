"""Tests for #434: classic CEN (WHO 15) short and long press replayed from authentic traces.

Traces contributed by @wave68runner on a shared MH200N + MyHomeServer1 bus.
Settles Item 1 of #434 with authentic on-wire physical captures.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from homeassistant.const import (
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from OWNd.message import OWNCENEvent, OWNMessage

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
)
from custom_components.myhome.gateway import MyHOMEGatewayHandler

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_434"
MH200N_TRACE_FILE = TRACES_DIR / "myhome_trace_MH200N_all_2026-09-29T18-31-59.json"
MHS1_TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_all_2026-09-29T16-34-38.json"
TRACE_FILES = [MH200N_TRACE_FILE, MHS1_TRACE_FILE]


def _cen_frames(trace_file: Path) -> list[str]:
    data = json.loads(trace_file.read_text(encoding="utf-8"))
    return [f["raw"] for f in data["frames"] if f["raw"].startswith("*15*")]


@pytest.fixture
def gateway_handler() -> MyHOMEGatewayHandler:
    entry = MagicMock()
    entry.entry_id = "entry_434"
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


def test_trace_files_present() -> None:
    """Ensure both trace fixture files exist and contain frames."""
    assert len(TRACE_FILES) == 2
    for tf in TRACE_FILES:
        assert tf.is_file(), f"Missing trace fixture: {tf}"
        data = json.loads(tf.read_text(encoding="utf-8"))
        assert len(data.get("frames", [])) > 0


def test_mh200n_cen_frames_parse_short_and_long_presses() -> None:
    """Validate that the MH200N trace frames parse as short/long press lifecycles."""
    frames = _cen_frames(MH200N_TRACE_FILE)
    assert len(frames) == 14

    expected_frames = [
        # Button 1 short press & release
        "*15*01*73##",
        "*15*01#1*73##",
        # Button 1 long press: initial, 3 hold repetitions, release
        "*15*01*73##",
        "*15*01#3*73##",
        "*15*01#3*73##",
        "*15*01#3*73##",
        "*15*01#2*73##",
        # Button 2 short press & release
        "*15*02*73##",
        "*15*02#1*73##",
        # Button 2 long press: initial, 3 hold repetitions, release
        "*15*02*73##",
        "*15*02#3*73##",
        "*15*02#3*73##",
        "*15*02#3*73##",
        "*15*02#2*73##",
    ]
    assert frames == expected_frames

    parsed = [OWNMessage.parse(f) for f in frames]
    assert all(isinstance(m, OWNCENEvent) for m in parsed)
    assert all(m.object == "73" for m in parsed)

    # Check button 1 short press sequence
    assert parsed[0].push_button == 1 and parsed[0].is_pressed
    assert parsed[1].push_button == 1 and parsed[1].is_released_after_short_press

    # Check button 1 long press sequence
    assert parsed[2].push_button == 1 and parsed[2].is_pressed
    assert all(m.push_button == 1 and m.is_held for m in parsed[3:6])
    assert parsed[6].push_button == 1 and parsed[6].is_released_after_long_press

    # Check button 2 short press sequence
    assert parsed[7].push_button == 2 and parsed[7].is_pressed
    assert parsed[8].push_button == 2 and parsed[8].is_released_after_short_press

    # Check button 2 long press sequence
    assert parsed[9].push_button == 2 and parsed[9].is_pressed
    assert all(m.push_button == 2 and m.is_held for m in parsed[10:13])
    assert parsed[13].push_button == 2 and parsed[13].is_released_after_long_press


def test_myhomeserver1_cen_frames_parse_long_press() -> None:
    """Validate that the MyHomeServer1 trace frames parse as a button 1 long press."""
    frames = _cen_frames(MHS1_TRACE_FILE)
    assert len(frames) == 6

    expected_frames = [
        "*15*01*73##",
        "*15*01#3*73##",
        "*15*01#3*73##",
        "*15*01#3*73##",
        "*15*01#3*73##",
        "*15*01#2*73##",
    ]
    assert frames == expected_frames

    parsed = [OWNMessage.parse(f) for f in frames]
    assert all(isinstance(m, OWNCENEvent) for m in parsed)
    assert all(m.object == "73" and m.push_button == 1 for m in parsed)

    assert parsed[0].is_pressed
    assert all(m.is_held for m in parsed[1:5])
    assert parsed[5].is_released_after_long_press


@pytest.mark.asyncio
async def test_mh200n_cen_trace_fires_all_event_types(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Replay MH200N CEN frames and verify that short press/release and long press/release events fire."""
    messages = [OWNMessage.parse(f) for f in _cen_frames(MH200N_TRACE_FILE)]

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

    base_b1 = {
        "object": 73,
        "pushbutton": 1,
        "where": "73",
        "gateway_mac": gateway_handler.mac,
        "entry_id": "entry_434",
    }
    base_b2 = {
        "object": 73,
        "pushbutton": 2,
        "where": "73",
        "gateway_mac": gateway_handler.mac,
        "entry_id": "entry_434",
    }

    # Button 1 events
    fire.assert_has_calls(
        [
            call("myhome_cen_event", {**base_b1, "event": CONF_SHORT_PRESS}),
            call("myhome_cen_event", {**base_b1, "event": CONF_SHORT_RELEASE}),
            call("myhome_cen_event", {**base_b1, "event": CONF_LONG_PRESS}),
            call("myhome_cen_event", {**base_b1, "event": CONF_LONG_RELEASE}),
        ],
        any_order=True,
    )

    # Button 2 events
    fire.assert_has_calls(
        [
            call("myhome_cen_event", {**base_b2, "event": CONF_SHORT_PRESS}),
            call("myhome_cen_event", {**base_b2, "event": CONF_SHORT_RELEASE}),
            call("myhome_cen_event", {**base_b2, "event": CONF_LONG_PRESS}),
            call("myhome_cen_event", {**base_b2, "event": CONF_LONG_RELEASE}),
        ],
        any_order=True,
    )


@pytest.mark.asyncio
async def test_myhomeserver1_cen_trace_fires_long_press_events(gateway_handler: MyHOMEGatewayHandler) -> None:
    """Replay MyHomeServer1 CEN frames and verify that long press and release events fire."""
    messages = [OWNMessage.parse(f) for f in _cen_frames(MHS1_TRACE_FILE)]

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
        "object": 73,
        "pushbutton": 1,
        "where": "73",
        "gateway_mac": gateway_handler.mac,
        "entry_id": "entry_434",
    }
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_SHORT_PRESS})
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_LONG_PRESS})
    fire.assert_any_call("myhome_cen_event", {**expected, "event": CONF_LONG_RELEASE})


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_file", TRACE_FILES, ids=lambda p: p.name)
async def test_full_trace_replay_without_errors(gateway_handler: MyHOMEGatewayHandler, trace_file: Path) -> None:
    """Replay ALL frames in the trace (including WHO 1, 13, 1001) to verify stability."""
    data = json.loads(trace_file.read_text(encoding="utf-8"))
    raw_frames = [f["raw"] for f in data["frames"]]
    messages = [OWNMessage.parse(f) for f in raw_frames]

    with (
        patch("custom_components.myhome.gateway.OWNEventSession") as session_class,
        patch("homeassistant.helpers.device_registry.async_get", return_value=MagicMock()),
    ):
        session = MagicMock()
        session.connect = AsyncMock(return_value={"Success": True})
        session.get_next = AsyncMock(side_effect=[*messages, asyncio.CancelledError()])
        session_class.return_value = session
        gateway_handler.send_status_request = AsyncMock()

        try:
            await gateway_handler.listening_loop()
        except asyncio.CancelledError:
            pass
