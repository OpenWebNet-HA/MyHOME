"""Unit and end-to-end integration tests for Issue #949 (WHO=4 0-10 V fan-coil output level).

Covers:
1. ``where_grammar`` proportional decoding helpers: ``thermo_output_level``,
   ``thermo_dim20_level``, ``thermo_dim20_actuator_target``, and ``thermo_dim19_levels``.
2. Live hardware capture replay from ``xmavgithub``'s ``MyHomeServer1`` ``2.87.13``
   Sabiana 0-10 V fan-coil plant (``tests/fixtures/traces/issue_949/``):
   - ``MyHOMEClimate`` keeps ``actuators`` strictly ``"on"`` / ``"off"`` while exposing
     ``actuator_levels: {"3": 20..100 -> 0}``.
   - ``MyHOMEActuatorOutputSensor`` auto-discovers only for proportional actuator ``1#3``
     (not discrete ``1#1`` or ``1#2``), attaches to the exact same Home Assistant Device
     as ``MyHOMEClimate`` Zone 1, ignores paired ``#20`` write frames, and deduplicates
     repeated ``*20*`` status frames with ``generate_events`` disabled.
3. ``MyHOMEClimate`` proportional edge cases: deferred ``#785`` Dimension 20 replay,
   ``#932`` ``repeated_off`` at ``20*100``, ``Z#0`` group actuator handling, discrete
   ``1..22`` transitions, zone OFF / Dimension 19 resets, and Dimension 19
   ``cooling_output_level`` / ``heating_output_level``.
4. ``MyHOMEThermoSwitch`` ``output_level`` (single actuator) and ``actuator_levels``
   (whole-zone) across ``*20*``, ``*#20*``, ``Z#0``, and inactive Dimension 19.
5. ``MyHOMEActuatorOutputSensor`` lifecycle: ``thermo_switch`` exclusion and stale
   registry pruning, YAML ``climate`` device linkage, ``RestoreSensor`` bounded integer
   restoration, router key isolation, ``F455`` ``watch_zone_actuators``, ``async_update``
   gateway guards, and all zone/central OFF resets.
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
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_MAC, CONF_NAME, PERCENTAGE
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNHeatingCommand, OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

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
)
from custom_components.myhome.sensor import async_setup_entry as sensor_setup_entry
from custom_components.myhome.sensor import (
    async_unload_entry as sensor_unload_entry,
)
from custom_components.myhome.switch import MyHOMEThermoSwitch
from custom_components.myhome.where_grammar import (
    thermo_dim19_levels,
    thermo_dim20_actuator_target,
    thermo_dim20_level,
    thermo_output_level,
)

TRACE_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_949"
MHS1_RAMP_TRACE_PATH = (
    TRACE_DIR / "myhome_trace_MyHomeServer1_fancoil_dim20_ramp_2026-11-18.json"
)
MAC = "00:03:50:00:09:49"


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
        ("*#4*1*20*155##", ("1", "1", None)),
        ("*#4*99#9*20*200##", ("99", "9", None)),
        ("*#4*2#1#4#01*20*140##", ("2", "1", "01")),
        ("*#4*2#4#01*20*140##", ("2", "1", "01")),
        ("*#4*2#3#4#1*20*140##", ("2", "3", "01")),
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
    """thermo_dim20_actuator_target accepts Z#N (1..99, 1..9) and rejects group/central/pumps/probes."""
    msg = OWNMessage.parse(raw)
    assert thermo_dim20_actuator_target(msg) == expected


def test_thermo_dim20_actuator_target_mock_edge_cases() -> None:
    """thermo_dim20_actuator_target rejects non-digit tails and invalid actuator properties."""
    bad_tail = SimpleNamespace(
        who=4, where="1", zone=1, where_param=["x"], actuator="x"
    )
    assert thermo_dim20_actuator_target(bad_tail) is None

    bad_act = SimpleNamespace(who=4, where="1", zone=1, where_param=[], actuator="0")
    assert thermo_dim20_actuator_target(bad_act) is None

    raw_if_fallback = SimpleNamespace(
        who=4, where="1", zone=1, where_param=["3", "4", "2"], actuator="3"
    )
    assert thermo_dim20_actuator_target(raw_if_fallback) == ("1", "3", "02")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*#4*1*19*100*155##", (0, 55)),
        ("*#4*1*19*140*100##", (40, 0)),
        ("*#4*1*19*200*200##", (100, 100)),
        ("*#4*1*19*0*180##", (None, 80)),
        ("*#4*1*19*125*0##", (25, None)),
        ("*#4*1*19*0*1##", (None, None)),
        ("*#4*1*19*155*23##", None),
        ("*#4*1*19*99*100##", None),
        ("*#4*1*19*0*##", None),
        ("*#4*1*19##", None),
        ("*#4*1#3*20*155##", None),
        ("*#1*01*2*0*0*0##", None),
    ],
)
def test_thermo_dim19_levels(
    raw: str, expected: tuple[int | None, int | None] | None
) -> None:
    """thermo_dim19_levels decodes CV/HV proportional percentages and fails closed on invalid codes."""
    msg = OWNMessage.parse(raw)
    assert thermo_dim19_levels(msg) == expected


