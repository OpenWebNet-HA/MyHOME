"""#944: an actuator status with WHERE ``#0`` is not a central unit.

Plant of the report: an F461 2.0.11, seven thermostats and four zone-0 actuators, no central
unit. MyHOME 2.0.0 asks the actuators of every zone with ``*#4*#0*20##`` on an F461; this one
answered with the actuator frames of its zones and, three times in the first burst,
``*#4*#0*20*1##``. Every WHO 4 frame with WHERE ``#0`` was taken as a central unit, so the
discovered zones (no ``standalone`` of their own) became zones of a central unit: the
setpoint went out as ``*#4*#6*#14*0225*3##``, which nothing answered, and the mode list of a
zone in heating lost ``cool``.

``ANSWER_BURST`` is the first answer of the diagnostics attached to the issue
(``bus_monitor.recent_frames``, 21:04:12.168Z to 21:04:13.208Z), ``ZONE_6_POLL`` the answer of
zone 6 to ``*#4*6##`` in the same file.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNEvent, OWNHeatingEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import MyHOMEClimate, async_setup_entry
from custom_components.myhome.const import DOMAIN
from custom_components.myhome.where_grammar import is_central_unit_report
from tests.conftest import attach_runtime
from tests.test_f455_zone_polling import MAC, _handler

ALL_ZONE_REQUEST = "*#4*#0*20##"
ANSWER_BURST = [
    "*#4*6#2*20*5##",
    "*#4*6#1*20*1##",
    "*#4*#0*20*1##",
    "*#4*7#1*20*0##",
    "*#4*1#1*20*0##",
    "*#4*2#1*20*0##",
    "*#4*3#1*20*1##",
    "*#4*4#1*20*0##",
    "*#4*#0*20*1##",
    "*#4*6#3*20*5##",
    "*#4*0#4*20*1##",
    "*#4*0#3*20*0##",
    "*#4*1#2*20*5##",
    "*#4*5#1*20*0##",
    "*#4*0#1*20*1##",
    "*#4*0#2*20*0##",
    "*#4*3#2*20*5##",
    "*#4*2#2*20*5##",
    "*#4*4#2*20*5##",
    "*#4*5#2*20*5##",
    "*#4*#0*20*1##",
]
ZONE_6_POLL = ["*#4*6*0*0228##", "*#4*6*12*0230*3##", "*4*1*6##", "*#4*6*14*0230*3##", "*#4*6*13*00##"]


async def _plant(hass: HomeAssistant) -> tuple[Any, list[MyHOMEClimate]]:
    """An F461 entry with no climate configured: every zone comes from the bus."""
    entry = MockConfigEntry(domain=DOMAIN, data={"mac": MAC}, unique_id=MAC)
    entry.add_to_hass(hass)
    handler = _handler(hass, "F461")
    handler.send = AsyncMock()
    handler.send_status_request = AsyncMock()
    runtime = attach_runtime(hass, entry, MAC, handler)
    runtime.platforms["climate"] = {}
    zones: list[MyHOMEClimate] = []
    await async_setup_entry(hass, entry, zones.extend)
    return handler, zones


async def _receive(hass: HomeAssistant, handler: Any, frames: list[str]) -> None:
    """Frames of the event session, through the gateway's own message processing."""
    for raw in frames:
        await handler._event_dispatcher.process_message(OWNHeatingEvent(raw))
    await hass.async_block_till_done()


def _sent(handler: Any) -> list[str]:
    return [str(call.args[0]) for call in handler.send.await_args_list]


@pytest.mark.parametrize("raw", sorted({ALL_ZONE_REQUEST, *ANSWER_BURST}))
def test_no_frame_of_the_actuator_answer_shows_a_central_unit(raw: str) -> None:
    assert is_central_unit_report(OWNEvent.parse(raw)) is False


@pytest.mark.parametrize(
    "raw",
    [
        "*4*210#0240*#0##",  # the 3550's own mode (central_flags_f455_poll_2026_11_09)
        "*4*21*#0##",  # remote control enabled
        "*4*303*#0##",  # off
        "*#4*#0*30*12*10*2026##",  # its date (f461_live_2026-10-23)
        "*4*210*#4##",  # zone 4 of the 3550, manual cooling
        "*#4*#4*14*0240*2##",  # the set point the 3550 gives its zone 4
    ],
)
def test_a_report_of_the_central_unit_still_shows_it(raw: str) -> None:
    assert is_central_unit_report(OWNEvent.parse(raw)) is True


async def test_a_discovered_zone_stays_standalone_after_the_actuator_answer(hass: HomeAssistant) -> None:
    handler, zones = await _plant(hass)
    await _receive(hass, handler, [*ANSWER_BURST, *ZONE_6_POLL])

    assert sorted(zone._where for zone in zones) == ["1", "2", "3", "4", "5", "6"]  # no "#0", no "0"
    zone = next(zone for zone in zones if zone._where == "6")
    zone.hass = hass
    assert handler.has_central_unit is False
    assert zone._standalone is True
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.hvac_modes == [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]

    handler.send.reset_mock()
    await zone.async_set_temperature(temperature=22.5)
    assert _sent(handler) == ["*#4*6*#14*0225*3##"]


async def test_a_central_unit_seen_later_still_takes_the_zone(hass: HomeAssistant) -> None:
    handler, zones = await _plant(hass)
    await _receive(hass, handler, [*ANSWER_BURST, *ZONE_6_POLL, "*4*110*#6##"])

    zone = next(zone for zone in zones if zone._where == "6")
    zone.hass = hass
    assert handler.has_central_unit is True
    assert zone._standalone is False

    handler.send.reset_mock()
    await zone.async_set_temperature(temperature=22.5)
    assert _sent(handler) == ["*#4*#6*#14*0225*3##"]
