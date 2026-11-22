"""Unit and end-to-end integration tests for Issue #949 (WHO=4 0-10 V fan-coil output level).

Covers:
1. ``where_grammar`` proportional decoding helpers: ``thermo_output_level``,
   ``thermo_dim20_level``, and ``thermo_dim20_actuator_target`` (strictly ``Z#N``,
   rejecting bare ``Z`` and ``Z#0``).
2. Live hardware capture replay from ``xmavgithub``'s ``MyHomeServer1`` ``2.87.13``
   Sabiana 0-10 V fan-coil plant (``tests/fixtures/traces/issue_949/``):
   - Unseeded 9-frame verbatim replay (``actuators`` ``{"2": "on", "3": "on"}`` during
     heating, ``{"1": "off", "2": "off", "3": "off"}`` at rest, ``actuator_levels`` and
     ``sensor`` stepping ``20 -> 23 -> 50 -> 80 -> 100 -> 0`` with zero sensors for
     ``1#1`` and ``1#2``).
   - Seeded replay with zone context, paired ``#20`` write frames, duplicate
     ``*20*`` suppression, and shared HA Device linkage.
3. ``MyHOMEClimate`` proportional edge cases: deferred ``#785`` Dimension 20 replay,
   ``#932`` ``repeated_off`` at ``20*100``, ignoring ``Z#0`` and bare ``Z`` for
   ``actuator_levels``, no Dimension 19 ``cooling_output_level`` / ``heating_output_level``,
   ``_unrecorded_attributes`` recorder exclusion, and zone-wide resets.
4. ``MyHOMEThermoSwitch`` ``output_level`` (single actuator) and ``actuator_levels``
   (whole-zone) strictly on explicit ``Z#N`` (not ``Z#0`` or bare ``Z``), inactive
   Dimension 19 resets, and ``_unrecorded_attributes`` recorder exclusion.
5. ``MyHOMEActuatorOutputSensor`` lifecycle: ``SensorEntity`` (not ``RestoreSensor``),
   ``unknown`` state after restart on ``MH202`` and climate-less zones, unconditional
   ``watch_zone_actuators`` registration when ``not interface_refused`` and
   ``_poll_on_add`` fallback, ``thermo_switch`` exclusion and stale registry pruning,
   YAML ``climate`` device linkage, router key isolation, ``async_update`` gateway
   guards, and zone-wide resets.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorEntity,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_MAC, CONF_NAME, PERCENTAGE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingCommand, OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.myhome import myhome_device
from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.climate import async_setup_entry as climate_setup_entry
from custom_components.myhome.const import (
    CONF_STANDALONE,
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_THERMO_SWITCH,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.sensor import (
    MyHOMEActuatorOutputSensor,
    MyHOMEClimateTemperatureSensor,
    MyHOMESensorEntity,
)
from custom_components.myhome.sensor import async_setup_entry as sensor_setup_entry
from custom_components.myhome.sensor import (
    async_unload_entry as sensor_unload_entry,
)
from custom_components.myhome.switch import MyHOMEThermoSwitch
from custom_components.myhome.typing_compat import device_info_to_dict
from custom_components.myhome.where_grammar import (
    thermo_dim20_actuator_target,
    thermo_dim20_level,
    thermo_output_level,
)
from tests.test_issue_930_device_info_dataclass import StrictDataclassDeviceInfo

TRACE_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_949"
MHS1_RAMP_TRACE_PATH = (
    TRACE_DIR / "myhome_trace_MyHomeServer1_fancoil_dim20_ramp_2026-11-18.json"
)
MHS1_FULL_CYCLE_TRACE_PATH = (
    TRACE_DIR / "myhome_trace_MyHomeServer1_fancoil_dim20_full_cycle_2026-11-18.json"
)
MAC = "00:03:50:00:09:08"


def _make_gateway(
    model: str = "MyHomeServer1",
    firmware: str = "2.87.13",
    *,
    mac: str = MAC,
    actuator_refused: bool = False,
    zone_actuator_refused: bool = False,
    interface_refused: bool = True,
) -> MagicMock:
    gw = MagicMock()
    gw.name = model
    gw.mac = mac
    gw.unique_id = mac
    gw.log_id = f"[{model} - {mac}]"
    gw.model = model
    gw.manufacturer = "BTicino S.p.A."
    gw.firmware = firmware
    gw.device_registry_id = None
    gw.is_connected = True
    gw.has_central_unit = False
    gw.is_follower = False
    gw.primary_gateway_mac = None
    gw.delegated_whos = set()
    gw.delegated_away_whos = set()
    gw.sending_workers = []
    gw.availability_signal = f"myhome_gw_available_{mac}"
    gw.is_who_available = MagicMock(return_value=True)
    gw.initial_discovery_pending = False
    gw.sweep_active = False
    gw.silent_zone_poll_needed = False
    gw.actuator_request_refused = actuator_refused
    gw.zone_actuator_request_refused = zone_actuator_refused
    gw.interface_zone_request_refused = interface_refused
    gw._who4_target = MagicMock(return_value=gw)
    gw._who4_startup_poll_lock = None
    gw.pace_startup_status_write = None
    gw.wait_for_initial_discovery = None
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw.request_zone_dimension = MagicMock()
    gw.watch_zone_actuators = MagicMock(return_value=MagicMock())
    return gw


def _make_zone(
    hass: HomeAssistant,
    gw: MagicMock,
    where: str = "1",
    *,
    fan: bool = False,
    cooling: bool = True,
    standalone: bool = True,
) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=cooling,
        fan=fan,
        standalone=standalone,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _make_entry(
    hass: HomeAssistant,
    gw: MagicMock,
    *,
    platforms: dict[str, dict[str, Any]] | None = None,
    subentries_data: list[dict[str, Any]] | None = None,
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: gw.mac},
        unique_id=gw.mac,
        subentries_data=subentries_data or [],
    )
    entry.add_to_hass(hass)
    plat_map: dict[str, dict[str, Any]] = {p: {} for p in PLATFORMS}
    if platforms:
        for k, v in platforms.items():
            plat_map[k] = v
    entry.runtime_data = MyHOMERuntimeData(gateway=gw, platforms=plat_map)
    return entry


# ---------------------------------------------------------------------------
# 1. Grammar & decoding helpers
# ---------------------------------------------------------------------------


def test_thermo_output_level_boundaries_and_types() -> None:
    """thermo_output_level maps int 100..200 to 0..100 and rejects discrete/invalid/bool."""
    for code in range(-5, 100):
        assert thermo_output_level(code) is None
    for code in range(100, 201):
        lvl = thermo_output_level(code)
        assert lvl == code - 100
        assert type(lvl) is int
    for code in (201, 202, 255, 999):
        assert thermo_output_level(code) is None
    assert thermo_output_level(True) is None  # type: ignore[arg-type]
    assert thermo_output_level(False) is None  # type: ignore[arg-type]
    assert thermo_output_level(150.0) is None  # type: ignore[arg-type]
    assert thermo_output_level("150") is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*#4*1#3*20*100##", 0),
        ("*#4*1#3*20*101##", 1),
        ("*#4*1#3*20*120##", 20),
        ("*#4*1#3*20*155##", 55),
        ("*#4*1#3*20*200##", 100),
        ("*#4*1#3*#20*100##", 0),
        ("*#4*1#3*#20*120##", 20),
        ("*#4*1#3*#20*200##", 100),
        ("*#4*1#1*20*0##", None),
        ("*#4*1#1*20*1##", None),
        ("*#4*1#2*20*8##", None),
        ("*#4*1#2*20*14##", None),
        ("*#4*1#2*20*22##", None),
        ("*#4*1#3*20*99##", None),
        ("*#4*1#3*20*201##", None),
        ("*#4*1#3*20##", None),
        ("*#4*1*19*100*155##", None),
        ("*#1*01*2*0*0*0##", None),
    ],
)
def test_thermo_dim20_level_parsed_frames(raw: str, expected: int | None) -> None:
    """thermo_dim20_level extracts 0..100 for *20* and *#20* proportional codes only."""
    msg = OWNMessage.parse(raw)
    assert thermo_dim20_level(msg) == expected


