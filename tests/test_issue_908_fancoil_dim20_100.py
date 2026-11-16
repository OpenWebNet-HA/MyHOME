"""Regression and multi-gateway trace tests for Issue #908 (WHO=4 Dimension 19/20 output codes).

Verifies that:
1. Proportional 0-10 V 0% output (``*#4*Z#N*20*100##``, ``*#4*Z#N*#20*100##``, and
   Dimension 19 ``100``) is decoded as **inactive** (``is_active = False, fan_on = None``),
   preventing fan-coil and modulating-valve zones from sticking on ``hvac_action = "heating"``
   or flipping between ``heating`` and ``idle`` every ~8 minutes (#908).
2. Proportional 0-10 V 1%..100% output (``101..200``) is decoded as **active** without
   falsely enabling discrete fan-coil mode on pure 0-10 V mixing valves or spawning
   ghost ``climate`` entities on probe-less ``MyHOMEThermoSwitch`` zones (#785).
3. ``MyHomeServer1`` / ``F461`` extended fan-coil OFF-speed states ``14, 15, 16`` (SCS ``'24'..'26'``)
   decode as ``is_active = False, fan_on = False`` (``running_fan_speed = "off"``), and auxiliary
   discrete states ``10..13, 17..22`` follow ``bt_thermo_manager`` bitmask parity without
   claiming a fan state.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import CONF_MAC
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import MESSAGE_TYPE_ACTION, OWNHeatingCommand, OWNHeatingEvent, OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.climate import async_setup_entry as climate_setup_entry
from custom_components.myhome.const import (
    CONF_WHERE,
    CONF_WHO,
    DOMAIN,
    PLATFORMS,
    SUBENTRY_TYPE_THERMO_SWITCH,
)
from custom_components.myhome.data import MyHOMERuntimeData
from custom_components.myhome.switch import MyHOMEThermoSwitch
from custom_components.myhome.switch import async_setup_entry as switch_setup_entry
from custom_components.myhome.where_grammar import (
    _decode_thermo_output_code,
    thermo_dim19_status,
    thermo_dim20_is_active,
    thermo_dim20_status,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_908"
MHS1_LIVE_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_MyHomeServer1_fancoil_dim20_100_2026-11-14.json"
)
F459_TRACE_PATH = TRACES_DIR / "myhome_trace_oracle_f459_sabiana_fancoil_dim20_100.json"
MH202_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_oracle_mh202_dim20_poll_dropped_periodic_100.json"
)
MHS1_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_oracle_myhomeserver1_extended_fancoil_off_14_16.json"
)
ORACLE_JSON_PATH = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
CORPUS_JSON_PATH = Path(__file__).resolve().parent / "golden" / "corpus.json"
MAC = "00:03:50:90:80:01"


def _make_gateway(
    model: str = "F459",
    firmware: str = "2.1.5",
    *,
    actuator_refused: bool = False,
) -> MagicMock:
    gw = MagicMock()
    gw.name = model
    gw.mac = MAC
    gw.unique_id = MAC
    gw.log_id = f"[{model} - {MAC}]"
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
    gw.availability_signal = f"myhome_gw_available_{MAC}"
    gw.is_who_available = MagicMock(return_value=True)
    gw.initial_discovery_pending = False
    gw.sweep_active = False
    gw.actuator_request_refused = actuator_refused
    gw.send = AsyncMock(return_value=True)
    gw.send_status_request = AsyncMock(return_value=True)
    gw.request_zone_dimension = MagicMock()
    gw.watch_zone_actuators = MagicMock(return_value=MagicMock())
    return gw


def _make_zone(
    hass: HomeAssistant,
    gw: MagicMock,
    where: str,
    *,
    fan: bool = False,
    cooling: bool = True,
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
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=gw,
    )
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    zone.async_schedule_update_ha_state = MagicMock()
    return zone


def test_issue_908_oracle_gateway_divergence_on_who4_dim20() -> None:
    """Verify the firmware oracle contract backing the Issue #908 gateway traces."""
    assert MHS1_LIVE_TRACE_PATH.is_file()
    assert json.loads(MHS1_LIVE_TRACE_PATH.read_text(encoding="utf-8"))["synthetic"] is False
    assert F459_TRACE_PATH.is_file()
    assert MH202_TRACE_PATH.is_file()
    assert MHS1_TRACE_PATH.is_file()

    oracle = json.loads(ORACLE_JSON_PATH.read_text(encoding="utf-8"))["verdicts"]
    dim20 = {row["product"]: row for row in oracle["*#4*1*20##"]}
    dim19 = {row["product"]: row for row in oracle["*#4*1*19##"]}

    for model in ("F459", "F454", "F461", "F450", "F460", "MyHomeServer1"):
        assert dim20[model]["verdict"] == "out"
        assert dim19[model]["verdict"] == "out"

    for model in ("MH200N", "MH202", "H4684", "MH200", "F453", "F453AV"):
        assert dim20[model]["reply"] == "nack"
        assert dim20[model]["verdict"] == "silent"
        assert dim19[model]["verdict"] == "out"


