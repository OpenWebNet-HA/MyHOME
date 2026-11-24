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

Master zone 1 is unified into ``Central Unit 01`` (``#0#1``) under Option A because BTicino
4695 firmware (``LH4695_AM5875_010103.fwz``, ROM ``0x0E987C..0x0E989C``) drops direct zone
writes to its built-in master zone, while subordinate zones 3 and 4 are written directly
(``*#4*Z*#14*TTTT*3##``, ``*4*303*Z##``) unless a 99-zone central unit (``#0``) is present.
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
    """Same-season preserves weekly program via 3100 (or writes M=3 in manual); cross-season sends season + 3100."""
    gateway, found = await _plant(hass, None, FIRST, SECOND)
    central = found["#0#1"]
    assert central.target_temperature == 14.0  # `*#4*1*12*0140*3##` during program 3
    assert central._program == 3 and central._nominal_from_dim12 is True

    # [N-P2-2] Same-season HEAT while running Weekly Program 3 keeps/resumes the schedule via 3100,
    # never overwriting the schedule with a permanent manual 14.0 °C night-setback override.
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*4*3100*#0#1##"]
    gateway.send.reset_mock()

    # [N-P2-2] Going OFF and waking HEAT from a Dim-12 schedule setpoint also resumes via 3100.
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert central.hvac_action == HVACAction.OFF
    gateway.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*4*3100*#0#1##"]
    assert central.hvac_mode == HVACMode.HEAT
    assert central.hvac_action == HVACAction.IDLE

    # Manual setpoint (*4*110#0175*#0#1##) -> OFF -> same-season wake writes M=3 manual setpoint.
    central.handle_event(OWNHeatingEvent("*4*110#0175*#0#1##"))
    assert central._program is None and central._nominal_from_dim12 is False
    await central.async_set_hvac_mode(HVACMode.OFF)
    gateway.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == ["*#4*#0#1*#14*0175*3##"]
    assert central.hvac_mode == HVACMode.HEAT
    assert central.hvac_action == HVACAction.IDLE
    assert central.target_temperature == 17.5

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


async def test_unified_four_zone_central_ignores_plain_master_season_and_knob_offset(
    hass: HomeAssistant,
) -> None:
    """Bugs 1 & 2: plain master-zone season frames (*4*1*1## / *4*0*1##) never corrupt #0#1 state,
    and master Zone N knob offset (*#4*1*13*02##) never shifts plant-wide central setpoints.
    """
    from homeassistant.core import State

    from tests.test_climate_mode_state_2026_11_08 import _climate

    gw = _gateway(has_central_unit=False)
    central = _climate(gw, "#0#1", standalone=None, central=True)

    # Bug 1a: OFF central unit stays OFF when plain *4*1*1## arrives.
    central.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    assert central.hvac_mode == HVACMode.OFF and central.hvac_action == HVACAction.OFF
    central.handle_event(OWNHeatingEvent("*4*1*1##"))
    assert central.hvac_mode == HVACMode.OFF and central.hvac_action == HVACAction.OFF

    # Bug 1b: Heating central unit keeps heating season when plain *4*0*1## arrives.
    central.handle_event(OWNHeatingEvent("*4*110#0180*#0#1##"))
    assert central._season == "heating" and central._central_reported_season == "heating"
    central.handle_event(OWNHeatingEvent("*4*0*1##"))
    assert central._season == "heating" and central.hvac_mode == HVACMode.HEAT

    # Bug 2: Master zone 1 knob +2 is exposed in attributes, never shifts display or central write.
    central.handle_event(OWNHeatingEvent("*#4*1*13*02##"))
    assert central.extra_state_attributes["local_offset"] == 2
    assert central.extra_state_attributes["knob_pos"] == "+2"
    assert central.target_temperature == 18.0
    central._show_nominal()
    assert central.target_temperature == 18.0
    await central.async_set_temperature(temperature=20.0)
    assert _sent(gw)[-1] == "*#4*#0#1*#14*0200*3##"
    # Even restoring with saved local_offset=2 preserves exact 18.0 nominal target.
    restored = _climate(gw, "#0#1", standalone=None, central=True)
    await restored.async_restore_last_state(
        State(
            "climate.central_unit_01",
            "heat",
            {"temperature": 18.0, "local_offset": 2, "season": "heating"},
        )
    )
    assert restored.target_temperature == 18.0
    assert restored._target_temperature == 18.0

    # Bug 3: Cross-season HEAT -> COOL right after restart uses restored self._season ("heating").
    gw.send.reset_mock()
    assert restored._central_reported_season is None and restored._season == "heating"
    await restored.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gw) == ["*4*0*#0#1##", "*4*3100*#0#1##"]


async def test_live_four_zone_central_discovery_prunes_existing_unconfigured_master_zone(
    hass: HomeAssistant,
) -> None:
    """Bug 4 (2.0.0 -> 2.0.1 upgrade / reverse discovery): when #0#1 arrives on the bus while
    an unconfigured Climate Zone 1 is already in the entity & device registries, it is pruned
    immediately instead of waiting for a second restart.
    """
    from homeassistant.helpers import device_registry as dr

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    zone1_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-4-1")}, name="Climate Zone 1"
    )
    zone1_ent = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-1", config_entry=entry, device_id=zone1_dev.id
    )
    # Unrelated non-climate and other-zone entries must stay untouched.
    other_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-4-3")}, name="Climate Zone 3"
    )
    other_ent = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-3", config_entry=entry, device_id=other_dev.id
    )
    ent_reg.async_get_or_create("sensor", DOMAIN, f"{MAC}-4-1-temperature", config_entry=entry)

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
    assert any(e._where == "1" for e in entities)

    # First #0#1 report arrives dynamically on the bus -> prunes Climate Zone 1 entity & empty device.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*110#0160*#0#1##"))
    await hass.async_block_till_done()
    assert any(e._where == "#0#1" for e in entities)
    assert ent_reg.async_get(zone1_ent.entity_id) is None
    assert dev_reg.async_get(zone1_dev.id) is None
    assert ent_reg.async_get(other_ent.entity_id) is not None

    # Configured (non-standalone) YAML entry for zone 1 is NOT auto-pruned by _prune_superseded_master_zone.
    zone2_ent = ent_reg.async_get_or_create("climate", DOMAIN, f"{MAC}-4-2", config_entry=entry)
    discovery = ZoneDiscovery(hass, entry, runtime, lambda ctx, cfg: entities[0])
    discovery._configured = {"2": {"zone": "2", "name": "Manual Zone 2"}}
    discovery._prune_superseded_master_zone(("2", None))
    assert ent_reg.async_get(zone2_ent.entity_id) is not None