def test_thermo_dim20_level_rejects_malformed_envelopes() -> None:
    """thermo_dim20_level fails closed on non-action non-command or non-ASCII digits."""
    non_action = SimpleNamespace(
        who=4, dimension=20, message_type="temperature", dimension_value=["150"]
    )
    assert thermo_dim20_level(non_action) is None

    empty_vals = SimpleNamespace(
        who=4, dimension=20, message_type="hvac_action", dimension_value=[]
    )
    assert thermo_dim20_level(empty_vals) is None

    unicode_digits = SimpleNamespace(
        who=4, dimension=20, message_type="hvac_action", dimension_value=["१५०"]
    )
    assert thermo_dim20_level(unicode_digits) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*#4*1#3*20*155##", ("1", "3", None)),
        ("*#4*99#9*20*200##", ("99", "9", None)),
        ("*#4*2#1#4#01*20*140##", ("2", "1", "01")),
        ("*#4*2#3#4#1*20*140##", ("2", "3", "01")),
        ("*#4*1*20*130##", None),
        ("*#4*1*20*155##", None),
        ("*#4*1#4#01*20*130##", None),
        ("*#4*2#4#01*20*140##", None),
        ("*#4*1#0*20*100##", None),
        ("*#4*0#1*20*100##", None),
        ("*#4*#0*20*100##", None),
        ("*#4*#1*20*100##", None),
        ("*#4*105*20*100##", None),
        ("*#4*1#10*20*150##", None),
        ("*#4*3#1#2*20*150##", None),
        ("*#4*1#3#4#99*20*150##", None),
    ],
)
def test_thermo_dim20_actuator_target(
    raw: str, expected: tuple[str, str, str | None] | None
) -> None:
    """thermo_dim20_actuator_target accepts explicit Z#N (1..99, 1..9) and rejects bare Z, group/central/pumps/probes."""
    msg = OWNMessage.parse(raw)
    assert thermo_dim20_actuator_target(msg) == expected


def test_thermo_dim20_actuator_target_mock_edge_cases() -> None:
    """thermo_dim20_actuator_target rejects bare Z, non-digit tails, and invalid actuator properties."""
    bad_tail = SimpleNamespace(
        who=4, where="1", zone=1, where_param=["x"], actuator="x"
    )
    assert thermo_dim20_actuator_target(bad_tail) is None

    bare_no_tail = SimpleNamespace(who=4, where="1", zone=1, where_param=[], actuator="1")
    assert thermo_dim20_actuator_target(bare_no_tail) is None

    bad_act = SimpleNamespace(who=4, where="1", zone=1, where_param=["0"], actuator="0")
    assert thermo_dim20_actuator_target(bad_act) is None

    raw_if_fallback = SimpleNamespace(
        who=4, where="1", zone=1, where_param=["3", "4", "2"], actuator="3"
    )
    assert thermo_dim20_actuator_target(raw_if_fallback) == ("1", "3", "02")


# ---------------------------------------------------------------------------
# 2. Live capture end-to-end replay (xmavgithub MyHomeServer1 2.87.13 #949)
# ---------------------------------------------------------------------------


async def test_issue_949_nine_captured_frames_unseeded(
    hass: HomeAssistant,
) -> None:
    """Replay ONLY the 9 verbatim captured frames from Issue #949 with zero seeded frames."""
    trace = json.loads(MHS1_RAMP_TRACE_PATH.read_text(encoding="utf-8"))
    assert trace["synthetic"] is False
    assert trace["capture"]["kind"] == "issue_log_excerpt"
    assert trace["gateway"]["model"] == "MyHomeServer1"
    assert trace["gateway"]["firmware"] == "2.87.13"
    assert trace["gateway"]["mac"] == MAC
    assert "exported_at" not in trace
    assert "exported_at" not in trace.get("environment", {})
    assert "home_assistant_version" not in trace.get("environment", {})
    assert "ownd_version" not in trace.get("environment", {})
    assert "started_at" not in trace["capture"]
    assert "window" not in trace["capture"]
    assert "clock_window" in trace["capture"]

    frames = trace["frames"]
    assert len(frames) == 9
    assert all(
        "clock_time" in f and "timestamp" not in f and "iso_time" not in f
        for f in frames
    )
    raw_frames = [f["raw"] for f in frames]

    gw = _make_gateway("MyHomeServer1", "2.87.13", mac=trace["gateway"]["mac"])
    entry = _make_entry(hass, gw)

    # Pre-register Zone 1 climate entity as an already-discovered zone (no seeded frames).
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("climate", DOMAIN, f"{gw.mac}-4-1", config_entry=entry)

    added_climates: list[MyHOMEClimate] = []
    added_sensors: list[Any] = []

    def _add_climates(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"climate.zone_{ent._where}"
            ent.async_write_ha_state = MagicMock()
            added_climates.append(ent)

    def _add_sensors(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            suffix = "temp" if isinstance(ent, MyHOMEClimateTemperatureSensor) else "output_level"
            ent.entity_id = f"sensor.zone_{ent._where.replace('#', '_')}_{suffix}"
            ent.async_schedule_update_ha_state = MagicMock()
            added_sensors.append(ent)

    def _actuators() -> list[MyHOMEActuatorOutputSensor]:
        return [s for s in added_sensors if isinstance(s, MyHOMEActuatorOutputSensor)]

    await climate_setup_entry(hass, entry, _add_climates)
    await sensor_setup_entry(hass, entry, _add_sensors)
    assert len(added_climates) == 1
    zone1 = added_climates[0]
    assert _actuators() == []
    assert [s.unique_id for s in added_sensors] == [f"{gw.mac}-4-1-temperature"]
    assert isinstance(added_sensors[0], MyHOMEClimateTemperatureSensor)

    signal = f"myhome_message_{gw.mac}"

    # Frame 0: *#4*1#2*20*1## (discrete relay 1#2 on -> zero sensor created)
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[0]))
    await hass.async_block_till_done()
    assert zone1.extra_state_attributes["actuators"] == {"2": "on"}
    assert "actuator_levels" not in zone1.extra_state_attributes
    assert _actuators() == []

    # Frame 1: *#4*1#3*20*120## (20 %) -> actuators {"2": "on", "3": "on"} + sensor 1#3 at 20 %
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[1]))
    await hass.async_block_till_done()
    assert len(_actuators()) == 1
    act3_sensor = _actuators()[0]
    assert isinstance(act3_sensor, MyHOMEActuatorOutputSensor)
    assert act3_sensor.native_value == 20
    assert zone1.extra_state_attributes["actuators"] == {"2": "on", "3": "on"}
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 20}

    # Frames 2..5: ramp 23 -> 50 -> 80 -> 100 %
    for idx, expected_pct in enumerate((23, 50, 80, 100), start=2):
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[idx]))
        await hass.async_block_till_done()
        assert act3_sensor.native_value == expected_pct
        assert zone1.extra_state_attributes["actuators"] == {"2": "on", "3": "on"}
        assert zone1.extra_state_attributes["actuator_levels"] == {"3": expected_pct}
        assert len(_actuators()) == 1

    # Frame 6: *#4*1#3*20*100## (0 % off)
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[6]))
    await hass.async_block_till_done()
    assert act3_sensor.native_value == 0
    assert zone1.extra_state_attributes["actuators"] == {"2": "on", "3": "off"}
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Frame 7: *#4*1#2*20*0##
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[7]))
    await hass.async_block_till_done()
    assert act3_sensor.native_value == 0
    assert zone1.extra_state_attributes["actuators"] == {"2": "off", "3": "off"}
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Frame 8: *#4*1#1*20*0## (floor loop 1#1 off -> {"1": "off", "2": "off", "3": "off"}, zero sensors for 1#1/1#2)
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[8]))
    await hass.async_block_till_done()
    assert act3_sensor.native_value == 0
    assert zone1.extra_state_attributes["actuators"] == {
        "1": "off",
        "2": "off",
        "3": "off",
    }
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 0}
    assert len(_actuators()) == 1
    assert not any(s._where in ("1#1", "1#2") for s in added_sensors)