# ---------------------------------------------------------------------------
# 2. Live capture end-to-end replay (xmavgithub MyHomeServer1 2.87.13 #949)
# ---------------------------------------------------------------------------


async def test_live_myhomeserver1_fancoil_ramp_trace_end_to_end(
    hass: HomeAssistant,
) -> None:
    """Replay xmavgithub's Issue #949 live capture across climate and sensor platforms."""
    trace = json.loads(MHS1_RAMP_TRACE_PATH.read_text(encoding="utf-8"))
    assert trace["synthetic"] is False
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

    # 1) Initial ambient + setpoint + season + discrete actuators 1#2 and 1#1 turning ON
    for raw in frames[:7]:
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
    async_dispatcher_send(hass, signal, OWNMessage.parse(frames[7]))
    await hass.async_block_till_done()
    assert added_sensors == []
    assert "3" not in zone1.extra_state_attributes["actuators"]

    # 3) *#4*1#3*20*120## status frame: discovers sensor at 20 % and sets actuator_levels["3"] = 20
    async_dispatcher_send(hass, signal, OWNMessage.parse(frames[8]))
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
    assert act3_sensor.device_info["identifiers"] == zone1.device_info["identifiers"]
    assert zone1.extra_state_attributes["actuators"] == {
        "1": "on",
        "2": "on",
        "3": "on",
    }
    assert zone1.extra_state_attributes["actuator_levels"] == {"3": 20}

    # 4) Ramp 123 -> 150 -> 180 -> 200 (each preceded by #20 write)
    expected_ramp = [23, 50, 80, 100]
    for step_idx, expected_pct in enumerate(expected_ramp):
        write_frame = frames[9 + step_idx * 2]
        status_frame = frames[10 + step_idx * 2]
        writes_before = act3_sensor.async_schedule_update_ha_state.call_count

        # #20 write frame must not write sensor state
        async_dispatcher_send(hass, signal, OWNMessage.parse(write_frame))
        await hass.async_block_till_done()
        assert act3_sensor.async_schedule_update_ha_state.call_count == writes_before

        # *20* status frame updates sensor and climate once
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

    # 5) Setpoint restored to 19.0 °C -> *#4*1#3*#20*100## + *#4*1#3*20*100## -> 1#2*20*0 -> 1#1*20*0
    for raw in frames[17:]:
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
    mac = "00:03:50:00:09:49"
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


def test_climate_repeated_off_dim19_resets_and_group_actuator_zero(
    hass: HomeAssistant,
) -> None:
    """Verify repeated_off (#932), Z#0 selector, discrete pop/zero, Dim 19 levels, and zone OFF resets."""
    gw = _make_gateway("MyHomeServer1", "2.87.13")
    zone = _make_zone(hass, gw, "1")
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*0*0200##"))
    zone.handle_event(OWNHeatingEvent.parse("*4*1*1##"))

    # Actuator 3 proportional 45% -> active
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*145##"))
    assert zone.extra_state_attributes["actuators"] == {"3": "on"}
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 45}

    # Z#0 proportional 70% updates known actuator "3" without adding key "0"
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*170##"))
    assert zone.extra_state_attributes["actuators"] == {"3": "on"}
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 70}

    # Z#0 inactive 100 resets known actuator "3" to 0 without adding key "0"
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*100##"))
    assert zone.extra_state_attributes["actuators"] == {"3": "off"}
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Dimension 19 active with proportional cooling=0%, heating=60% leads as "zone"
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*100*160##"))
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["cooling_output_level"] == 0
    assert zone.extra_state_attributes["heating_output_level"] == 60

    # Actuator 1 reports discrete active 1 -> discrete active does not touch #3 level,
    # then #1 reports 0 -> false, Dim 19 active again, then repeated_off on #3 at 20*100:
    zone._actuator_levels["3"] = 35
    zone._actuator_states["3"] = False
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*100*160##"))
    assert zone._active_source == "zone"
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*100##"))
    # repeated_off keeps _active_source == "zone" (still HEATING) while updating #3 level to 0!
    assert zone._active_source == "zone"
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["actuator_levels"]["3"] == 0

    # Discrete inactive on #3 while already False (repeated_off without dim20_level) keeps 0
    zone._actuator_levels["3"] = 15
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*0##"))
    assert zone.extra_state_attributes["actuator_levels"]["3"] == 0

    # Discrete active *#4*1#3*20*1## pops "3" from actuator_levels
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*150##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 50}
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*1##"))
    assert "actuator_levels" not in zone.extra_state_attributes

    # Re-add #3 proportional, then discrete inactive *#4*1#3*20*0## sets level to 0
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*150##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*0##"))
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Z#0 discrete active *#4*1#0*20*1## clears actuator_levels
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*150##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*1##"))
    assert "actuator_levels" not in zone.extra_state_attributes

    # Dimension 19 discrete active pops cooling_output_level / heating_output_level
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*140*180##"))
    assert zone.extra_state_attributes["cooling_output_level"] == 40
    assert zone.extra_state_attributes["heating_output_level"] == 80
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*1*1##"))
    assert "cooling_output_level" not in zone.extra_state_attributes
    assert "heating_output_level" not in zone.extra_state_attributes

    # Dimension 19 discrete inactive sets known proportional levels to 0
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*140*180##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*155##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*0*0##"))
    assert zone.extra_state_attributes["cooling_output_level"] == 0
    assert zone.extra_state_attributes["heating_output_level"] == 0
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}

    # Zone mode OFF resets all levels via _mark_actuators_off()
    zone.handle_event(OWNHeatingEvent.parse("*#4*1*19*125*175##"))
    zone.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*190##"))
    zone.handle_event(OWNHeatingEvent.parse("*4*303*1##"))
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.extra_state_attributes["actuator_levels"] == {"3": 0}
    assert zone.extra_state_attributes["cooling_output_level"] == 0
    assert zone.extra_state_attributes["heating_output_level"] == 0


