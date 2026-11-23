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
from homeassistant.components.climate import HVACMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from OWNd.message import OWNEvent, OWNHeatingEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.legacy_yaml import _apply_yaml_platforms
from custom_components.myhome.sensor import MyHOMEActuatorOutputSensor
from custom_components.myhome.validate import climate_schema
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


async def _plant(hass: HomeAssistant, yaml_cfg: dict | None, *files: Path) -> tuple[MagicMock, dict[str, MyHOMEClimate]]:
    """The climate platform of a gateway that received the frames of ``files``, in order."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
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
    assert set(found) == {"#0#1", "1", "3", "4"}
    assert found["#0#1"]._central is True
    assert [found[zone]._central for zone in ("1", "3", "4")] == [False, False, False]
    assert gateway.has_central_unit is False  # no 99-zone central unit on this plant


@pytest.mark.parametrize("zone", ["1", "3", "4"])
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
    assert _sent(gateway)[0] == "*#4*#0#1*#14*0180*1##"  # the form of frame 19
    gateway.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.OFF)
    assert _sent(gateway)[0] == "*4*303*#0#1##"  # frame 38


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
    assert set(found) == {"#0#1", "1", "3", "4"}
    assert (found["#0#1"]._device_name, found["3"]._device_name, found["4"]._device_name) == ("Centrale", "Zona 3", "Zona 4")
    await found["#0#1"].async_set_temperature(temperature=18.0)
    await found["3"].async_set_temperature(temperature=18.0)
    await found["4"].async_set_temperature(temperature=18.0)
    assert _sent(gateway) == ["*#4*#0#1*#14*0180*1##", "*#4*3*#14*0180*3##", "*#4*4*#14*0180*3##"]


async def test_zone_1_beside_the_configured_central_unit_is_not_the_central_units_entry(hass: HomeAssistant) -> None:
    """``zone: "1"`` with ``central: true`` is the central unit ``#0#1``; zone 1 itself is found on the bus."""
    gateway, found = await _plant(hass, REPORTERS_FILE, FIRST, SECOND)
    zone = found["1"]
    assert zone._central is False
    assert zone._device_name != "Centrale"
    assert zone._standalone is True  # not the central unit's `standalone: false`
    await zone.async_set_temperature(temperature=18.0)
    assert _sent(gateway) == ["*#4*1*#14*0180*3##"]
