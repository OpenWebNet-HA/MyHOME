"""A plant with a 4-zone central unit (issue 994): found on the bus, its zones written directly.

Fixture: ``tests/fixtures/traces/issue_994`` (an F454 2.0.51; the central unit reports as
``#0#1``, zones 1, 3 and 4). What the plant answered, by frame number of the second file:

- ``*#4*#0#1*#14*0175*1##`` (19): the central unit confirms with ``*4*110#0175*#0#1##`` (20);
- ``*4*303*#0#1##`` (38): it confirms with ``*4*103*#0#1##`` (39) and every zone goes off;
- ``*#4*3*#14*0170*3##`` (33): zone 3 confirms with ``*#4*3*12*0170*3##`` (35);
- ``*#4*#3#1*#14*0175*3##`` (37): nothing in the 47.9 s until the next send.

The form a 99-zone central unit's zones require (``*#4*#Z*#14*TTTT*3##``,
``tests/fixtures/traces/issue_697``) is what version 2.0.0 wrote on this plant. The first file
holds one such write, ``*#4*#1*#14*0150*3##`` (13), and ends 0.08 s after it; that the plant
does not act on it is the reporter's account in the issue ("the command is ignored").

A zone's own off (``*4*303*Z##``) and a direct write to zone 1 are in neither capture: the
first is the frame version 0.9.3 sent for a zone marked ``standalone``, as the reporter's file
did, the second follows the rule of zones 3 and 4.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent, OWNHeatingEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.climate_discovery import ZoneDiscovery, _zone_config
from custom_components.myhome.const import CONF_STANDALONE_ZONES, DOMAIN
from custom_components.myhome.discovery import Address
from custom_components.myhome.legacy_yaml import _apply_yaml_platforms
from custom_components.myhome.sensor import MyHOMEActuatorOutputSensor
from custom_components.myhome.validate import climate_schema
from custom_components.myhome.where_grammar import (
    four_zone_central_where,
    four_zone_central_zone,
    is_actuator_where,
    is_four_zone_central_where,
)
from tests.conftest import attach_runtime
from tests.test_climate_mode_state_2026_11_08 import _dispatcher, _gateway

MAC = "00:03:50:44:55:66"
TRACES = Path(__file__).parent / "fixtures" / "traces" / "issue_994"
FIRST = TRACES / "myhome_trace_F454_all_2026-11-22T18-38-46.json"
SECOND = TRACES / "myhome_trace_F454_all_2026-11-22T21-40-54.json"

# The reporter's ``myhome.yaml`` of version 0.9.3 (issue 994), with the names replaced.
REPORTERS_FILE = {
    "centrale": {"zone": "1", "name": "Centrale", "heat": True, "cool": False, "central": True, "standalone": False},
    "zona3": {"zone": "3", "name": "Zona 3", "heat": True, "cool": False, "standalone": True},
    "zona4": {"zone": "4", "name": "Zona 4", "heat": True, "cool": False, "standalone": True},
}


def _received(*files: Path) -> list[str]:
    return [frame["raw"] for file in files for frame in json.loads(file.read_text(encoding="utf-8"))["frames"] if frame["direction"] == "rx"]


async def _plant(
    hass: HomeAssistant,
    yaml_cfg: dict | None,
    *files: Path,
    options: dict | None = None,
) -> tuple[MagicMock, dict[str, MyHOMEClimate]]:
    """The climate platform of a gateway that received the frames of ``files``, in order."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, options=options or {}, unique_id=MAC)
    entry.add_to_hass(hass)
    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[issue 994]"
    gateway.has_central_unit = False
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, MAC, gateway)
    configured: dict[str, dict] = {"climate": {}}
    if yaml_cfg is not None:
        _apply_yaml_platforms({"climate": climate_schema(yaml_cfg)}, configured)
    runtime.platforms["climate"] = configured["climate"]
    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)

    def _attach() -> None:
        for entity in entities:
            if entity.entity_id is None:
                entity.hass = hass
                entity.entity_id = f"climate.{entity._where.replace('#', 'c')}"
                entity.async_write_ha_state = MagicMock()
                entity.async_schedule_update_ha_state = MagicMock()

    _attach()
    for raw in _received(*files):
        async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse(raw))
        await hass.async_block_till_done()
        _attach()
    return gateway, {entity._where: entity for entity in entities}


def _sent(gateway: MagicMock) -> list[str]:
    return [str(call.args[0]) for call in gateway.send.await_args_list]