def test_thermo_dim20_and_dim19_status_protocol_table_and_fallbacks() -> None:
    """Verify protocol-exact decoding of the full 0..259 & -1 Dim 19/20 table and mocks."""
    # Exhaustive 0..259 matrix across Dimension 20 (status & #20 write) and Dimension 19
    expected_by_code: dict[int, tuple[bool, bool | None, int | None] | None] = {}
    for code in range(260):
        if code in (0, 3, 4, 13, 17, 100):
            expected_by_code[code] = (False, None, None)
        elif code in (5, 14, 15, 16):
            expected_by_code[code] = (False, False, None)
        elif code in (1, 2, 10, 11, 12, 18, 19, 20, 21, 22):
            expected_by_code[code] = (True, None, None)
        elif code in (6, 7, 8):
            expected_by_code[code] = (True, True, code - 5)
        elif code == 9:
            expected_by_code[code] = (True, True, None)
        elif 101 <= code <= 200:
            expected_by_code[code] = (True, None, None)
        else:
            expected_by_code[code] = None

    for code, expected in expected_by_code.items():
        evt20 = OWNHeatingEvent(f"*#4*1#3*20*{code}##")
        cmd20 = OWNHeatingCommand(f"*#4*1#3*#20*{code}##")
        expected_active = expected[0] if expected is not None else None
        assert thermo_dim20_status(evt20) == expected
        assert thermo_dim20_status(cmd20) == expected
        assert thermo_dim20_is_active(evt20) is expected_active
        assert thermo_dim20_is_active(cmd20) is expected_active

        evt19_heat = OWNHeatingEvent(f"*#4*1*19*0*{code}##")
        evt19_cool = OWNHeatingEvent(f"*#4*1*19*{code}*0##")
        if expected is None:
            assert thermo_dim19_status(evt19_heat) is None
            assert thermo_dim19_status(evt19_cool) is None
        else:
            assert thermo_dim19_status(evt19_heat) == (
                False,
                expected[0],
                expected[0],
                None,
                expected[1],
                expected[2],
            )
            assert thermo_dim19_status(evt19_cool) == (
                expected[0],
                False,
                expected[0],
                expected[1],
                None,
                expected[2],
            )

    # Explicit boundary checks for bt_thermo_manager bitmask codes 10..13 & 17..22
    for code in (13, 17):
        assert thermo_dim20_status(OWNHeatingEvent(f"*#4*1#3*20*{code}##")) == (False, None, None)
        assert thermo_dim20_status(OWNHeatingCommand(f"*#4*1#3*#20*{code}##")) == (
            False,
            None,
            None,
        )
        assert thermo_dim19_status(OWNHeatingEvent(f"*#4*1*19*0*{code}##")) == (
            False,
            False,
            False,
            None,
            None,
            None,
        )
        assert thermo_dim19_status(OWNHeatingEvent(f"*#4*1*19*{code}*0##")) == (
            False,
            False,
            False,
            None,
            None,
            None,
        )

    for code in (10, 11, 12, 18, 19, 20, 21, 22):
        assert thermo_dim20_status(OWNHeatingEvent(f"*#4*1#3*20*{code}##")) == (True, None, None)
        assert thermo_dim20_status(OWNHeatingCommand(f"*#4*1#3*#20*{code}##")) == (
            True,
            None,
            None,
        )
        assert thermo_dim19_status(OWNHeatingEvent(f"*#4*1*19*0*{code}##")) == (
            False,
            True,
            True,
            None,
            None,
            None,
        )
        assert thermo_dim19_status(OWNHeatingEvent(f"*#4*1*19*{code}*0##")) == (
            True,
            False,
            True,
            None,
            None,
            None,
        )

    # Unassigned gaps (23..99, 201..259) and mixed valid + 23 on Dimension 19 -> None
    for code in (*range(23), *range(100, 201)):
        assert thermo_dim19_status(OWNHeatingEvent(f"*#4*1*19*{code}*23##")) is None
        assert thermo_dim19_status(OWNHeatingEvent(f"*#4*1*19*23*{code}##")) is None

    # Negative value (-1) via helper and non-negative-string fallback -> None
    assert _decode_thermo_output_code(-1) is None
    assert (
        thermo_dim20_status(
            SimpleNamespace(
                who=4,
                dimension=20,
                message_type=MESSAGE_TYPE_ACTION,
                dimension_value=["-1"],
            )
        )
        is None
    )
    for vals in (["-1", "0"], ["0", "-1"]):
        assert (
            thermo_dim19_status(
                SimpleNamespace(
                    who=4,
                    dimension=19,
                    message_type=MESSAGE_TYPE_ACTION,
                    dimension_value=vals,
                )
            )
            is None
        )

    # Status requests, cross-WHO frames, non-ASCII digits, out-of-table codes, and non-Dim20 -> None
    assert thermo_dim20_status(OWNHeatingCommand("*#4*1#3*20##")) is None
    assert thermo_dim20_is_active(OWNHeatingCommand("*#4*1#3*20##")) is None
    assert thermo_dim20_status(OWNMessage.parse("*#13**20*1##")) is None
    assert thermo_dim20_is_active(OWNMessage.parse("*#13**20*1##")) is None
    assert thermo_dim20_status(OWNHeatingEvent("*#4*1*0*0200##")) is None
    assert (
        thermo_dim20_status(
            SimpleNamespace(
                who=4,
                dimension=20,
                message_type=MESSAGE_TYPE_ACTION,
                dimension_value=["abc"],
            )
        )
        is None
    )
    assert (
        thermo_dim20_status(
            SimpleNamespace(
                who=4,
                dimension=20,
                message_type=MESSAGE_TYPE_ACTION,
                dimension_value=["\u00b2"],
            )
        )
        is None
    )
    assert thermo_dim20_status(OWNHeatingEvent("*#4*1#3*20*999##")) is None

    # Mock fallback without sequence dimension_value
    mock_d20 = MagicMock(
        dimension=20,
        message_type=MESSAGE_TYPE_ACTION,
        fan_on=True,
        fan_speed=2,
    )
    mock_d20.is_active.return_value = True
    assert thermo_dim20_status(mock_d20) == (True, True, 2)
    assert thermo_dim20_status(SimpleNamespace(dimension=20, message_type="other")) is None
    assert (
        thermo_dim20_status(
            SimpleNamespace(dimension=20, message_type=MESSAGE_TYPE_ACTION)
        )
        is None
    )

    # Dimension 19 decoding across 100, 101..200, 14..16, cooling fan speed, and invalid/mock fallbacks
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19*0*100##")) == (
        False,
        False,
        False,
        None,
        None,
        None,
    )
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19*0*150##")) == (
        False,
        True,
        True,
        None,
        None,
        None,
    )
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19*140*0##")) == (
        True,
        False,
        True,
        None,
        None,
        None,
    )
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19*14*15##")) == (
        False,
        False,
        False,
        False,
        False,
        None,
    )
    assert thermo_dim19_status(OWNHeatingEvent("*#4*2*19*7*5##")) == (
        True,
        False,
        True,
        True,
        False,
        2,
    )
    assert thermo_dim19_status(OWNHeatingEvent("*#4*2*19*7*0##")) == (
        True,
        False,
        True,
        True,
        None,
        2,
    )
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19*0*999##")) is None
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*0*0200##")) is None
    assert (
        thermo_dim19_status(
            SimpleNamespace(
                who=13,
                dimension=19,
                message_type=MESSAGE_TYPE_ACTION,
                dimension_value=["7", "5"],
            )
        )
        is None
    )
    assert (
        thermo_dim19_status(
            SimpleNamespace(
                who=4,
                dimension=19,
                message_type=MESSAGE_TYPE_ACTION,
                dimension_value=["\u00b2", "0"],
            )
        )
        is None
    )

    mock_d19 = MagicMock(
        dimension=19,
        message_type=MESSAGE_TYPE_ACTION,
        cooling_fan_on=False,
        fan_on=True,
        fan_speed=3,
    )
    mock_d19.is_cooling.return_value = False
    mock_d19.is_heating.return_value = True
    mock_d19.is_active.return_value = True
    assert thermo_dim19_status(mock_d19) == (False, True, True, False, True, 3)

    mock_d19_cool = MagicMock(
        dimension=19,
        message_type=MESSAGE_TYPE_ACTION,
        cooling_fan_on=True,
        cooling_fan_speed=2,
        fan_on=False,
        fan_speed=None,
    )
    mock_d19_cool.is_cooling.return_value = True
    mock_d19_cool.is_heating.return_value = False
    mock_d19_cool.is_active.return_value = True
    assert thermo_dim19_status(mock_d19_cool) == (True, False, True, True, False, 2)
    assert (
        thermo_dim19_status(
            SimpleNamespace(dimension=19, message_type=MESSAGE_TYPE_ACTION)
        )
        is None
    )

    # Out-of-table Dimension 19 empty-field frame emitted by emulated MyHomeServer1/F461 -> None
    empty_dim19 = OWNMessage.parse("*#4*1*19*0*##")
    assert isinstance(empty_dim19, OWNHeatingEvent)
    assert empty_dim19.message_type is None
    assert empty_dim19.dimension == 19
    assert empty_dim19.dimension_value == ["0", ""]
    assert thermo_dim19_status(empty_dim19) is None
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19*0*##")) is None
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19**0##")) is None
    assert thermo_dim19_status(OWNHeatingEvent("*#4*1*19**##")) is None
    for bad_vals in (["0", ""], ["", "0"], ["0"]):
        assert (
            thermo_dim19_status(
                MagicMock(
                    who=4,
                    dimension=19,
                    message_type=MESSAGE_TYPE_ACTION,
                    dimension_value=bad_vals,
                )
            )
            is None
        )


