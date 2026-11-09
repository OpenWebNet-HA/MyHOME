"""Trace replay verification tests for issue #863 / PR #864.

Validates the authentic MyHomeServer1 (firmware 3.87.13) sweep and WHO 4 actuator
toggling trace captured on @andreasorbelli's physical plant:
  tests/fixtures/traces/issue_863/myhome_sweep_MyHomeServer1_all_2026-11-09T13-00-57.json

Key facts verified from real hardware:
1. Actuator #4 addressing: `*#4*30#4*#20*1##` (TX) followed by `*#4*30#4*20*1##` (RX, +122.8 ms)
   and `*#4*30#4*#20*0##` (TX) followed by `*#4*30#4*20*0##` (RX, +125.8 ms).
2. Routing isolation: frames on actuator `30#4` route strictly to `switch.riscaldamento_sala`
   (`WHERE = "30#4"`) and never overwrite or alter `switch.riscaldamento_camera_ginevra`
   (`WHERE = "30#1"`), and do not bleed into F422-routed interfaces (`30#4#4#01`).
3. Climate actuator tracking: `MyHOMEClimate.handle_event` updates `_actuator_states["4"]`
   without polluting `_actuator_states["1"]`.
4. Sweep refusal: MyHomeServer1 refuses `*#4*0##` (GENERAL_ZONE_REQUEST_REFUSED), so the
   diagnostic sweep correctly withholds it to avoid unnecessary NACK round-trip delays.
5. Lighting and Energy: sweep captures 42 WHO 1 light states and live WHO 18 active-power
   streaming from address 51 (~1 Hz).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant
from OWNd.message import OWNHeatingCommand, OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.climate import (
    async_setup_entry as climate_setup_entry,
)
from custom_components.myhome.const import (
    CONF_BUS_INTERFACE,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    PLATFORMS,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.gateway import GENERAL_ZONE_REQUEST_REFUSED
from custom_components.myhome.switch import (
    MyHOMEThermoSwitch,
)
from custom_components.myhome.switch import (
    async_setup_entry as switch_setup_entry,
)
from custom_components.myhome.where_grammar import (
    thermo_dim20_is_active,
    thermo_event_actuator,
    thermo_event_interface,
    thermo_event_zone,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "traces"
    / "issue_863"
    / "myhome_sweep_MyHomeServer1_all_2026-11-09T13-00-57.json"
)
MAC = "00:03:50:86:30:01"


@pytest.fixture(scope="module")
def trace_archive() -> dict[str, Any]:
    """Load the authentic 115-frame MyHomeServer1 trace archive."""
    assert FIXTURE_PATH.is_file(), f"Missing trace fixture: {FIXTURE_PATH}"
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def trace_frames(trace_archive: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all 115 frames from the trace archive."""
    return trace_archive["frames"]


def _mock_gateway() -> MagicMock:
    gw = MagicMock()
    gw.name = "MyHomeServer1"
    gw.mac = MAC
    gw.unique_id = MAC
    gw.log_id = f"[MyHomeGateway - {MAC}]"
    gw.model = "MyHomeServer1"
    gw.manufacturer = "BTicino S.p.A."
    gw.firmware = "3.87.13"
    gw.is_connected = True
    gw.has_central_unit = False
    gw.is_follower = False
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.sending_workers = []
    gw.availability_signal = f"myhome_gw_available_{MAC}"
    gw.is_who_available = MagicMock(return_value=True)
    gw.actuator_request_refused = False
    gw.interface_zone_request_refused = False
    gw.general_zone_request_refused = True
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw.close_listener = AsyncMock(return_value=True)
    gw.watch_zone_actuators = MagicMock(return_value=MagicMock())
    return gw


def _mock_entry(
    hass: HomeAssistant,
    gw: MagicMock,
    platforms: dict[str, Any] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: gw.mac, "host": "192.0.2.86", "port": 20000},
        unique_id=gw.mac,
    )
    entry.add_to_hass(hass)
    plat_dict = {p: {} for p in PLATFORMS}
    if platforms:
        for k, v in platforms.items():
            plat_dict[k] = dict(v)
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms=plat_dict,
        entities={p: {} for p in PLATFORMS},
    )
    return entry


# ---------------------------------------------------------------------------
# 1. Archive Structure & Gateway Identification
# ---------------------------------------------------------------------------