async def test_issue_949_ramp_with_seeded_zone_context_and_paired_writes(
    hass: HomeAssistant,
) -> None:
    """Replay Issue #949 ramp with explicitly seeded zone context and paired #20 writes.

    Seeded frames outside the 9-frame verbatim JSON capture:
    - Zone discovery / heating context: ``*#4*1*0*0189##``, ``*#4*1*12*0240*1##``,
      ``*4*1*1##``, ``*#4*1#1*20*1##``, and ``*#4*1*12*0190*1##``.
    - Paired ``#20`` write frames (ignored by both climate and sensor):
      ``*#4*1#3*#20*120##``, ``*#4*1#3*#20*123##``, ``*#4*1#3*#20*150##``,
      ``*#4*1#3*#20*180##``, ``*#4*1#3*#20*200##``, and ``*#4*1#3*#20*100##``.
    - Synthetic duplicate status frame for deduplication check: ``*#4*1#3*20*200##``.
    """
    trace = json.loads(MHS1_RAMP_TRACE_PATH.read_text(encoding="utf-8"))
    assert trace["synthetic"] is False
    assert trace["capture"]["kind"] == "issue_log_excerpt"
    assert trace["gateway"]["model"] == "MyHomeServer1"
    assert trace["gateway"]["firmware"] == "2.87.13"

    gw = _make_gateway("MyHomeServer1", "2.87.13", mac=trace["gateway"]["mac"])
    entry = _make_entry(hass, gw)

    added_climates: list[MyHOMEClimate] = []
    added_sensors: list[Any] = []

    def _add_climates(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"climate.zone_{ent._where}"
            ent.async_write_ha_state = MagicMock()
            added_climates.append(ent)

    def _add_sensors(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            ent.entity_id = f"sensor.zone_{ent._where.replace('#', '_')}_output_level"
            ent.async_schedule_update_ha_state = MagicMock()
            added_sensors.append(ent)

    await climate_setup_entry(hass, entry, _add_climates)
    await sensor_setup_entry(hass, entry, _add_sensors)

    signal = f"myhome_message_{gw.mac}"
    frames = [item["raw"] for item in trace["frames"]]
    assert len(frames) == 9

    # 1) Seed Zone 1 ambient 18.9 °C + raised setpoint 24.0 °C + floor valve 1#1 ON + trace[0] (1#2*20*1)
    for raw in (
        "*#4*1*0*0189##",
        "*#4*1*12*0240*1##",
        "*4*1*1##",
        "*#4*1#1*20*1##",
        frames[0],
    ):
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    assert len(added_climates) == 1
    zone1 = added_climates[0]
    assert zone1.hvac_action == HVACAction.HEATING
    assert zone1.extra_state_attributes["actuators"] == {"1": "on", "2": "on"}
    assert "actuator_levels" not in zone1.extra_state_attributes
    # Discrete 0/1 frames on 1#2 and 1#1 must NOT spawn any output-level sensor!
    assert added_sensors == []

    # 2) Paired *#4*1#3*#20*120## write frame alone: ignored by both climate and sensor
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#3*#20*120##"))
    await hass.async_block_till_done()
    assert added_sensors == []
    assert "3" not in zone1.extra_state_attributes["actuators"]

    # 3) trace[1] (*#4*1#3*20*120##): discovers sensor at 20 % and sets actuator_levels["3"] = 20
    async_dispatcher_send(hass, signal, OWNMessage.parse(frames[1]))
    await hass.async_block_till_done()
    assert len(added_sensors) == 1
    act3_sensor = added_sensors[0]
    assert isinstance(act3_sensor, MyHOMEActuatorOutputSensor)
    assert act3_sensor.unique_id == f"{gw.mac}-4-1#3-output-level"
    assert act3_sensor.translation_key == "actuator_output_level"
    assert act3_sensor.native_unit_of_measurement == PERCENTAGE
    assert act3_sensor.state_class == SensorStateClass.MEASUREMENT
    assert act3_sensor.suggested_display_precision == 0
    assert act3_sensor.entity_registry_enabled_default is True
    assert act3_sensor.native_value == 20
    assert act3_sensor.extra_state_attributes == {"zone": 1, "actuator": 3}
    # Shared HA Device with MyHOMEClimate Zone 1
    assert (
        device_info_to_dict(act3_sensor.device_info)["identifiers"]
        == device_info_to_dict(zone1.device_info)["identifiers"]
    )
    assert zone1.extra_state_attributes["actuators"] == {
        "1": "on",
        "2": "on",
        "3": "on",
    }
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 20}

    # 4) Ramp trace[2..5]: 123 -> 150 -> 180 -> 200 (each paired with preceding #20 write)
    expected_ramp = [(123, 23), (150, 50), (180, 80), (200, 100)]
    for step_idx, (code, expected_pct) in enumerate(expected_ramp):
        status_frame = frames[2 + step_idx]
        writes_before = act3_sensor.async_schedule_update_ha_state.call_count

        # Paired #20 write frame must not write sensor state
        async_dispatcher_send(
            hass, signal, OWNMessage.parse(f"*#4*1#3*#20*{code}##")
        )
        await hass.async_block_till_done()
        assert act3_sensor.async_schedule_update_ha_state.call_count == writes_before

        # Verbatim *20* status frame updates sensor and climate once
        async_dispatcher_send(hass, signal, OWNMessage.parse(status_frame))
        await hass.async_block_till_done()
        assert act3_sensor.native_value == expected_pct
        assert act3_sensor.async_schedule_update_ha_state.call_count == writes_before + 1
        assert zone1.extra_state_attributes["actuator_levels"] == {"3": expected_pct}
        assert zone1.extra_state_attributes["actuators"]["3"] == "on"

    # Duplicate *#4*1#3*20*200## status frame must NOT rewrite unchanged sensor state
    writes_at_100 = act3_sensor.async_schedule_update_ha_state.call_count
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#3*20*200##"))
    await hass.async_block_till_done()
    assert act3_sensor.async_schedule_update_ha_state.call_count == writes_at_100

    # 5) Setpoint restored to 19.0 °C -> paired #20*100 + trace[6..8] (1#3*20*100, 1#2*20*0, 1#1*20*0)
    for raw in ("*#4*1*12*0190*1##", "*#4*1#3*#20*100##", *frames[6:]):
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    assert act3_sensor.native_value == 0
    assert len(added_sensors) == 1
    assert zone1.hvac_action == HVACAction.IDLE
    assert zone1.extra_state_attributes["actuators"] == {
        "1": "off",
        "2": "off",
        "3": "off",
    }
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 0}


async def test_shared_ha_device_registry_between_climate_and_actuator_sensor(
    hass: HomeAssistant,
) -> None:
    """Full HA entity/device registry registration attaches both entities to one DeviceEntry."""
    mac = MAC
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.98",
            "port": 20000,
            "password": "pass",
            "mac": mac,
            "friendly_name": "MyHomeServer1",
            "manufacturer": "BTicino S.p.A.",
            "name": "MyHomeServer1",
            "firmware": "2.87.13",
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
        entry.runtime_data.gateway._on_event_connection_state_change(True)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        signal = f"myhome_message_{mac}"
        for raw in (
            "*#4*1*0*0189##",
            "*#4*1*12*0210*1##",
            "*4*1*1##",
            "*#4*1#3*20*155##",
        ):
            async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
        await hass.async_block_till_done()

        ent_reg = er.async_get(hass)
        climate_ent_id = ent_reg.async_get_entity_id("climate", DOMAIN, f"{mac}-4-1")
        sensor_ent_id = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mac}-4-1#3-output-level"
        )
        assert climate_ent_id is not None
        assert sensor_ent_id is not None

        climate_reg = ent_reg.async_get(climate_ent_id)
        sensor_reg = ent_reg.async_get(sensor_ent_id)
        assert climate_reg is not None and sensor_reg is not None
        assert climate_reg.device_id is not None
        assert sensor_reg.device_id == climate_reg.device_id

        sensor_state = hass.states.get(sensor_ent_id)
        assert sensor_state is not None
        assert sensor_state.state == "55"
        assert sensor_state.attributes["unit_of_measurement"] == "%"
        assert sensor_state.attributes["state_class"] == "measurement"
        assert "Actuator 3 output level" in (
            sensor_state.attributes.get("friendly_name") or ""
        )

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# 3. MyHOMEClimate proportional attributes & edge cases
# ---------------------------------------------------------------------------


