"""F455: the state of a zone with more than one actuator (review of the #932 pull request).

The F455 refuses the zone form of the actuator request (``*#4*Z*20##``) at once, so the
silent-zone poll asked for actuator 1 only. On a physical F455 1.0.76 plant zone 4 has a valve
on actuator 1 and a fan coil on actuator 2: ``*#4*4#1*20##`` answered ``*#4*4#1*20*0##`` (off)
while the fan coil ran, and nothing tells which actuators a zone has (``*#4*4#0*20##`` was NACKed
after 9 s, ``*#4*#0*20##`` at once, the read of an absent actuator ``*#4*2#2*20##`` after 8.9 s).

Two things replace a blind read of actuator 2, 3, ...:

- Dimension 19 (``*#4*Z*19##``) is the last read of the poll on a gateway that refuses the zone
  form. It carries the state of both sides of the zone, whatever the number of actuators. On the
  plant: ``*#4*4*19*8*0##`` (zone 4, fan coil on the cooling side at speed 3), ``*#4*3*19*0*0##``
  (zone 3, nothing active), ``*#4*2*19*5*7##`` (zone 2), each in about 0.1 s.
- The actuator numbers a zone reported are remembered across a restart (the keys of the saved
  ``actuators`` attribute, not the states), and the poll asks for each of them.

Dimension 19 goes after the actuator reads because the most recent of the two kinds of report
drives ``hvac_action``: an "off" of actuator 1 says nothing about a fan coil on actuator 2.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from homeassistant.components.climate import HVACAction
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import get_gateway_profile

from tests.test_f455_zone_polling import _handler, _queued, _ready, _sent, _zone

# The poll waits for the bus to be quiet after every read; the mock gateways answer at once.
pytestmark = pytest.mark.usefixtures("fast_bus_pacing")

REPORTED = ("*#4*4*0*0255##", "*#4*4*13*00##", "*#4*4*14*0215*3##", "*#4*4*12*0215*3##", "*4*0*4##")


def _saved(**attributes) -> State:
    """What Home Assistant saved for zone 4 before the restart."""
    return State("climate.zone_4", "cool", {"temperature": 21.5, "current_temperature": 25.5, **attributes})


def _reported(zone) -> None:
    """Temperature, knob, setpoint and season: all the zone can say without its actuators."""
    for frame in REPORTED:
        zone.handle_event(OWNHeatingEvent(frame))


# ------------------------------------------------------------------ the capture behind it


def test_capture_backs_the_dimension_19_read_on_an_f455() -> None:
    """2026-11-06, the same F455: zone 2 answers in 0.11 s; a zone without a probe NACKs after 9 s."""
    answered = _sent()["*#4*2*19##"]
    assert answered["ack"] is True and answered["replies"] == ["*#4*2*19*7*5##"] and answered["dt"] < 0.2


# ------------------------------------------------------------------ dimension 19 in the poll


async def test_dimension_19_is_the_last_read_on_an_f455(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    assert zone._silent_zone_reads() == ["*#4*4*0##", "*#4*4*13##", "*#4*4*14##", "*#4*4*12##", "*#4*4#1*20##", "*#4*4*19##"]


async def test_a_zone_that_reported_everything_else_is_still_asked_for_its_state(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    _reported(zone)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4#1*20##", "*#4*4*19##"]


@pytest.mark.parametrize("model", ["F454", "F461", "MH201", "MH200", "MyHomeServer1", "Generic"])
async def test_no_dimension_19_where_the_zone_form_is_not_refused(hass, model) -> None:
    """Elsewhere the zone is asked ``*#4*Z*20##`` when it is added, or the gateway drops every actuator read."""
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler, "4")
    assert not [read for read in zone._silent_zone_reads() if read.endswith("*19##")]