@pytest.mark.asyncio
async def test_f459_sabiana_trace_replay_no_stuck_or_flipping_heating(
    hass: HomeAssistant,
) -> None:
    """Replay the F459 Sabiana + floor-heating trace: idle at 20*100, heating only on real demand."""
    trace = json.loads(F459_TRACE_PATH.read_text(encoding="utf-8"))
    gw = _make_gateway(model=trace["gateway"]["model"], firmware=trace["gateway"]["firmware"])
    zone1 = _make_zone(hass, gw, "1")
    zone2 = _make_zone(hass, gw, "2")
    zones = {"1": zone1, "2": zone2}

    z2_actions: list[HVACAction | None] = []

    for idx, frame in enumerate(trace["frames"]):
        if frame["direction"] != "rx" or frame["who"] != "4":
            continue
        parsed = OWNMessage.parse(frame["raw"])
        assert isinstance(parsed, OWNHeatingEvent)
        target_zone = str(frame["where"]).split("#")[0]
        zones[target_zone].handle_event(parsed)
        if target_zone == "2":
            z2_actions.append(zone2.hvac_action)

        # Before frame index 20 (T+15m room drop to 18.2 C), Zone 1 MUST stay IDLE!
        if idx < 20 and zone1.hvac_mode == HVACMode.HEAT and zone1._actuator_states:
            assert zone1.hvac_action == HVACAction.IDLE
            if "3" in zone1._actuator_states:
                assert zone1.extra_state_attributes["actuators"]["3"] == "off"
            assert zone1._fan is False

    # Zone 2 never flipped to HEATING despite alternating 19*5*0 and 2#3*20*100!
    assert z2_actions
    assert all(act in (None, HVACAction.IDLE) for act in z2_actions)
    assert zone2.hvac_action == HVACAction.IDLE
    assert zone2.extra_state_attributes["actuators"] == {"1": "off", "3": "off"}

    # At the end of the trace, Zone 1's #1 valve closed (0), while #3 Sabiana is still at 155 (55%):
    assert zone1.hvac_action == HVACAction.HEATING
    assert zone1.extra_state_attributes["actuators"] == {"1": "off", "3": "on"}

    # Once Sabiana #3 drops back to 0% (*#4*1#3*20*100##), Zone 1 transitions cleanly to IDLE!
    zone1.handle_event(OWNHeatingEvent("*#4*1#3*20*100##"))
    assert zone1.hvac_action == HVACAction.IDLE
    assert zone1.extra_state_attributes["actuators"] == {"1": "off", "3": "off"}


