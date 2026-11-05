"""Tests for #866: BTicino MH202 FW 1.0 standalone thermostat trace replay & *#4*Z## zone status poll.

Verifies from the live MH202 FW 1.0 trace captured on @xduss's 5-zone plant
(tests/fixtures/traces/issue_866/myhome_trace_MH202_all_2026-11-04T20-39-35.json):
1. Standalone thermostats (no #0 central unit) answer per-zone status queries (*#4*1##..*#4*5##)
   through the MH202 in ~160-250 ms with all five frames: Dimension 14 manual setpoint,
   Dimension 12 effective setpoint, *4*1*Z## heating mode, Dimension 13 local offset, and
   Dimension 0 ambient temperature.
2. Cold-start standalone zones hydrate hvac_mode, hvac_action, target_temperature (preserving
   Dimension 12 over trailing Dimension 14), and current_temperature when these frames are received.
3. Provenance and SHA-256 fixture checksum are pinned against regressions.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import MH202Profile, get_gateway_profile

from custom_components.myhome.climate import MyHOMEClimate

MAC = "00:03:50:00:08:66"
TRACE_FILE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_866"
    / "myhome_trace_MH202_all_2026-11-04T20-39-35.json"
)
TRACE_SHA256 = "b76a5294f6ba0ab44b6ce95887433231bd8ae527bbc2faec3abc8a95fbf1ec53"


@pytest.fixture(scope="module")
def mh202_trace_archive() -> dict[str, Any]:
    """Load the authentic 31-frame MH202 FW 1.0 trace archive from issue #866."""
    assert TRACE_FILE.is_file(), f"Missing trace fixture: {TRACE_FILE}"
    return json.loads(TRACE_FILE.read_text(encoding="utf-8"))


def _zone(hass, where: str, gateway=None) -> MyHOMEClimate:
    if gateway is None:
        gateway = MagicMock()
        gateway.mac = MAC
        gateway.send = AsyncMock()
        gateway.send_status_request = AsyncMock()
        gateway.has_central_unit = False
    zone = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=False,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gateway,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def test_mh202_trace_fixture_provenance_and_invariants(mh202_trace_archive: dict[str, Any]) -> None:
    """Verify SHA-256 fixture checksum and structural invariants of the MH202 trace."""
    assert hashlib.sha256(TRACE_FILE.read_bytes()).hexdigest() == TRACE_SHA256

    gw = mh202_trace_archive["gateway"]
    assert gw["model"] == "MH202"
    assert gw["firmware"] == "1.0"
    assert gw["mac_prefix"] == "00:03:50"
    assert gw["connection_type"] == "tcp"

    frames = mh202_trace_archive["frames"]
    assert len(frames) == 31

    tx_frames = [f for f in frames if f["direction"] == "tx"]
    rx_frames = [f for f in frames if f["direction"] == "rx"]
    assert len(tx_frames) == 5
    assert len(rx_frames) == 26

    # 5 sequential queries: *#4*1## .. *#4*5##
    assert [f["raw"] for f in tx_frames] == [f"*#4*{z}##" for z in range(1, 6)]

    # For each query, 5 frames returned: Dim 14, Dim 12, mode (*4*1*Z##), Dim 13, Dim 0
    for z in range(1, 6):
        z_str = str(z)
        z_rx = [f for f in rx_frames if f["where"] == z_str]
        assert len(z_rx) == 5, f"Expected 5 frames for zone {z_str}, got {len(z_rx)}"
        dims = [f["dimension"] for f in z_rx]
        assert dims == ["14", "12", None, "13", "0"]
        # Mode is WHAT 1 (heating)
        mode_frame = next(f for f in z_rx if f["dimension"] is None)
        assert mode_frame["raw"] == f"*4*1*{z_str}##"

        # Verify response latency is well under 1.5 seconds (~160-250 ms)
        tx_time = next(f["timestamp"] for f in tx_frames if f["where"] == z_str)
        last_rx_time = z_rx[-1]["timestamp"]
        elapsed = last_rx_time - tx_time
        assert 0.05 < elapsed < 1.5, f"Zone {z_str} response elapsed time {elapsed}s outside expected window"

    # Zone 15 background ambient temperature broadcast
    z15_frames = [f for f in rx_frames if f["where"] == "15"]
    assert len(z15_frames) == 1
    assert z15_frames[0]["raw"] == "*#4*15*0*0211##"


async def test_mh202_cold_start_replay_hydrates_standalone_zones(
    hass,
    mh202_trace_archive: dict[str, Any],
) -> None:
    """Replay all 26 RX frames on cold start and verify temperature, mode, and target temperature."""
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.has_central_unit = False

    zones = {str(z): _zone(hass, str(z), gateway=gateway) for z in range(1, 6)}
    zone15 = _zone(hass, "15", gateway=gateway)

    rx_frames = [f for f in mh202_trace_archive["frames"] if f["direction"] == "rx"]
    for f in rx_frames:
        msg = OWNHeatingEvent(f["raw"])
        where = f["where"]
        if where in zones:
            zones[where].handle_event(msg)
        elif where == "15":
            zone15.handle_event(msg)

    # Zones 1 and 2: setpoint 21.0 (Dim 12 0210 over Dim 14 0200), temp 21.0, heat
    for z in ("1", "2"):
        zone = zones[z]
        assert zone.hvac_mode == HVACMode.HEAT
        assert zone.current_temperature == 21.0
        assert zone.target_temperature == 21.0
        assert zone.extra_state_attributes["local_target_temperature"] == 21.0

    # Zones 3 and 4: setpoint 20.5 (Dim 12 0205 over Dim 14 0200), temp 20.6, heat
    for z in ("3", "4"):
        zone = zones[z]
        assert zone.hvac_mode == HVACMode.HEAT
        assert zone.current_temperature == 20.6
        assert zone.target_temperature == 20.5
        assert zone.extra_state_attributes["local_target_temperature"] == 20.5

    # Zone 5: setpoint 21.0, temp 21.0, heat
    zone5 = zones["5"]
    assert zone5.hvac_mode == HVACMode.HEAT
    assert zone5.current_temperature == 21.0
    assert zone5.target_temperature == 21.0

    # Zone 15: only received background temp 21.1; mode and setpoint remain uninitialized
    assert zone15.current_temperature == 21.1
    assert zone15.target_temperature is None
    assert zone15.hvac_mode is None


def test_mh202_profile_attributes() -> None:
    """Verify MH202Profile properties in OWNd."""
    profile = get_gateway_profile("MH202")
    assert isinstance(profile, MH202Profile)
    assert profile.model_name == "MH202"
    assert profile.max_command_sessions == 2
    assert profile.command_queue_delay == 0.10
    assert profile.supports_hmac is True
