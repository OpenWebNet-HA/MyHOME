"""Replay of a start with no climate entity: live F461 2.0.11 and F455 1.0.76, 2026-11-18 16:00.

tests/fixtures/traces/thermo_discovery_at_start_2026_11_18: both Home Assistant instances started with an empty climate
registry and no ``myhome.yaml`` on a plant with a 3550 (zones 3 and 4) and a standalone zone 2. The event session of
each is replayed through the platform: the central unit and the three zones are found, zone 3 (plain valves only, #785)
not before its thermostat answers the temperature read.
"""

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant

from tests.test_issue_944_actuator_answer_not_central import _plant, _receive

FIXTURE = Path(__file__).parent / "fixtures" / "traces" / "thermo_discovery_at_start_2026_11_18"
FRAMES = json.loads((FIXTURE / "frames.json").read_text(encoding="utf-8"))
STARTS = ["f461_blank_start", "f455_blank_start"]


def _tx(trace: str) -> list[str]:
    return [raw for _t, direction, raw in FRAMES[trace] if direction == "tx"]


def _rx(trace: str) -> list[str]:
    return [raw for _t, direction, raw in FRAMES[trace] if direction == "rx"]


def test_what_each_instance_asked_at_start() -> None:
    f461, f455 = _tx("f461_blank_start"), _tx("f455_blank_start")
    assert f461[:3] == ["*#4*#0##", "*#4*0#0*20##", "*#4*3*0##"]
    assert f455[:2] == ["*#4*0#0*20##", "*#4*3*0##"]
    assert "*#4*0##" not in f461 + f455  # the general request both firmwares drop
    assert "*#4*#0*20##" not in f461 + f455  # the form 2.0.0 sent
    # Zone 2's season, then the confirmation read ten seconds later, on both.
    for trace in STARTS:
        rows = FRAMES[trace]
        first_season = next(t for t, direction, raw in rows if direction == "rx" and raw == "*4*0*2##")
        read = next(t for t, direction, raw in rows if direction == "tx" and raw == "*#4*2*12##")
        assert first_season < read


def test_the_confirmation_read_is_answered_with_the_season_frame() -> None:
    """WHO 4 document 1.5.3, on an F461, an F455 and a MyHomeServer1."""
    assert [raw for _t, _d, raw in FRAMES["f461_dim12_reads"]] == [
        "*#4*2*12##", "*#4*2*12*0200*3##", "*4*0*2##",
        "*#4*3*12##", "*#4*3*12*0265*3##", "*4*1*3##",
    ]
    assert [raw for _t, _d, raw in FRAMES["f455_dim12_reads"]] == [
        "*#4*2*12##", "*#4*2*12*0200*3##", "*4*0*2##",
        "*#4*3*12##", "*#4*3*12*0265*3##", "*4*1*3##",
    ]
    # The MyHomeServer1 sends each frame of the reply twice.
    assert sorted(set(_rx("myhomeserver1_dim12_read"))) == ["*#4*70*12*0230*3##", "*4*1*70##"]


@pytest.mark.parametrize("trace", STARTS)
async def test_the_zones_are_found_from_the_start_up_answers(hass: HomeAssistant, trace: str) -> None:
    handler, zones = await _plant(hass)
    frames = _rx(trace)
    answer = frames.index("*#4*3*0*0260##")  # zone 3's thermostat answers *#4*3*0##

    await _receive(hass, handler, frames[:answer])
    found = {zone._where for zone in zones}
    assert {"#0", "4"} <= found  # the 3550 by its status, zone 4 by its two actuator frames (#966: zone 2 awaits its 2nd frame)
    assert "3" not in found  # plain valves only: held, the address may have no thermostat (#785)
    assert handler.has_central_unit is True
    assert handler.central_season == "heating"  # *4*102*#0##

    await _receive(hass, handler, frames[answer:])
    by_where = {zone._where: zone for zone in zones}
    assert sorted(by_where) == ["#0", "2", "3", "4"]
    assert by_where["3"].extra_state_attributes["actuators"]  # the held valve frames reached the zone
    assert by_where["2"]._own_season == "conditioning"  # *4*0*2##
    assert by_where["3"]._own_season == by_where["4"]._own_season == "heating"
    assert by_where["3"]._standalone is False and by_where["4"]._standalone is False  # zones of the 3550