# ---------------------------------------------------------------------------
# 4. MyHOMEThermoSwitch proportional attributes
# ---------------------------------------------------------------------------


def test_thermo_switch_per_actuator_and_whole_zone_output_levels(
    hass: HomeAssistant,
) -> None:
    """MyHOMEThermoSwitch exposes output_level (Z#N) and actuator_levels (Z) on 20 and #20."""
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
    # #20 write at 60 %
    sw_act.handle_event(OWNHeatingCommand.parse("*#4*7#1*#20*160##"))  # type: ignore[arg-type]
    assert sw_act.is_on is True
    assert sw_act.extra_state_attributes["output_level"] == 60
    # *20* status at 25 %
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*125##"))
    assert sw_act.is_on is True
    assert sw_act.extra_state_attributes["output_level"] == 25
    # Group Z#0 proportional 80 %
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*180##"))
    assert sw_act.extra_state_attributes["output_level"] == 80
    # Group Z#0 discrete inactive resets to 0
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*0##"))
    assert sw_act.is_on is False
    assert sw_act.extra_state_attributes["output_level"] == 0
    # Group Z#0 discrete active pops output_level
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#1*20*140##"))
    sw_act.handle_event(OWNHeatingEvent.parse("*#4*7#0*20*1##"))
    assert "output_level" not in sw_act.extra_state_attributes
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

    # Empty state receiving Z#0 proportional initializes actuator "1"
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*130##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"1": 30}
    sw_zone.handle_event(OWNHeatingCommand.parse("*#4*8#3*#20*190##"))  # type: ignore[arg-type]
    assert sw_zone.extra_state_attributes["actuators"] == {"1": "on", "3": "on"}
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"1": 30, "3": 90}
    # Z#0 proportional updates all known proportional keys
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*150##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"1": 50, "3": 50}
    # Z#0 discrete inactive resets to 0
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*0##"))
    assert sw_zone.extra_state_attributes["actuator_levels"] == {"1": 0, "3": 0}
    # Z#0 discrete active clears actuator_levels
    sw_zone.handle_event(OWNHeatingEvent.parse("*#4*8#0*20*1##"))
    assert "actuator_levels" not in sw_zone.extra_state_attributes
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
    entry = _make_entry(
        hass,
        gw,
        platforms={
            "climate": {
                "living": {
                    CONF_WHO: "4",
                    CONF_WHERE: "#1",
                    CONF_NAME: "Living Room",
                    CONF_STANDALONE: False,
                }
            },
            "switch": {
                "zone8_whole": {CONF_WHO: "4", CONF_WHERE: "8"},
            },
        },
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
        ],
    )

    # Seed a stale registry entry on probe-less thermo_switch 7#1 AND a valid registry entry on 2#3
    ent_reg = er.async_get(hass)
    stale_entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-7#1-output-level",
        config_entry=entry,
    )
    restored_entry = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{gw.mac}-4-2#3-output-level",
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

    # Stale 7#1 entry was pruned from entity registry; valid 2#3 was restored!
    assert ent_reg.async_get(stale_entry.entity_id) is None
    assert ent_reg.async_get(restored_entry.entity_id) is not None
    assert [e.unique_id for e in added] == [f"{gw.mac}-4-2#3-output-level"]

    signal = f"myhome_message_{gw.mac}"
    ignored_frames = [
        "*#4*3#1*20*0##",
        "*#4*3#1*20*1##",
        "*#4*3#1*20*8##",
        "*#4*3#1*20*14##",
        "*#4*3#1*20*22##",
        "*#4*3#0*20*150##",
        "*#4*0#1*20*150##",
        "*#4*#0*20*150##",
        "*#4*#3*20*150##",
        "*#4*105*20*150##",
        "*#4*3#1*#20*150##",
        # Claimed by probe-less thermo_switch 7#1 (including sibling 7#2 on probe-less zone 7)
        "*#4*7#1*20*150##",
        "*#4*7#2*20*150##",
        # Claimed by whole-zone thermo_switch 8
        "*#4*8#1*20*150##",
    ]
    for raw in ignored_frames:
        async_dispatcher_send(hass, signal, OWNMessage.parse(raw))
    await hass.async_block_till_done()
    assert len(added) == 1

    # Zone 1 HAS explicit climate config ("#1", "Living Room"), so 1#3 discovers and links to "#1"!
    async_dispatcher_send(hass, signal, OWNMessage.parse("*#4*1#3*20*175##"))
    await hass.async_block_till_done()
    assert len(added) == 2
    living_act3 = added[1]
    assert living_act3.unique_id == f"{gw.mac}-4-1#3-output-level"
    assert living_act3._device_id == "#1"
    assert living_act3.device_info["identifiers"] == {(DOMAIN, f"{gw.mac}-4-#1")}
    assert living_act3._device_name == "Living Room"
    assert living_act3.native_value == 75
    assert living_act3._standalone is False

    assert await sensor_unload_entry(hass, entry)