@pytest.mark.asyncio
async def test_mh202_dropped_poll_trace_periodic_100_keeps_idle(
    hass: HomeAssistant,
) -> None:
    """On MH202 (*#4*1*20## dropped), unsolicited 8-min *#4*1#3*20*100## keeps hvac_action IDLE."""
    trace = json.loads(MH202_TRACE_PATH.read_text(encoding="utf-8"))
    gw = _make_gateway(
        model=trace["gateway"]["model"],
        firmware=trace["gateway"]["firmware"],
        actuator_refused=True,
    )
    zone1 = _make_zone(hass, gw, "1")

    for frame in trace["frames"]:
        if frame["direction"] != "rx" or frame["who"] != "4":
            continue
        parsed = OWNMessage.parse(frame["raw"])
        assert isinstance(parsed, OWNHeatingEvent)
        zone1.handle_event(parsed)

    assert zone1._active_source == "actuators"
    assert zone1._actuator_states == {"3": False}
    assert zone1.hvac_action == HVACAction.IDLE


@pytest.mark.asyncio
async def test_myhomeserver1_extended_off_14_16_and_probeless_zone_no_ghost_climate(
    hass: HomeAssistant,
) -> None:
    """Replay MyHomeServer1 trace: 14..16 stay idle/off and 7#1*20*100 spawns no ghost climate."""
    trace = json.loads(MHS1_TRACE_PATH.read_text(encoding="utf-8"))
    gw = _make_gateway(model=trace["gateway"]["model"], firmware=trace["gateway"]["firmware"])

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: gw.mac, "host": "192.0.2.98", "port": 20000},
        unique_id=gw.mac,
        subentries_data=[
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "7#1", "name": "Sabiana Damper"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Sabiana Damper",
                "unique_id": f"{gw.mac}-thermo-switch-7#1",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "8", "name": "Zone 8 Towel"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Zone 8 Towel",
                "unique_id": f"{gw.mac}-thermo-switch-8",
            },
        ],
    )
    entry.add_to_hass(hass)
    plat_dict: dict[str, dict[str, dict[str, str]]] = {p: {} for p in PLATFORMS}
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms=plat_dict,
        entities={p: {} for p in PLATFORMS},
    )

    added_climates: list[MyHOMEClimate] = []
    added_switches: list[MyHOMEThermoSwitch] = []

    def _collect_climates(entities: list[MyHOMEClimate]) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"climate.zone_{ent._where}"
            ent.async_write_ha_state = MagicMock()
            ent.async_schedule_update_ha_state = MagicMock()
        added_climates.extend(entities)

    def _collect_switches(entities: list[MyHOMEThermoSwitch], **_: object) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"switch.thermo_{ent._where.replace('#', '_')}"
            ent.async_write_ha_state = MagicMock()
            ent.async_schedule_update_ha_state = MagicMock()
        added_switches.extend(entities)

    await climate_setup_entry(hass, entry, _collect_climates)  # type: ignore[arg-type]
    await switch_setup_entry(hass, entry, _collect_switches)  # type: ignore[arg-type]

    sw_by_where = {s._full_where: s for s in added_switches}
    assert set(sw_by_where) == {"7#1", "8"}

    for frame in trace["frames"]:
        if frame["direction"] != "rx" or frame["who"] != "4":
            continue
        parsed = OWNMessage.parse(frame["raw"])
        assert parsed is not None
        if isinstance(parsed, OWNHeatingEvent):
            async_dispatcher_send(hass, f"myhome_message_{MAC}", parsed)
            async_dispatcher_send(hass, f"myhome_event_{MAC}_4", parsed)
        for sw in added_switches:
            if isinstance(parsed, (OWNHeatingEvent, OWNHeatingCommand)):
                sw.handle_event(parsed)

    # Only Zone 1 was created as a climate entity — probe-less Zone 7 did NOT spawn a ghost climate!
    clim_by_where = {c._where: c for c in added_climates}
    assert set(clim_by_where) == {"1"}
    z1 = clim_by_where["1"]
    assert z1._fan is True
    assert z1.extra_state_attributes["running_fan_speed"] == "off"
    assert z1.hvac_action == HVACAction.IDLE

    # Switch 7#1 went False on 20*100 and True on #20*200; #20*100 returns it to False
    sw_7_1 = sw_by_where["7#1"]
    assert sw_7_1.is_on is True
    sw_7_1.handle_event(OWNHeatingCommand("*#4*7#1*#20*100##"))
    assert sw_7_1.is_on is False

    # Zone-wide thermo switch 8: Dim 19 0*100 and 0*14 defer climate creation and set switch OFF,
    # while Dim 19 0*150 defers climate creation and sets switch ON!
    for raw_d19, expected_on in (
        ("*#4*8*19*0*100##", False),
        ("*#4*8*19*0*14##", False),
        ("*#4*8*19*0*150##", True),
    ):
        evt_d19 = OWNHeatingEvent(raw_d19)
        if raw_d19 != "*#4*8*19*0*14##":
            async_dispatcher_send(hass, f"myhome_message_{MAC}", evt_d19)
        sw_by_where["8"].handle_event(evt_d19)
        assert sw_by_where["8"].is_on is expected_on

    # Unlisted Dim 20 (250), Dim 19 (999), and out-of-table empty-field Dim 19 (*#4*8*19*0*##)
    # on probe-less zones ALSO defer (#785) and leave switch state untouched
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*250##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8*19*0*999##"))
    empty_dim19_probe = OWNMessage.parse("*#4*8*19*0*##")
    assert isinstance(empty_dim19_probe, OWNHeatingEvent)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", empty_dim19_probe)
    sw_by_where["8"].handle_event(empty_dim19_probe)
    assert sw_by_where["8"].is_on is True
    assert {c._where for c in added_climates} == {"1"}


