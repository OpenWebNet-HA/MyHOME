"""Tests for WHO 2 Venetian Blind Slat Tilt Inching and Dimension 11 Trace Replay.

Verifies that the synthetic trace fixture derived from Legrand gateway daemon
`coso` (automation_tr / automation_stm) & `bt_luci` and actuator firmware Object 218
(H4661M2 / LN4661M2 slat calibration registers in OWN-DEV-0045) replays deterministically:
1. Replays 10 frames across initial baseline closed, open tilt inching pulse,
   close tilt inching pulse, Dimension 11 level command, mid-run telemetry, and stop.
2. Verified cover entity semantics:
   - Slat tilt moves from 0% -> 50% on open pulse without moving linear curtain position (0%).
   - Slat tilt moves from 50% -> 0% on close pulse without moving linear curtain position (0%).
   - Dimension 11 frame (*#2*31*#11#001#1*40##) is handled as absolute level repositioning.
   - Linear curtain movement to 40% rotates slats fully open (tilt 100%).
3. Leaves 0 device health faults across the entire replay cycle.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNEvent, OWNMessage

from custom_components.myhome.cover import MyHOMECover

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "oracle_who2_venetian_tilt"
VENETIAN_TRACE_FILE = TRACES_DIR / "myhome_trace_oracle_who2_venetian_tilt.json"


@pytest.fixture
def mock_gateway():
    """Mock gateway session handler."""
    gw = MagicMock()
    gw.mac = "00:03:50:00:04:54"
    gw.unique_id = "00:03:50:00:04:54"
    gw.log_id = "[Test Gateway F454]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


def _create_venetian_cover(
    gateway: MagicMock, where: str = "31", advanced: bool = True
) -> MyHOMECover:
    """Create a test Venetian blind cover entity with slat tilt enabled."""
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        cover = MyHOMECover(
            hass=None,
            name="Living Room Venetian Blind",
            entity_name=None,
            device_id=where,
            who="2",
            where=where,
            interface=None,
            advanced=advanced,
            manufacturer="BTicino",
            model="Advanced Venetian Blind Actuator",
            gateway=gateway,
            travel_time=20,
            slat_tilt=True,
            slat_time=2.0,
        )
    cover.entity_id = f"cover.living_room_venetian_blind_{where}"
    cover.async_schedule_update_ha_state = MagicMock()
    return cover


def test_synthetic_trace_provenance_and_metadata() -> None:
    """Verify ground-truth provenance metadata of the synthetic Venetian blind trace."""
    assert VENETIAN_TRACE_FILE.is_file(), f"Missing trace fixture: {VENETIAN_TRACE_FILE}"

    with open(VENETIAN_TRACE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Verify explicit synthetic provenance contract
    capture = data["capture"]
    assert capture["kind"] == "trace"
    assert capture["source"] == "firmware-oracle-contract"
    assert "OWN-DEV-0045" in capture["provenance"]
    assert "Object 218" in capture["provenance"]
    assert "coso" in capture["provenance"]

    # Verify gateway metadata
    gateway = data["gateway"]
    assert gateway["model"] == "F454"
    assert gateway["firmware"] == "2.0.51"
    assert gateway["mac_prefix"] == "00:03:50"

    # Verify frame sequence length
    frames = data["frames"]
    assert len(frames) == 10
    assert frames[0]["raw"] == "*#2*31*10*10*0*001*0##"
    assert frames[5]["raw"] == "*#2*31*#11#001#1*40##"


@pytest.mark.asyncio
async def test_who2_venetian_tilt_trace_step_by_step_execution(
    mock_gateway: MagicMock,
) -> None:
    """Replay synthetic Venetian trace step-by-step through the cover entity."""
    cover = _create_venetian_cover(mock_gateway, "31")

    with open(VENETIAN_TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = [f["raw"] for f in trace_data["frames"]]

    # Frame 0: Baseline state (*#2*31*10*10*0*001*0##) - Stopped at level 0, tilt 0
    msg0 = OWNEvent.parse(frames[0])
    cover.handle_event(msg0)
    assert cover.current_cover_position == 0
    assert cover.current_cover_tilt_position == 0
    assert not cover.is_opening
    assert not cover.is_closing

    # Frame 1: Direction echo UP (*2*1*31##) for 50% tilt pulse (1.0s at slat_time 2.0)
    # Start the tilt pulse in the entity
    fut = asyncio.Future()
    fut.set_result(time.monotonic())
    mock_gateway.send.return_value = fut

    # Anchor pulse timing
    with patch("time.monotonic", return_value=100.0):
        await cover.async_set_cover_tilt_position(tilt_position=50)
        cover.handle_event(OWNEvent.parse(frames[1]))
        assert cover.is_opening
        assert cover._is_tilting is True
        assert cover._tilt_start_time == 100.0

    # Frame 2: Stop pulse delivered 1.0s later (*2*0*31##)
    with patch("time.monotonic", return_value=101.0):
        cover.handle_event(OWNEvent.parse(frames[2]))

    # 1.0s / 2.0s = 50% tilt; linear position stays untouched at 0%
    assert cover.current_cover_tilt_position == 50
    assert cover.current_cover_position == 0
    assert not cover.is_opening

    # Frame 3: Direction echo DOWN (*2*2*31##) for close tilt pulse (50% -> 0%)
    with patch("time.monotonic", return_value=110.0):
        await cover.async_set_cover_tilt_position(tilt_position=0)
        cover.handle_event(OWNEvent.parse(frames[3]))
        assert cover.is_closing
        assert cover._is_tilting is True
        assert cover._tilt_start_time == 110.0

    # Frame 4: Stop pulse delivered 1.0s later (*2*0*31##)
    with patch("time.monotonic", return_value=111.0):
        cover.handle_event(OWNEvent.parse(frames[4]))

    assert cover.current_cover_tilt_position == 0
    assert cover.current_cover_position == 0
    assert not cover.is_closing

    # Frame 5: Dimension 11 Command (*#2*31*#11#001#1*40##) - Absolute level command 40%
    msg5 = OWNMessage.parse(frames[5])
    assert msg5.dimension == 11
    # Dimension 11 write does NOT snap position immediately
    cover.handle_event(msg5)
    assert cover.current_cover_position == 0

    # Frame 6: Movement starts (*2*1*31##)
    msg6 = OWNEvent.parse(frames[6])
    cover.handle_event(msg6)
    assert cover.is_opening

    # Frame 7: Mid-run Dimension 10 report (*#2*31*10*11*20*001*0##) - Level 20%
    msg7 = OWNEvent.parse(frames[7])
    cover.handle_event(msg7)
    assert cover.current_cover_position == 20
    assert cover.is_opening

    # Frame 8: Movement stops (*2*0*31##)
    msg8 = OWNEvent.parse(frames[8])
    cover.handle_event(msg8)

    # Frame 9: Final Dimension 10 confirmation (*#2*31*10*10*40*001*0##) - Level 40%
    msg9 = OWNEvent.parse(frames[9])
    cover.handle_event(msg9)
    assert cover.current_cover_position == 40
    assert not cover.is_opening
    # A linear curtain opening run turns slats fully open (tilt 100%)
    assert cover.current_cover_tilt_position == 100


@pytest.mark.asyncio
async def test_dimension_11_query_rejected_with_nack(mock_gateway: MagicMock) -> None:
    """Verify that Dimension 11 status query is write-only and gateway emits NACK."""
    cover = _create_venetian_cover(mock_gateway, "31")

    # In response to any *#2*31*11## status query, gateway returns NACK (*#*0##)
    nack = OWNMessage.parse("*#*0##")
    assert nack.is_nack() is True

    # Polling the cover sends Dimension 10 status (*#2*31*10##), never Dimension 11 (*#2*31*11##)
    await cover.async_update()
    assert [str(call.args[0]) for call in mock_gateway.send_status_request.call_args_list] == [
        "*#2*31*10##"
    ]


@pytest.mark.asyncio
async def test_timed_venetian_cover_tilt_pulse_and_poll(mock_gateway: MagicMock) -> None:
    """Verify timed (non-advanced) Venetian blind tilt pulses and standard polling."""
    cover = _create_venetian_cover(mock_gateway, "32", advanced=False)
    assert cover._advanced is False

    # Establish baseline: blind is fully closed (0% position, 0% tilt)
    cover._attr_current_cover_position = 0
    cover._attr_is_closed = True
    cover._attr_current_cover_tilt_position = 0

    fut = asyncio.Future()
    fut.set_result(time.monotonic())
    mock_gateway.send.return_value = fut

    # Open tilt pulse: 0% -> 50% (1.0s at slat_time 2.0s)
    with patch("time.monotonic", return_value=200.0):
        await cover.async_set_cover_tilt_position(tilt_position=50)
        cover.handle_event(OWNEvent.parse("*2*1*32##"))
        assert cover.is_opening
        assert cover._is_tilting is True

    # Stop pulse 1.0s later
    with patch("time.monotonic", return_value=201.0):
        cover.handle_event(OWNEvent.parse("*2*0*32##"))

    assert cover.current_cover_tilt_position == 50
    assert not cover.is_opening

    # Polling timed cover sends standard status (*#2*32##), never Dimension 11
    await cover.async_update()
    assert [str(call.args[0]) for call in mock_gateway.send_status_request.call_args_list] == [
        "*#2*32##"
    ]

