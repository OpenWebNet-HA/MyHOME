"""hvac_modes follows the zone's place in the plant: live F461 + F455 with a 3550, 2026-11-08 / 2026-11-12.

Audit of 2026-11-12 (test plant, both gateway entries): ``climate.set_hvac_mode auto`` on zone 2, a
standalone thermostat next to the 3550, answered HTTP 400 "is a standalone thermostat: the central
unit has no program for it" (14:37:09 UTC) although ``hvac_modes`` listed ``auto``; ``heat`` on zone 4,
a zone of the 3550 in the conditioning season, answered HTTP 400 "follows the central unit, which is in
the conditioning season" (15:13:12 UTC) although ``hvac_modes`` listed ``heat``. The refusals are the
design (#880); the list now says the same. Frames: tests/fixtures/traces/climate_mode_2026_11_08.
"""

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import ServiceValidationError

from tests.test_climate_mode_state_2026_11_08 import _climate, _gateway, _replay, _rx


async def test_standalone_zone_drops_auto_once_the_central_unit_is_known() -> None:
    gateway = _gateway(has_central_unit=False)
    zone2 = _climate(gateway, "2", standalone=True)
    assert zone2.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]

    gateway.has_central_unit = True  # the 3550 reported on the bus
    _replay(zone2, _rx("f455_zone2_heat"))  # the zone's own season frame *4*1*2##
    assert zone2.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    assert zone2.hvac_mode == HVACMode.HEAT
    with pytest.raises(ServiceValidationError):
        await zone2.async_set_hvac_mode(HVACMode.AUTO)


async def test_zone_of_the_central_unit_drops_the_off_season_mode() -> None:
    zone4 = _climate(_gateway(has_central_unit=True), "4", standalone=False)
    assert zone4.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]

    _replay(zone4, _rx("f461_zone4_auto_in_cooling"))  # *4*210*#4##, *4*0*4##, *#4*4*12*0205*3##
    assert zone4.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.COOL]
    assert zone4.hvac_mode in zone4.hvac_modes
    with pytest.raises(ServiceValidationError):
        await zone4.async_set_hvac_mode(HVACMode.HEAT)


async def test_central_unit_and_lone_standalone_zone_keep_every_mode() -> None:
    central = _climate(_gateway(has_central_unit=True), "#0", standalone=None, central=True)
    _replay(central, _rx("f461_central_cool"))
    assert central.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]

    alone = _climate(_gateway(has_central_unit=False), "2", standalone=True)
    _replay(alone, _rx("f461_zone2_heat_to_cool"))  # *4*0*2## with no central unit on the bus
    assert alone.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]


def test_the_mode_shown_is_never_dropped() -> None:
    zone4 = _climate(_gateway(has_central_unit=True), "4", standalone=False)
    zone4._attr_hvac_mode = HVACMode.HEAT  # restored from a heating season
    _replay(zone4, _rx("f461_zone4_auto_in_cooling"))
    assert zone4.hvac_mode in zone4.hvac_modes


async def test_an_invalid_mode_kept_because_it_was_shown_drops_when_the_plant_reports_a_valid_one() -> None:
    """DeepSeek A02 point 1: the 'keep the shown mode' clause does not leave a stale invalid mode.

    A zone of the central unit in the conditioning season shows COOL (valid). If it is restored
    as HEAT (off-season) the list keeps HEAT because it is shown; once the plant reports the
    season again (``*4*0*4##`, conditioning), the handler recomputes and HEAT drops. A zone
    never sets its mode optimistically (mode state from status frames only, #880), so the list
    always converges on the plant's report.
    """
    zone4 = _climate(_gateway(has_central_unit=True), "4", standalone=False)
    zone4._attr_hvac_mode = HVACMode.HEAT  # restored off-season mode
    assert HVACMode.HEAT in zone4._advertised_hvac_modes()  # kept because it is shown
    _replay(zone4, _rx("f461_zone4_auto_in_cooling"))  # ends conditioning; mode becomes AUTO/COOL
    assert zone4.hvac_mode in zone4.hvac_modes
    assert HVACMode.HEAT not in zone4.hvac_modes  # the stale off-season mode is gone