@pytest.mark.asyncio
async def test_climate_dim19_and_dim20_unrecognized_and_modulating_cooling(
    hass: HomeAssistant,
) -> None:
    """Unrecognized Dim 19/20 codes are ignored cleanly; Dim 19 modulating & cooling fan speed work."""
    gw = _make_gateway()
    zone = _make_zone(hass, gw, "1")
    zone._attr_hvac_mode = HVACMode.AUTO

    # Unrecognized Dim 20 (999), Dim 19 (999), and empty-field Dim 19 (*#4*1*19*0*##)
    # return early without mutating _actuator_states or _zone_active
    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*999##"))
    assert zone._actuator_states == {}
    zone.handle_event(OWNHeatingEvent("*#4*1*19*0*999##"))
    assert zone._zone_active is None
    empty_dim19_evt = OWNMessage.parse("*#4*1*19*0*##")
    assert isinstance(empty_dim19_evt, OWNHeatingEvent)
    zone.handle_event(empty_dim19_evt)
    assert zone._zone_active is None

    # Modulating 0-10 V cooling (140 = 40%) and heating (160 = 60%) on Dim 19 set COOLING / HEATING
    zone.handle_event(OWNHeatingEvent("*#4*1*19*140*0##"))
    assert zone.hvac_action == HVACAction.COOLING
    zone.handle_event(OWNHeatingEvent("*#4*1*19*0*160##"))
    assert zone.hvac_action == HVACAction.HEATING
    zone.handle_event(OWNHeatingEvent("*#4*1*19*100*100##"))
    assert zone.hvac_action == HVACAction.IDLE

    # Live F455 cooling fan-coil frames (*#4*2*19*7*5## and *#4*2*19*7*0##) preserve cooling speed
    # and transition cleanly to running_fan_speed == "off" & HVACAction.IDLE on 14..16 / 5!
    cool_zone = _make_zone(hass, gw, "2")
    cool_zone._attr_hvac_mode = HVACMode.COOL
    cool_zone.handle_event(OWNHeatingEvent("*#4*2*19*7*5##"))
    assert cool_zone.hvac_action == HVACAction.COOLING
    assert cool_zone._fan is True
    assert cool_zone.extra_state_attributes["running_fan_speed"] == "medium"

    cool_zone.handle_event(OWNHeatingEvent("*#4*2*19*14*5##"))
    assert cool_zone.hvac_action == HVACAction.IDLE
    assert cool_zone.extra_state_attributes["running_fan_speed"] == "off"

    cool_only_fan = _make_zone(hass, gw, "3")
    cool_only_fan._attr_hvac_mode = HVACMode.COOL
    cool_only_fan.handle_event(OWNHeatingEvent("*#4*3*19*7*0##"))
    assert cool_only_fan.hvac_action == HVACAction.COOLING
    assert cool_only_fan._fan is True
    assert cool_only_fan.extra_state_attributes["running_fan_speed"] == "medium"

    cool_only_fan.handle_event(OWNHeatingEvent("*#4*3*19*14*0##"))
    assert cool_only_fan.hvac_action == HVACAction.IDLE
    assert cool_only_fan.extra_state_attributes["running_fan_speed"] == "off"


@pytest.mark.asyncio
async def test_live_myhomeserver1_gist_trace_replay_zones_1_and_2_stay_idle(
    hass: HomeAssistant,
) -> None:
    """Replay @xmavgithub's live MyHomeServer1 (2.87.13) WHO=4 capture end-to-end.

    Verifies that across all 130 captured frames (including paired ``#20*100`` + ``20*100``
    0-10 V 0% Sabiana broadcasts, pump release ``*4*4002#2*0#1##`` / ``*4*4002*2##``, and
    ``#20*0`` + ``20*0`` valve closure), Zones 1 and 2 are discovered cleanly without ghost
    entities and remain ``HVACAction.IDLE`` with ``actuators`` all ``"off"``.
    """
    trace = json.loads(MHS1_LIVE_TRACE_PATH.read_text(encoding="utf-8"))
    gw = _make_gateway(
        model=trace["gateway"]["model"],
        firmware=trace["gateway"]["firmware"],
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: gw.mac, "host": trace["gateway"]["host"], "port": 20000},
        unique_id=gw.mac,
    )
    entry.add_to_hass(hass)
    plat_dict: dict[str, dict[str, dict[str, str]]] = {p: {} for p in PLATFORMS}
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms=plat_dict,
        entities={p: {} for p in PLATFORMS},
    )

    added_climates: list[MyHOMEClimate] = []

    def _collect_climates(entities: list[MyHOMEClimate]) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"climate.zone_{ent._where}"
            ent.async_write_ha_state = MagicMock()
            ent.async_schedule_update_ha_state = MagicMock()
        added_climates.extend(entities)

    await climate_setup_entry(hass, entry, _collect_climates)  # type: ignore[arg-type]

    for frame in trace["frames"]:
        if frame["direction"] != "rx":
            continue
        parsed = OWNMessage.parse(frame["raw"])
        assert parsed is not None
        if isinstance(parsed, OWNHeatingEvent):
            async_dispatcher_send(hass, f"myhome_message_{MAC}", parsed)
            async_dispatcher_send(hass, f"myhome_event_{MAC}_4", parsed)
            for ent in added_climates:
                if ent.hvac_mode == HVACMode.HEAT:
                    assert ent.hvac_action in (None, HVACAction.IDLE)

    clim_by_where = {c._where: c for c in added_climates}
    assert set(clim_by_where) == {"1", "2"}

    z1 = clim_by_where["1"]
    assert z1.hvac_mode == HVACMode.HEAT
    assert z1.hvac_action == HVACAction.IDLE
    assert z1.current_temperature == pytest.approx(20.1)
    assert z1.target_temperature == pytest.approx(19.0)
    assert z1.current_humidity == 60
    assert z1.extra_state_attributes["actuators"] == {"3": "off"}

    z2 = clim_by_where["2"]
    assert z2.hvac_mode == HVACMode.HEAT
    assert z2.hvac_action == HVACAction.IDLE
    assert z2.current_temperature == pytest.approx(19.5)
    assert z2.target_temperature == pytest.approx(18.0)
    assert z2.current_humidity == 63
    assert z2.extra_state_attributes["actuators"] == {"1": "off", "3": "off"}