# ── found on the bus ──


async def test_the_central_unit_and_its_zones_are_found_on_the_bus(hass: HomeAssistant) -> None:
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    # Option A: #0#1 unifies built-in master zone 1 (no separate dead Climate Zone 1).
    assert set(found) == {"#0#1", "3", "4"}
    central = found["#0#1"]
    assert central._central is True
    assert central.current_temperature == 19.4  # *#4*1*0*0194## routed onto Central Unit 01
    assert central.extra_state_attributes["knob_pos"] == "0"  # *#4*1*13*00##
    assert central.target_temperature == 14.0  # *#4*1*12*0140*3## active schedule setpoint of program 3
    assert central.extra_state_attributes["program"] == 3
    assert [found[zone]._central for zone in ("3", "4")] == [False, False]
    assert gateway.has_central_unit is False  # no 99-zone central unit on this plant


@pytest.mark.parametrize("zone", ["3", "4"])
async def test_a_zone_found_on_the_bus_is_written_directly(hass: HomeAssistant, zone: str) -> None:
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    await found[zone].async_set_temperature(temperature=18.0)
    assert _sent(gateway) == [f"*#4*{zone}*#14*0180*3##"]  # the form of frame 33, not `*#4*#Z*#14*...`
    gateway.send.reset_mock()
    await found[zone].async_set_hvac_mode(HVACMode.OFF)
    assert _sent(gateway) == [f"*4*303*{zone}##"]


async def test_the_central_unit_found_on_the_bus_is_written_at_its_own_address(hass: HomeAssistant) -> None:
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    central = found["#0#1"]
    assert central.hvac_mode == HVACMode.HEAT  # `*4*1103*#0#1##`, the last report of the second file
    await central.async_set_temperature(temperature=18.0)
    assert _sent(gateway)[0] == "*#4*#0#1*#14*0180*3##"  # generic manual M=3 (frame 5; works in heat & cool)
    gateway.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert _sent(gateway)[0] == "*4*303*#0#1##"  # frame 38


async def test_heat_and_cool_on_the_four_zone_central_unit_same_and_cross_season(hass: HomeAssistant) -> None:
    """Same-season writes generic manual M=3 (and clears HVACAction.OFF); cross-season sends season + 3100."""
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    central = found["#0#1"]
    assert central.target_temperature == 14.0  # `*#4*1*12*0140*3##` during program 3
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert central.hvac_action == HVACAction.OFF
    gateway.send.reset_mock()

    # Same-season wake (reported heating): writes M=3 setpoint, no season frame, resets action OFF -> IDLE.
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*#4*#0#1*#14*0140*3##"]
    assert central.hvac_mode == HVACMode.HEAT
    assert central.hvac_action == HVACAction.IDLE
    assert central.target_temperature == 14.0

    # Cross-season switch (heating -> cool): sends season frame + 3100 restore, waits for bus report.
    gateway.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gateway) == ["*4*0*#0#1##", "*4*3100*#0#1##"]
    central.handle_event(OWNHeatingEvent("*4*2101*#0#1##"))
    assert central.hvac_mode == HVACMode.COOL

    # Cross-season switch back (cooling -> heat): sends *4*1*#0#1## + *4*3100*#0#1##.
    gateway.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*4*1*#0#1##", "*4*3100*#0#1##"]


