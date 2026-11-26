"""A zone switched off and on again before its actuator had stopped, 2026-11-19.

An actuator reports when it changes. The zone's off marks its actuators off, which is an
assumption until the actuator says so. Live (tests/fixtures/traces/zone_short_off_2026_11_19),
zone 2, a fan coil thermostat behind an F461 2.0.11 and an F455 1.0.76, the same with the zone
found on the bus and with ``myhome.yaml``:

* off for 1.2 s (``*4*303*2##`` at 16:29:12.872, ``*4*0*2##`` at 16:29:14.105): no
  ``*#4*2#1*20*...##`` followed either frame, the zone was shown idle, and another client's
  request for every actuator was answered ``*#4*2#1*20*8##`` 98.7 s later (fan speed 3, as
  before the off);
* off for 8.3 s: the actuator reported ``*#4*2#1*20*5##`` 3.1 s after the off and
  ``*#4*2#1*20*8##`` 4.9 s after the zone ran again.

So the actuators that were marked off without having said so are asked once the zone runs.
Every frame below is in a capture under tests/fixtures (``test_the_frames_this_file_rests_on``).
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode

from tests.test_climate_standalone_zone_2026_11_18 import _Plant, _replay, _zone

_FIXTURES = Path(__file__).parent / "fixtures"
SHORT_OFF = json.loads((_FIXTURES / "traces" / "zone_short_off_2026_11_19" / "frames.json").read_text(encoding="utf-8"))
FAN_COIL_ON, FAN_COIL_OFF = "*#4*2#1*20*8##", "*#4*2#1*20*5##"
ZONE_2_OFF, ZONE_2_COOLING = "*4*303*2##", "*4*0*2##"
ZONE_2_VALVE_STATUS = "*#4*2*19*8*5##"
F455_GENERIC = "*4*311*#2##"  # what an F455 adds after a status of a standalone thermostat
VALVE_ON, ZONE_3_OFF, ZONE_3_HEATING = "*#4*3#1*20*1##", "*4*303*3##", "*4*1*3##"


@pytest.fixture
def no_entity_base() -> Iterator[None]:
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _cooling_zone_2() -> tuple[Any, Any]:
    zone2 = _zone(_Plant(), "2", standalone=True)
    _replay(zone2, [ZONE_2_COOLING, FAN_COIL_ON])
    assert zone2.hvac_mode == HVACMode.COOL and zone2.hvac_action == HVACAction.COOLING
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    return zone2, ask


def test_the_frames_this_file_rests_on() -> None:
    captured = "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for folder in ("standalone_zone_2026_11_18", "who4_four_gateways_2026_11_18", "thermo_discovery_at_start_2026_11_18", "issue_932", "central_restore_3100_live_2026-11-12")
        for path in sorted((_FIXTURES / "traces" / folder).iterdir())
        if path.is_file()
    )
    for raw in (FAN_COIL_ON, FAN_COIL_OFF, ZONE_2_OFF, ZONE_2_COOLING, ZONE_2_VALVE_STATUS, F455_GENERIC, VALVE_ON,
                ZONE_3_OFF, ZONE_3_HEATING):
        assert raw in captured, raw


def test_a_zone_back_on_before_its_actuator_reported_asks_it(no_entity_base: None) -> None:
    zone2, ask = _cooling_zone_2()

    _replay(zone2, [ZONE_2_OFF])
    assert zone2.hvac_action == HVACAction.OFF and zone2.extra_state_attributes["actuators"] == {"1": "off"}
    ask.assert_not_called()  # nothing is asked while the zone is off

    _replay(zone2, [ZONE_2_COOLING])  # back on, and the fan coil has said nothing
    ask.assert_called_once_with("2", 20, again=True)
    assert zone2.hvac_action == HVACAction.IDLE  # until it answers

    _replay(zone2, [FAN_COIL_ON])  # its answer: it never stopped
    assert zone2.hvac_action == HVACAction.COOLING
    assert zone2.extra_state_attributes["actuators"] == {"1": "on"}
    _replay(zone2, [ZONE_2_COOLING])
    ask.assert_called_once()  # asked once


def test_an_actuator_that_reported_its_off_is_not_asked(no_entity_base: None) -> None:
    zone2, ask = _cooling_zone_2()
    _replay(zone2, [ZONE_2_OFF, FAN_COIL_OFF, ZONE_2_COOLING])  # off for long enough: it said so
    ask.assert_not_called()
    assert zone2.hvac_action == HVACAction.IDLE
    _replay(zone2, [FAN_COIL_ON])
    assert zone2.hvac_action == HVACAction.COOLING


def test_an_actuator_that_reported_its_off_is_not_asked_whatever_the_valve_status_said(no_entity_base: None) -> None:
    """Active by its own frame and by dimension 19: its own off settles it."""
    zone2 = _zone(_Plant(), "2", standalone=True)
    _replay(zone2, [ZONE_2_COOLING, ZONE_2_VALVE_STATUS, FAN_COIL_ON])
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(zone2, [ZONE_2_OFF, FAN_COIL_OFF, ZONE_2_COOLING])
    assert [call.args for call in ask.call_args_list if call.args[1] == 20] == []

    zone2 = _zone(_Plant(), "2", standalone=True)
    _replay(zone2, [ZONE_2_COOLING, ZONE_2_VALVE_STATUS, FAN_COIL_ON])
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(zone2, [ZONE_2_OFF, ZONE_2_COOLING])  # and with no report it is asked, once
    assert [call.args for call in ask.call_args_list if call.args[1] == 20] == [("2", 20)]


def test_a_zone_that_was_idle_when_switched_off_asks_nothing(no_entity_base: None) -> None:
    zone2 = _zone(_Plant(), "2", standalone=True)
    _replay(zone2, [ZONE_2_COOLING, FAN_COIL_OFF])
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(zone2, [ZONE_2_OFF, ZONE_2_COOLING])
    ask.assert_not_called()  # no actuator was marked off that had been on
    assert zone2.hvac_action == HVACAction.IDLE


def test_the_zones_valve_status_replaces_the_assumption(no_entity_base: None) -> None:
    zone2, ask = _cooling_zone_2()
    _replay(zone2, [ZONE_2_OFF, ZONE_2_VALVE_STATUS, ZONE_2_COOLING])  # dimension 19 came meanwhile
    assert [call.args for call in ask.call_args_list if call.args[1] == 20] == []  # (the fan speed is asked, 11)


def test_a_zone_known_active_from_its_valve_status_asks_too(no_entity_base: None) -> None:
    zone2 = _zone(_Plant(), "2", standalone=True)
    _replay(zone2, [ZONE_2_COOLING, ZONE_2_VALVE_STATUS])  # active by dimension 19, no actuator frame
    assert zone2.hvac_action == HVACAction.COOLING
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(zone2, [ZONE_2_OFF, ZONE_2_COOLING])
    ask.assert_called_once_with("2", 20, again=True)


def test_an_f455s_generic_frame_for_a_zone_that_is_off_asks_nothing(no_entity_base: None) -> None:
    """The zone's own frame says off; the gateway's ``*4*311*#2##`` is not the zone running."""
    zone2, ask = _cooling_zone_2()
    _replay(zone2, [ZONE_2_OFF, F455_GENERIC])
    ask.assert_not_called()
    _replay(zone2, [ZONE_2_COOLING])
    ask.assert_called_once_with("2", 20, again=True)