@pytest.mark.asyncio
async def test_running_fan_speed_clears_to_off_when_all_actuators_become_inactive(
    hass: HomeAssistant,
) -> None:
    """When a fan-coil zone had running_fan_speed in ('low','medium','high') and all actuators
    become inactive via non-fan-code frames (20*100, 20*0, 19*0*0, 19*100*100) or mode OFF,
    running_fan_speed transitions to 'off' instead of sticking on the previous speed.
    """
    gw = _make_gateway(model="MyHomeServer1", firmware="2.87.13")
    zone = _make_zone(hass, gw, "1", fan=True)
    zone._attr_hvac_mode = HVACMode.HEAT

    # 1. Speed 2 ('medium') -> 0-10 V 0% OFF (*#4*1#3*20*100##) clears running_fan_speed to 'off'
    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*7##"))
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["running_fan_speed"] == "medium"

    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*100##"))
    assert zone.hvac_action == HVACAction.IDLE
    assert zone.extra_state_attributes["running_fan_speed"] == "off"

    # 2. Multi-actuator zone: #1 valve ON + #3 fan coil 'low' -> #1 turns OFF while #3 stays ON
    #    keeps running_fan_speed == 'low'; then #3 turns OFF (*20*0) -> running_fan_speed == 'off'!
    zone.handle_event(OWNHeatingEvent("*#4*1#1*20*1##"))
    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*6##"))
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["running_fan_speed"] == "low"

    zone.handle_event(OWNHeatingEvent("*#4*1#1*20*0##"))
    assert zone.hvac_action == HVACAction.HEATING
    assert zone.extra_state_attributes["running_fan_speed"] == "low"

    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*0##"))
    assert zone.hvac_action == HVACAction.IDLE
    assert zone.extra_state_attributes["running_fan_speed"] == "off"

    # 3. Zone-wide Dimension 19 inactive frames (0*0 and 100*100) clear 'high' -> 'off'
    for inactive_dim19 in ("*#4*1*19*0*0##", "*#4*1*19*100*100##"):
        zone.handle_event(OWNHeatingEvent("*#4*1#3*20*8##"))
        assert zone.hvac_action == HVACAction.HEATING
        assert zone.extra_state_attributes["running_fan_speed"] == "high"

        zone.handle_event(OWNHeatingEvent(inactive_dim19))
        assert zone.hvac_action == HVACAction.IDLE
        assert zone.extra_state_attributes["running_fan_speed"] == "off"

    # 4. Switching zone OFF (*4*303*1## -> _mark_actuators_off) clears 'medium' -> 'off'
    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*7##"))
    assert zone.extra_state_attributes["running_fan_speed"] == "medium"

    zone.handle_event(OWNHeatingEvent("*4*303*1##"))
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.hvac_action == HVACAction.OFF
    assert zone.extra_state_attributes["actuators"] == {"1": "off", "3": "off"}
    assert zone.extra_state_attributes["running_fan_speed"] == "off"

    # 5. Hybrid zone (#1 floor valve ON + #3 Sabiana fan coil 'medium'):
    #    #3 stops via 20*100 while #1 stays ON -> hvac_action stays HEATING, running_fan_speed == 'off'!
    hybrid = _make_zone(hass, gw, "2", fan=True)
    hybrid._attr_hvac_mode = HVACMode.HEAT
    hybrid.handle_event(OWNHeatingEvent("*#4*2#1*20*1##"))
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*7##"))
    assert hybrid.hvac_action == HVACAction.HEATING
    assert hybrid.extra_state_attributes["running_fan_speed"] == "medium"
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*100##"))
    assert hybrid.hvac_action == HVACAction.HEATING
    assert hybrid.extra_state_attributes["actuators"] == {"1": "on", "3": "off"}
    assert hybrid.extra_state_attributes["running_fan_speed"] == "off"

    # 6. Auto speed (*20*9) or proportional modulation (*20*145) -> stops via *20*100 -> 'off'
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*9##"))
    assert "running_fan_speed" not in hybrid.extra_state_attributes
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*100##"))
    assert hybrid.extra_state_attributes["running_fan_speed"] == "off"
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*7##"))
    assert hybrid.extra_state_attributes["running_fan_speed"] == "medium"
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*145##"))
    assert "running_fan_speed" not in hybrid.extra_state_attributes
    hybrid.handle_event(OWNHeatingEvent("*#4*2#3*20*100##"))
    assert hybrid.extra_state_attributes["running_fan_speed"] == "off"

    # 7. Multi-fancoil zone (#2 'medium', #3 'low'): #3 turning OFF (*20*5) preserves #2's 'medium'
    multi_fan = _make_zone(hass, gw, "3", fan=True)
    multi_fan._attr_hvac_mode = HVACMode.COOL
    multi_fan.handle_event(OWNHeatingEvent("*#4*3#2*20*7##"))
    multi_fan.handle_event(OWNHeatingEvent("*#4*3#3*20*6##"))
    assert multi_fan.extra_state_attributes["running_fan_speed"] == "low"
    multi_fan.handle_event(OWNHeatingEvent("*#4*3#3*20*5##"))
    assert multi_fan.hvac_action == HVACAction.COOLING
    assert multi_fan.extra_state_attributes["running_fan_speed"] == "medium"
    multi_fan.handle_event(OWNHeatingEvent("*#4*3#2*20*100##"))
    assert multi_fan.hvac_action == HVACAction.IDLE
    assert multi_fan.extra_state_attributes["running_fan_speed"] == "off"

    # 8. Dimension 19 modulating (150*0) / active non-fan valve (100*1) / Dim 19 -> Dim 20*100 fallback
    dim19_zone = _make_zone(hass, gw, "4", fan=True)
    dim19_zone._attr_hvac_mode = HVACMode.HEAT
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4*19*0*7##"))
    assert dim19_zone.extra_state_attributes["running_fan_speed"] == "medium"
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4#3*20*100##"))
    assert dim19_zone.extra_state_attributes["running_fan_speed"] == "off"
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4#3*20*7##"))
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4*19*100*1##"))
    assert dim19_zone.hvac_action == HVACAction.HEATING
    assert dim19_zone.extra_state_attributes["running_fan_speed"] == "off"
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4#3*20*7##"))
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4*19*150*0##"))
    assert "running_fan_speed" not in dim19_zone.extra_state_attributes
    dim19_zone.handle_event(OWNHeatingEvent("*#4*4#3*20*100##"))
    assert dim19_zone.extra_state_attributes["running_fan_speed"] == "off"