def test_saved_central_doubled_and_where_grammar_edge_cases() -> None:
    """Bug 5: _SAVED_CENTRAL_DOUBLED catches #0#00/#0##0/#0##00, canonical_central_zone pads
    single-digit #4#1 -> #4#01, and four_zone_central_where validates interface params.
    """
    from custom_components.myhome.climate_discovery import _SAVED_CENTRAL_DOUBLED
    from custom_components.myhome.where_grammar import (
        canonical_central_zone,
        four_zone_central_where,
    )

    for bad in ("#0#0", "#0#00", "#0##0", "#0##00", "#0#01", "#0##1", "#0##0#1"):
        m = _SAVED_CENTRAL_DOUBLED.fullmatch(f"{MAC}-4-{bad}")
        assert m is not None, f"missed {bad}"
    for good in ("#0", "#0#1", "#0#21", "#0#96"):
        assert _SAVED_CENTRAL_DOUBLED.fullmatch(f"{MAC}-4-{good}") is None

    assert canonical_central_zone("0#4#1") == "#0#4#01"
    assert canonical_central_zone("1#4#1") == "#0#1#4#01"
    assert canonical_central_zone("00#4#1") == "#0#4#01"

    bad_iface = MagicMock(where="#0", where_param=["1", "4", "abc"])
    assert four_zone_central_where(bad_iface) is None
    ok_iface = MagicMock(where="#0", where_param=["1", "4", "01"])
    assert four_zone_central_where(ok_iface) == "#0#1"


# ── review 6040005040 regressions ──