async def test_a_zone_whose_state_is_known_is_not_asked(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    _reported(zone)
    zone.handle_event(OWNHeatingEvent("*#4*4*19*0*0##"))
    assert zone._silent_zone_reads() == []


async def test_the_fan_coil_on_actuator_2_shows_after_actuator_1_said_off(hass) -> None:
    """The plant's zone 4: valve on actuator 1 (off), fan coil on actuator 2 (running, unknown to the zone)."""
    handler = _handler(hass, "F455")
    handler.request_zone_dimension = lambda where, dimension: None  # the fan speed read that follows
    zone = _zone(hass, handler, "4")
    answers = {"*#4*4#1*20##": "*#4*4#1*20*0##", "*#4*4*19##": "*#4*4*19*8*0##"}

    async def _send(command):
        if str(command) in answers:
            zone.handle_event(OWNHeatingEvent(answers[str(command)]))

    _ready(handler, side_effect=_send)
    _reported(zone)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4#1*20##", "*#4*4*19##"]
    assert zone.hvac_action == HVACAction.COOLING
    attributes = zone.extra_state_attributes
    assert attributes["running_fan_speed"] == "high" and attributes["fan_coil_cooling"] is True
    assert attributes["actuators"] == {"1": "off"}  # actuator 2 has not reported


async def test_a_zone_with_nothing_active_is_idle(hass) -> None:
    """The plant's zone 3: `*#4*3*19*0*0##`."""
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler, "3")
    answers = {"*#4*3#1*20##": "*#4*3#1*20*0##", "*#4*3*19##": "*#4*3*19*0*0##"}

    async def _send(command):
        if str(command) in answers:
            zone.handle_event(OWNHeatingEvent(answers[str(command)]))

    _ready(handler, side_effect=_send)
    for frame in ("*#4*3*0*0252##", "*#4*3*13*00##", "*#4*3*14*0215*3##", "*#4*3*12*0215*3##", "*4*0*3##"):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert zone.hvac_action == HVACAction.IDLE and zone.extra_state_attributes["actuators"] == {"1": "off"}
    assert "fan_mode" not in zone.extra_state_attributes


async def test_dimension_19_is_not_asked_twice(hass) -> None:
    """An actuator that reports a fan coil makes the zone ask for dimension 19 itself (``_ask_fan_coil_sides``)."""
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler, "2")
    asked: list[tuple[str, int]] = []
    handler.request_zone_dimension = lambda where, dimension: asked.append((where, dimension))

    async def _send(command):
        if str(command) == "*#4*2#1*20##":
            zone.handle_event(OWNHeatingEvent("*#4*2#1*20*7##"))  # a fan coil at speed 2

    _ready(handler, side_effect=_send)
    for frame in ("*#4*2*0*0239##", "*#4*2*13*00##", "*#4*2*14*0240*3##", "*#4*2*12*0240*3##", "*4*1*2##"):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert asked == [("2", 19)]
    assert _queued(handler) == ["*#4*2#1*20##"]


async def test_a_refused_actuator_read_ends_the_poll_before_dimension_19(hass) -> None:
    handler = _handler(hass, "F455")
    refused = hass.loop.create_future()
    refused.cancel()
    _ready(handler, side_effect=[refused])
    zone = _zone(hass, handler, "4")
    _reported(zone)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4#1*20##"]


# ------------------------------------------------------------------ remembered actuators