def test_issue_863_trace_metadata_and_gateway_discovery(
    trace_archive: dict[str, Any],
    trace_frames: list[dict[str, Any]],
) -> None:
    """Verify trace archive metadata and gateway identification frames."""
    assert trace_archive["capture"]["kind"] == "sweep"
    assert trace_archive["capture"]["window"]["frames"] == 115
    assert len(trace_frames) == 115

    gw_meta = trace_archive["gateway"]
    assert gw_meta["model"] == "MyHomeServer1"
    assert gw_meta["firmware"] == "3.87.13"
    assert gw_meta["mac_prefix"] == "00:03:50"

    ident = gw_meta["identification"]
    assert ident["who13_code"] == "200"
    assert ident["who13_firmware"] == "3.87.13"
    assert ident["who13_kernel"] == "5.10.35"
    assert ident["who13_distribution"] == "2.26.3"
    assert ident["who1013_code"] == "67"
    assert ident["profile"] == "MyHomeServer1Profile"

    # Gateway queries in the sweep: RTC (dim 0), model (dim 15), fw (dim 16),
    # kernel (dim 23), distro (dim 24), and object model (WHO 1013).
    raw_frames = [f["raw"] for f in trace_frames]
    assert "*#13**0*14*00*05*999##" in raw_frames
    assert "*#13**15*200##" in raw_frames
    assert "*#13**16*3*87*13##" in raw_frames
    assert "*#13**23*5*10*35##" in raw_frames
    assert "*#13**24*2*26*3##" in raw_frames
    assert "*#1013**1*67*15*5*0##" in raw_frames


def test_issue_863_refused_general_zone_request_not_sent(
    trace_frames: list[dict[str, Any]],
) -> None:
    """Verify *#4*0## is absent from the sweep due to GENERAL_ZONE_REQUEST_REFUSED."""
    assert "MYHOMESERVER1" in GENERAL_ZONE_REQUEST_REFUSED
    tx_frames = [f["raw"] for f in trace_frames if f.get("direction") == "tx"]
    assert "*#4*0##" not in tx_frames


# ---------------------------------------------------------------------------
# 2. WHO 4 Actuator #4 Trace Replay & Entity Routing
# ---------------------------------------------------------------------------


def test_issue_863_trace_actuator_grammar_parsing(
    trace_frames: list[dict[str, Any]],
) -> None:
    """Verify the 4 WHO=4 frames parse with exact zone, actuator, and active state."""
    who4_frames = [f for f in trace_frames if f.get("who") == "4"]
    assert len(who4_frames) == 4

    cmd_on = who4_frames[0]
    evt_on = who4_frames[1]
    cmd_off = who4_frames[2]
    evt_off = who4_frames[3]

    assert cmd_on["direction"] == "tx" and cmd_on["raw"] == "*#4*30#4*#20*1##"
    assert evt_on["direction"] == "rx" and evt_on["raw"] == "*#4*30#4*20*1##"
    assert cmd_off["direction"] == "tx" and cmd_off["raw"] == "*#4*30#4*#20*0##"
    assert evt_off["direction"] == "rx" and evt_off["raw"] == "*#4*30#4*20*0##"

    # Command session round-trip latency on real MyHomeServer1
    on_latency_ms = (evt_on["timestamp"] - cmd_on["timestamp"]) * 1000
    off_latency_ms = (evt_off["timestamp"] - cmd_off["timestamp"]) * 1000
    assert 100 < on_latency_ms < 150
    assert 100 < off_latency_ms < 150

    # Grammar helpers: actuator must be '4' and interface None
    parsed_cmd_on = OWNHeatingCommand(cmd_on["raw"])
    assert thermo_event_zone(parsed_cmd_on) == "30"
    assert thermo_event_actuator(parsed_cmd_on) == "4"
    assert thermo_event_interface(parsed_cmd_on) is None
    assert thermo_dim20_is_active(parsed_cmd_on) is True

    parsed_evt_on = OWNHeatingEvent(evt_on["raw"])
    assert thermo_event_zone(parsed_evt_on) == "30"
    assert thermo_event_actuator(parsed_evt_on) == "4"
    assert thermo_event_interface(parsed_evt_on) is None
    assert thermo_dim20_is_active(parsed_evt_on) is True

    parsed_cmd_off = OWNHeatingCommand(cmd_off["raw"])
    assert thermo_event_zone(parsed_cmd_off) == "30"
    assert thermo_event_actuator(parsed_cmd_off) == "4"
    assert thermo_event_interface(parsed_cmd_off) is None
    assert thermo_dim20_is_active(parsed_cmd_off) is False

    parsed_evt_off = OWNHeatingEvent(evt_off["raw"])
    assert thermo_event_zone(parsed_evt_off) == "30"
    assert thermo_event_actuator(parsed_evt_off) == "4"
    assert thermo_event_interface(parsed_evt_off) is None
    assert thermo_dim20_is_active(parsed_evt_off) is False