async def test_heat_on_a_four_zone_central_unit_without_setpoint_wakes_with_3100(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """OFF/protection/program with no reported setpoint wakes via ``*4*3100*#0#1##`` instead of failing."""
    only_off = tmp_path / "off.json"
    only_off.write_text(json.dumps({"frames": [{"direction": "rx", "raw": "*4*103*#0#1##"}]}), encoding="utf-8")
    gateway, found = await _plant(hass, None, only_off)
    central = found["#0#1"]
    assert central.target_temperature is None
    assert central.hvac_action == HVACAction.OFF
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*4*3100*#0#1##"]
    assert central.hvac_mode == HVACMode.HEAT
    assert central.hvac_action == HVACAction.IDLE


async def test_off_and_the_program_of_the_central_unit_are_written_as_before(hass: HomeAssistant) -> None:
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    central = found["#0#1"]
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert central.hvac_action == HVACAction.OFF
    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert _sent(gateway) == ["*4*303*#0#1##", "*4*3100*#0#1##"]
    assert central.hvac_action == HVACAction.IDLE  # P2-3: OFF -> AUTO resets action to IDLE


# ── not a 99-zone central unit ──


@pytest.mark.parametrize("raw", ["*4*110#0160*#0#1##", "*4*21*#0#1##", "*4*31*#0#1##", "*4*103*#0#1##", "*4*1103*#0#1##"])
async def test_a_report_of_a_four_zone_central_unit_does_not_put_the_zones_under_a_central_unit(raw: str) -> None:
    gateway = _gateway(has_central_unit=False)
    await _dispatcher(gateway).process_message(OWNEvent.parse(raw))
    assert gateway.has_central_unit is False


async def test_a_report_of_a_99_zone_central_unit_still_does() -> None:
    gateway = _gateway(has_central_unit=False)
    await _dispatcher(gateway).process_message(OWNEvent.parse("*4*102*#0##"))  # tests/fixtures/traces/issue_697
    assert gateway.has_central_unit is True


def test_the_output_sensor_does_not_take_a_four_zone_central_unit_for_a_99_zone_one(hass: HomeAssistant) -> None:
    gateway = _gateway(has_central_unit=False)
    sensor = MyHOMEActuatorOutputSensor(
        hass=hass, name="Climate Zone 3", device_id="3", where="3#1", zone="3", actuator="1", interface=None,
        standalone=None, manufacturer="BTicino", model="Heating Zone", gateway=gateway,
    )
    sensor.handle_event(OWNHeatingEvent("*4*110#0160*#0#1##"))
    assert gateway.has_central_unit is False
    sensor.handle_event(OWNHeatingEvent("*4*102*#0##"))
    assert gateway.has_central_unit is True


def test_the_entity_of_a_four_zone_central_unit_does_not_set_the_flag_either(hass: HomeAssistant) -> None:
    from tests.test_climate_mode_state_2026_11_08 import _climate

    gateway = _gateway(has_central_unit=False)
    _climate(gateway, "#0#1", standalone=None, central=True)
    assert gateway.has_central_unit is False
    _climate(gateway, "#0", standalone=None, central=True)
    assert gateway.has_central_unit is True


# ── the reporter's file ──


async def test_the_file_that_worked_on_0_9_3_writes_the_same_addresses(hass: HomeAssistant) -> None:
    gateway, found = await _plant(hass, REPORTERS_FILE, FIRST, SECOND)
    # Master zone 1 is unified into Centrale (#0#1) unless explicitly configured standalone: true.
    assert set(found) == {"#0#1", "3", "4"}
    assert (found["#0#1"]._device_name, found["3"]._device_name, found["4"]._device_name) == ("Centrale", "Zona 3", "Zona 4")
    await found["#0#1"].async_set_temperature(temperature=18.0)
    await found["3"].async_set_temperature(temperature=18.0)
    await found["4"].async_set_temperature(temperature=18.0)
    assert _sent(gateway) == ["*#4*#0#1*#14*0180*3##", "*#4*3*#14*0180*3##", "*#4*4*#14*0180*3##"]


async def test_zone_1_marked_standalone_in_options_remains_separate_entity(hass: HomeAssistant) -> None:
    """Listing zone 1 in gateway ``standalone_zones`` opts out of Option A unification."""
    gateway, found = await _plant(hass, REPORTERS_FILE, FIRST, SECOND, options={CONF_STANDALONE_ZONES: ["1"]})
    assert set(found) == {"#0#1", "1", "3", "4"}
    zone = found["1"]
    assert zone._central is False
    assert zone._device_name != "Centrale"
    assert zone._standalone is True  # not the central unit's `standalone: false`
    await zone.async_set_temperature(temperature=18.0)
    assert _sent(gateway) == ["*#4*1*#14*0180*3##"]


@pytest.mark.parametrize(
    ("zone", "extra", "address"),
    [
        ("1", {}, Address("#0#1")),
        ("4", {"interface": "01"}, Address("#0#4", "01")),  # a 4-zone central unit behind an interface
        ("4#4#01", {}, Address("#0#4", "01")),  # the same, the interface written in the zone
        ("0", {"interface": "01"}, Address("#0", "01")),  # a 99-zone central unit behind an interface
        ("0#4#01", {}, Address("#0", "01")),  # the same, written in the zone: stored as `#0#4#01`
    ],
)
def test_a_central_unit_keeps_its_own_entry(zone: str, extra: dict, address: Address) -> None:
    """Only a plain zone loses the entry of a 4-zone central unit; `#0#4#01` is no 4-zone central unit."""
    validated = climate_schema({"plant": {"zone": zone, "central": True, "name": "Plant", "cool": False, **extra}})
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, configured)
    assert _zone_config(configured["climate"], address, address.key).get("name") == "Plant"