async def test_the_actuator_numbers_are_remembered_not_their_states(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    await zone.async_restore_last_state(_saved(actuators={"2": "on", "1": "off"}))
    assert zone._remembered_actuators == ("1", "2")
    assert zone._actuator_states == {} and "actuators" not in zone.extra_state_attributes
    assert zone.hvac_action is None


@pytest.mark.parametrize(
    ("saved", "remembered"),
    [
        ({"1": "off"}, ("1",)),
        ({"3": "on", "1": "off", "2": "off"}, ("1", "2", "3")),
        ({2: "on"}, ("2",)),
        ({"0": "on", "10": "on", "x": "on", "": "on", "02": "off"}, ("2",)),
        ({}, ()),
        (None, ()),
        ("1", ()),
        (["1", "2"], ()),
    ],
)
async def test_only_actuator_numbers_1_to_9_are_remembered(hass, saved, remembered) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    await zone.async_restore_last_state(_saved(actuators=saved))
    assert zone._remembered_actuators == remembered


async def test_a_restart_asks_for_every_actuator_the_zone_had(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    await zone.async_restore_last_state(_saved(actuators={"1": "off", "2": "on"}))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [
        "*#4*4*0##", "*#4*4*13##", "*#4*4*14##", "*#4*4*12##", "*#4*4#1*20##", "*#4*4#2*20##", "*#4*4*19##",
    ]


async def test_a_zone_with_one_actuator_is_never_asked_for_a_second_one(hass) -> None:
    """The F455 NACKs the read of an absent actuator only after 8.9 s."""
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "2")
    await zone.async_restore_last_state(State("climate.zone_2", "heat", {"temperature": 24.0, "actuators": {"1": "on"}}))
    reads = zone._silent_zone_reads()
    assert [read for read in reads if read.endswith("*20##")] == ["*#4*2#1*20##"]


async def test_an_actuator_that_reported_since_the_restart_is_not_asked_again(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    asked: list[tuple[str, int]] = []
    handler.request_zone_dimension = lambda where, dimension: asked.append((where, dimension))
    await zone.async_restore_last_state(_saved(actuators={"1": "off", "2": "on"}))
    _reported(zone)
    zone.handle_event(OWNHeatingEvent("*#4*4#2*20*8##"))  # a fan coil: the zone asks for dimension 19 itself
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4#1*20##"] and asked == [("4", 19)]


async def test_without_a_saved_state_actuator_1_is_asked_unless_one_reported(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    handler.request_zone_dimension = lambda where, dimension: None
    _reported(zone)
    assert zone._silent_zone_reads() == ["*#4*4#1*20##", "*#4*4*19##"]
    zone.handle_event(OWNHeatingEvent("*#4*4#2*20*0##"))
    assert zone._silent_zone_reads() == ["*#4*4*19##"]


async def test_the_remembered_actuators_are_asked_on_the_other_gateways_too(hass) -> None:
    """Where the poll asks for an actuator at all (a zone that is still silent), it asks for the ones it had."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler, "4")
    await zone.async_restore_last_state(_saved(actuators={"1": "off", "2": "on"}))
    reads = zone._silent_zone_reads()
    assert [read for read in reads if read.endswith("*20##")] == ["*#4*4#1*20##", "*#4*4#2*20##"]
    dropped = _handler(hass, "MH200")  # drops every actuator read
    _ready(dropped)
    other = _zone(hass, dropped, "4")
    await other.async_restore_last_state(_saved(actuators={"1": "off", "2": "on"}))
    assert not [read for read in other._silent_zone_reads() if read.endswith("*20##")]


async def test_behind_an_interface_the_actuator_number_comes_before_the_routing(hass) -> None:
    handler = _handler(hass, "F455")
    handler.gateway.profile = SimpleNamespace(model_name="F455", supports_interface_zone_requests=True)
    _ready(handler)
    zone = _zone(hass, handler, "4", interface="1")
    assert zone._interface_poll_refused is False
    await zone.async_restore_last_state(_saved(actuators={"1": "off", "2": "on"}))
    assert zone._silent_zone_reads()[-3:] == ["*#4*4#1#4#01*20##", "*#4*4#2#4#01*20##", "*#4*4#4#01*19##"]


# ------------------------------------------------------------------ a read dropped by the #886 guard


@pytest.mark.parametrize(
    ("read", "answer", "repeated"),
    [
        ("*#4*4#2*20##", "*#4*4#2*20*8##", False),
        ("*#4*4#2*20##", "*#4*4#1*20*0##", True),  # another actuator's frame is not its answer
        ("*#4*4*19##", "*#4*4*19*8*0##", False),
        ("*#4*4*19##", "*#4*4#2*20*8##", True),
    ],
)
async def test_a_dropped_read_is_repeated_only_if_its_own_answer_is_missing(hass, read, answer, repeated) -> None:
    handler = _handler(hass, "F455")
    handler.request_zone_dimension = lambda where, dimension: None  # the zone's own fan coil requests
    zone = _zone(hass, handler, "4")
    await zone.async_restore_last_state(_saved(actuators={"1": "off", "2": "on"}))
    dropped = [True]

    async def _send(command):
        frame = str(command)
        if frame == "*#4*4#1*20##":
            zone.handle_event(OWNHeatingEvent("*#4*4#1*20*0##"))
        if frame != read or not dropped:
            written = hass.loop.create_future()
            written.set_result(0.0)
            return written
        dropped.clear()
        zone.handle_event(OWNHeatingEvent(answer))
        written = hass.loop.create_future()
        written._gateway_desync = True
        written.cancel()
        return written

    _ready(handler, side_effect=_send)
    _reported(zone)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler).count(read) == (2 if repeated else 1)


def test_the_profile_of_the_capture_is_the_one_under_test() -> None:
    assert get_gateway_profile("F455").model_name == "F455"
