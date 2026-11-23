"""Three paths of the central unit code that no other test walks.

Composed cases, not captures. Frames marked BUILT are not from a capture or a document.
"""

from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent, OWNHeatingCommand

from tests.test_crew_central_season_restore import HEAT, RESTORE, _climate, _gateway, _replay, _sent
from tests.test_crew_central_season_restore import _entity_base_init as _entity_base_init

MANUAL_18 = "*4*110#0180*#0##"  # tests/fixtures/traces/issue_466/README.md:30
GENERAL_OFF = "*4*303*0##"  # tests/test_issue_256_257.py:251-252 ("General OFF broadcast")
FOUR_ZONE_MANUAL_21 = "*4*110#0210*#0#1##"  # tests/test_climate_central_discovery.py:74
# BUILT: no capture and no document has an off WHAT with a setpoint for `#0`; OWNd parses the
# shape as "off, with a target". The code treats it as an off report of the central unit.
OFF_WITH_SETPOINT = "*4*103#0180*#0##"


async def test_a_zone_of_a_four_zone_central_unit_writes_its_own_setpoint() -> None:
    """`#0#N` is not the 99-zone central unit: its setpoint is written to its own WHERE, once."""
    gateway = _gateway()
    zone = _climate(gateway, "#0#1", standalone=None, central=True)
    _replay(zone, [FOUR_ZONE_MANUAL_21])
    assert zone.hvac_mode == HVACMode.HEAT

    await zone.async_set_temperature(temperature=22.0)

    # The frame is whatever OWNd's builder gives for this WHERE (no capture of a 4-zone
    # central unit's setpoint write exists); the test fixes who is addressed and how often.
    expected = str(OWNHeatingCommand.set_central_temperature(where="#0#1", temperature=22.0, mode="heat"))
    assert _sent(gateway) == [expected]
    assert "#0#1" in expected
    assert zone._target_temperature == 22.0


async def test_a_general_probes_frame_is_not_a_report_of_the_central_unit() -> None:
    """WHERE `0` is "General probes (all probes)", not `#0`: it leaves what the central unit reported."""
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [MANUAL_18])
    assert central._central_reported_idle is False

    central._note_central_reported_state(OWNEvent.parse(GENERAL_OFF), True)

    assert central._central_reported_idle is False
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == [HEAT]  # running in the heating season: the season frame alone


async def test_an_off_report_with_a_setpoint_is_an_off_report() -> None:
    """BUILT frame: an off WHAT that carries a setpoint counts as "the central unit reported off"."""
    gateway = _gateway()
    central = _climate(gateway, "#0", standalone=None, central=True)
    _replay(central, [MANUAL_18])
    assert central._central_reported_idle is False

    _replay(central, [OFF_WITH_SETPOINT])

    assert central.hvac_mode == HVACMode.OFF
    assert central._central_reported_idle is True
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gateway) == [HEAT, RESTORE]  # off was reported: the last weekly program follows