def test_a_zone_of_the_central_unit_back_on_asks_its_valve(no_entity_base: None) -> None:
    zone3 = _zone(_Plant(), "3")
    _replay(zone3, [ZONE_3_HEATING, VALVE_ON])
    assert zone3.hvac_action == HVACAction.HEATING
    ask = zone3._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(zone3, [ZONE_3_OFF, ZONE_3_HEATING])
    ask.assert_called_once_with("3", 20, again=True)
    _replay(zone3, [VALVE_ON])
    assert zone3.hvac_action == HVACAction.HEATING


def test_the_central_units_off_and_a_standalone_finding_ask_once(no_entity_base: None) -> None:
    """The ask of a zone found standalone after the central unit's off covers the same actuators."""
    zone2 = _zone(_Plant(), "2")
    _replay(zone2, [FAN_COIL_ON, ZONE_2_COOLING])
    zone2._handle_central_mode_update(HVACMode.OFF)
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    zone2._mark_standalone_detected()
    ask.assert_called_once_with("2", 20, again=True)
    _replay(zone2, [ZONE_2_COOLING])
    ask.assert_called_once()  # not a second time


# ------------------------------------------------- the captures replayed


def _received(trace: str) -> list[str]:
    return [raw for _time, direction, raw in SHORT_OFF[trace] if direction == "rx"]


def _asked_for_actuators(ask: Any) -> list[tuple[Any, ...]]:
    return [call.args for call in ask.call_args_list if call.args[1] == 20]