async def test_four_zone_central_protection_dim12_preserves_nominal_setpoint() -> None:
    """[N-P0-1] Antifreeze/thermal protection + master Zone N Dim 12 never clobbers #0#N nominal."""
    from tests.test_climate_mode_state_2026_11_08 import _climate

    # 1. Antifreeze (*4*102*#0#1##) followed by master Zone 1 Dim 12 (7.0 °C) + *4*102*1##.
    gw = _gateway(has_central_unit=False)
    central = _climate(gw, "#0#1", standalone=None, central=True)
    central.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    assert central.target_temperature == 20.0
    central.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    central.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    central.handle_event(OWNHeatingEvent("*4*102*1##"))
    assert central._central_operation == "antifreeze"
    assert central._target_temperature == 20.0
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*#4*#0#1*#14*0200*3##"]
    assert central._central_operation is None
    assert central.target_temperature == 20.0

    # 2. Thermal protection (*4*202*#0#1##) followed by master Zone 1 Dim 12 (35.0 °C).
    gw.send.reset_mock()
    central.handle_event(OWNHeatingEvent("*4*210#0250*#0#1##"))
    central.handle_event(OWNHeatingEvent("*4*202*#0#1##"))
    central.handle_event(OWNHeatingEvent("*#4*1*12*0350*3##"))
    assert central._central_operation == "thermal_protection"
    assert central._target_temperature == 25.0
    await central.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gw) == ["*#4*#0#1*#14*0250*3##"]
    assert central.target_temperature == 25.0

    # 3. Reverse arrival (*#4*1*12*0070*3## before *4*102*#0#1##) + bus exit from protection.
    gw2 = _gateway(has_central_unit=False)
    central2 = _climate(gw2, "#0#1", standalone=None, central=True)
    central2.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    central2.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    assert central2._target_temperature == 20.0
    assert central2._nominal_from_dim12 is False
    # Leaving central protection via bus report restores displayed nominal setpoint (20.0, not 7.0).
    central2.handle_event(OWNHeatingEvent("*4*110*#0#1##"))
    assert central2._central_operation is None
    assert central2.target_temperature == 20.0

    # 4. Boot straight into Antifreeze with no known nominal: Dim 12 7.0 °C must not leak into HEAT wake.
    gw3 = _gateway(has_central_unit=False)
    central3 = _climate(gw3, "#0#1", standalone=None, central=True)
    central3.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    central3.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    assert central3._effective_nominal_temperature() is None
    await central3.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw3) == ["*4*3100*#0#1##"]


async def test_four_zone_central_upgrade_prunes_companions_empty_device_and_attaches_0_10v_actuator(
    hass: HomeAssistant,
) -> None:
    """[N-P1-1] Upgrade/restart prunes Climate Zone 1 + #981 companions + empty device,
    and 1#A 0-10V proportional actuator sensors attach to Central Unit 01 (#0#1).
    """
    from homeassistant.components.sensor import SensorDeviceClass
    from homeassistant.helpers import device_registry as dr

    from custom_components.myhome.sensor import async_setup_entry as sensor_setup_entry

    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    zone1_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{MAC}-4-1")}, name="Climate Zone 1"
    )
    cu_dev = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}-4-#0#1")},
        name="Central Unit 01",
    )
    zone1_ent = ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-1", config_entry=entry, device_id=zone1_dev.id
    )
    temp_ent = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{MAC}-4-1-temperature",
        config_entry=entry,
        device_id=zone1_dev.id,
        original_device_class=SensorDeviceClass.TEMPERATURE,
    )
    hum_ent = ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{MAC}-4-1-humidity",
        config_entry=entry,
        device_id=zone1_dev.id,
        original_device_class=SensorDeviceClass.HUMIDITY,
    )
    ent_reg.async_get_or_create(
        "climate", DOMAIN, f"{MAC}-4-#0#1", config_entry=entry, device_id=cu_dev.id
    )

    gateway = MagicMock()
    gateway.mac = MAC
    gateway.is_connected = True
    gateway.log_id = "[issue 994]"
    gateway.has_central_unit = False
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, MAC, gateway)
    runtime.platforms["climate"] = {}
    runtime.platforms["sensor"] = {}

    # Run sensor setup FIRST (worst-case concurrent startup order) then climate setup.
    sensors: list = []
    climates: list[MyHOMEClimate] = []
    await sensor_setup_entry(hass, entry, sensors.extend)
    await async_setup_entry(hass, entry, climates.extend)
    await hass.async_block_till_done()

    assert [c._where for c in climates] == ["#0#1"]
    assert sensors == []
    assert ent_reg.async_get(zone1_ent.entity_id) is None
    assert ent_reg.async_get(temp_ent.entity_id) is None
    assert ent_reg.async_get(hum_ent.entity_id) is None
    assert dev_reg.async_get(zone1_dev.id) is None

    # 0-10V proportional actuator output on master Zone 1 (1#1) attaches to Central Unit 01 (#0#1).
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*1#1*20*150##"))
    await hass.async_block_till_done()
    assert len(sensors) == 1
    out_sensor = sensors[0]
    assert isinstance(out_sensor, MyHOMEActuatorOutputSensor)
    assert out_sensor._device_id == "#0#1"
    assert out_sensor._device_name == "Central Unit 01"
    assert out_sensor._model == "Central Unit (4695)"
    assert out_sensor.native_value == 50

    # Live bus-discovered #0#5 (no YAML, no registry entry yet) also routes 5#1 output sensor to #0#5.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*4*110#0200*#0#5##"))
    await hass.async_block_till_done()
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*5#1*20*140##"))
    await hass.async_block_till_done()
    out5 = [s for s in sensors if isinstance(s, MyHOMEActuatorOutputSensor) and s._zone == "5"]
    assert len(out5) == 1 and out5[0]._device_id == "#0#5" and out5[0].native_value == 40

    # YAML-configured #0#9 (zone: "9", central: true) also routes 9#1 output sensor to #0#9 with YAML name.
    yaml_cfg: dict[str, dict] = {"climate": {}}
    _apply_yaml_platforms(
        {"climate": climate_schema({"cu9": {"zone": "9", "central": True, "name": "Centrale 9"}})},
        yaml_cfg,
    )
    runtime.platforms["climate"].update(yaml_cfg["climate"])
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*9#1*20*160##"))
    await hass.async_block_till_done()
    out9 = [s for s in sensors if isinstance(s, MyHOMEActuatorOutputSensor) and s._zone == "9"]
    assert len(out9) == 1 and out9[0]._device_id == "#0#9" and out9[0]._device_name == "Centrale 9"