def test_issue_908_golden_corpus_conformance_entries() -> None:
    """Verify Issue #908 live capture and Encyclopedia frames are registered in Golden Corpus CI."""
    corpus_by_id = {
        rec["id"]: rec
        for rec in json.loads(CORPUS_JSON_PATH.read_text(encoding="utf-8"))
    }
    live_raw_frames = {
        f["raw"]
        for f in json.loads(MHS1_LIVE_TRACE_PATH.read_text(encoding="utf-8"))["frames"]
    }

    expected_capture_ids = {
        "thermo.capture.dim20.write.fancoil_0pct.z1.act3": "*#4*1#3*#20*100##",
        "thermo.capture.dim20.status.fancoil_0pct.z1.act3": "*#4*1#3*20*100##",
        "thermo.capture.dim20.write.fancoil_0pct.z2.act3": "*#4*2#3*#20*100##",
        "thermo.capture.dim20.status.fancoil_0pct.z2.act3": "*#4*2#3*20*100##",
        "thermo.capture.dim20.write.valve_off.z2.act1": "*#4*2#1*#20*0##",
        "thermo.resp.actuator.status.off.zone2.act1": "*#4*2#1*20*0##",
        "thermo.capture.pump_release.zone2.pump1": "*4*4002#2*0#1##",
        "thermo.capture.pump_release.zone2": "*4*4002*2##",
        "thermo.capture.humidity.zone1.60": "*#4*1*60*60##",
    }
    for rec_id, raw_frame in expected_capture_ids.items():
        assert rec_id in corpus_by_id
        rec = corpus_by_id[rec_id]
        assert rec["frame"] == raw_frame
        assert rec["source"] == "community-plant-capture"
        assert raw_frame in live_raw_frames
        parsed = OWNMessage.parse(raw_frame)
        assert parsed is not None
        if rec["direction"] == "dimension_write":
            assert isinstance(parsed, OWNHeatingCommand)
        else:
            assert isinstance(parsed, OWNHeatingEvent)
        if "where_param" in rec:
            assert parsed._interface_and_where_params()[1] == rec["where_param"]
        if rec.get("dimension") == 20:
            assert thermo_dim20_status(parsed) == (False, None, None)

    act_off = OWNMessage.parse("*#4*2#1*20*0##")
    assert isinstance(act_off, OWNHeatingEvent) and act_off.where_param == ["1"]

    pump_sub = OWNMessage.parse("*4*4002#2*0#1##")
    assert isinstance(pump_sub, OWNHeatingEvent)
    assert isinstance(pump_sub.what, int) and pump_sub.what == 4002
    assert pump_sub.what_param == ["2"]
    assert pump_sub.where == "0" and pump_sub.where_param == ["1"]

    pump_zone = OWNMessage.parse("*4*4002*2##")
    assert isinstance(pump_zone, OWNHeatingEvent)
    assert isinstance(pump_zone.what, int) and pump_zone.what == 4002
    assert pump_zone.what_param == [] and pump_zone.where == "2"

    hum_z1 = OWNMessage.parse("*#4*1*60*60##")
    assert isinstance(hum_z1, OWNHeatingEvent) and hum_z1.main_humidity == 60.0

    ext_rec = corpus_by_id["thermo.resp.valves.extended_fancoil_off_speed1.zone1"]
    assert ext_rec["frame"] == "*#4*1*19*0*14##"
    assert ext_rec["source"] == "encyclopedia"
    assert thermo_dim19_status(OWNMessage.parse(ext_rec["frame"])) == (
        False,
        False,
        False,
        None,
        False,
        None,
    )


