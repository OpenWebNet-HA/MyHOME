"""A repeated "off" of an actuator after dimension 19, replayed from a physical F455 1.0.76 capture.

The plant (``tests/fixtures/traces/issue_932/README.md``): zone 4 has two actuators, and the fan
coil that cools it is on actuator 2. After a start with nothing stored the poll asked for actuator 1
(off) and then for dimension 19 (``*#4*4*19*8*0##``, the cooling fan at speed 3), and the zone
showed ``cooling``. 23.4 s later another client read actuator 1 through the F461 on the same bus.
Its answer, ``*#4*4#1*20*0##`` again, set the zone to ``idle`` for 3.17 s, until the answer to that
client's read of actuator 2 arrived (the states file, written while the build of that day ran).

What the replay pins: an "off" of an actuator that is already known to be off changes nothing
while dimension 19 says a side is active. The first report of an actuator still takes the lead.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from homeassistant.components.climate import HVACAction
from OWNd.message import OWNMessage

from custom_components.myhome.where_grammar import zone_number
from tests.test_f455_zone_polling import _handler, _ready
from tests.test_issue_932_mixed_plant_poll_replay import _entity

TRACES = Path(__file__).parent / "fixtures" / "traces" / "issue_932"
POLL = TRACES / "F455_who4_monitor_zone_status_read_2026-11-17T13-46.log"
READS = TRACES / "F455_who4_monitor_actuator_reads_2026-11-17T13-46.log"
STATES = TRACES / "F455_ha_states_zone_4_2026-11-17T13-46.jsonl"
SHOWN = ("hvac_action", "running_fan_speed", "actuators", "fan_mode", "season", "fan_coil_cooling", "fan_coil_heating")


def _frames(path: Path, count: int) -> list[tuple[datetime, str]]:
    rows = [re.match(r"^(\S+)Z EVT (\S+) ", line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert all(rows) and len(rows) == count
    return [(datetime.fromisoformat(row.group(1)), row.group(2)) for row in rows if row]


def _states() -> list[dict]:
    rows = [json.loads(line) for line in STATES.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 3 and {row["entity"] for row in rows} == {"climate.n4692fan_2"}
    return rows


def _of_zone_4(frames: list[tuple[datetime, str]]) -> list[str]:
    return [raw for _when, raw in frames if zone_number(str(OWNMessage.parse(raw).where)).split("#")[0] == "4"]


def _shown(zone) -> dict:
    return {"hvac_action": zone.hvac_action.value, **{key: zone.extra_state_attributes[key] for key in SHOWN[1:]}}


def test_the_capture_shows_the_zone_idle_while_the_fan_coil_ran() -> None:
    poll, reads = _frames(POLL, 29), _frames(READS, 4)
    assert _of_zone_4(poll)[-3:] == ["*#4*4#1*20*0##", "*#4*4*19*8*0##", "*#4*4*11*0##"]
    assert [raw for _when, raw in reads] == ["*#4*4#1*20*0##", "*#4*4#2*20*0##", "*#4*4#2*20*8##", "*#4*4*19*8*0##"]
    answered = next(when for when, raw in poll if raw == "*#4*4*19*8*0##")
    assert round((reads[0][0] - answered).total_seconds(), 3) == 23.418
    assert round((reads[2][0] - reads[1][0]).total_seconds(), 3) == 0.039  # one read, two frames

    states = _states()
    assert [(row["attributes"]["hvac_action"], row["attributes"]["actuators"]) for row in states] == [
        ("cooling", {"1": "off"}),
        ("idle", {"1": "off"}),
        ("cooling", {"1": "off", "2": "on"}),
    ]
    assert [row["attributes"]["running_fan_speed"] for row in states] == ["high", "off", "high"]
    changed = [datetime.fromisoformat(row["last_updated"]) for row in states]
    assert round((changed[1] - changed[0]).total_seconds(), 3) == 23.418
    assert round((changed[2] - changed[1]).total_seconds(), 2) == 3.17


async def test_the_replay_keeps_the_zone_cooling_through_the_repeated_off(hass) -> None:
    handler = _handler(hass, "F455")
    handler.request_zone_dimension = lambda where, dimension: None  # the fan speed read the zone makes itself
    _ready(handler)
    zone = _entity(hass, handler, "4", standalone=False, model="N4692FAN")
    before, _dip, after = ({key: row["attributes"][key] for key in SHOWN} for row in _states())

    for raw in _of_zone_4(_frames(POLL, 29)):
        zone.handle_event(OWNMessage.parse(raw))
    assert _shown(zone) == before
    assert zone.target_temperature == 21.5 and zone.current_temperature == 26.0

    reads = [raw for _when, raw in _frames(READS, 4)]
    zone.handle_event(OWNMessage.parse(reads[0]))  # actuator 1, off as before
    assert _shown(zone) == before  # the build of that day: idle, running_fan_speed off
    zone.handle_event(OWNMessage.parse(reads[1]))  # the first report of actuator 2 takes the lead, as before
    assert zone.hvac_action == HVACAction.IDLE
    zone.handle_event(OWNMessage.parse(reads[2]))  # 0.039 s later on the plant
    assert _shown(zone) == after
    zone.handle_event(OWNMessage.parse(reads[3]))
    assert _shown(zone) == after
    zone.handle_event(OWNMessage.parse(reads[0]))  # actuator 1 once more, with actuator 2 known
    assert _shown(zone) == after