# ── the master panel is unified with zone 1 ──


async def test_unified_four_zone_central_ingests_master_zone_telemetry_and_actuators(hass: HomeAssistant) -> None:
    """Option A: ``Central Unit 01`` owns master zone 1's probe, knob, actuators and setpoint."""
    gateway, found = await _plant(hass, None, FIRST)
    assert set(found) == {"#0#1", "3", "4"}
    central = found["#0#1"]
    assert central.current_temperature == 20.1
    assert central.target_temperature == 16.0
    assert central.hvac_mode == HVACMode.HEAT
    assert found["3"].target_temperature == 16.0

    # Master zone 1 actuator reports update hvac_action without polling *#4*#0#1*20##.
    gateway.request_zone_dimension = MagicMock()
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*1#1*20*1##"))
    await hass.async_block_till_done()
    assert central.hvac_action == HVACAction.HEATING
    assert central.extra_state_attributes["actuators"] == {"1": "on"}

    # Going OFF and waking back up must never queue a zone dimension query for #0#1.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*103*#0#1##"))
    await hass.async_block_till_done()
    assert central.hvac_action == HVACAction.OFF
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*110#0170*#0#1##"))
    await hass.async_block_till_done()
    assert central.hvac_mode == HVACMode.HEAT
    assert central.target_temperature == 17.0
    gateway.request_zone_dimension.assert_not_called()

    # Plain master-zone season frame does not clobber AUTO mode on #0#1, and plain zone mode is ignored.
    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert central.hvac_mode == HVACMode.AUTO
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*1*1##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*303*1##"))
    await hass.async_block_till_done()
    assert central.hvac_mode == HVACMode.AUTO


async def test_unconfigured_master_zone_updates_own_off_and_season_on_four_zone_central_report() -> None:
    """P2-4: if an unconfigured non-central master zone sees ``#0#1`` reports, ``_own_off`` & ``_own_season`` track them."""
    from tests.test_climate_mode_state_2026_11_08 import _climate

    gw = _gateway(has_central_unit=False)
    zone = _climate(gw, "1", standalone=None, central=False)
    zone.handle_event(OWNHeatingEvent("*4*303*1##"))
    assert zone._own_off is True
    zone.handle_event(OWNHeatingEvent("*4*110#0170*#0#1##"))
    assert zone._own_off is False
    assert zone._own_season == "heating"
    assert (zone.hvac_mode, zone.target_temperature) == (HVACMode.HEAT, 17.0)


async def test_a_zone_configured_standalone_keeps_ignoring_reports_through_a_central_unit(hass: HomeAssistant) -> None:
    """`standalone: true` in `myhome.yaml` is the owner's word: nothing addressed `#0#N` or `#Z` is its state."""
    file = {**REPORTERS_FILE, "zona1": {"zone": "1", "name": "Zona 1", "heat": True, "cool": False, "standalone": True}}
    _gateway_, found = await _plant(hass, file, FIRST)
    zone = found["1"]
    assert zone._device_name == "Zona 1"
    before = (zone.hvac_mode, zone.target_temperature)
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*110#0170*#0#1##"))
    await hass.async_block_till_done()
    assert (zone.hvac_mode, zone.target_temperature) == before


# ── audit & UI fixes ──


async def test_central_unit_set_temperature_waits_for_bus_report(hass: HomeAssistant) -> None:
    """Setting temperature on `#0#1` or `#0` must not write state optimistically; it follows the bus report."""
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    central4 = found["#0#1"]
    assert central4.target_temperature == 14.0
    await central4.async_set_temperature(temperature=19.0)
    assert central4.target_temperature == 14.0
    central4.handle_event(OWNHeatingEvent("*4*110#0190*#0#1##"))
    assert central4.target_temperature == 19.0

    from tests.test_climate_mode_state_2026_11_08 import _climate

    gw99 = _gateway(has_central_unit=False)
    central99 = _climate(gw99, "#0", standalone=None, central=True)
    central99.handle_event(OWNHeatingEvent("*4*110#0240*#0##"))
    assert central99.target_temperature == 24.0
    await central99.async_set_temperature(temperature=24.5)
    assert central99.target_temperature == 24.0
    central99.handle_event(OWNHeatingEvent("*4*110#0245*#0##"))
    assert central99.target_temperature == 24.5

    # P2-3: #0 OFF -> AUTO resets hvac_action from OFF to IDLE.
    await central99.async_set_hvac_mode(HVACMode.OFF)
    assert central99.hvac_action == HVACAction.OFF
    await central99.async_set_hvac_mode(HVACMode.AUTO)
    assert central99.hvac_action == HVACAction.IDLE