def test_the_capture_of_the_short_off_replayed(no_entity_base: None) -> None:
    """Off for 1.2 s, taken before the change: the zone now asks, and the fan coil's 8 is what it had."""
    frames = _received("f461_zone2_off_1s")
    back_on = frames.index(ZONE_2_COOLING)
    assert ZONE_2_OFF in frames[:back_on]
    first_report = next(index for index, raw in enumerate(frames) if raw.startswith("*#4*2#1*"))
    assert first_report > back_on and frames[first_report] == FAN_COIL_ON  # 98.7 s later, another client's request
    times = {raw: time for time, _direction, raw in reversed(SHORT_OFF["f461_zone2_off_1s"])}  # first occurrence
    assert (times[ZONE_2_COOLING], times[FAN_COIL_ON]) == ("16:29:14.105", "16:30:52.848")

    zone2, ask = _cooling_zone_2()
    _replay(zone2, frames[: back_on + 1])
    assert _asked_for_actuators(ask) == [("2", 20)] and ask.call_args_list[-1].kwargs == {"again": True}
    _replay(zone2, frames[back_on + 1 : first_report])
    assert zone2.hvac_mode == HVACMode.COOL and zone2.hvac_action == HVACAction.IDLE  # nothing came by itself
    _replay(zone2, frames[first_report:])
    assert zone2.hvac_action == HVACAction.COOLING
    assert _asked_for_actuators(ask) == [("2", 20)]  # once


def test_the_capture_of_the_longer_off_replayed(no_entity_base: None) -> None:
    """Off for 8.3 s: the fan coil says off and on itself, and nothing is asked."""
    frames = _received("f461_zone2_off_8s")
    assert [raw for raw in frames if raw in (ZONE_2_OFF, ZONE_2_COOLING, FAN_COIL_OFF, FAN_COIL_ON)] == [
        ZONE_2_OFF, ZONE_2_OFF, FAN_COIL_OFF, ZONE_2_COOLING, ZONE_2_COOLING, ZONE_2_COOLING, FAN_COIL_ON, FAN_COIL_ON,
    ]
    zone2, ask = _cooling_zone_2()
    _replay(zone2, frames)
    assert _asked_for_actuators(ask) == []
    assert zone2.hvac_action == HVACAction.COOLING


def test_the_capture_where_the_actuator_reported_replayed(no_entity_base: None) -> None:
    """Off for 2.5 s and the fan coil said its off in time: nothing is asked, it says its on itself."""
    rows = SHORT_OFF["f461_zone2_off_2s_actuator_reported"]
    frames = [raw for _time, direction, raw in rows if direction == "rx"]
    assert frames.index(ZONE_2_OFF) < frames.index(FAN_COIL_OFF) < frames.index(ZONE_2_COOLING) < frames.index(FAN_COIL_ON)
    zone2 = _zone(_Plant(), "2", standalone=True)
    _replay(zone2, [ZONE_2_COOLING, ZONE_2_VALVE_STATUS, FAN_COIL_ON])  # as the zone was: both had said active
    ask = zone2._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(zone2, frames[: frames.index(FAN_COIL_ON)])
    assert _asked_for_actuators(ask) == [] and zone2.hvac_action == HVACAction.IDLE
    _replay(zone2, frames[frames.index(FAN_COIL_ON) :])
    assert zone2.hvac_action == HVACAction.COOLING


@pytest.mark.parametrize(
    ("trace", "zone", "running", "actuator_on", "action"),
    [
        ("f461_zone2_off_2s_asked", "2", ZONE_2_COOLING, FAN_COIL_ON, HVACAction.COOLING),
        ("f455_zone2_off_2s_asked", "2", ZONE_2_COOLING, FAN_COIL_ON, HVACAction.COOLING),
        ("myhomeserver1_zone53_off_2s_asked", "53", "*4*1*53##", "*#4*53#1*20*1##", HVACAction.HEATING),
        ("g3488_zone53_off_2s_asked", "53", "*4*1*53##", "*#4*53#1*20*1##", HVACAction.HEATING),
    ],
)
def test_the_captures_with_the_request_replayed(
    no_entity_base: None, trace: str, zone: str, running: str, actuator_on: str, action: HVACAction
) -> None:
    """Taken with the change, on four gateways: the instance asked every actuator after the zone's on."""
    rows = SHORT_OFF[trace]
    sent = [raw for _time, direction, raw in rows if direction == "tx"]
    assert "*#4*0#0*20##" in sent  # what ``request_zone_dimension`` becomes on these four gateways
    frames = _received(trace)
    assert frames.index(f"*4*303*{zone}##") < frames.index(running) < frames.index(actuator_on)

    entity = _zone(_Plant(), zone, standalone=True)
    _replay(entity, [running, actuator_on])
    assert entity.hvac_action == action
    ask = entity._gateway_handler.request_zone_dimension
    ask.reset_mock()
    _replay(entity, frames)
    assert _asked_for_actuators(ask) == [(zone, 20)]
    assert entity.hvac_action == action and entity.extra_state_attributes["actuators"] == {"1": "on"}