async def test_live_reverse_discovery_from_f454_sweep_transfers_master_telemetry_to_four_zone_central(
    hass: HomeAssistant,
) -> None:
    """[N-P1-1] On live reverse discovery (F454 + LN4695 sweep #1002 creates Climate Zone 1 first,
    then #0#1 arrives later), Central Unit 01 inherits live temperature, humidity, knob, and actuators.
    """
    sweep = (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "issue_994_f454_sweep_2026-11-22"
        / "myhome_sweep_F454_all_2026-11-22T21-35-56.json"
    )
    _gateway_, found = await _plant(hass, None, sweep)
    assert "1" in found and "#0#1" not in found
    zone1 = found["1"]
    assert zone1.current_temperature == 19.4
    # Also feed humidity & knob onto live Zone 1 before #0#1 arrives.
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*1*60*48##"))
    async_dispatcher_send(hass, f"myhome_message_{MAC}", OWNEvent.parse("*#4*1*13*01##"))
    await hass.async_block_till_done()
    assert zone1.current_humidity == 48.0
    assert zone1.extra_state_attributes["knob_pos"] == "+1"

    # Now #0#1 reports dynamically on the bus -> inherits Zone 1's live telemetry cleanly.
    created: list[MyHOMEClimate] = []
    # Re-run via full setup capturing newly added #0#1 entity from runtime/entities list.
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": "00:03:50:44:55:77"}, unique_id="00:03:50:44:55:77")
    entry.add_to_hass(hass)
    gw2 = MagicMock(mac="00:03:50:44:55:77", is_connected=True, log_id="[sweep]", has_central_unit=False)
    gw2.send = AsyncMock()
    gw2.send_status_request = AsyncMock()
    rt2 = attach_runtime(hass, entry, gw2.mac, gw2)
    rt2.platforms["climate"] = {}
    await async_setup_entry(hass, entry, created.extend)
    for raw in (*_received(sweep), "*#4*1*60*48##", "*#4*1*13*01##"):
        async_dispatcher_send(hass, f"myhome_message_{gw2.mac}", OWNEvent.parse(raw))
        await hass.async_block_till_done()
    assert any(e._where == "1" for e in created)
    async_dispatcher_send(hass, f"myhome_message_{gw2.mac}", OWNEvent.parse("*4*110#0160*#0#1##"))
    await hass.async_block_till_done()
    central = next(e for e in created if e._where == "#0#1")
    assert central.current_temperature == 19.4
    assert central.current_humidity == 48.0
    assert central.extra_state_attributes["knob_pos"] == "+1"
    assert central.extra_state_attributes["actuators"] == {"1": "off"}
    assert central.target_temperature == 16.0


