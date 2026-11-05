"""Tests for Issue #868: MyHomeServer1 / BTicino F401 Venetian Blind Tilt Stale Estimate.

Verifies the fix for:
[Bug]: MyHomeServer1 gateway / BTicino F401 actuator – Venetian blind tilt estimate stays stale after downward movement
https://github.com/OpenWebNet-HA/MyHOME/issues/868

Covering:
1. Authentic MyHomeServer1 + F401 trace replay (6 frames).
2. Actuator reporting Dimension 10 stopped (`status 10`) at an intermediate level (e.g. 75)
   before or without `*2*0*WHERE##`.
3. Slat tilt updating appropriately based on movement direction (downward closes, upward opens).
4. External level change / position jump rotating slats accordingly.
5. Preserving tilt when position does not change (e.g. status polling).
6. Subsequent `open_cover_tilt` / `close_cover_tilt` driving the motor as expected.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from OWNd.message import OWNEvent, OWNMessage

from custom_components.myhome.cover import MyHOMECover

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_868"
TRACE_FILE = TRACES_DIR / "myhome_trace_MyHomeServer1_venetian_blind_75_2026-11-05.json"


@pytest.fixture
def mock_gateway() -> MagicMock:
    """Mock gateway session handler."""
    gw = MagicMock()
    gw.mac = "00:03:50:00:01:15"
    gw.unique_id = "00:03:50:00:01:15"
    gw.log_id = "[MyHomeServer1 00:03:50:00:01:15]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


def _create_venetian_cover(
    gateway: MagicMock,
    where: str = "0115",
    *,
    advanced: bool = True,
    slat_tilt: bool = True,
    slat_time: float = 2.0,
    travel_time: float = 25.0,
) -> MyHOMECover:
    """Create a test Venetian blind cover entity."""
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
            model="F401",
            gateway=gateway,
            travel_time=travel_time,
            slat_tilt=slat_tilt,
            slat_time=slat_time,
        )
    cover.entity_id = f"cover.living_room_venetian_blind_{where}"
    cover.async_schedule_update_ha_state = MagicMock()
    return cover


def test_issue_868_trace_metadata() -> None:
    """Verify ground-truth provenance metadata of the authentic issue #868 trace."""
    assert TRACE_FILE.is_file(), f"Missing trace fixture: {TRACE_FILE}"

    with open(TRACE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    gateway = data["gateway"]
    assert gateway["model"] == "MyHomeServer1"
    assert gateway["firmware"] == "3.87.13"

    frames = data["frames"]
    assert len(frames) == 6
    assert frames[0]["raw"] == "*#2*0115*#11#001*75##"
    assert frames[2]["raw"] == "*#2*0115*10*12*100*001*0##"
    assert frames[4]["raw"] == "*#2*0115*10*10*75*001*0##"
    assert frames[5]["raw"] == "*2*0*0115##"


@pytest.mark.asyncio
async def test_issue_868_authentic_trace_replay_and_subsequent_tilt(
    mock_gateway: MagicMock,
) -> None:
    """Replay authentic MyHomeServer1 + F401 trace and verify tilt updates and subsequent open_tilt."""
    cover = _create_venetian_cover(mock_gateway, "0115")

    with open(TRACE_FILE, "r", encoding="utf-8") as f:
        trace_data = json.load(f)

    frames = [f["raw"] for f in trace_data["frames"]]

    # Baseline: Cover is fully raised (100%) and slats are open (tilt 100%)
    cover._attr_current_cover_position = 100
    cover._attr_current_cover_tilt_position = 100
    cover._attr_is_closed = False

    t0 = 1000.0

    # Frame 0: TX command to 75% (*#2*0115*#11#001*75##)
    # Frame 1: RX echo (*#2*0115*#11#001#1*75##)
    cover.handle_event(OWNMessage.parse(frames[1]))
    assert cover.current_cover_position == 100
    assert cover.current_cover_tilt_position == 100

    # Frame 2: RX moving DOWN Dimension 10 report (*#2*0115*10*12*100*001*0##)
    with patch("time.monotonic", return_value=t0):
        cover.handle_event(OWNEvent.parse(frames[2]))
    assert cover.is_closing is True
    assert cover.is_opening is False
    assert cover._run_started_at == t0

    # Frame 3: RX direction frame DOWN (*2*2*0115##)
    with patch("time.monotonic", return_value=t0 + 0.1):
        cover.handle_event(OWNEvent.parse(frames[3]))
    assert cover.is_closing is True
    # Anchor stays at t0
    assert cover._run_started_at == t0

    # Frame 4: RX Dimension 10 STOP at 75% (*#2*0115*10*10*75*001*0##)
    # This arrives BEFORE *2*0*0115##.
    # The downward run must immediately rotate the slats to closed (tilt = 0%).
    t_stop = t0 + 15.4
    with patch("time.monotonic", return_value=t_stop):
        cover.handle_event(OWNEvent.parse(frames[4]))

    assert cover.current_cover_position == 75
    assert cover.is_closing is False
    assert cover.is_opening is False
    # Slat tilt MUST be 0% after downward movement
    assert cover.current_cover_tilt_position == 0

    # Frame 5: RX stop frame (*2*0*0115##)
    with patch("time.monotonic", return_value=t_stop + 0.05):
        cover.handle_event(OWNEvent.parse(frames[5]))

    assert cover.current_cover_position == 75
    assert cover.current_cover_tilt_position == 0
    assert cover.is_closing is False

    # Now verify Requirement 5: subsequent open_cover_tilt drives the motor
    fut = asyncio.Future()
    fut.set_result(t_stop + 1.0)
    mock_gateway.send.return_value = fut

    with (
        patch("time.monotonic", return_value=t_stop + 1.0),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await cover.async_open_cover_tilt()
        # Verify outgoing motor command was sent to OPEN
        assert mock_gateway.send.await_count == 1
        assert str(mock_gateway.send.call_args[0][0]) == "*2*1*0115##"
        assert cover._is_tilting is True

        cover._motor_started.set()
        if cover._stop_task:
            await cover._stop_task

    # Tilt is now 100%, position remains 75%
    assert cover.current_cover_tilt_position == 100
    assert cover.current_cover_position == 75
    assert cover._is_tilting is False


@pytest.mark.asyncio
async def test_dimension_10_stop_without_what0_frame(mock_gateway: MagicMock) -> None:
    """Verify that Dimension 10 status 10 updates tilt even if *2*0*WHERE## never arrives."""
    cover = _create_venetian_cover(mock_gateway, "0115")

    # 1. Downward run without *2*0*WHERE##
    cover._attr_current_cover_position = 100
    cover._attr_current_cover_tilt_position = 100
    t0 = 100.0

    # Moving down
    with patch("time.monotonic", return_value=t0):
        cover.handle_event(OWNEvent.parse("*#2*0115*10*12*100*001*0##"))
    assert cover.is_closing is True

    # Stopped at 60 via Dimension 10; NO *2*0*0115## is sent
    with patch("time.monotonic", return_value=t0 + 10.0):
        cover.handle_event(OWNEvent.parse("*#2*0115*10*10*60*001*0##"))

    assert cover.current_cover_position == 60
    assert cover.is_closing is False
    assert cover.current_cover_tilt_position == 0

    # 2. Upward run without *2*0*WHERE##
    # Moving up from 60
    t1 = 200.0
    with patch("time.monotonic", return_value=t1):
        cover.handle_event(OWNEvent.parse("*#2*0115*10*11*60*001*0##"))
    assert cover.is_opening is True

    # Stopped at 80 via Dimension 10; NO *2*0*0115## is sent
    with patch("time.monotonic", return_value=t1 + 8.0):
        cover.handle_event(OWNEvent.parse("*#2*0115*10*10*80*001*0##"))

    assert cover.current_cover_position == 80
    assert cover.is_opening is False
    assert cover.current_cover_tilt_position == 100


@pytest.mark.asyncio
async def test_external_position_jump_rotates_slats(mock_gateway: MagicMock) -> None:
    """Verify that an external position jump without prior movement frames rotates slats accordingly."""
    cover = _create_venetian_cover(mock_gateway, "0115")

    # Baseline: Cover is idle at 100%, tilt 100%
    cover._attr_current_cover_position = 100
    cover._attr_current_cover_tilt_position = 100
    assert cover.is_opening is False
    assert cover.is_closing is False

    # External downward position jump from 100 to 75
    cover.handle_event(OWNEvent.parse("*#2*0115*10*10*75*001*0##"))
    assert cover.current_cover_position == 75
    # Downward jump rotates slats to closed (0%)
    assert cover.current_cover_tilt_position == 0

    # External upward position jump from 75 to 90
    cover.handle_event(OWNEvent.parse("*#2*0115*10*10*90*001*0##"))
    assert cover.current_cover_position == 90
    # Upward jump rotates slats to open (100%)
    assert cover.current_cover_tilt_position == 100


@pytest.mark.asyncio
async def test_status_polling_preserves_tilt(mock_gateway: MagicMock) -> None:
    """Verify that status polling (no position change) preserves current slat tilt."""
    cover = _create_venetian_cover(mock_gateway, "0115")

    # Set position to 75, tilt to 50% (partially tilted)
    cover._attr_current_cover_position = 75
    cover._attr_current_cover_tilt_position = 50

    # Actuator poll reply arrives with same position 75
    cover.handle_event(OWNEvent.parse("*#2*0115*10*10*75*001*0##"))
    assert cover.current_cover_position == 75
    assert cover.current_cover_tilt_position == 50

    # Multiple successive poll replies
    for _ in range(3):
        cover.handle_event(OWNEvent.parse("*#2*0115*10*10*75*001*0##"))
        assert cover.current_cover_position == 75
        assert cover.current_cover_tilt_position == 50


@pytest.mark.asyncio
async def test_subsequent_close_cover_tilt_drives_motor(mock_gateway: MagicMock) -> None:
    """Verify that subsequent close_cover_tilt drives the motor when slats are open."""
    cover = _create_venetian_cover(mock_gateway, "0115")

    # Cover stopped at 75, tilt is 100
    cover._attr_current_cover_position = 75
    cover._attr_current_cover_tilt_position = 100

    fut = asyncio.Future()
    fut.set_result(100.0)
    mock_gateway.send.return_value = fut

    with (
        patch("time.monotonic", return_value=100.0),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await cover.async_close_cover_tilt()
        # Verify outgoing motor command was sent to CLOSE (DOWN)
        assert mock_gateway.send.await_count == 1
        assert str(mock_gateway.send.call_args[0][0]) == "*2*2*0115##"
        assert cover._is_tilting is True

        cover._motor_started.set()
        if cover._stop_task:
            await cover._stop_task

    assert cover.current_cover_tilt_position == 0
    assert cover.current_cover_position == 75
    assert cover._is_tilting is False


@pytest.mark.asyncio
async def test_partial_slat_rotation_on_short_impulse(mock_gateway: MagicMock) -> None:
    """Verify proportional slat rotation on short motor impulses shorter than slat_time."""
    cover = _create_venetian_cover(mock_gateway, "0115", slat_time=2.0)

    # Blind at 50%, tilt at 0%
    cover._attr_current_cover_position = 50
    cover._attr_current_cover_tilt_position = 0

    t0 = 500.0
    # Short opening run: 0.5s (25% of 2.0s slat_time)
    with patch("time.monotonic", return_value=t0):
        cover.handle_event(OWNEvent.parse("*2*1*0115##"))
    assert cover.is_opening is True

    # Stop after 0.5s
    with patch("time.monotonic", return_value=t0 + 0.5):
        cover.handle_event(OWNEvent.parse("*2*0*0115##"))

    assert cover.current_cover_position == 50
    # 0% + (0.5 / 2.0) * 100% = 25%
    assert cover.current_cover_tilt_position == 25
