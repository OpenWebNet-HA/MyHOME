"""Regression and multi-gateway trace tests for Issue #908 (WHO=4 Dimension 19/20 value 100 & 14..16).

Verifies that:
1. Proportional 0-10 V 0% output (``*#4*Z#N*20*100##``, ``*#4*Z#N*#20*100##``, and
   Dimension 19 ``100``) is decoded as **inactive** (``is_active = False, fan_on = None``),
   preventing fan-coil and modulating-valve zones from sticking on ``hvac_action = "heating"``
   or flipping between ``heating`` and ``idle`` every ~8 minutes (#908).
2. Proportional 0-10 V 1%..100% output (``101..200``) is decoded as **active** without
   falsely enabling discrete fan-coil mode on pure 0-10 V mixing valves or spawning
   ghost ``climate`` entities on probe-less ``MyHOMEThermoSwitch`` zones (#785).
3. ``MyHomeServer1`` extended fan-coil OFF-speed states ``14, 15, 16`` (SCS ``'24'..'26'``)
   decode as ``is_active = False, fan_on = False`` (``running_fan_speed = "off"``).
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
    thermo_dim19_status,
    thermo_dim20_is_active,
    thermo_dim20_status,
)

TRACES_DIR = Path(__file__).resolve().parent / "fixtures" / "traces" / "issue_908"
F459_TRACE_PATH = TRACES_DIR / "myhome_trace_oracle_f459_sabiana_fancoil_dim20_100.json"
MH200N_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_oracle_mh200n_dim20_poll_dropped_periodic_100.json"
)
MHS1_TRACE_PATH = (
    TRACES_DIR / "myhome_trace_oracle_myhomeserver1_extended_fancoil_off_14_16.json"
)
ORACLE_JSON_PATH = Path(__file__).resolve().parent / "golden" / "firmware_oracle.json"
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
    """Verify the firmware oracle contract backing the three Issue #908 gateway traces."""
    assert F459_TRACE_PATH.is_file()
    assert MH200N_TRACE_PATH.is_file()
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
    """Verify protocol-exact decoding of all Dimension 20 and Dimension 19 codes and mocks."""
    # Inactive non-fan codes: 0 (OFF), 3 (Closed), 4 (Stop), 100 (0% 0-10 V OFF)
    for code in (0, 3, 4, 100):
        evt = OWNHeatingEvent(f"*#4*1#3*20*{code}##")
        cmd = OWNHeatingCommand(f"*#4*1#3*#20*{code}##")
        assert thermo_dim20_status(evt) == (False, None, None)
        assert thermo_dim20_status(cmd) == (False, None, None)
        assert thermo_dim20_is_active(evt) is False
        assert thermo_dim20_is_active(cmd) is False

    # Inactive fan-coil codes: 5 (Fan OFF), 14..16 (MyHomeServer1 OFF speeds 1..3)
    for code in (5, 14, 15, 16):
        evt = OWNHeatingEvent(f"*#4*1#2*20*{code}##")
        cmd = OWNHeatingCommand(f"*#4*1#2*#20*{code}##")
        assert thermo_dim20_status(evt) == (False, False, None)
        assert thermo_dim20_status(cmd) == (False, False, None)
        assert thermo_dim20_is_active(evt) is False
        assert thermo_dim20_is_active(cmd) is False

    # Active discrete valve codes: 1 (ON), 2 (Opened)
    for code in (1, 2):
        assert thermo_dim20_status(OWNHeatingEvent(f"*#4*1#1*20*{code}##")) == (
            True,
            None,
            None,
        )

    # Active fan-coil codes: 6..8 (speeds 1..3), 9 (Auto speed)
    assert thermo_dim20_status(OWNHeatingEvent("*#4*1#2*20*6##")) == (True, True, 1)
    assert thermo_dim20_status(OWNHeatingEvent("*#4*1#2*20*7##")) == (True, True, 2)
    assert thermo_dim20_status(OWNHeatingEvent("*#4*1#2*20*8##")) == (True, True, 3)
    assert thermo_dim20_status(OWNHeatingEvent("*#4*1#2*20*9##")) == (True, True, None)

    # Active proportional 0-10 V codes: 101 (1%) .. 200 (100%)
    for code in (101, 155, 200):
        evt = OWNHeatingEvent(f"*#4*1#3*20*{code}##")
        cmd = OWNHeatingCommand(f"*#4*1#3*#20*{code}##")
        assert thermo_dim20_status(evt) == (True, None, None)
        assert thermo_dim20_status(cmd) == (True, None, None)
        assert thermo_dim20_is_active(evt) is True
        assert thermo_dim20_is_active(cmd) is True

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
async def test_mh200n_dropped_poll_trace_periodic_100_keeps_idle(
    hass: HomeAssistant,
) -> None:
    """On MH200N (*#4*1*20## dropped), unsolicited 8-min *#4*1#3*20*100## keeps hvac_action IDLE."""
    trace = json.loads(MH200N_TRACE_PATH.read_text(encoding="utf-8"))
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

    # Unlisted Dim 20 (250) and Dim 19 (999) codes on probe-less zones ALSO defer (#785)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*7#1*20*250##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNHeatingEvent("*#4*8*19*0*999##"))
    assert {c._where for c in added_climates} == {"1"}


@pytest.mark.asyncio
async def test_climate_dim19_and_dim20_unrecognized_and_modulating_cooling(
    hass: HomeAssistant,
) -> None:
    """Unrecognized Dim 19/20 codes are ignored cleanly; Dim 19 modulating & cooling fan speed work."""
    gw = _make_gateway()
    zone = _make_zone(hass, gw, "1")
    zone._attr_hvac_mode = HVACMode.AUTO

    # Unrecognized Dim 20 (999) and Dim 19 (999) codes return early without mutating _actuator_states
    zone.handle_event(OWNHeatingEvent("*#4*1#3*20*999##"))
    assert zone._actuator_states == {}
    zone.handle_event(OWNHeatingEvent("*#4*1*19*0*999##"))
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
