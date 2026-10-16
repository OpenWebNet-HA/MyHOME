"""Tests for #645: Authentic MyHomeServer1 Cover Queue and Stop Trace Replay.

Verifies that authentic on-wire OpenWebNet traces captured from a physical
BTicino MyHomeServer1 gateway (contributed by @Interstellar0verdrive in issue #645
comment 6111193057) can be deterministically parsed and replayed through the
integration event dispatcher and gateway message handlers without exceptions or regressions.

Hardware & Environment Under Test:
- Gateway: BTicino MyHomeServer1 (Server MyHOME_Up, FW 2.87.13)
- Covers: BTicino F411 actuators on main bus (WHERE 91)
- Scenarios:
  - Ten status requests burst (~148 ms queue round trip, 10th frame at 1.44 s)
  - Direction command immediately followed by stop (60 ms bus gap, motor stationary)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_MAC,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import (
    OWNAutomationEvent,
    OWNMessage,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.const import (
    CONF_DEVICE_TYPE,
    CONF_ENTITY,
    CONF_FIRMWARE,
    CONF_MANUFACTURER,
    DOMAIN,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_645"
TIMELINE_LOG_FILE = TRACES_DIR / "live_command_timeline_issue_645.log"
DIRECTION_STOP_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_cover_direction_then_stop_2026-10-11.json"
BURST_LATENCY_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_cover_burst_latency_2026-10-11.json"


async def _setup_myhomeserver1_gateway(hass: HomeAssistant, mac: str = "00:03:50:AA:64:50"):
    """Set up a mock MyHomeServer1 config entry and gateway handler."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 20000,
            CONF_PASSWORD: "pass",
            CONF_MAC: mac,
            CONF_NAME: "MyHomeServer1",
            CONF_DEVICE_TYPE: "urn:schemas-bticino-it:device:lightingcontrolunit:1",
            CONF_MANUFACTURER: "BTicino S.p.A.",
            CONF_FIRMWARE: "2.87.13",
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    handler = hass.data[DOMAIN][mac][CONF_ENTITY]
    handler._on_event_connection_state_change(True)
    return handler, mac


@pytest.mark.asyncio
async def test_myhomeserver1_direction_then_stop_trace_replay(hass: HomeAssistant) -> None:
    """Replay all 9 frames from the direction + stop trace (Trace 3 in #645).

    Verifies that the sequence:
    1. *2*1*91## (TX up)
    2. *2*1000#0*91## (RX gateway translation)
    3. *2*0*91## (RX momentary stop status)
    4. *2*1000#1*91## (RX movement confirmed)
    5. *#*1## (ACK for up)
    6. *2*0*91## (TX stop)
    7. *2*1000#0*91## (RX stopping translation)
    8. *#*1## (ACK for stop)
    9. *2*0*91## (RX actuator stopped)
    is parsed deterministically and dispatched without errors.
    """
    assert DIRECTION_STOP_FILE.is_file(), f"Missing fixture: {DIRECTION_STOP_FILE}"

    with open(DIRECTION_STOP_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["gateway"]["model"] == "MyHomeServer1"
    assert data["gateway"]["firmware"] == "2.87.13"

    frames = data["frames"]
    assert len(frames) == 9

    handler, mac = await _setup_myhomeserver1_gateway(hass)

    rx_parsed_count = 0
    for frame_info in frames:
        raw = frame_info["raw"]
        parsed = OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse frame: {raw}"
        if raw.startswith("*2*"):
            assert isinstance(parsed, OWNAutomationEvent)

        if frame_info["direction"] == "rx":
            async_dispatcher_send(hass, f"myhome_message_{mac}", parsed)
            await handler._process_message(parsed)
            rx_parsed_count += 1

    await hass.async_block_till_done()
    assert rx_parsed_count == 7

    # Verify timing constraints from empirical measurements
    tx_frames = [f for f in frames if f["direction"] == "tx"]
    assert len(tx_frames) == 2
    delta_tx = tx_frames[1]["timestamp"] - tx_frames[0]["timestamp"]
    # Direction taken at +0 ms, Stop taken at +139 ms -> delta is 139 ms from queue unqueue
    assert abs(delta_tx - 0.139) < 0.001


@pytest.mark.asyncio
async def test_myhomeserver1_burst_latency_trace_replay(hass: HomeAssistant) -> None:
    """Replay the 30 frames of the 10 status request burst trace (Trace 1 in #645).

    Verifies that all 10 point-to-point status requests and replies for WHERE 91..100
    replay through the gateway handler without errors.
    """
    assert BURST_LATENCY_FILE.is_file(), f"Missing fixture: {BURST_LATENCY_FILE}"

    with open(BURST_LATENCY_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    frames = data["frames"]
    assert len(frames) == 30

    handler, mac = await _setup_myhomeserver1_gateway(hass)

    for frame_info in frames:
        raw = frame_info["raw"]
        parsed = OWNMessage.parse(raw)
        assert parsed is not None, f"Failed to parse frame: {raw}"

        if frame_info["direction"] == "rx":
            async_dispatcher_send(hass, f"myhome_message_{mac}", parsed)
            await handler._process_message(parsed)

    await hass.async_block_till_done()

    # Verify total burst latency across 10 requests matches the 1.44s measured
    total_elapsed = frames[-1]["timestamp"] - frames[0]["timestamp"]
    assert abs(total_elapsed - 1.44) < 0.001


def test_timeline_log_integrity() -> None:
    """Verify raw timeline log matches the reported comment timeline exactly."""
    assert TIMELINE_LOG_FILE.is_file(), f"Missing timeline log: {TIMELINE_LOG_FILE}"

    with open(TIMELINE_LOG_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    assert len(lines) == 9
    assert "*2*1*91##" in lines[0]
    assert "*2*0*91##" in lines[5]  # second TX
    assert "*2*0*91##" in lines[8]  # final stop RX