async def test_four_zone_central_polls_status_and_master_temp_after_f454_sweep(
    hass: HomeAssistant,
) -> None:
    """[N-P1-2] #0#N polls *#4*#0#N## on startup/update (even if #0 central_status_just_asked is True),
    and polls master Zone N temperature (*#4*N*0##) on gateways dropping *#4*0## when unreported.
    """
    sweep = (
        Path(__file__).parent
        / "fixtures"
        / "traces"
        / "issue_994_f454_sweep_2026-11-22"
        / "myhome_sweep_F454_all_2026-11-22T21-35-56.json"
    )
    gateway, found = await _plant(hass, REPORTERS_FILE, sweep)
    central = found["#0#1"]
    gateway.general_zone_request_refused = True
    gateway.central_status_just_asked = True  # #0 sweep must NOT suppress #0#1's own *#4*#0#1## poll
    assert central._poll_on_add is True
    assert central.current_temperature == 19.4  # already reported by sweep

    # Sweep already reported temperature -> only *#4*#0#1## is polled (no redundant *#4*1*0##).
    await central.async_update()
    assert [str(c.args[0]) for c in gateway.send_status_request.await_args_list] == ["*#4*#0#1##"]

    # Without a live temperature report (e.g. F461 / MHS1 / F455), both *#4*#0#1## and *#4*1*0## are polled.
    gateway.send_status_request.reset_mock()
    central._attr_native_current_temperature = None
    await central.async_update()
    assert [str(c.args[0]) for c in gateway.send_status_request.await_args_list] == [
        "*#4*#0#1##",
        "*#4*1*0##",
    ]


async def test_four_zone_central_sensor_event_validation_and_pre_report_season() -> None:
    """[N-P2-1] & [N-P2-3]: #0#N enforces thermo_zone_sensor_target + 0..100% humidity bounds,
    and accepts pre-report plain *4*1*N## / *4*0*N## until #0#N itself reports.
    """
    from tests.test_climate_mode_state_2026_11_08 import _climate

    gw = _gateway(has_central_unit=False)
    central = _climate(gw, "#0#1", standalone=None, central=True)

    # [N-P2-3] Before any #0#1 report (_central_report_count == 0), *4*1*1## seeds season & mode.
    central.handle_event(OWNHeatingEvent("*4*1*1##"))
    assert central._central_report_count == 0
    assert central._season == "heating"
    assert central.hvac_mode == HVACMode.HEAT

    # [N-P2-1] Valid master Zone 1 temperature & humidity update #0#1; invalid selectors & 255% rejected.
    central.handle_event(OWNHeatingEvent("*#4*1*0*0194##"))
    central.handle_event(OWNHeatingEvent("*#4*1*60*47##"))
    assert central.current_temperature == 19.4
    assert central.current_humidity == 47.0

    for bad_raw in (
        "*#4*001*0*0299##",
        "*#4*1#1*0*0299##",
        "*#4*#1*0*0299##",
        "*#4*1*#0*0299##",
        "*#4*1*60*255##",
    ):
        central.handle_event(OWNHeatingEvent(bad_raw))
    assert central.current_temperature == 19.4
    assert central.current_humidity == 47.0


# ── critical PR #1000 state-machine regressions (F-1, F-2, F-3) ──