async def test_climate_deferred_pending_actions_preserves_actuator_level(
    hass: HomeAssistant,
) -> None:
    """A Dimension 20 proportional frame buffered before probe discovery (#785) replays actuator_levels."""
    gw = _make_gateway("F459", "2.1.5")
    entry = _make_entry(hass, gw)
    added: list[MyHOMEClimate] = []

    def _add(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"climate.zone_{ent._where}"
            ent.async_write_ha_state = MagicMock()
            added.append(ent)

    await climate_setup_entry(hass, entry, _add)
    signal = f"myhome_message_{gw.mac}"

    # Proportional Dimension 20 arrives BEFORE any probe temperature/setpoint
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*5#3*20*165##"))
    await hass.async_block_till_done()
    assert added == []

    # Probe temperature + heating mode + season materialize Zone 5 and replay buffered 5#3*20*165
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*5*0*0200##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*4*110*5##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*4*1*5##"))
    await hass.async_block_till_done()

    assert len(added) == 1
    zone5 = added[0]
    assert zone5._any_actuator_active() is True
    assert zone5.extra_state_attributes["actuators"] == {"3": "on"}
    assert zone5.extra_state_attributes["actuator_levels"] == {"3": 65}


def test_climate_repeated_off_dim19_resets_unrecorded_and_rejects_group_or_bare_levels(
    hass: HomeAssistant,
) -> None:
    """Verify _unrecorded_attributes, repeated_off (#932), Z#0/bare-Z rejection, Dim 19 without levels, and zone resets."""
    assert "actuator_levels" in MyHOMEClimate._Entity__combined_unrecorded_attributes

    gw = _make_gateway("MyHomeServer1", "2.87.13")
    zone = _make_zone(hass, gw, "1")
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*0*0200##"))
    zone.handle_event(OWNHeatingEvent.parse("*4*1*1##"))

    # Bare Z and Z#0 on fresh zone do NOT set actuator_levels
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*20*130##"))
    assert "actuator_levels" not in zone.extra_state_attributes
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*150##"))
    assert "actuator_levels" not in zone.extra_state_attributes

    # Actuator 3 proportional 45% -> active
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*145##"))
    assert zone.extra_state_attributes["actuators"]["3"] == "on"
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 45}

    # Z#0 (*#4*1#0*20*150## and *#4*1#0*20*1##) do NOT modify actuator_levels
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*150##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 45}
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*1##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 45}

    # Bare Z (*#4*1*20*130##) does NOT set or overwrite actuator_levels["1"]
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#1*20*125##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"1": 25, "3": 45}
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*20*130##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"1": 25, "3": 45}
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#1*20*1##"))  # pop "1"
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 45}

    # Dimension 19 carrying 100..200 drives hvac_action ONLY; NEVER exposes cooling/heating_output_level
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*100*160##"))
    assert zone.hvac_action == HVACAction.HEATING
    assert "cooling_output_level" not in zone.extra_state_attributes
    assert "heating_output_level" not in zone.extra_state_attributes
    assert not hasattr(zone, "_cooling_output_level")
    assert not hasattr(zone, "_heating_output_level")

    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*140*180##"))
    assert "cooling_output_level" not in zone.extra_state_attributes
    assert "heating_output_level" not in zone.extra_state_attributes

    # repeated_off (#932) on explicit Z#N sets level to 0 while keeping _active_source == "zone";
    # bare Z during repeated_off does NOT touch actuator_levels!
    zone._actuator_levels["3"] = 35
    zone._actuator_states["3"] = False
    zone._actuator_levels["1"] = 35
    zone._actuator_states["1"] = False
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*100*160##"))
    assert zone._active_source == "zone"
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*20*100##"))
    assert zone.extra_state_attributes["actuator_levels"]["1"] == 35
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*100##"))
    assert zone._active_source == "zone"
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["actuator_levels"]["3"] == 0

    # Discrete inactive on #3 while already False (repeated_off without dim20_level) keeps 0
    zone._actuator_levels["3"] = 15
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*0##"))
    assert zone.extra_state_attributes["actuator_levels"]["3"] == 0
    zone._actuator_levels.pop("1", None)

    # Discrete active *#4*1#3*20*1## pops "3" from actuator_levels
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*150##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 50}
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*1##"))
    assert "actuator_levels" not in zone.extra_state_attributes

    # Re-add #3 proportional, then discrete inactive *#4*1#3*20*0## sets level to 0
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*150##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*0##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Inactive Dimension 19 (*19*0*0 and *19*100*100) sets known proportional levels to 0
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*155##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*0*0##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}
    assert "cooling_output_level" not in zone.extra_state_attributes
    assert "heating_output_level" not in zone.extra_state_attributes
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*165##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*100*100##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Zone mode OFF resets all levels via _mark_actuators_off()
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*190##"))
    zone.handle_event(OWNHeatingEvent.parse("*4*303*1##"))
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Zone state Dimension 7 protection resets all levels via _mark_actuators_off()
    zone.handle_event(OWNHeatingEvent.parse("*4*1*1##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*180##"))
    dim7_off = SimpleNamespace(
        who=4,
        where="1",
        zone=1,
        where_param=[],
        dimension=7,
        message_type="zone_state",
        zone_state="protection",
        human_readable_log="Zone 1 protection",
    )
    zone.handle_event(dim7_off)  # type: ignore[arg-type]
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Pre-PR #952 Z#0 behavior preserved on actuators while not touching actuator_levels
    zone_fan = _make_zone(hass, gw, "3", fan=True)
    zone_fan.handle_event(OWNHeatingEvent.parse("*4*1*3##"))
    zone_fan.handle_event(OWNHeatingEvent.parse("*#4*3#2*20*7##"))
    assert zone_fan.extra_state_attributes["running_fan_speed"] == "medium"
    zone_fan.handle_event(OWNHeatingEvent.parse("*#4*3#0*20*150##"))
    assert "actuator_levels" not in zone_fan.extra_state_attributes
    zone_fan.handle_event(OWNHeatingEvent.parse("*#4*3*19*0*0##"))
    assert zone_fan.extra_state_attributes["running_fan_speed"] == "off"


# ---------------------------------------------------------------------------
# 4. MyHOMEThermoSwitch proportional attributes & _unrecorded_attributes
# ---------------------------------------------------------------------------


def test_thermo_switch_per_actuator_and_whole_zone_output_levels(
    hass: HomeAssistant,
) -> None:
    """MyHOMEThermoSwitch excludes output_level/actuator_levels from recorder and limits Dim 20 levels to Z#N."""
    assert {"output_level", "actuator_levels"} <= (
        MyHOMEThermoSwitch._Entity__combined_unrecorded_attributes
    )

    gw = _make_gateway("MyHomeServer1", "2.87.13")

    # 1) Single-actuator switch 7#1
    sw_act = MyHOMEThermoSwitch(
        hass=hass,
        name="Modulating Actuator 7#1",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="7#1",
        where="7#1",
        zone="7",
        actuator="1",
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="Thermo Actuator",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gw,
    )
    sw_act.hass = hass
    sw_act.async_write_ha_state = MagicMock()

    assert "output_level" not in sw_act.extra_state_attributes
    # Z#0 and bare Z on empty switch do NOT set output_level
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*150##"))
    assert sw_act.is_on is True
    assert "output_level" not in sw_act.extra_state_attributes
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7*20*130##"))
    assert sw_act.is_on is True
    assert "output_level" not in sw_act.extra_state_attributes

    # #20 write at 60 %
    sw_act.handle_event(OWNHeatingCommand.parse("*#4*7#1*#20*160##"))  # type: ignore[arg-type]
    assert sw_act.is_on is True
    assert sw_act.extra_state_attributes["output_level"] == 60
    # *20* status at 25 %
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*125##"))
    assert sw_act.is_on is True
    assert sw_act.extra_state_attributes["output_level"] == 25
    # Group Z#0 (*#4*7#0*20*150## / *#4*7#0*20*1##) and bare Z (*#4*7*20*130##) do NOT mutate output_level
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*150##"))
    assert sw_act.extra_state_attributes["output_level"] == 25
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*1##"))
    assert sw_act.extra_state_attributes["output_level"] == 25
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7*20*130##"))
    assert sw_act.extra_state_attributes["output_level"] == 25
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*0##"))
    assert sw_act.is_on is False
    assert sw_act.extra_state_attributes["output_level"] == 25
    # Point discrete active pops output_level; point discrete inactive sets 0
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*140##"))
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*1##"))
    assert "output_level" not in sw_act.extra_state_attributes
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*140##"))
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*0##"))
    assert sw_act.extra_state_attributes["output_level"] == 0
    # Zone-level Dimension 19 is ignored on per-actuator switch (#785 hybrid zone safety),
    # while #20*100 resets output_level to 0
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*175##"))
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7*19*0*0##"))
    assert sw_act.is_on is True
    assert sw_act.extra_state_attributes["output_level"] == 75
    sw_act.handle_event(OWNHeatingCommand.parse("*#4*7#1*#20*100##"))  # type: ignore[arg-type]
    assert sw_act.is_on is False
    assert sw_act.extra_state_attributes["output_level"] == 0

    # 2) Whole-zone switch 8
    sw_zone = MyHOMEThermoSwitch(
        hass=hass,
        name="Zone 8 Switch",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="8",
        where="8",
        zone="8",
        actuator=None,
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="Thermo Actuator",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gw,
    )
    sw_zone.hass = hass
    sw_zone.async_write_ha_state = MagicMock()

    # Empty whole-zone switch receiving Z#0 or bare Z tracks #785 binary actuators only, NEVER actuator_levels
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*150##"))
    assert sw_zone.extra_state_attributes["actuators"] == {"1": "on"}
    assert "actuator_levels" not in sw_zone.extra_state_attributes
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8*20*130##"))
    assert "actuator_levels" not in sw_zone.extra_state_attributes

    # Explicit Z#3 sets actuator_levels["3"] = 90
    sw_zone.handle_event(OWNHeatingCommand.parse("*#4*8#3*#20*190##"))  # type: ignore[arg-type]
    assert sw_zone.extra_state_attributes["actuators"] == {"1": "on", "3": "on"}
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"3": 90}

    # Z#0 (*#4*8#0*20*150##, *#4*8#0*20*1##) and bare Z (*#4*8*20*130##) do NOT alter actuator_levels
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*150##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"3": 90}
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*1##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"3": 90}
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8*20*130##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"3": 90}
    # Explicit Z#3 discrete active pops "3"; discrete inactive sets 0
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#3*20*1##"))
    assert "actuator_levels" not in sw_zone.extra_state_attributes
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#3*20*140##"))
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#3*20*0##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"3": 0}
    # Out-of-range actuator id (> 9) is ignored cleanly
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#10*20*1##"))
    assert "10" not in sw_zone.extra_state_attributes["actuators"]

    # Inactive Dim 19 resets known levels to 0
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#3*20*165##"))
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8*19*100*100##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"3": 0}