@pytest.mark.asyncio
async def test_issue_863_trace_replay_thermo_switches(
    hass: HomeAssistant,
    trace_frames: list[dict[str, Any]],
) -> None:
    """Replay the authentic trace frames against MyHOMEThermoSwitch entities.

    Verifies:
    1. Actuator 30#4 switches ON then OFF.
    2. Actuator 30#1 is never updated or overwritten.
    3. F422 routed actuator 30#4#4#01 is never updated.
    """
    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "switch": {
                "4-30#1": {
                    CONF_WHO: "4",
                    CONF_WHERE: "30#1",
                    CONF_NAME: "Riscaldamento Camera Ginevra",
                },
                "4-30#4": {
                    CONF_WHO: "4",
                    CONF_WHERE: "30#4",
                    CONF_NAME: "Riscaldamento Sala",
                },
                "4-30#4-if01": {
                    CONF_WHO: "4",
                    CONF_WHERE: "30#4",
                    CONF_BUS_INTERFACE: "01",
                    CONF_NAME: "Riscaldamento Sala F422",
                },
            }
        },
    )
    added: list[MyHOMEThermoSwitch] = []
    await switch_setup_entry(hass, entry, lambda ents, _u=False: added.extend(ents))
    assert len(added) == 3
    by_key = {(ent._where, ent._interface): ent for ent in added}
    sw_1 = by_key[("30#1", None)]
    sw_4 = by_key[("30#4", None)]
    routed_sw_4 = by_key[("30#4", "01")]

    for sw in added:
        sw.async_write_ha_state = MagicMock()

    # Pre-seed sw_1 to OFF to ensure it does not flip to ON
    sw_1.handle_event(OWNHeatingEvent("*#4*30#1*20*0##"))
    assert sw_1.is_on is False
    assert sw_4.is_on is None
    assert routed_sw_4.is_on is None

    # Step 1: Replay ON sequence from trace: TX *#4*30#4*#20*1##, then RX *#4*30#4*20*1##
    cmd_on = OWNHeatingCommand("*#4*30#4*#20*1##")
    evt_on = OWNHeatingEvent("*#4*30#4*20*1##")

    sw_4.handle_event(cmd_on)
    sw_1.handle_event(cmd_on)
    routed_sw_4.handle_event(cmd_on)
    assert sw_4.is_on is True
    assert sw_1.is_on is False
    assert routed_sw_4.is_on is None

    sw_4.handle_event(evt_on)
    sw_1.handle_event(evt_on)
    routed_sw_4.handle_event(evt_on)
    assert sw_4.is_on is True
    assert sw_1.is_on is False
    assert routed_sw_4.is_on is None

    # Step 2: Replay OFF sequence from trace: TX *#4*30#4*#20*0##, then RX *#4*30#4*20*0##
    cmd_off = OWNHeatingCommand("*#4*30#4*#20*0##")
    evt_off = OWNHeatingEvent("*#4*30#4*20*0##")

    sw_4.handle_event(cmd_off)
    sw_1.handle_event(cmd_off)
    routed_sw_4.handle_event(cmd_off)
    assert sw_4.is_on is False
    assert sw_1.is_on is False
    assert routed_sw_4.is_on is None

    sw_4.handle_event(evt_off)
    sw_1.handle_event(evt_off)
    routed_sw_4.handle_event(evt_off)
    assert sw_4.is_on is False
    assert sw_1.is_on is False
    assert routed_sw_4.is_on is None


@pytest.mark.asyncio
async def test_issue_863_trace_replay_climate_actuator_tracking(
    hass: HomeAssistant,
) -> None:
    """Verify MyHOMEClimate records actuator states cleanly on trace replay."""
    gw = _mock_gateway()
    entry = _mock_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "4-30": {
                    CONF_WHO: "4",
                    CONF_WHERE: "30",
                    CONF_NAME: "Zona 30",
                }
            }
        },
    )
    added: list[MyHOMEClimate] = []
    await climate_setup_entry(hass, entry, lambda ents, _u=False: added.extend(ents))
    assert len(added) == 1
    climate = added[0]
    climate.async_write_ha_state = MagicMock()

    # Replay trace ON event: *#4*30#4*20*1##
    climate.handle_event(OWNHeatingEvent("*#4*30#4*20*1##"))
    assert climate._actuator_states.get("4") is True
    assert "1" not in climate._actuator_states

    # Replay trace OFF event: *#4*30#4*20*0##
    climate.handle_event(OWNHeatingEvent("*#4*30#4*20*0##"))
    assert climate._actuator_states.get("4") is False
    assert "1" not in climate._actuator_states


# ---------------------------------------------------------------------------
# 3. WHO 1 & WHO 18 Sweep Subsystems
# ---------------------------------------------------------------------------


def test_issue_863_trace_lighting_and_energy_frames(
    trace_frames: list[dict[str, Any]],
) -> None:
    """Verify WHO 1 and WHO 18 frames in the sweep parse cleanly."""
    who1_frames = [f for f in trace_frames if f.get("who") == "1"]
    assert len(who1_frames) == 43
    assert who1_frames[0]["direction"] == "tx" and who1_frames[0]["raw"] == "*#1*0##"

    # All 42 rx frames should parse as valid OWNLightingEvent
    for f in who1_frames[1:]:
        assert f["direction"] == "rx"
        msg = OWNMessage.parse(f["raw"])
        assert msg is not None
        assert msg.is_valid

    who18_frames = [f for f in trace_frames if f.get("who") == "18"]
    assert len(who18_frames) == 55

    # Filter active-power reports from device 51
    dim113_reports = [f for f in who18_frames if f.get("dimension") == "113"]
    assert len(dim113_reports) == 19
    for f in dim113_reports:
        assert f["where"] == "51"
        assert f["raw"].startswith("*#18*51*113*")