@pytest.mark.parametrize(
    ("stored_val", "expected_val"),
    [
        (42, 42),
        (0, 0),
        (100, 100),
        (75.0, 75),
        (-1, None),
        (101, None),
        (42.5, None),
        ("invalid", None),
    ],
)
async def test_actuator_output_sensor_restore_state_bounds(
    hass: HomeAssistant, stored_val: Any, expected_val: int | None
) -> None:
    """RestoreSensor restores only bounded integer 0..100 percentages across restarts."""
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
                State(sensor.entity_id, str(stored_val)),
                SensorExtraStoredData(
                    native_value=stored_val,
                    native_unit_of_measurement=PERCENTAGE,
                ),
            )
        ],
    )
    await sensor.async_added_to_hass()
    assert sensor.native_value == expected_val


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


async def test_actuator_output_sensor_polling_f455_watch_and_interface_isolation(
    hass: HomeAssistant,
) -> None:
    """Verify F455 watch_zone_actuators, async_update gateway guards, and #4#01 interface isolation."""
    # 1) F455 (zone_actuator_request_refused=True) registers watch_zone_actuators("1#3")
    gw_f455 = _make_gateway(
        "F455",
        "1.0.76",
        actuator_refused=False,
        zone_actuator_refused=True,
    )
    unsub = MagicMock()
    gw_f455.watch_zone_actuators = MagicMock(return_value=unsub)

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

    # async_update sends *#4*1#3*20## (NEVER dimensionless *#4*1#3##!)
    await s_f455.async_update()
    gw_f455.send_status_request.assert_awaited_once()
    sent_cmd = gw_f455.send_status_request.await_args.args[0]
    assert str(sent_cmd) == "*#4*1#3*20##"

    # 2) H4684 / MH200 (actuator_request_refused=True) skips async_update and watch_zone_actuators
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
    gw_h4684.watch_zone_actuators.assert_not_called()
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

    await s_if.async_added_to_hass()
    gw_if.watch_zone_actuators.assert_not_called()
    await s_if.async_update()
    gw_if.send_status_request.assert_not_awaited()

    # When profile allows interface requests, async_update sends *#4*1#3#4#01*20##
    gw_if.interface_zone_request_refused = False
    await s_if.async_update()
    assert str(gw_if.send_status_request.await_args.args[0]) == "*#4*1#3#4#01*20##"

    # Main-bus 1#3 frame ignored; #4#01 frame accepted
    s_if.handle_event(OWNHeatingEvent.parse("*#4*1#3*20*155##"))
    assert s_if.native_value is None
    s_if.handle_event(OWNHeatingEvent.parse("*#4*1#3#4#01*20*145##"))
    assert s_if.native_value == 45


async def test_actuator_output_sensor_all_reset_paths(
    hass: HomeAssistant,
) -> None:
    """Verify every reset-to-0 path on MyHOMEActuatorOutputSensor (standalone vs central)."""
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

    # 2) Group selector Z#0 proportional and inactive
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*145##"))
    assert sensor.native_value == 45
    sensor.handle_event(OWNHeatingEvent.parse("*#4*1#0*20*0##"))
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