# ---------------------------------------------------------------------------
# 5. MyHOMEActuatorOutputSensor discovery, exclusions, restore, polling & resets
# ---------------------------------------------------------------------------


async def test_sensor_non_creation_and_thermo_switch_pruning(
    hass: HomeAssistant,
) -> None:
    """Discrete 0..22, Z#0, pumps, probes, central, and probe-less thermo_switch zones are excluded."""
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    alt_entry_mac = "00:03:50:00:99:99"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: alt_entry_mac},
        unique_id=gw.mac,
        subentries_data=[
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "7#1"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Towel Warmer 7#1",
                "unique_id": "thermo_switch_4_7#1",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "1#2"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Hybrid Relay 1#2",
                "unique_id": "thermo_switch_4_1#2",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "2#1"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Bedroom Towel Warmer 2#1",
                "unique_id": "thermo_switch_4_2#1",
            },
        ],
    )
    entry.add_to_hass(hass)
    plat_map: dict[str, dict[str, Any]] = {p: {} for p in PLATFORMS}
    plat_map["climate"] = {
        "bad_non_dict": "ignored",  # type: ignore[dict-item]
        "missing_where": {CONF_NAME: "Orphan"},
        "living": {
            CONF_WHO: "4",
            CONF_WHERE: "#1",
            CONF_NAME: "Living Room",
            CONF_STANDALONE: False,
        },
        "4": {
            CONF_WHO: "4",
            CONF_WHERE: "4",
            CONF_NAME: "Direct Zone 4",
        },
        "5#4#01": {
            CONF_WHO: "4",
            CONF_WHERE: "5#4#01",
            CONF_NAME: "Subbus Zone 5",
        },
    }
    plat_map["switch"] = {
        "zone8_whole": {CONF_WHO: "4", CONF_WHERE: "8"},
        "invalid_where_sw": {CONF_WHO: "4", CONF_WHERE: "bad#where#99"},
    }
    plat_map["sensor"] = {
        "yaml_sensor_ignored_by_4_actuator": {CONF_WHO: "4", CONF_WHERE: "9#1"},
    }
    entry.runtime_data = MyHOMERuntimeData(gateway=gw, platforms=plat_map)

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    # Seed non-WHO-4, non-matching Zone 9, and matching Zone 2 climate entities in registry
    ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{gw.mac}-1-99", config_entry=entry
    )
    ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{gw.mac}-4-9", config_entry=entry
    )
    z2_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{gw.mac}-4-2")},
        name="Master Bedroom",
        manufacturer="Legrand",
        model="Smarter",
    )
    ent_reg.async_get_or_create(
        "climate",
        DOMAIN,
        f"{gw.mac}-4-2",
        config_entry=entry,
        device_id=z2_dev.id,
    )

    # Seed a stale registry entry on probe-less thermo_switch 7#1 AND a valid registry entry on 6#3 (using alt_entry_mac)
    stale_entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-7#1-output-level",
        config_entry=entry,
    )
    restored_entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{alt_entry_mac}-4-6#3-output-level",
        config_entry=entry,
    )

    added: list[Any] = []

    def _add(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"sensor.mock_{len(added)}"
            ent.async_write_ha_state = MagicMock()
            added.append(ent)

    await sensor_setup_entry(hass, entry, _add)

    def _actuators() -> list[MyHOMEActuatorOutputSensor]:
        return [e for e in added if isinstance(e, MyHOMEActuatorOutputSensor)]

    # Stale 7#1 entry was pruned from entity registry; valid 6#3 was restored!
    assert ent_reg.async_get(stale_entry.entity_id) is None
    assert ent_reg.async_get(restored_entry.entity_id) is not None
    assert [e.unique_id for e in _actuators()] == [f"{gw.mac}-4-6#3-output-level"]
    temps = [e for e in added if isinstance(e, MyHOMEClimateTemperatureSensor)]
    assert {e.unique_id for e in temps} == {
        f"{gw.mac}-4-9-temperature",
        f"{gw.mac}-4-1-temperature",
        f"{gw.mac}-4-4-temperature",
        f"{gw.mac}-4-5#4#01-temperature",
    }

    signal = f"myhome_message_{gw.mac}"
    ignored_frames = [
        "*#4*3#1*20*0##",
        "*#4*3#1*20*1##",
        "*#4*3#1*20*8##",
        "*#4*3#1*20*14##",
        "*#4*3#1*20*22##",
        "*#4*3*20*130##",
        "*#4*1*20*130##",
        "*#4*1#4#01*20*130##",
        "*#4*3#0*20*150##",
        "*#4*3#0*20*1##",
        "*#4*0#1*20*150##",
        "*#4*#0*20*150##",
        "*#4*#3*20*150##",
        "*#4*105*20*150##",
        "*#4*3#1*#20*150##",
        # Claimed by probe-less thermo_switch 7#1 (including sibling 7#2 on probe-less zone 7)
        "*#4*7#1*20*150##",
        "*#4*7#2*20*150##",
        # Claimed by per-actuator thermo_switch 2#1 even though Zone 2 has a climate entity
        "*#4*2#1*20*150##",
        # Claimed by whole-zone thermo_switch 8
        "*#4*8#1*20*150##",
    ]
    for raw in ignored_frames:
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()
    assert len(_actuators()) == 1

    # Zone 1 HAS explicit climate config ("#1", "Living Room"), so 1#3 discovers and links to "#1"!
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#3*20*175##"))
    # Zone 2 has a registry climate entity ("Master Bedroom"), so 2#3 discovers and adopts its DeviceEntry metadata!
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*2#3*20*140##"))
    # Zone 4 & 5#4#01 hit direct config_for branches
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*4#1*20*130##"))
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*5#1#4#01*20*120##"))
    await hass.async_block_till_done()
    assert len(_actuators()) == 5

    living_act3 = _actuators()[1]
    assert living_act3.unique_id == f"{gw.mac}-4-1#3-output-level"
    assert living_act3._device_id == "#1"
    assert device_info_to_dict(living_act3.device_info)["identifiers"] == {
        (DOMAIN, f"{gw.mac}-4-#1")
    }
    assert living_act3._device_name == "Living Room"
    assert living_act3.native_value == 75
    assert living_act3._standalone is False

    bed_act3 = _actuators()[2]
    assert bed_act3.unique_id == f"{gw.mac}-4-2#3-output-level"
    assert bed_act3._device_name == "Master Bedroom"
    assert bed_act3._manufacturer == "Legrand"
    assert bed_act3._model == "Smarter"
    assert bed_act3.native_value == 40

    assert _actuators()[3]._device_name == "Direct Zone 4"
    assert _actuators()[4]._device_name == "Subbus Zone 5"
    assert _actuators()[4]._device_id == "5#4#01"

    assert await sensor_unload_entry(hass, entry)


async def test_actuator_output_sensor_is_plain_sensor_entity_not_restore_sensor(
    hass: HomeAssistant,
) -> None:
    """MyHOMEActuatorOutputSensor inherits SensorEntity (not RestoreSensor) and ignores restore cache."""
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    sensor = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1",
        device_id="1",
        where="1#3",
        zone="1",
        actuator="3",
        interface=None,
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    assert isinstance(sensor, SensorEntity)
    assert not isinstance(sensor, RestoreSensor)
    assert not isinstance(sensor, MyHOMESensorEntity)

    sensor.hass = hass
    sensor.platform = MagicMock()
    sensor.platform_data = SimpleNamespace(
        platform_name="myhome",
        domain="sensor",
        default_language_platform_translations={},
    )
    sensor.entity_id = "sensor.climate_zone_1_actuator_3_output_level"
    sensor.async_write_ha_state = MagicMock()

    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(sensor.entity_id, "60"),
                SensorExtraStoredData(
                    native_value=60,
                    native_unit_of_measurement=PERCENTAGE,
                ),
            )
        ],
    )
    await sensor.async_added_to_hass()
    assert sensor.native_value is None