@pytest.mark.asyncio
async def test_climate_and_thermo_switch_extended_bitmask_codes_10_to_13_and_17_to_22(
    hass: HomeAssistant,
) -> None:
    """Codes 10..13 and 17..22 drive Climate/ThermoSwitch without enabling fan mode or ghost entities."""
    gw = _make_gateway(model="MyHomeServer1", firmware="2.82.6")

    # 1. MyHOMEClimate: active codes (10..12, 18..22) set HEATING/COOLING; inactive (13, 17) set IDLE;
    #    none of them enable _fan or leak a fan speed attribute.
    heat_zone = _make_zone(hass, gw, "1")
    heat_zone._attr_hvac_mode = HVACMode.HEAT
    cool_zone = _make_zone(hass, gw, "2")
    cool_zone._attr_hvac_mode = HVACMode.COOL
    auto_zone = _make_zone(hass, gw, "3")
    auto_zone._attr_hvac_mode = HVACMode.AUTO

    for active_code, inactive_code in (
        (10, 13),
        (11, 17),
        (12, 13),
        (18, 17),
        (19, 13),
        (20, 17),
        (21, 13),
        (22, 17),
    ):
        heat_zone.handle_event(OWNHeatingEvent(f"*#4*1#1*20*{active_code}##"))
        assert heat_zone.hvac_action == HVACAction.HEATING
        assert heat_zone._fan is False
        assert "running_fan_speed" not in heat_zone.extra_state_attributes

        heat_zone.handle_event(OWNHeatingEvent(f"*#4*1#1*20*{inactive_code}##"))
        assert heat_zone.hvac_action == HVACAction.IDLE
        assert heat_zone._fan is False

        cool_zone.handle_event(OWNHeatingEvent(f"*#4*2#1*20*{active_code}##"))
        assert cool_zone.hvac_action == HVACAction.COOLING
        assert cool_zone._fan is False
        cool_zone.handle_event(OWNHeatingEvent(f"*#4*2#1*20*{inactive_code}##"))
        assert cool_zone.hvac_action == HVACAction.IDLE
        assert cool_zone._fan is False

        auto_zone.handle_event(OWNHeatingEvent(f"*#4*3*19*0*{active_code}##"))
        assert auto_zone.hvac_action == HVACAction.HEATING
        assert auto_zone._fan is False
        auto_zone.handle_event(OWNHeatingEvent(f"*#4*3*19*{active_code}*0##"))
        assert auto_zone.hvac_action == HVACAction.COOLING
        assert auto_zone._fan is False
        auto_zone.handle_event(OWNHeatingEvent(f"*#4*3*19*{inactive_code}*13##"))
        assert auto_zone.hvac_action == HVACAction.IDLE
        assert auto_zone._fan is False

    # 2. Probe-less discovery deferral + MyHOMEThermoSwitch tracking for 10..13 and 17..22
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_MAC: gw.mac, "host": "192.0.2.99", "port": 20000},
        unique_id=gw.mac,
        subentries_data=[
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "7#1", "name": "Aux Actuator"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Aux Actuator",
                "unique_id": f"{gw.mac}-thermo-switch-7#1",
            },
            {
                "data": {CONF_WHO: "4", CONF_WHERE: "8", "name": "Aux Zone"},
                "subentry_type": SUBENTRY_TYPE_THERMO_SWITCH,
                "title": "Aux Zone",
                "unique_id": f"{gw.mac}-thermo-switch-8",
            },
        ],
    )
    entry.add_to_hass(hass)
    plat_dict: dict[str, dict[str, dict[str, str]]] = {p: {} for p in PLATFORMS}
    entry.runtime_data = MyHOMERuntimeData(
        gateway=gw,
        platforms=plat_dict,
        entities={p: {} for p in PLATFORMS},
    )

    added_climates: list[MyHOMEClimate] = []
    added_switches: list[MyHOMEThermoSwitch] = []

    def _collect_switches(entities: list[MyHOMEThermoSwitch], **_: object) -> None:
        for ent in entities:
            ent.hass = hass
            ent.entity_id = f"switch.thermo_{ent._where.replace('#', '_')}"
            ent.async_write_ha_state = MagicMock()
            ent.async_schedule_update_ha_state = MagicMock()
        added_switches.extend(entities)

    await climate_setup_entry(hass, entry, added_climates.extend)  # type: ignore[arg-type]
    await switch_setup_entry(hass, entry, _collect_switches)  # type: ignore[arg-type]
    sw_by_where = {s._full_where: s for s in added_switches}

    for code, expected_on in (
        (10, True),
        (13, False),
        (11, True),
        (17, False),
        (12, True),
        (13, False),
        (18, True),
        (17, False),
        (19, True),
        (13, False),
        (20, True),
        (17, False),
        (21, True),
        (13, False),
        (22, True),
        (17, False),
    ):
        evt20 = OWNHeatingEvent(f"*#4*7#1*20*{code}##")
        cmd20 = OWNHeatingCommand(f"*#4*7#1*#20*{code}##")
        async_dispatcher_send(hass, f"myhome_message_{MAC}", evt20)
        sw_by_where["7#1"].handle_event(evt20)
        assert sw_by_where["7#1"].is_on is expected_on
        sw_by_where["7#1"].handle_event(cmd20)
        assert sw_by_where["7#1"].is_on is expected_on

        evt19_h = OWNHeatingEvent(f"*#4*8*19*0*{code}##")
        evt19_c = OWNHeatingEvent(f"*#4*8*19*{code}*0##")
        async_dispatcher_send(hass, f"myhome_message_{MAC}", evt19_h)
        sw_by_where["8"].handle_event(evt19_h)
        assert sw_by_where["8"].is_on is expected_on
        async_dispatcher_send(hass, f"myhome_message_{MAC}", evt19_c)
        sw_by_where["8"].handle_event(evt19_c)
        assert sw_by_where["8"].is_on is expected_on

    # None of the probe-less zones spawned a climate entity across 10..13 and 17..22!
    assert added_climates == []