async def test_four_zone_central_restart_preserves_protection_program_and_manual_semantics() -> None:
    """F-1: protection/OFF/program Dim 12 never leaks across HA restarts as a manual setpoint,
    while genuine central manual setpoints (*4*110#TTTT*#0#N##) do survive restarts.
    """
    from homeassistant.core import State

    from tests.test_climate_mode_state_2026_11_08 import _climate

    def _saved(entity: MyHOMEClimate) -> State:
        assert entity.hvac_mode is not None
        return State(
            "climate.central_unit_01",
            entity.hvac_mode.value,
            {"temperature": entity.target_temperature, **entity.extra_state_attributes},
        )

    # 1. Antifreeze (7.0 °C) across restart -> HEAT must send 3100, never manual 7.0 °C.
    gw = _gateway(has_central_unit=False)
    live = _climate(gw, "#0#1", standalone=None, central=True)
    live.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    live.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    assert live.target_temperature == 7.0
    assert live.extra_state_attributes["central_operation"] == "antifreeze"
    assert "nominal_temperature" not in live.extra_state_attributes
    saved_af = _saved(live)

    restored_af = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_af.async_restore_last_state(saved_af)
    assert restored_af.target_temperature == 7.0
    assert restored_af._target_temperature is None
    assert restored_af.extra_state_attributes["central_operation"] == "antifreeze"
    restored_af.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    await restored_af.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*4*3100*#0#1##"]
    assert restored_af.target_temperature is None
    assert "central_operation" not in restored_af.extra_state_attributes
    # Subsequent *4*1103*#0#1## without a fresh Dim 12 (plant-cu4695.tsv:46) stays None until fresh Dim 12.
    restored_af.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert restored_af.target_temperature is None
    restored_af.handle_event(OWNHeatingEvent("*#4*1*12*0185*3##"))
    assert restored_af.target_temperature == 18.5
    assert restored_af._nominal_from_dim12 is True

    # 2. Thermal protection (35.0 °C) across restart -> COOL must send 3100, never manual 35.0 °C.
    gw.send.reset_mock()
    live_tp = _climate(gw, "#0#1", standalone=None, central=True)
    live_tp.handle_event(OWNHeatingEvent("*4*202*#0#1##"))
    live_tp.handle_event(OWNHeatingEvent("*#4*1*12*0350*3##"))
    assert "nominal_temperature" not in live_tp.extra_state_attributes
    restored_tp = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_tp.async_restore_last_state(_saved(live_tp))
    assert restored_tp._target_temperature is None
    restored_tp.handle_event(OWNHeatingEvent("*4*202*#0#1##"))
    await restored_tp.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gw) == ["*4*3100*#0#1##"]
    assert restored_tp.target_temperature is None

    # 3. Weekly Program 3 (14.0 °C) -> OFF across restart -> HEAT must send 3100, never manual 14.0 °C.
    gw.send.reset_mock()
    live_prog = _climate(gw, "#0#1", standalone=None, central=True)
    live_prog.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    live_prog.handle_event(OWNHeatingEvent("*#4*1*12*0140*3##"))
    assert "nominal_temperature" not in live_prog.extra_state_attributes
    saved_running_prog = _saved(live_prog)
    live_prog.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    assert "nominal_temperature" not in live_prog.extra_state_attributes
    restored_off_prog = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_off_prog.async_restore_last_state(_saved(live_prog))
    assert restored_off_prog._target_temperature is None
    restored_off_prog.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await restored_off_prog.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*4*3100*#0#1##"]

    # 4. Weekly Program 3 saved while running -> restart -> OFF -> HEAT also resumes via 3100.
    gw.send.reset_mock()
    restored_prog = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_prog.async_restore_last_state(saved_running_prog)
    assert restored_prog.target_temperature == 14.0
    assert restored_prog._nominal_from_dim12 is True
    assert "nominal_temperature" not in restored_prog.extra_state_attributes
    restored_prog.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await restored_prog.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*4*3100*#0#1##"]

    # 5. Control: genuine manual 20.0 °C persists nominal_temperature across running, antifreeze, and OFF restarts.
    live_man = _climate(gw, "#0#1", standalone=None, central=True)
    live_man.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    assert live_man.extra_state_attributes["nominal_temperature"] == 20.0
    saved_man_run = _saved(live_man)

    gw.send.reset_mock()
    restored_man_run = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_man_run.async_restore_last_state(saved_man_run)
    assert restored_man_run.target_temperature == 20.0
    assert restored_man_run.extra_state_attributes["nominal_temperature"] == 20.0
    await restored_man_run.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*#4*#0#1*#14*0200*3##"]

    live_man.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    live_man.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    assert live_man.target_temperature == 7.0
    assert live_man.extra_state_attributes["nominal_temperature"] == 20.0
    saved_man_af = _saved(live_man)

    gw.send.reset_mock()
    restored_man_af = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_man_af.async_restore_last_state(saved_man_af)
    assert restored_man_af.target_temperature == 7.0
    assert restored_man_af.extra_state_attributes["nominal_temperature"] == 20.0
    await restored_man_af.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*#4*#0#1*#14*0200*3##"]
    assert restored_man_af.target_temperature == 20.0

    live_man.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    saved_man_off = _saved(live_man)
    gw.send.reset_mock()
    restored_man_off = _climate(gw, "#0#1", standalone=None, central=True)
    await restored_man_off.async_restore_last_state(saved_man_off)
    assert restored_man_off.extra_state_attributes["nominal_temperature"] == 20.0
    restored_man_off.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await restored_man_off.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*#4*#0#1*#14*0200*3##"]

    # 6. Pre-restore live bus frames:
    # 6a. Live antifreeze + Dim 12 before restore still recovers saved manual 20.0 nominal.
    gw.send.reset_mock()
    live_before_af = _climate(gw, "#0#1", standalone=None, central=True)
    live_before_af.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    live_before_af.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    await live_before_af.async_restore_last_state(saved_man_af)
    assert live_before_af.target_temperature == 7.0
    assert live_before_af.extra_state_attributes["nominal_temperature"] == 20.0
    await live_before_af.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*#4*#0#1*#14*0200*3##"]

    # 6b. Live weekly program before restore never inherits a stale saved manual nominal.
    gw.send.reset_mock()
    live_before_prog = _climate(gw, "#0#1", standalone=None, central=True)
    live_before_prog.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    await live_before_prog.async_restore_last_state(saved_man_off)
    assert live_before_prog.target_temperature is None
    assert live_before_prog._target_temperature is None
    await live_before_prog.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*4*3100*#0#1##"]

    # 6c. Live active pre-report season before restore discards saved antifreeze 7.0 display.
    live_before_season = _climate(gw, "#0#1", standalone=None, central=True)
    live_before_season.handle_event(OWNHeatingEvent("*4*1*1##"))
    await live_before_season.async_restore_last_state(saved_af)
    assert live_before_season.target_temperature is None