async def test_actuator_output_sensor_unknown_after_unload_and_resetup(
    hass: HomeAssistant,
) -> None:
    """After unload + re-setup with '60' in restore cache, sensor is unknown on MH202 and climate-less zones."""
    # 1) MH202 (ACTUATOR_REQUEST_REFUSED): Zone 1 (with climate) + Zone 2 (climate-less)
    entry_mh202 = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.98",
            "port": 20000,
            "password": "pass",
            "mac": MAC,
            "friendly_name": "MH202",
            "manufacturer": "BTicino S.p.A.",
            "name": "MH202",
            "firmware": "1.0.24",
        },
        unique_id=MAC,
    )
    entry_mh202.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry_mh202.entry_id)
        await hass.async_block_till_done()
        assert entry_mh202.runtime_data.gateway.actuator_request_refused is True
        entry_mh202.runtime_data.gateway._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        signal = f"myhome_message_{MAC}"
        for raw in (
            "*#4*1*0*0200##",
            "*#4*1#3*20*160##",
            "*#4*2#3*20*160##",
        ):
            async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
        await hass.async_block_till_done()

        ent_reg = er.async_get(hass)
        assert ent_reg.async_get_entity_id("climate", DOMAIN, f"{MAC}-4-1") is not None
        assert ent_reg.async_get_entity_id("climate", DOMAIN, f"{MAC}-4-2") is None
        s1_eid = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{MAC}-4-1#3-output-level"
        )
        s2_eid = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{MAC}-4-2#3-output-level"
        )
        assert s1_eid is not None and s2_eid is not None
        assert hass.states.get(s1_eid).state == "60"  # type: ignore[union-attr]
        assert hass.states.get(s2_eid).state == "60"  # type: ignore[union-attr]

        assert await hass.config_entries.async_unload(entry_mh202.entry_id)
        await hass.async_block_till_done()

        mock_restore_cache_with_extra_data(
            hass,
            [
                (
                    State(s1_eid, "60"),
                    SensorExtraStoredData(
                        native_value=60, native_unit_of_measurement=PERCENTAGE
                    ),
                ),
                (
                    State(s2_eid, "60"),
                    SensorExtraStoredData(
                        native_value=60, native_unit_of_measurement=PERCENTAGE
                    ),
                ),
            ],
        )

        assert await hass.config_entries.async_setup(entry_mh202.entry_id)
        await hass.async_block_till_done()
        entry_mh202.runtime_data.gateway._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        sensor_comp = hass.data["sensor"]
        for eid in (s1_eid, s2_eid):
            state_after = hass.states.get(eid)
            assert state_after is not None
            assert state_after.state == STATE_UNKNOWN
            ent_after = sensor_comp.get_entity(eid)
            assert ent_after is not None
            assert ent_after.native_value is None

        assert await hass.config_entries.async_unload(entry_mh202.entry_id)
        await hass.async_block_till_done()

    # 2) MyHomeServer1 climate-less zone 3#3 with prior "60" in restore cache also starts unknown
    mac_mhs1 = "00:03:50:00:09:09"
    entry_mhs1 = MockConfigEntry(
        domain=DOMAIN,
        data={
            "host": "192.0.2.99",
            "port": 20000,
            "password": "pass",
            "mac": mac_mhs1,
            "friendly_name": "MyHomeServer1",
            "manufacturer": "BTicino S.p.A.",
            "name": "MyHomeServer1",
            "firmware": "2.87.13",
        },
        unique_id=mac_mhs1,
    )
    entry_mhs1.add_to_hass(hass)

    with (
        patch(
            "custom_components.myhome.gateway.OWNSession.test_connection",
            return_value={"Success": True, "Message": None},
        ),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.listening_loop"),
        patch("custom_components.myhome.gateway.MyHOMEGatewayHandler.sending_loop"),
    ):
        assert await hass.config_entries.async_setup(entry_mhs1.entry_id)
        await hass.async_block_till_done()
        entry_mhs1.runtime_data.gateway._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        async_dispatcher_send(
            hass,
            f"myhome_message_{mac_mhs1}",
            OWNMessage.parse("*#4*3#3*20*160##"),
        )
        await hass.async_block_till_done()

        s3_eid = er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{mac_mhs1}-4-3#3-output-level"
        )
        assert s3_eid is not None
        assert hass.states.get(s3_eid).state == "60"  # type: ignore[union-attr]

        assert await hass.config_entries.async_unload(entry_mhs1.entry_id)
        await hass.async_block_till_done()

        mock_restore_cache_with_extra_data(
            hass,
            [
                (
                    State(s3_eid, "60"),
                    SensorExtraStoredData(
                        native_value=60, native_unit_of_measurement=PERCENTAGE
                    ),
                )
            ],
        )

        assert await hass.config_entries.async_setup(entry_mhs1.entry_id)
        await hass.async_block_till_done()
        entry_mhs1.runtime_data.gateway._on_event_connection_state_change(True)
        await hass.async_block_till_done()

        s3_state = hass.states.get(s3_eid)
        assert s3_state is not None
        assert s3_state.state == STATE_UNKNOWN
        s3_ent = hass.data["sensor"].get_entity(s3_eid)
        assert s3_ent is not None
        assert s3_ent.native_value is None

        assert await hass.config_entries.async_unload(entry_mhs1.entry_id)
        await hass.async_block_till_done()


async def test_actuator_output_sensor_router_isolation(
    hass: HomeAssistant,
) -> None:
    """Router key isolation ensures climate and temperature sensors never receive duplicate deliveries."""
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    entry = _make_entry(hass, gw)

    climates: list[MyHOMEClimate] = []
    sensors: list[Any] = []

    await climate_setup_entry(
        hass,
        entry,
        lambda ents, update_before_add=False: [
            (
                setattr(e, "hass", hass),
                setattr(e, "platform", MagicMock()),
                setattr(e, "entity_id", f"climate.z_{e._where}"),
                setattr(e, "async_schedule_update_ha_state", MagicMock()),
                climates.append(e),
            )
            for e in ents
        ],
    )
    await sensor_setup_entry(
        hass,
        entry,
        lambda ents, update_before_add=False: [
            (
                setattr(e, "hass", hass),
                setattr(e, "platform", MagicMock()),
                setattr(e, "entity_id", f"sensor.s_{len(sensors)}"),
                setattr(e, "async_schedule_update_ha_state", MagicMock()),
                sensors.append(e),
            )
            for e in ents
        ],
    )

    signal = f"myhome_message_{gw.mac}"
    for raw in ("*#4*1*0*0205##", "*4*1*1##"):
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    assert len(climates) == 1
    assert len(sensors) == 0
    zone1 = climates[0]

    with patch.object(zone1, "_publish_state", wraps=zone1._publish_state) as spy_before:
        async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*0206##"))
        await hass.async_block_till_done()
        baseline_climate_calls = spy_before.call_count

    # Discover actuator output sensor on 1#3
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#3*20*150##"))
    await hass.async_block_till_done()
    assert len(sensors) == 1
    act3_sensor = sensors[0]

    # Adding 4_actuator adds ZERO extra deliveries to climate Zone 1, and delivers strictly once to act3_sensor
    with (
        patch.object(zone1, "_publish_state", wraps=zone1._publish_state) as spy_climate,
        patch.object(act3_sensor, "_publish_state", wraps=act3_sensor._publish_state) as spy_sensor,
    ):
        async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1*0*0207##"))
        async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#3*20*160##"))
        await hass.async_block_till_done()
        assert spy_climate.call_count == baseline_climate_calls * 2
        assert spy_sensor.call_count == 1


@pytest.mark.parametrize(
    ("model", "firmware", "actuator_refused", "zone_actuator_refused"),
    [
        ("MyHomeServer1", "2.87.13", False, False),
        ("MH202", "1.0.24", True, False),
        ("F461", "2.0.11", False, False),
        ("F455", "1.0.76", False, True),
    ],
)
async def test_actuator_output_sensor_unconditional_watch_across_gateways(
    hass: HomeAssistant,
    model: str,
    firmware: str,
    actuator_refused: bool,
    zone_actuator_refused: bool,
) -> None:
    """watch_zone_actuators('1#3') is registered unconditionally when not interface_refused."""
    gw = _make_gateway(
        model,
        firmware,
        actuator_refused=actuator_refused,
        zone_actuator_refused=zone_actuator_refused,
    )
    unsub = MagicMock()
    gw.watch_zone_actuators = MagicMock(return_value=unsub)

    sensor = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1",
        device_id="1",
        where="1#3",
        zone="1",
        actuator="3",
        interface=None,
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    assert sensor._poll_on_add is False
    sensor.hass = hass
    sensor.entity_id = f"sensor.{model.lower()}_zone_1_act_3"
    sensor.async_write_ha_state = MagicMock()

    await sensor.async_added_to_hass()
    gw.watch_zone_actuators.assert_called_once_with("1#3")

    sensor._call_on_remove_callbacks()
    unsub.assert_called_once()


