"""#932: the silent-zone poll on a mixed plant, replayed from a physical F455 1.0.76 capture.

The plant (``tests/fixtures/traces/issue_932/README.md``): a 3550 central unit in cooling with
zones 3 and 4 under it, and zone 2, a standalone thermostat with its own season (heating). The
capture is the WHO 4 traffic of one Home Assistant start with nothing stored for the four climate
entities, 2026-11-17: the central unit's answer to the status request sent when it was added, one
temperature report of zone 2, and 120 s after the startup sweep (the grace of the build that ran
then) the answers to the reads of the silent-zone poll.

What the replay pins:

- the zones under the central unit take their mode from it before they have said anything, and
  after their own season frame they offer only what the central unit's season allows;
- the standalone zone keeps its own season and offers both;
- each zone asks only for what it has not reported, and ends with what the plant showed.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from OWNd.message import OWNMessage

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.where_grammar import zone_number
from tests.test_f455_zone_polling import _handler

CAPTURE = (
    Path(__file__).parent / "fixtures" / "traces" / "issue_932" / "F455_who4_monitor_start_nothing_stored_2026-11-17.log"
)
POLL_STARTED = "2026-11-17T08:33:53"  # the reads were queued at 08:33:53.405Z


def _frames() -> list[tuple[str, str]]:
    rows = [re.match(r"^(\S+) EVT (\S+) ", line) for line in CAPTURE.read_text(encoding="utf-8").splitlines()]
    assert all(rows) and len(rows) == 30
    return [(row.group(1), row.group(2)) for row in rows if row]


def _entity(hass, handler, where: str, *, standalone: bool, model: str) -> MyHOMEClimate:
    """A zone of the capture's ``myhome`` configuration (heat and cool, no fan, no humidity)."""
    entity = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        interface=None,
        heating=True,
        cooling=True,
        fan=False,
        standalone=standalone,
        central=False,
        manufacturer="BTicino",
        model=model,
        gateway=handler,
    )
    entity.hass = hass
    entity.entity_id = f"climate.zone_{zone_number(where)}"
    entity.async_write_ha_state = MagicMock()
    return entity


async def test_central_unit_zones_and_a_standalone_zone_after_a_start_with_nothing_stored(hass) -> None:
    handler = _handler(hass, "F455")
    plant = {
        "0": _entity(hass, handler, "#0", standalone=False, model="3550"),
        "2": _entity(hass, handler, "2", standalone=True, model="Heating Zone"),
        "3": _entity(hass, handler, "3", standalone=False, model="N4692"),
        "4": _entity(hass, handler, "4", standalone=False, model="N4692FAN"),
    }
    central, standalone, under_central = plant["0"], plant["2"], (plant["3"], plant["4"])
    assert central._central is True and handler.has_central_unit is True
    for zone in under_central:  # what a zone under a central unit does when it is added
        async_dispatcher_connect(hass, f"myhome_central_mode_{handler.mac}", zone._handle_central_mode_update)

    def _replay(frames: list[tuple[str, str]]) -> None:
        for _when, raw in frames:
            message = OWNMessage.parse(raw)
            plant[zone_number(str(message.where)).split("#")[0]].handle_event(message)

    frames = _frames()
    before = [frame for frame in frames if frame[0] < POLL_STARTED]
    assert [raw for _when, raw in before] == [
        "*4*210#0215*#0##",
        "*4*21*#0##",
        "*4*31*#0##",
        "*4*210#0215*#0##",
        "*4*21*#0##",
        "*4*31*#0##",
        "*#4*2*0*0235##",
    ]
    _replay(before)
    await hass.async_block_till_done()

    assert central.hvac_mode == HVACMode.COOL and central.target_temperature == 21.5
    for zone in under_central:  # the central unit's season, nothing of their own yet
        assert zone.hvac_mode == HVACMode.COOL
        assert zone.target_temperature is None and zone.current_temperature is None
    assert standalone.hvac_mode is None and standalone.target_temperature is None
    assert standalone.current_temperature == 23.5

    # The log of that start: "asking ['*#4*2*13##', '*#4*2*14##', '*#4*2*12##', '*#4*2#1*20##']" and,
    # for zones 3 and 4, the same reads after their temperature. The build of that start did not
    # ask for dimension 19 yet; it is the last read now.
    assert standalone._silent_zone_reads() == ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##", "*#4*2*19##"]
    for zone, number in zip(under_central, ("3", "4"), strict=True):
        assert zone._silent_zone_reads() == [
            f"*#4*{number}*0##",
            f"*#4*{number}*13##",
            f"*#4*{number}*14##",
            f"*#4*{number}*12##",
            f"*#4*{number}#1*20##",
            f"*#4*{number}*19##",
        ]

    _replay([frame for frame in frames if frame[0] >= POLL_STARTED])
    await hass.async_block_till_done()

    # Zone 2, standalone: its own season, heating, while the central unit is in cooling.
    assert standalone.hvac_mode == HVACMode.HEAT and standalone.target_temperature == 24.0
    assert standalone.hvac_action == HVACAction.HEATING
    assert standalone.hvac_modes == [HVACMode.OFF, HVACMode.HEAT, HVACMode.COOL]
    assert standalone.extra_state_attributes["actuators"] == {"1": "on"}
    # Zones 3 and 4, under the 3550: cooling at the central unit's setpoint, and no ``heat`` to pick.
    for zone, temperature in zip(under_central, (25.2, 25.3), strict=True):
        assert zone.hvac_mode == HVACMode.COOL and zone.target_temperature == 21.5
        assert zone.current_temperature == temperature
        assert zone.hvac_action == HVACAction.IDLE
        assert zone.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.COOL]
        assert zone.extra_state_attributes["actuators"] == {"1": "off"}
    # Nothing left to ask, but for the dimension 19 read that start did not send. Zone 2 has its
    # answer in the capture (`*#4*2*19*5*7##`, asked because its actuator reported a fan coil).
    assert standalone._silent_zone_reads() == []
    for zone, number in zip(under_central, ("3", "4"), strict=True):
        assert zone._silent_zone_reads() == [f"*#4*{number}*19##"]