async def test_four_zone_central_leaving_protection_or_off_into_program_ignores_stale_dim12() -> None:
    """F-2: transitioning from antifreeze/OFF/manual into a weekly program without a fresh
    active master-zone Dim 12 clears stale 7/35 °C or old manual setpoints, while preserving
    active-master Dim 12 in both F454 and F461/MHS1/MH200N frame orders.
    """
    from tests.test_climate_mode_state_2026_11_08 import _climate

    # 1. Manual 19.0 -> Antifreeze -> master Dim 12 (7.0 °C) -> Program 3 (*4*1103*#0#1##, no new Dim 12).
    gw = _gateway(has_central_unit=False)
    central = _climate(gw, "#0#1", standalone=None, central=True)
    central.handle_event(OWNHeatingEvent("*4*110#0190*#0#1##"))
    central.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    central.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    assert central.target_temperature == 7.0
    central.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["program"] == 3
    assert central.target_temperature is None
    assert central._target_temperature is None
    assert "nominal_temperature" not in central.extra_state_attributes
    # Fresh schedule Dim 12 updates display and survives periodic *4*1103*#0#1## poll repeats.
    central.handle_event(OWNHeatingEvent("*#4*1*12*0180*3##"))
    assert central.target_temperature == 18.0
    assert central._nominal_from_dim12 is True
    central.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert central.target_temperature == 18.0

    # 2. Manual 19.0 -> OFF -> master Dim 12 (7.0 °C) + *4*303*1## -> Program 3 (no new Dim 12).
    gw2 = _gateway(has_central_unit=False)
    central2 = _climate(gw2, "#0#1", standalone=None, central=True)
    central2.handle_event(OWNHeatingEvent("*4*110#0190*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    central2.handle_event(OWNHeatingEvent("*4*303*1##"))
    central2.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert central2.target_temperature is None
    assert central2._target_temperature is None
    central2.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await central2.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw2) == ["*4*3100*#0#1##"]

    # 3. F461/MHS1/MH200N wake order (Dim 12 immediately before *4*1*1##, ahead of *4*1103*#0#1##).
    central2.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*#4*1*12*0140*3##"))
    central2.handle_event(OWNHeatingEvent("*4*1*1##"))
    central2.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert central2.target_temperature == 14.0
    assert central2._nominal_from_dim12 is True

    # 4a. Direct Manual 20.0 -> Program 3 without fresh Dim 12 clears old manual 20.0.
    gw3 = _gateway(has_central_unit=False)
    central3 = _climate(gw3, "#0#1", standalone=None, central=True)
    central3.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    central3.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert central3.target_temperature is None
    assert central3._target_temperature is None
    central3.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await central3.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw3) == ["*4*3100*#0#1##"]

    # 4b. Direct Manual 20.0 -> *4*1*1## + *#4*1*12*0140*3## -> *4*1103*#0#1## adopts fresh 14.0.
    central3.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    central3.handle_event(OWNHeatingEvent("*4*1*1##"))
    central3.handle_event(OWNHeatingEvent("*#4*1*12*0140*3##"))
    assert central3.target_temperature == 20.0  # manual stays untouched until #0#1 confirms Program 3
    central3.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    assert central3.target_temperature == 14.0
    assert central3._nominal_from_dim12 is True
    assert "nominal_temperature" not in central3.extra_state_attributes

    # 5. Optimistic AUTO wake from Antifreeze clears stale 7.0 °C display.
    gw4 = _gateway(has_central_unit=False)
    central4 = _climate(gw4, "#0#1", standalone=None, central=True)
    central4.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    central4.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    assert central4.target_temperature == 7.0
    await central4.async_set_hvac_mode(HVACMode.AUTO)
    assert _sent(gw4) == ["*4*3100*#0#1##"]
    assert central4.target_temperature is None