async def test_actuator_output_sensor_polling_guards_no_watch_fallback_and_interface_isolation(
    hass: HomeAssistant,
) -> None:
    """Verify async_update guards, _poll_on_add fallback without watch_zone_actuators, and #4#01 isolation."""
    # 1) async_update sends *#4*1#3*20## (NEVER dimensionless *#4*1#3##!) when allowed
    gw_f455 = _make_gateway(
        "F455",
        "1.0.76",
        actuator_refused=False,
        zone_actuator_refused=True,
    )
    s_f455 = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1",
        device_id="1",
        where="1#3",
        zone="1",
        actuator="3",
        interface=None,
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw_f455,
    )
    s_f455.hass = hass
    s_f455.entity_id = "sensor.zone_1_act_3"
    s_f455.async_write_ha_state = MagicMock()
    await s_f455.async_added_to_hass()
    gw_f455.watch_zone_actuators.assert_called_once_with("1#3")

    await s_f455.async_update()
    gw_f455.send_status_request.assert_awaited_once()
    sent_cmd = gw_f455.send_status_request.await_args.args[0]
    assert str(sent_cmd) == "*#4*1#3*20##"

    # 2) H4684 / MH200 (actuator_request_refused=True) still registers watch_zone_actuators
    #    (gateway gates sending) while async_update skips send_status_request
    gw_h4684 = _make_gateway(
        "H4684",
        "2.0.54",
        actuator_refused=True,
        zone_actuator_refused=False,
    )
    s_h4684 = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1",
        device_id="1",
        where="1#3",
        zone="1",
        actuator="3",
        interface=None,
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw_h4684,
    )
    s_h4684.hass = hass
    s_h4684.entity_id = "sensor.h4684_act_3"
    s_h4684.async_write_ha_state = MagicMock()
    await s_h4684.async_added_to_hass()
    gw_h4684.watch_zone_actuators.assert_called_once_with("1#3")
    await s_h4684.async_update()
    gw_h4684.send_status_request.assert_not_awaited()

    # 3) Interface #4#01 isolation & interface_zone_request_refused guard
    gw_if = _make_gateway(
        "MyHomeServer1",
        "2.87.13",
        zone_actuator_refused=True,
        interface_refused=True,
    )
    s_if = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1 (Interface 01)",
        device_id="1#4#01",
        where="1#3",
        zone="1",
        actuator="3",
        interface="01",
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw_if,
    )
    s_if.hass = hass
    s_if.entity_id = "sensor.if01_act_3"
    s_if.async_write_ha_state = MagicMock()
    assert s_if.unique_id == f"{gw_if.mac}-4-1#3#4#01-output-level"
    assert s_if.extra_state_attributes == {"zone": 1, "actuator": 3, "Int": "01"}
    assert s_if._poll_on_add is False

    await s_if.async_added_to_hass()
    gw_if.watch_zone_actuators.assert_not_called()
    await s_if.async_update()
    gw_if.send_status_request.assert_not_awaited()

    # When profile allows interface requests, watch + async_update use *#4*1#3#4#01*20##
    gw_if.interface_zone_request_refused = False
    await s_if.async_added_to_hass()
    gw_if.watch_zone_actuators.assert_called_once_with("1#3#4#01")
    await s_if.async_update()
    assert str(gw_if.send_status_request.await_args.args[0]) == "*#4*1#3#4#01*20##"

    # Main-bus 1#3 frame and bare 1#4#01 ignored; explicit 1#3#4#01 frame accepted
    s_if.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*155##"))
    assert s_if.native_value is None
    s_if.handle_event(OWNHeatingEvent.parse("*#4*1#4#01*20*130##"))
    assert s_if.native_value is None
    s_if.handle_event(OWNHeatingEvent.parse("*#4*1#3#4#01*20*145##"))
    assert s_if.native_value == 45

    # 4) _poll_on_add fallback when watch_zone_actuators is absent on gateway
    gw_no_watch = _make_gateway("MyHomeServer1", "2.87.13", interface_refused=True)
    del gw_no_watch.watch_zone_actuators
    s_no_watch = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1",
        device_id="1",
        where="1#3",
        zone="1",
        actuator="3",
        interface=None,
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw_no_watch,
    )
    assert s_no_watch._poll_on_add is True
    s_no_watch.hass = hass
    s_no_watch.entity_id = "sensor.no_watch_act_3"
    s_no_watch.async_write_ha_state = MagicMock()
    await s_no_watch.async_added_to_hass()
    gw_no_watch.send_status_request.assert_awaited_once()
    assert str(gw_no_watch.send_status_request.await_args.args[0]) == "*#4*1#3*20##"

    # With interface_refused=True and watch_zone_actuators absent, _poll_on_add remains False
    s_no_watch_if = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1 (Interface 01)",
        device_id="1#4#01",
        where="1#3",
        zone="1",
        actuator="3",
        interface="01",
        standalone=True,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw_no_watch,
    )
    assert s_no_watch_if._poll_on_add is False
    s_no_watch_if.hass = hass
    s_no_watch_if.entity_id = "sensor.no_watch_if01_act_3"
    s_no_watch_if.async_write_ha_state = MagicMock()
    await s_no_watch_if.async_added_to_hass()
    gw_no_watch.send_status_request.assert_awaited_once()  # unchanged


async def test_actuator_output_sensor_all_reset_paths(
    hass: HomeAssistant,
) -> None:
    """Verify Z#0/bare-Z are ignored and every zone-wide reset-to-0 path works (standalone vs central)."""
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    sensor = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Climate Zone 1",
        device_id="1",
        where="1#3",
        zone="1",
        actuator="3",
        interface=None,
        standalone=None,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    sensor.hass = hass
    sensor.entity_id = "sensor.zone_1_act_3"
    sensor.async_write_ha_state = MagicMock()
    await sensor.async_added_to_hass()

    # 1) Discrete inactive on 1#3 -> 0; sibling 1#1 inactive does NOT reset 1#3!
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*160##"))
    assert sensor.native_value == 60
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#1*20*0##"))
    assert sensor.native_value == 60
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*0##"))
    assert sensor.native_value == 0

    # 2) Z#0 (*#4*1#0*20*145##, *#4*1#0*20*1##, *#4*1#0*20*0##) and bare Z (*#4*1*20*130##, *#4*1*20*0##)
    #    never set or clear MyHOMEActuatorOutputSensor!
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*165##"))
    assert sensor.native_value == 65
    for ignored in (
        "*#4*1#0*20*145##",
        "*#4*1#0*20*1##",
        "*#4*1#0*20*0##",
        "*#4*1#0*20*100##",
        "*#4*1*20*130##",
        "*#4*1*20*0##",
    ):
        sensor.handle_event(OWNHeatingEvent.parse(ignored))
        assert sensor.native_value == 65

    # Explicit 1#3*20*100 resets to 0
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*100##"))
    assert sensor.native_value == 0

    # 3) Inactive Dimension 19 -> 0 (while active Dim 19 leaves per-actuator level intact)
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*170##"))
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1*19*0*1##"))
    assert sensor.native_value == 70
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1*19*0*0##"))
    assert sensor.native_value == 0

    # 4) Standalone zone: *4*303*#1## (central #Z) and central #0 HVACMode.OFF are ignored;
    #    standalone *4*303*1## resets to 0!
    assert sensor._standalone is True
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*180##"))
    sensor.handle_event(OWNHeatingEvent.parse("*4*303*#1##"))
    assert sensor.native_value == 80
    async_dispatcher_send(hass, f"myhome_central_mode_{gw.mac}", HVACMode.OFF)
    await hass.async_block_till_done()
    assert sensor.native_value == 80
    sensor.handle_event(OWNHeatingEvent.parse("*4*303*1##"))
    assert sensor.native_value == 0

    # 5) Central unit report (*4*1101*#0##) flips has_central_unit=True -> #1 OFF & central #0 OFF reset to 0!
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*185##"))
    sensor.handle_event(OWNHeatingEvent.parse("*4*1101*#0##"))
    assert gw.has_central_unit is True
    assert sensor._standalone is False
    sensor.handle_event(OWNHeatingEvent.parse("*4*303*#1##"))
    assert sensor.native_value == 0

    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*190##"))
    async_dispatcher_send(hass, f"myhome_central_mode_{gw.mac}", HVACMode.HEAT)
    await hass.async_block_till_done()
    assert sensor.native_value == 90
    async_dispatcher_send(hass, f"myhome_central_mode_{gw.mac}", HVACMode.OFF)
    await hass.async_block_till_done()
    assert sensor.native_value == 0

    # 6) Zone state Dimension 7 protection / off -> 0
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*150##"))
    dim7_off = SimpleNamespace(
        who=4,
        where="1",
        zone=1,
        where_param=[],
        dimension=7,
        message_type="zone_state",
        zone_state="protection",
        human_readable_log="Zone 1 protection",
    )
    sensor.handle_event(dim7_off)  # type: ignore[arg-type]
    assert sensor.native_value == 0

    await sensor.async_will_remove_from_hass()


