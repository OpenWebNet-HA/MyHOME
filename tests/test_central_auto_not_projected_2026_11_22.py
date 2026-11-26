"""The central unit's `auto` is a command to the central unit: its zones keep the mode they show.

Rule (decision of 2026-11-22, completing the one of 2026-11-18, "a program is shown `heat` / `cool`, never
`auto`"): a climate entity shows `auto` only when it was itself asked for `auto`, and only until its device
reports. Neither the central unit's `auto` command nor a program frame of the central unit that carries no
season changes what a zone of the central unit shows; `off`, `heat` and `cool` of the central unit still do.

Measured on a live plant, 2026-11-22 (a 3550 with zones 3 and 4, an F461 2.0.11 and an F455 1.0.76 on the same
bus, one Home Assistant per gateway): `climate.set_hvac_mode auto` on the central unit showed zones 3 and 4
`auto` for 0.15 s on the instance that sent it, until the central unit's `*4*1101*#0##`; the zones' own
`*4*111*#3##` / `*4*111*#4##` followed 2 and 4 s later. The F455 hands `*4*3100*#0##` back on its own event
session, the F461 does not, so the two instances disagreed.

These are composed scenarios in the order of that capture, not a recorded transaction.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent

from custom_components.myhome.myhome_device import MyHOMEEntity
from tests.test_crew_central_season_restore import (  # noqa: F401  (the fixture is used by name)
    COOLING_PROGRAM,
    HEATING_OFF,
    HEATING_PROGRAM,
    RESTORE,
    _climate,
    _entity_base_init,
    _gateway,
    _replay,
    _sent,
)

ZONE_3_MANUAL = "*4*110*#3##"  # tests/fixtures/traces/zone_short_off_2026_11_19/frames.json:198
ZONE_3_AUTOMATIC = "*4*111*#3##"  # tests/fixtures/traces/issue_466/myhome_sweep_MH200N_all_2026-11-02T12-53-15.json:1567
ZONE_4_MANUAL_COOLING = "*4*210*#4##"  # tests/fixtures/traces/climate_mode_2026_11_08/frames.json:81
# tests/fixtures/traces/central_restore_3100_live_2026-11-12/myhome_trace_F461_central_restore_2026-11-12.json:618
ZONE_4_AUTOMATIC_COOLING = "*4*211*#4##"

Plant = tuple[MagicMock, Any, Any, list[HVACMode | None]]


@pytest.fixture
async def plant(hass: HomeAssistant) -> AsyncIterator[Callable[[str], Awaitable[Plant]]]:
    """A 99-zone central unit and one of its zones on one gateway; every mode the zone publishes is kept."""
    zones: list[Any] = []

    async def _build(zone_where: str) -> Plant:
        gateway = _gateway()
        central = _climate(gateway, "#0", standalone=None, central=True)
        zone = _climate(gateway, zone_where, standalone=False)
        central.hass = zone.hass = hass
        shown: list[HVACMode | None] = []
        zone.async_write_ha_state = MagicMock(side_effect=lambda: shown.append(zone.hvac_mode))
        with patch.object(MyHOMEEntity, "async_added_to_hass", AsyncMock()):
            await zone.async_added_to_hass()
        zones.append(zone)
        return gateway, central, zone, shown

    yield _build
    for zone in zones:
        zone._call_on_remove_callbacks()


async def test_the_auto_command_of_the_central_unit_leaves_a_heating_zone_heat(
    plant: Callable[[str], Awaitable[Plant]],
) -> None:
    gateway, central, zone, shown = await plant("3")
    zone.handle_event(OWNEvent.parse(ZONE_3_MANUAL))
    assert zone.hvac_mode == HVACMode.HEAT
    shown.clear()

    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert _sent(gateway) == [RESTORE]
    assert central.hvac_mode == HVACMode.AUTO  # the entity that was asked shows it until the report
    assert zone.hvac_mode == HVACMode.HEAT

    _replay(central, [HEATING_PROGRAM])
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["program"] == 1
    zone.handle_event(OWNEvent.parse(ZONE_3_AUTOMATIC))
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.extra_state_attributes["zone_operation"] == "automatic"
    assert HVACMode.AUTO not in shown


async def test_the_auto_command_of_the_central_unit_leaves_a_cooling_zone_cool(
    plant: Callable[[str], Awaitable[Plant]],
) -> None:
    _gateway_double, central, zone, shown = await plant("4")
    zone.handle_event(OWNEvent.parse(ZONE_4_MANUAL_COOLING))
    assert zone.hvac_mode == HVACMode.COOL
    shown.clear()

    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert zone.hvac_mode == HVACMode.COOL

    _replay(central, [COOLING_PROGRAM])
    zone.handle_event(OWNEvent.parse(ZONE_4_AUTOMATIC_COOLING))
    assert zone.hvac_mode == HVACMode.COOL
    assert zone.extra_state_attributes["zone_operation"] == "automatic"
    assert HVACMode.AUTO not in shown


async def test_a_program_frame_without_a_season_leaves_the_zone_as_shown(
    plant: Callable[[str], Awaitable[Plant]],
) -> None:
    """`*4*3100*#0##` seen on the bus (an F455 hands the command back on its event session, live 2026-11-22)."""
    gateway, central, zone, shown = await plant("3")
    zone.handle_event(OWNEvent.parse(ZONE_3_MANUAL))
    shown.clear()

    _replay(central, [RESTORE])
    assert central.hvac_mode == HVACMode.AUTO
    assert zone.hvac_mode == HVACMode.HEAT
    _replay(central, [HEATING_PROGRAM])
    assert central.hvac_mode == HVACMode.HEAT
    assert zone.hvac_mode == HVACMode.HEAT
    assert HVACMode.AUTO not in shown
    assert _sent(gateway) == []


async def test_a_zone_that_is_off_stays_off_when_the_central_unit_is_asked_for_auto(
    plant: Callable[[str], Awaitable[Plant]],
) -> None:
    _gateway_double, central, zone, shown = await plant("3")
    zone.handle_event(OWNEvent.parse(ZONE_3_MANUAL))
    _replay(central, [HEATING_OFF])
    assert zone.hvac_mode == HVACMode.OFF
    shown.clear()

    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert zone.hvac_mode == HVACMode.OFF
    assert HVACMode.AUTO not in shown


async def test_a_zone_already_shown_auto_takes_the_season_the_central_unit_reports(
    plant: Callable[[str], Awaitable[Plant]],
) -> None:
    """An `auto` the zone already shows (a state saved by an earlier version) is left alone, then replaced."""
    _gateway_double, central, zone, _shown = await plant("3")
    zone._attr_hvac_mode = HVACMode.AUTO

    await central.async_set_hvac_mode(HVACMode.AUTO)
    assert zone.hvac_mode == HVACMode.AUTO

    _replay(central, [HEATING_PROGRAM])
    assert zone.hvac_mode == HVACMode.HEAT


async def test_the_handler_keeps_the_mode_of_a_zone_for_auto() -> None:
    zone = _climate(_gateway(), "3", standalone=False)
    for mode in (HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF):
        zone._attr_hvac_mode = mode
        zone._handle_central_mode_update(HVACMode.AUTO)
        assert zone.hvac_mode == mode