async def test_four_zone_central_master_dim12_and_dim14_do_not_corrupt_manual_or_program_state() -> None:
    """F-3: master-zone Dim 12 / Dim 14 reports (*#4*1##, plant-cu4695.tsv:23) never turn a
    weekly program or antifreeze into a manual setpoint, and never flip manual mode into program.
    """
    from tests.test_climate_mode_state_2026_11_08 import _climate

    # 1. Weekly Program 3 + *#4*1## Dim 12 & Dim 14 -> OFF -> HEAT stays 3100 (never manual 18.0).
    gw = _gateway(has_central_unit=False)
    central = _climate(gw, "#0#1", standalone=None, central=True)
    central.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    central.handle_event(OWNHeatingEvent("*#4*1*12*0180*3##"))
    central.handle_event(OWNHeatingEvent("*#4*1*14*0180*3##"))
    assert central.target_temperature == 18.0
    assert central._nominal_from_dim12 is True
    assert "nominal_temperature" not in central.extra_state_attributes
    central.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == ["*4*3100*#0#1##"]

    # 2. Antifreeze + master Dim 14 (*#4*1*14*0070*3##) never plants 7.0 °C manual nominal.
    gw2 = _gateway(has_central_unit=False)
    central2 = _climate(gw2, "#0#1", standalone=None, central=True)
    central2.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*#4*1*14*0070*3##"))
    assert central2._target_temperature is None
    await central2.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw2) == ["*4*3100*#0#1##"]

    # 2b. Manual 20.0 -> Antifreeze + master Dim 14 (7.0 °C) preserves genuine manual 20.0.
    gw2.send.reset_mock()
    central2.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*4*102*#0#1##"))
    central2.handle_event(OWNHeatingEvent("*#4*1*14*0070*3##"))
    assert central2._target_temperature == 20.0
    await central2.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw2) == ["*#4*#0#1*#14*0200*3##"]

    # 3. Manual 20.0 + master knob +2 & delayed Dim 12 (22.0 °C) -> OFF -> HEAT keeps manual 20.0.
    gw3 = _gateway(has_central_unit=False)
    central3 = _climate(gw3, "#0#1", standalone=None, central=True)
    central3.handle_event(OWNHeatingEvent("*4*110#0200*#0#1##"))
    central3.handle_event(OWNHeatingEvent("*#4*1*13*02##"))
    central3.handle_event(OWNHeatingEvent("*#4*1*12*0220*3##"))
    assert central3.target_temperature == 20.0
    assert central3._target_temperature == 20.0
    assert central3._nominal_from_dim12 is False
    assert central3.extra_state_attributes["nominal_temperature"] == 20.0
    central3.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    await central3.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw3) == ["*#4*#0#1*#14*0200*3##"]

    # 4. Reverse-arrival Program 3 -> Dim 12 (7.0 °C) -> OFF preserves _nominal_from_dim12=True.
    gw4 = _gateway(has_central_unit=False)
    central4 = _climate(gw4, "#0#1", standalone=None, central=True)
    central4.handle_event(OWNHeatingEvent("*4*1103*#0#1##"))
    central4.handle_event(OWNHeatingEvent("*#4*1*12*0140*3##"))
    central4.handle_event(OWNHeatingEvent("*#4*1*12*0070*3##"))
    central4.handle_event(OWNHeatingEvent("*4*103*#0#1##"))
    assert central4._nominal_from_dim12 is True
    assert "nominal_temperature" not in central4.extra_state_attributes
    await central4.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw4) == ["*4*3100*#0#1##"]