async def test_live_myhomeserver1_fancoil_ramp_with_dataclass_device_info(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay seeded ramp trace under HA 2026.12+ slotted dataclass DeviceInfo (core#192323)."""
    monkeypatch.setattr(myhome_device, "DeviceInfo", StrictDataclassDeviceInfo)
    await test_issue_949_ramp_with_seeded_zone_context_and_paired_writes(hass)


async def test_sensor_non_creation_and_pruning_with_dataclass_device_info(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify actuator output sensor discovery/pruning under HA 2026.12+ dataclass DeviceInfo."""
    monkeypatch.setattr(myhome_device, "DeviceInfo", StrictDataclassDeviceInfo)
    await test_sensor_non_creation_and_thermo_switch_pruning(hass)


async def test_issue_952_full_221_frame_live_cycle_unseeded(
    hass: HomeAssistant,
) -> None:
    """Replay the full 221-frame verbatim UTC bus-listener cycle from PR #952 comment #6984792289.

    Starts from a completely blank MockConfigEntry with zero pre-registered entities
    and zero seeded frames: verifies cold-start Zone 1 discovery, pump 0#1 exclusion,
    36 s valve->fan delay, paired #20/20 deduplication across all 25 ramp steps
    (20..100 %), interleaved multi-zone isolation (Zones 1, 2, 4, 5 + H4691 humidity),
    and full return to 0 % / idle.
    """
    trace = json.loads(MHS1_FULL_CYCLE_TRACE_PATH.read_text(encoding="utf-8"))
    assert trace["synthetic"] is False
    assert trace["capture"]["kind"] == "bus_listener_log"
    assert trace["gateway"]["model"] == "MyHomeServer1"
    assert trace["gateway"]["firmware"] == "2.87.13"
    assert trace["gateway"]["mac"] == MAC
    assert "exported_at" not in trace
    assert "exported_at" not in trace.get("environment", {})
    assert trace["environment"]["home_assistant_version"] == "2026.11.2"
    assert trace["environment"]["ownd_version"] == "2.0.1"

    frames = trace["frames"]
    assert len(frames) == 221
    raw_frames = [f["raw"] for f in frames]
    assert sum(1 for r in raw_frames if r.startswith("*#4*1#3*#20*")) == 31
    assert sum(1 for r in raw_frames if r.startswith("*#4*1#3*20*")) == 63

    gw = _make_gateway("MyHomeServer1", "2.87.13", mac=trace["gateway"]["mac"])
    entry = _make_entry(hass, gw)

    added_climates: list[MyHOMEClimate] = []
    added_sensors: list[Any] = []
    published_levels: list[int | None] = []

    def _add_climates(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"climate.zone_{ent._where}"
            ent.async_write_ha_state = MagicMock()
            added_climates.append(ent)

    def _add_sensors(entities: list[Any], update_before_add: bool = False) -> None:
        for ent in entities:
            ent.hass = hass
            ent.platform = MagicMock()
            ent.entity_id = f"sensor.zone_{ent._where.replace('#', '_')}_output_level"

            def _record_publish(*_args: Any, _e: Any = ent, **_kwargs: Any) -> None:
                published_levels.append(_e.native_value)

            ent.async_schedule_update_ha_state = MagicMock(side_effect=_record_publish)
            added_sensors.append(ent)

    await climate_setup_entry(hass, entry, _add_climates)
    await sensor_setup_entry(hass, entry, _add_sensors)
    assert added_climates == []
    assert added_sensors == []

    signal = f"myhome_message_{gw.mac}"

    # 1) Pre-fan delay window (frames 0..36): Zone 1 heating with floor valve 1#1 + enable 1#2,
    #    central pump 0#1 demand, and zero output-level sensors yet.
    for raw in raw_frames[:37]:
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    assert [c._where for c in added_climates] == ["1"]
    zone1 = added_climates[0]
    assert zone1.current_temperature == 19.6
    assert zone1.target_temperature == 23.0
    assert zone1.hvac_mode == HVACMode.HEAT
    assert zone1.hvac_action == HVACAction.HEATING
    assert zone1.extra_state_attributes["actuators"] == {"1": "on", "2": "on"}
    assert "actuator_levels" not in zone1.extra_state_attributes
    assert added_sensors == []

    # Frame 37: first *#4*1#3*#20*120## write alone does NOT spawn sensor or add "3" to actuators
    assert raw_frames[37] == "*#4*1#3*#20*120##"
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[37]))
    await hass.async_block_till_done()
    assert added_sensors == []
    assert "3" not in zone1.extra_state_attributes["actuators"]

    # 2) Frame 38 (16:27:27.438): *#4*1#3*20*120## discovers SensorEntity at 20 % on Zone 1's device
    assert raw_frames[38] == "*#4*1#3*20*120##"
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[38]))
    await hass.async_block_till_done()

    assert len(added_sensors) == 1
    act3_sensor = added_sensors[0]
    assert isinstance(act3_sensor, MyHOMEActuatorOutputSensor)
    assert act3_sensor.unique_id == f"{gw.mac}-4-1#3-output-level"
    assert act3_sensor.native_value == 20
    assert (
        device_info_to_dict(act3_sensor.device_info)["identifiers"]
        == device_info_to_dict(zone1.device_info)["identifiers"]
    )
    assert zone1.extra_state_attributes["actuators"] == {
        "1": "on",
        "2": "on",
        "3": "on",
    }
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 20}
    assert act3_sensor.async_schedule_update_ha_state.call_count == 1
    assert published_levels == [20]

    # 3) Full 25-step ramp + hold repeats + 3x 143 burst + interleaved Zones 2/4/5 & H4691 humidity (frames 39..207)
    for raw in raw_frames[39:208]:
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    expected_ramp = [
        20, 23, 26, 30, 33, 36, 40, 43, 46, 50, 53, 56, 60,
        63, 66, 70, 73, 76, 80, 83, 86, 90, 93, 96, 100,
    ]
    assert published_levels == expected_ramp
    assert act3_sensor.async_schedule_update_ha_state.call_count == 25
    assert act3_sensor.native_value == 100
    assert zone1.current_humidity == 61
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 100}

    by_where = {c._where: c for c in added_climates}
    # Zones 4 and 5 only emit one temperature frame each in the capture, so they stay
    # held as candidates until confirmed by a second frame (#966).
    assert set(by_where) == {"1", "2"}
    for raw in ("*4*1*4##", "*4*1*5##"):
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    by_where = {c._where: c for c in added_climates}
    assert set(by_where) == {"1", "2", "4", "5"}
    zone2 = by_where["2"]
    assert zone2.current_temperature == 19.3
    assert zone2.target_temperature == 18.0
    assert zone2.current_humidity == 63
    assert zone2.hvac_mode == HVACMode.HEAT
    assert zone2.hvac_action is None
    assert "actuators" not in zone2.extra_state_attributes
    assert "actuator_levels" not in zone2.extra_state_attributes
    assert by_where["4"].current_temperature == 20.0
    assert by_where["5"].current_temperature == 19.6
    assert len(added_sensors) == 1

    # 4) Shutdown (frames 208..220): setpoint back to 19.0 °C -> #20*100 ignored -> first 20*100 sets 0 %,
    #    duplicate 20*100 writes 0 extra updates -> pump 0#1 release -> 1#1 off -> idle & all actuators off
    for raw in raw_frames[208:212]:
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()
    assert raw_frames[211] == "*#4*1#3*#20*100##"
    assert act3_sensor.native_value == 100
    assert act3_sensor.async_schedule_update_ha_state.call_count == 25

    assert raw_frames[212] == "*#4*1#3*20*100##"
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[212]))
    await hass.async_block_till_done()
    assert act3_sensor.native_value == 0
    assert act3_sensor.async_schedule_update_ha_state.call_count == 26
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 0}

    assert raw_frames[213] == "*#4*1#3*20*100##"
    async_dispatcher_send(hass, signal, OWNMessage.parse(raw_frames[213]))
    await hass.async_block_till_done()
    assert act3_sensor.async_schedule_update_ha_state.call_count == 26

    for raw in raw_frames[214:]:
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()

    assert act3_sensor.native_value == 0
    assert published_levels == [*expected_ramp, 0]
    assert zone1.target_temperature == 19.0
    assert zone1.hvac_action == HVACAction.IDLE
    assert zone1.extra_state_attributes["actuators"] == {
        "1": "off",
        "2": "off",
        "3": "off",
    }
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 0}
    assert len(added_sensors) == 1