async def test_zero_padded_and_hashed_central_zone_in_yaml_normalize_to_four_zone_central(
    hass: HomeAssistant,
) -> None:
    from custom_components.myhome.where_grammar import canonical_central_zone

    assert canonical_central_zone("01") == "#0#1"
    assert canonical_central_zone("#0#01") == "#0#1"
    assert canonical_central_zone("#1") == "#0#1"
    assert canonical_central_zone("#0##1") == "#0#1"
    assert canonical_central_zone("00") == "#0"
    assert canonical_central_zone("#0#00") == "#0"
    assert canonical_central_zone("01#4#01") == "#0#1#4#01"
    assert canonical_central_zone("foo") == "#0#foo"

    padded = {**REPORTERS_FILE, "centrale": {**REPORTERS_FILE["centrale"], "zone": "01"}}
    gateway, found = await _plant(hass, padded, FIRST, SECOND)
    assert set(found) == {"#0#1", "3", "4"}
    assert gateway.has_central_unit is False


def test_routed_central_unit_and_output_sensor_do_not_set_local_has_central_unit(hass: HomeAssistant) -> None:
    gateway = _gateway(has_central_unit=False)
    MyHOMEClimate(
        hass=hass, device_id="#0#4#01", who="4", where="#0", interface="01", name="Remote CU",
        heating=True, cooling=True, fan=False, humidity=False, standalone=False, central=True,
        manufacturer="BTicino", model="Central Unit (3550)", gateway=gateway,
    )
    assert gateway.has_central_unit is False

    sensor = MyHOMEActuatorOutputSensor(
        hass=hass, name="Climate Zone 3", device_id="3#4#01", where="3#1", zone="3", actuator="1",
        interface="01", standalone=None, manufacturer="BTicino", model="Heating Zone", gateway=gateway,
    )
    sensor.handle_event(OWNHeatingEvent("*4*110*#3#4#01##"))
    assert gateway.has_central_unit is False


async def test_actuator_status_with_four_zone_central_where_discovers_and_mutates_nothing(
    hass: HomeAssistant,
) -> None:
    from custom_components.myhome.climate_discovery import _zone_address, _zone_route_keys
    from custom_components.myhome.where_grammar import is_central_unit_actuator_status

    for raw in ("*#4*#0#1*20*5##", "*#4*#0#1*#20*1##", "*#4*#0#1#4#01*20*1##"):
        msg = OWNEvent.parse(raw)
        assert is_central_unit_actuator_status(msg) is True
        assert _zone_address(msg) is None
        assert _zone_route_keys(msg, None) == []

    _gateway_, found = await _plant(hass, None, FIRST, SECOND)
    central = found["#0#1"]
    before_action = central.hvac_action
    before_attrs = dict(central.extra_state_attributes)
    central.handle_event(OWNHeatingEvent("*#4*#0#1*20*5##"))
    assert central.hvac_action == before_action
    assert dict(central.extra_state_attributes) == before_attrs


async def test_standalone_zone_ignores_four_zone_central_dim14_writes(hass: HomeAssistant) -> None:
    file = {**REPORTERS_FILE, "zona1": {"zone": "1", "name": "Zona 1", "heat": True, "cool": False, "standalone": True}}
    _gateway_, found = await _plant(hass, file, FIRST)
    zone = found["1"]
    before = zone.target_temperature
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*#0#1*#14*0195*1##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*#1*14*0195*1##"))
    await hass.async_block_till_done()
    assert zone.target_temperature == before


def test_inline_interface_central_unit_does_not_alias_local_zone_number() -> None:
    validated = climate_schema(
        {
            "remote_cu": {"zone": "0#4#01", "central": True, "name": "Remote CU"},
            "remote_zone": {"zone": "3#4#01", "name": "Remote Zone 3"},
        }
    )
    assert "01" not in validated and "zone_01" not in validated
    assert "0#4#01" in validated and "3#4#01" in validated
    configured: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms({"climate": validated}, configured)
    assert "01" not in configured["climate"] and "zone_01" not in configured["climate"]
    assert _zone_config(configured["climate"], Address("#0", "01"), "#0#4#01").get("name") == "Remote CU"
    assert _zone_config(configured["climate"], Address("3", "01"), "3#4#01").get("name") == "Remote Zone 3"


async def test_four_zone_central_supports_base_zones_beyond_four_up_to_99(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    """P1-4: 4695 base zone N can be any zone 1..96 (gateway grammar 1..99), e.g. #0#5, #0#21, #0#96."""
    assert four_zone_central_zone("#0#5") == "5"
    assert four_zone_central_zone("#0#21") == "21"
    assert four_zone_central_zone("#0#96#4#01") == "96"
    assert four_zone_central_zone("#0") is None
    assert four_zone_central_zone("#0#0") is None
    assert four_zone_central_zone("#0#4#01") is None
    assert four_zone_central_zone("#0#100") is None
    assert is_four_zone_central_where("#0#21") is True
    assert is_actuator_where("#0#5") is False
    assert is_actuator_where("#0#21") is False
    assert four_zone_central_where(OWNEvent.parse("*4*110#0200*#0#21##")) == "#0#21"
    assert four_zone_central_where(OWNEvent.parse("*4*110#0200*#0#96##")) == "#0#96"
    assert four_zone_central_where(OWNEvent.parse("*4*110#0200*#0#4#01##")) is None

    high_base = tmp_path / "base21.json"
    high_base.write_text(
        json.dumps(
            {
                "frames": [
                    {"direction": "rx", "raw": "*#4*21*0*0213##"},  # held master zone 21 frame before #0#21
                    {"direction": "rx", "raw": "*4*110#0200*#0#21##"},
                    {"direction": "rx", "raw": "*#4*22*0*0198##"},
                    {"direction": "rx", "raw": "*#4*22*12*0200*3##"},
                ]
            }
        ),
        encoding="utf-8",
    )
    gateway, found = await _plant(hass, None, high_base)
    assert set(found) == {"#0#21", "22"}
    central = found["#0#21"]
    assert central._central is True
    assert central.current_temperature == 21.3  # held master-zone frame replayed onto #0#21
    assert central.target_temperature == 20.0
    assert gateway.has_central_unit is False
    await central.async_set_temperature(temperature=21.5)
    assert _sent(gateway) == ["*#4*#0#21*#14*0215*3##"]


async def test_stale_master_zone_registry_entry_is_pruned_when_four_zone_central_present(
    hass: HomeAssistant,
) -> None:
    """Option A upgrade: an unconfigured registry entry for zone 1 is pruned beside ``#0#1``."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    zone1_reg = registry.async_get_or_create("climate", DOMAIN, f"{MAC}-4-1", config_entry=entry)
    cu_reg = registry.async_get_or_create("climate", DOMAIN, f"{MAC}-4-#0#1", config_entry=entry)
    registry.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-1-temperature", config_entry=entry)

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[issue 994]"
    gateway.has_central_unit = False
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, MAC, gateway)
    runtime.platforms["climate"] = {}
    entities: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, entities.extend)

    assert [e._where for e in entities] == ["#0#1"]
    assert registry.async_get(zone1_reg.entity_id) is None
    assert registry.async_get(cu_reg.entity_id) is not None

    # Exercise registry-unavailable fallback in _four_zone_central_pairs and zero-padded standalone yaml check.
    discovery = ZoneDiscovery(hass, entry, runtime, lambda ctx, cfg: entities[0])
    discovery._configured = {
        "non_dict": "ignored",
        "01": {"zone": "01", "standalone": True},
        "cu5": {"zone": "#0#5"},
    }
    assert discovery._is_explicit_standalone_zone("1", None) is True
    discovery._hass = None  # type: ignore[assignment]
    assert discovery._four_zone_central_pairs() == {("5", None)}


async def test_four_zone_central_mode_report_mid_send_keeps_reported_state() -> None:
    """If a central report arrives while same-season wake awaits delivery, optimistic write is skipped."""
    from tests.test_climate_mode_state_2026_11_08 import _climate

    gw = _gateway(has_central_unit=False)
    central = _climate(gw, "#0#1", standalone=None, central=True)
    central.handle_event(OWNHeatingEvent("*4*103*#0#1##"))

    async def send_and_report(cmd: object) -> None:
        central.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))

    gw.send.side_effect = send_and_report
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert central.extra_state_attributes["program"] == 3
    assert central.hvac_mode == HVACMode.HEAT
