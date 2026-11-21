"""The silent-zone poll goes by the result, on every gateway (#466, #932).

Issue 466 was an F455, issue 932 an MH201: zones that showed no setpoint after a restart because
nothing asked for it and the thermostat pushed nothing. Whether a gateway answers the general
request (``*#4*0##``) or the zone status request (``*#4*Z##``) with the state of a zone is known
for the models in the request tables and unknown for any other. So the poll has no model list:
``SILENT_ZONE_GRACE`` seconds after the startup sweep, and after its own startup poll, a zone of
any gateway asks for what it has not reported, read by read, and a refused read ends it.

- A zone that reported its temperature, setpoint and mode sends nothing.
- The knob read (``*#4*Z*13##``) is sent when the zone is added (#466 / #902). The poll does not
  repeat a read that was answered; one that brought nothing is repeated once, as the last read of
  a zone that is still silent. Only the F455 keeps the first knob read for the poll
  (``KNOB_READ_IN_SILENT_ZONE_POLL``, #674).
- Actuator 1 (``*#4*Z#1*20##``) is not asked where the gateway drops that form of the actuator
  request (H4684 and MH200: ``*#4*1#1*20##`` "silent" in own-firmware-oracle thermo-actuator.tsv; the
  F453, F453AV, MH200N and MH202, which drop only the zone form ``*#4*1*20##``, put it on the bus),
  and a zone that misses nothing else asks for it only where its watch could not (the F455).
- A zone marked unresponsive (``poll_health``) is left alone.
- The poll starts after the zone's own poll on add, the zones are asked one after another, and
  every read waits for the bus to be quiet after the one before (the pacing of #886). A read the
  #886 guard dropped is sent once more, unless the zone's own answer arrived in the meantime.

Evidence: the F455 1.0.76 capture (``tests/test_f455_zone_polling.py``), the MH201 3.6.43 plant of
#932 (``tests/test_issue_932_mh201_silent_zone_poll.py``), and own-firmware-oracle (main
``3c1634b``), where all thirteen emulated gateways put ``*#4*Z*0##``, ``*#4*Z*12##``,
``*#4*Z*13##`` and ``*#4*Z*14##`` on the bus (verdict ``out``). For the F450, F453, F453AV, F459,
H4684, MH200 and MH200N the poll has not run on a physical plant; the F452 and the F458 are not in
the oracle.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import State
from OWNd.message import OWNCommand, OWNHeatingEvent, OWNMessage
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.gateway import KNOB_READ_IN_SILENT_ZONE_POLL, MyHOMEGatewayHandler
from custom_components.myhome.gateway_sessions import (
    CommandWorkerPool,
    _mismatched_who4_reply_zones,
)
from tests.test_f455_zone_polling import _handler, _queued, _ready, _spin, _zone

# The poll waits for the bus to be quiet after every read; the mock gateways answer at once.
pytestmark = pytest.mark.usefixtures("fast_bus_pacing")

TEMPERATURE = "*#4*2*0*0196##"
SETPOINT = "*#4*2*14*0200*3##"
MODE = "*4*1*2##"
KNOB = "*#4*2*13*00##"
# model: (asked when the zone is added, asked by the poll once the zone reported a temperature)
SCS_ONLY = {
    "F450": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F452": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F453": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F453AV": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F455": ([], ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##", "*#4*2*19##"]),
    "F458": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F459": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "H4684": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "BTicino H/L4684": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "MH200": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "MH200N": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "MH201": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
}
# model: whether the poll may ask for actuator 1 (the gateway does not drop the actuator form of the
# request; the MH202 drops the zone form and acknowledges *#4*1#1*20##, thermo-actuator.tsv "ack / out")
ZONE_STATUS = {"F454": True, "F460": True, "F461": True, "MH202": True, "MyHomeServer1": True, "Generic": True}


# ------------------------------------------------------------------ the gate


@pytest.mark.parametrize("model", sorted([*SCS_ONLY, *ZONE_STATUS]))
def test_every_gateway_runs_the_poll(hass, model) -> None:
    handler = _handler(hass, model)
    assert handler.supports_zone_status is (model in ZONE_STATUS)
    assert handler.silent_zone_poll_needed is True
    assert handler.knob_read_in_silent_zone_poll is (handler._model_key == "F455")


def test_the_only_table_left_is_the_f455_knob_guard() -> None:
    assert KNOB_READ_IN_SILENT_ZONE_POLL == {"F455"}


def test_a_passive_follower_polls_nothing(hass, monkeypatch) -> None:
    handler = _handler(hass, "MH200")
    monkeypatch.setattr(MyHOMEGatewayHandler, "is_follower", property(lambda self: True))
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: set()))
    assert handler.silent_zone_poll_needed is False
    assert handler.knob_read_in_silent_zone_poll is False
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: {4}))
    assert handler.silent_zone_poll_needed is True


async def test_a_passive_follower_s_zone_sends_nothing(hass, monkeypatch) -> None:
    handler = _handler(hass, "F455")
    _ready(handler)
    monkeypatch.setattr(MyHOMEGatewayHandler, "is_follower", property(lambda self: True))
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: set()))
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == []


def test_the_gateway_that_serves_who_4_decides_the_knob_and_the_actuator_read(hass, monkeypatch) -> None:
    """A primary whose WHO 4 goes through another gateway (delegation, failover) follows that one."""
    primary = _handler(hass, "F461")
    other = _handler(hass, "MH200")
    monkeypatch.setattr(primary, "_who4_target", lambda: other)
    assert primary.silent_zone_poll_needed is True
    assert primary.actuator_request_refused is False and primary.silent_zone_actuator_read_refused is True
    monkeypatch.setattr(primary, "_who4_target", lambda: primary)
    monkeypatch.setattr(other, "_who4_target", lambda: primary)
    assert other.actuator_request_refused is True and other.silent_zone_actuator_read_refused is False
    # The zone form and the actuator form are two refusals: only H4684 and MH200 drop both.
    for model, zone_form, actuator_form in (
        ("H4684", True, True),
        ("MH200", True, True),
        ("F453", True, False),
        ("F453AV", True, False),
        ("MH200N", True, False),
        ("MH202", True, False),
        ("F454", False, False),
    ):
        handler = _handler(hass, model)
        assert handler.actuator_request_refused is zone_form, model
        assert handler.silent_zone_actuator_read_refused is actuator_form, model
        assert handler.actuator_read_refused("2") is zone_form, model
        assert handler.actuator_read_refused("2#1") is actuator_form, model
    f455_reads = _handler(hass, "F455")
    assert f455_reads.actuator_read_refused("2") is True and f455_reads.actuator_read_refused("2#1") is False
    f455 = _handler(hass, "F455")
    monkeypatch.setattr(primary, "_who4_target", lambda: f455)
    assert primary.knob_read_in_silent_zone_poll is True


def test_a_model_corrected_in_band_changes_nothing_about_the_gate(hass) -> None:
    """The gate no longer depends on the model, so a late in-band correction cannot switch the poll off."""
    handler = _handler(hass, "MH200")
    handler.gateway.profile = get_gateway_profile("F454")
    assert handler.silent_zone_poll_needed is True
    handler.gateway.profile = get_gateway_profile("F455")
    assert handler.silent_zone_poll_needed is True and handler.knob_read_in_silent_zone_poll is True


# ------------------------------------------------------------------ gateways sent the zone status request


@pytest.mark.parametrize("model", sorted(ZONE_STATUS))
async def test_a_zone_that_answered_its_status_request_is_asked_nothing(hass, model) -> None:
    """The usual case: ``*#4*Z##`` brought the temperature, the setpoint and the mode."""
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    assert _queued(handler) == ["*#4*2##"]
    for frame in (TEMPERATURE, SETPOINT, MODE):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2##"]  # not the knob it left out, not an actuator its watch asked for


@pytest.mark.parametrize("model", sorted(ZONE_STATUS))
async def test_a_zone_whose_status_request_brought_nothing_asks_read_by_read(hass, model) -> None:
    """Whatever the reason (a gateway that drops ``*#4*Z##``, a lost answer), the result decides."""
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    await zone._poll_if_silent(grace=0)
    actuator = ["*#4*2#1*20##"] if ZONE_STATUS[model] else []
    assert _queued(handler) == ["*#4*2##", "*#4*2*0##", "*#4*2*13##", "*#4*2*14##", "*#4*2*12##", *actuator]


async def test_a_zone_that_refuses_the_first_read_is_asked_nothing_more(hass) -> None:
    handler = _handler(hass, "F461")
    refused = hass.loop.create_future()
    _ready(handler, side_effect=[None, refused])  # the status request on add, then the poll's first read
    zone = _zone(hass, handler)
    await zone.async_update()
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    refused.cancel()
    await task
    assert _queued(handler) == ["*#4*2##", "*#4*2*0##"]


def _acknowledged(hass, *, empty: bool) -> asyncio.Future:
    """What the sender resolves for an ACK: marked when the command session carried no frame."""
    written = hass.loop.create_future()
    if empty:
        written._empty_reply = True
    written.set_result(0.0)
    return written


async def test_the_sender_marks_an_acknowledgement_that_carried_no_frame(hass) -> None:
    """OWNd returns the collected frames for an answered request and ``True`` for a bare ACK."""
    handler = _handler(hass, "F455")
    handler.is_connected = True
    pool = CommandWorkerPool(handler)
    pool._event_session_ready.set()
    session = MagicMock()
    session.connect = AsyncMock(return_value={"Success": True})
    session.is_connected = True
    session.close = AsyncMock()
    session._stream_reader = object()
    session._stream_writer = object()
    session.last_error = None

    async def _send(message, is_status_request=False):
        return [OWNMessage.parse(SETPOINT)] if str(message) == "*#4*2*14##" else True

    session.send = AsyncMock(side_effect=_send)
    with patch("custom_components.myhome.gateway.OWNCommandSession", return_value=session):
        answered = await pool.send_status_request(OWNCommand("*#4*2*14##"))
        bare_ack = await pool.send_status_request(OWNCommand("*#4*7*14##"))
        command = await pool.send(OWNCommand("*1*1*11##"))
        await pool.send_buffer.put(None)
        await pool.sending_loop(0)
    assert answered.done() and not answered.cancelled() and not getattr(answered, "_empty_reply", False)
    assert bare_ack.done() and not bare_ack.cancelled() and bare_ack._empty_reply is True
    assert not getattr(command, "_empty_reply", False)  # a command has no answer to wait for


async def test_an_acknowledged_read_that_brought_nothing_ends_the_poll(hass, monkeypatch) -> None:
    """An ACK alone is not an answer: the F455 ACKs `*#4*0##` after 9 s and sends nothing."""
    monkeypatch.setattr("custom_components.myhome.climate.EMPTY_REPLY_SETTLE", 0)
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=[_acknowledged(hass, empty=False), _acknowledged(hass, empty=True)])
    zone = _zone(hass, handler)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##"]


async def test_an_answer_on_the_event_session_keeps_the_poll_going(hass, monkeypatch) -> None:
    """The command session carried no frame, but the zone answered while the read was out."""
    monkeypatch.setattr("custom_components.myhome.climate.EMPTY_REPLY_SETTLE", 0)
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler)

    async def _send(command):
        if str(command) == "*#4*2*0##":
            zone.handle_event(OWNHeatingEvent(TEMPERATURE))
        return _acknowledged(hass, empty=True)

    _ready(handler, side_effect=_send)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##"]  # *13 brought nothing in turn


async def test_an_answer_within_the_settle_time_keeps_the_poll_going(hass, monkeypatch) -> None:
    monkeypatch.setattr("custom_components.myhome.climate.EMPTY_REPLY_SETTLE", 0.01)
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler)

    async def _send(command):
        frame = {"*#4*2*0##": TEMPERATURE, "*#4*2*13##": KNOB, "*#4*2*14##": SETPOINT, "*#4*2*12##": MODE}.get(str(command))
        if frame is not None:
            hass.loop.call_later(0.001, zone.handle_event, OWNHeatingEvent(frame))
        return _acknowledged(hass, empty=True)

    _ready(handler, side_effect=_send)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


def _dropped_by_the_desync_guard(hass) -> asyncio.Future:
    """What the sender leaves of a read that collected only another zone's frames (#886)."""
    written = hass.loop.create_future()
    written._gateway_desync = True
    written.cancel()
    return written


async def test_a_read_that_collected_another_zone_s_frame_is_sent_once_more(hass) -> None:
    """issue_932/README.md: `*#4*3#1*20*0##` came a second time, inside the read that followed.

    Zone 3 only misses its actuator, so its one read goes out just before zone 4's first one; the
    second copy of its answer is collected by `*#4*4*0##`, which the guard drops. That is not an
    answer of zone 4: without a second try its poll would end there, with no setpoint. (Here zone
    4's own answer does not arrive either; when it does the read is not repeated, see below.)
    """
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=[_dropped_by_the_desync_guard(hass), *[_acknowledged(hass, empty=False)] * 5])
    zone = _zone(hass, handler, "4")
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4*0##", "*#4*4*0##", "*#4*4*13##", "*#4*4*14##", "*#4*4*12##", "*#4*4#1*20##", "*#4*4*19##"]


async def test_a_read_dropped_twice_ends_the_poll(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=[_dropped_by_the_desync_guard(hass), _dropped_by_the_desync_guard(hass)])
    zone = _zone(hass, handler, "4")
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4*0##", "*#4*4*0##"]


@pytest.mark.parametrize(
    ("read", "answer"),
    [
        ("*#4*4*0##", "*#4*4*0*0255##"),
        ("*#4*4*13##", "*#4*4*13*00##"),
        ("*#4*4*14##", "*#4*4*14*0215*3##"),
        ("*#4*4#1*20##", "*#4*4#1*20*0##"),
    ],
)
async def test_a_dropped_read_is_not_repeated_when_the_zone_s_answer_came_all_the_same(hass, read, answer) -> None:
    """The read went out on the bus, so the zone answers; only the command session lost the reply.

    On the F455 plant zone 4's `*#4*4#1*20*0##` was on the event session 0.1 s after the warning
    of the guard. The poll looks at the zone after the quiet time and asks again only for what
    is still missing.
    """
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler, "4")

    async def _send(command):
        if str(command) != read:
            return _acknowledged(hass, empty=False)
        zone.handle_event(OWNHeatingEvent(answer))  # on the event session
        return _dropped_by_the_desync_guard(hass)

    _ready(handler, side_effect=_send)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4*0##", "*#4*4*13##", "*#4*4*14##", "*#4*4*12##", "*#4*4#1*20##", "*#4*4*19##"]


async def test_a_dropped_setpoint_in_force_read_needs_the_setpoint_and_the_mode(hass) -> None:
    """`*12` is asked for the setpoint in force and for the season frame the F455 sends after it."""
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler, "4")
    answers = [["*#4*4*12*0215*3##"], ["*#4*4*12*0215*3##", "*4*0*4##"]]

    async def _send(command):
        if str(command) != "*#4*4*12##":
            return _acknowledged(hass, empty=False)
        for frame in answers.pop(0):
            zone.handle_event(OWNHeatingEvent(frame))
        return _dropped_by_the_desync_guard(hass) if answers else _acknowledged(hass, empty=False)

    _ready(handler, side_effect=_send)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*4*0##", "*#4*4*13##", "*#4*4*14##", "*#4*4*12##", "*#4*4*12##", "*#4*4#1*20##", "*#4*4*19##"]


async def test_the_bus_is_quiet_before_the_next_read_goes_out(hass) -> None:
    """Every read is followed by the wait of the paced startup polls (#886) before the next one.

    The state of an actuator came twice, 0.09 to 0.27 s apart, after one read on the F455 plant
    (issue_932/README.md): the second copy has to fall into this wait, not into the next read.
    """
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=lambda command: _acknowledged(hass, empty=False))
    zone = _zone(hass, handler)
    paced: list[tuple[str, list[str]]] = []
    gate = asyncio.Event()

    async def _pace(written, message):
        assert written.done()  # the reply is in before the quiet time starts
        paced.append((str(message), _queued(handler)))
        await gate.wait()

    handler.pace_startup_status_write = _pace
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    assert _queued(handler) == ["*#4*2*0##"] and not task.done()  # held back by the quiet time
    gate.set()
    await task
    reads = ["*#4*2*0##", "*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##", "*#4*2*19##"]
    assert paced == [(read, reads[: index + 1]) for index, read in enumerate(reads)]


async def test_the_quiet_time_is_the_gateway_s_bus_quiet_period(hass, monkeypatch) -> None:
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=lambda command: _acknowledged(hass, empty=False))
    zone = _zone(hass, handler)
    waits: list[float] = []

    async def _quiet(since):
        waits.append(since)

    monkeypatch.setattr(handler, "_wait_for_bus_quiet", _quiet)
    await zone._poll_if_silent(grace=0)
    assert len(waits) == len(_queued(handler)) == 6


async def test_a_request_a_zone_made_on_its_own_goes_out_before_the_next_read(hass) -> None:
    """On the F455 plant zone 2's actuator reported a fan coil, the zone asked for dimension 19
    through the gateway's paced sender, and that read was queued 1 ms before the next poll read:
    both had waited for the same quiet bus. The poll lets such a request through first."""
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler)

    async def _send(command):
        if str(command) == "*#4*2*13##":
            zone._ask_fan_coil_sides()  # what a fan coil report makes the zone do
        return _acknowledged(hass, empty=False)

    _ready(handler, side_effect=_send)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##", "*#4*2*19##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]
    assert handler._zone_request_task.done()


async def test_the_zones_are_asked_one_after_another(hass) -> None:
    """The poll holds the startup poll lock for all reads of a zone: no read of zone 4 between zone 3's."""
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=lambda command: _acknowledged(hass, empty=False))
    zones = [_zone(hass, handler, where) for where in ("3", "4", "2")]
    await asyncio.gather(*(zone._poll_if_silent(grace=0) for zone in zones))
    assert _queued(handler) == [
        f"*#4*{where}{read}##" for where in ("3", "4", "2") for read in ("*0", "*13", "*14", "*12", "#1*20", "*19")
    ]


async def test_a_gateway_double_without_the_startup_lock_is_asked_all_the_same(hass) -> None:
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=lambda command: _acknowledged(hass, empty=False))
    handler._who4_startup_poll_lock = None
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##", "*#4*2*19##"]


async def test_a_read_that_is_never_answered_ends_the_poll_without_the_quiet_time(hass, monkeypatch) -> None:
    """No reply at all within ``PACED_READ_TIMEOUT``: the poll ends, and the lock is free for the next zone."""
    monkeypatch.setattr("custom_components.myhome.climate.PACED_READ_TIMEOUT", 0.01)
    handler = _handler(hass, "F455")
    _ready(handler, side_effect=lambda command: hass.loop.create_future())
    handler.pace_startup_status_write = AsyncMock()
    zone = _zone(hass, handler)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##"]
    handler.pace_startup_status_write.assert_not_awaited()
    assert not handler._who4_startup_poll_lock.locked()


async def test_the_capture_s_second_copy_is_what_the_guard_drops() -> None:
    """The frames of 08:00:49Z: zone 3's actuator state twice, the second before zone 4's own answer."""
    capture = Path(__file__).parent / "fixtures" / "traces" / "issue_932" / "F455_who4_monitor_poll_2026-11-17T08-00.log"
    actuators = [line.split(" ")[2] for line in capture.read_text(encoding="utf-8").splitlines() if "#1*20*" in line]
    assert actuators == ["*#4*3#1*20*0##", "*#4*3#1*20*0##", "*#4*4#1*20*0##", "*#4*2#1*20*7##"]
    collected = [OWNMessage.parse(actuators[1])]
    assert _mismatched_who4_reply_zones("*#4*4#1*20##", collected) == {"3"}
    assert _mismatched_who4_reply_zones("*#4*4*0##", collected) == {"3"}
    assert _mismatched_who4_reply_zones("*#4*3#1*20##", collected) is None


def test_the_paced_capture_has_the_second_copy_before_the_next_answer() -> None:
    """The poll of 11:47:44Z, on the build with the quiet time (issue_932/README.md): the answers
    come zone after zone, zone 3's actuator state comes twice again, and the next frame follows
    the second copy by more than the quiet time of 0.75 s."""
    capture = (
        Path(__file__).parent / "fixtures" / "traces" / "issue_932" / "F455_who4_monitor_paced_poll_2026-11-17T11-47.log"
    )
    lines = [line.split(" ") for line in capture.read_text(encoding="utf-8").splitlines()]
    frames = [(datetime.fromisoformat(line[0]), line[2]) for line in lines]
    zones = [frame.split("*")[2][0] for _at, frame in frames if frame.startswith("*#4*")]
    assert zones == sorted(zones) and set(zones) == {"2", "3", "4"}
    copies = [at for at, frame in frames if frame == "*#4*3#1*20*0##"]
    assert len(copies) == 2
    following = min(at for at, _frame in frames if at > copies[1])
    assert (copies[1] - copies[0]).total_seconds() == 0.079
    assert (following - copies[1]).total_seconds() == 0.861


async def test_a_zone_marked_unresponsive_is_left_alone(hass) -> None:
    """Two status requests in a row without an answer or a frame: not asked again until the re-probe."""
    handler = _handler(hass, "MyHomeServer1")
    _ready(handler)
    zone = _zone(hass, handler)
    now = time.time()
    zone._poll_health.failed(now)
    assert zone._poll_health.failed(now) is True
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == []
    zone.handle_event(OWNHeatingEvent(TEMPERATURE))  # any frame of the zone clears the mark
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


async def test_the_poll_waits_for_the_startup_polls_still_queued(hass) -> None:
    """Startup polls go out one zone after another (#886); the poll must not ask a zone before its turn."""
    handler = _handler(hass, "MyHomeServer1")
    _ready(handler)
    zone = _zone(hass, handler)
    await handler._who4_startup_poll_lock.acquire()  # another zone's startup poll is running
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    assert _queued(handler) == [] and not task.done()
    for frame in (TEMPERATURE, SETPOINT, MODE):  # the zone's own answer arrives meanwhile
        zone.handle_event(OWNHeatingEvent(frame))
    zone._zone_status_request_sent = True
    handler._who4_startup_poll_lock.release()
    await task
    assert _queued(handler) == []


async def test_the_poll_waits_for_the_zone_s_own_poll_on_add(hass) -> None:
    """The grace is short; on a large plant the zone's own poll on add can still be waiting for its turn."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    turn = asyncio.Event()

    async def _own_poll_on_add() -> None:
        await turn.wait()
        for frame in (TEMPERATURE, KNOB, SETPOINT, MODE):  # what it brought
            zone.handle_event(OWNHeatingEvent(frame))

    zone._poll_on_add_task = hass.loop.create_task(_own_poll_on_add())
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    assert _queued(handler) == [] and not task.done()
    assert not handler._who4_startup_poll_lock.locked()  # the poll on add needs that lock itself
    turn.set()
    await task
    assert _queued(handler) == []


async def test_a_cancelled_poll_on_add_does_not_hold_the_poll_back(hass) -> None:
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    zone._poll_on_add_task = hass.loop.create_task(asyncio.Event().wait())
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    assert not task.done()
    zone._poll_on_add_task.cancel()
    await task
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


async def test_the_deferred_poll_on_add_is_kept_for_the_silent_zone_poll(hass, monkeypatch) -> None:
    """While the startup sweep is still to run the poll on add is a task; the silent-zone poll waits for it."""
    handler = _handler(hass, "MH201")
    assert handler.initial_discovery_pending is True
    zone = _zone(hass, handler)
    created: dict[str, MagicMock] = {}

    def _capture(coro, name=None, **_kwargs):
        coro.close()
        created[name] = MagicMock()
        return created[name]

    monkeypatch.setattr(hass, "async_create_background_task", _capture)
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    assert zone._poll_on_add is True and zone._poll_on_add_task is None
    await zone.async_added_to_hass()
    assert set(created) == {"myhome_climate.zone_2_poll_on_add", "myhome_climate.zone_2_silent_zone_poll"}
    assert zone._poll_on_add_task is created["myhome_climate.zone_2_poll_on_add"]


# ------------------------------------------------------------------ what is asked, and when


@pytest.mark.parametrize("model", sorted(SCS_ONLY))
async def test_the_reads_on_add_stay_and_the_poll_asks_the_rest(hass, model) -> None:
    """On add nothing changes; the poll asks for the setpoint, and for the knob only where it was not asked."""
    on_add, poll = SCS_ONLY[model]
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    assert _queued(handler) == on_add
    zone.handle_event(OWNHeatingEvent(TEMPERATURE))
    if on_add:
        zone.handle_event(OWNHeatingEvent(KNOB))  # the answer to the knob read sent on add
    assert zone.current_temperature == 19.6 and zone.target_temperature is None
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [*on_add, *poll]


@pytest.mark.parametrize("model", sorted(set(SCS_ONLY) - {"F455"}))
async def test_a_knob_read_that_brought_nothing_is_repeated_once_as_the_last_read(hass, model) -> None:
    """A refusal of the repeated read cannot keep the setpoint reads from going out: it comes after them."""
    on_add, poll = SCS_ONLY[model]
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    zone.handle_event(OWNHeatingEvent(TEMPERATURE))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [*on_add, *poll, "*#4*2*13##"]
    for frame in (SETPOINT, MODE):
        zone.handle_event(OWNHeatingEvent(frame))
    before = _queued(handler)
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == before  # the zone is no longer silent: the knob alone is not asked for


async def test_a_refused_setpoint_read_ends_the_poll_before_the_repeated_knob_read(hass) -> None:
    handler = _handler(hass, "MH200")
    refused = hass.loop.create_future()
    _ready(handler, side_effect=[None, None, refused])  # *0 and *13 on add, then the poll's *14
    zone = _zone(hass, handler)
    await zone.async_update()
    zone.handle_event(OWNHeatingEvent(TEMPERATURE))
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    refused.cancel()
    await task
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##", "*#4*2*14##"]


@pytest.mark.parametrize("model", ["F453", "MH200", "MH200N"])
async def test_a_zone_without_a_temperature_asks_for_it_first(hass, model) -> None:
    """The temperature read proves the probe: a zone that refuses it is asked nothing more."""
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    on_add = _queued(handler)
    assert on_add == SCS_ONLY[model][0]
    zone.handle_event(OWNHeatingEvent(KNOB))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler)[len(on_add) :] == ["*#4*2*0##", *SCS_ONLY[model][1]]


async def test_a_refused_read_ends_the_poll(hass) -> None:
    handler = _handler(hass, "MH200N")
    refused = hass.loop.create_future()
    _ready(handler, side_effect=[None, refused])  # the knob read on add, then the poll's first read
    zone = _zone(hass, handler)
    await zone.async_update()
    task = asyncio.ensure_future(zone._poll_if_silent(grace=0))
    await _spin()
    refused.cancel()
    await task
    assert _queued(handler) == ["*#4*2*13##", "*#4*2*0##"]


async def test_a_knob_that_answered_on_add_is_not_asked_again(hass) -> None:
    handler = _handler(hass, "F459")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    for frame in (TEMPERATURE, "*#4*2*13*00##"):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler)[2:] == ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


async def test_a_zone_that_reported_everything_by_itself_is_not_asked(hass) -> None:
    handler = _handler(hass, "MH200")
    _ready(handler)
    zone = _zone(hass, handler)
    for frame in (TEMPERATURE, "*#4*2*14*0200*3##", "*4*1*2##", "*#4*2*13*00##"):
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.target_temperature == 20.0 and zone.hvac_mode == "heat"
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == []


async def test_a_setpoint_pushed_by_the_thermostat_is_not_read_again(hass) -> None:
    handler = _handler(hass, "H4684")
    _ready(handler)
    zone = _zone(hass, handler)
    for frame in (TEMPERATURE, "*#4*2*14*0205*3##"):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert "*#4*2*14##" not in _queued(handler) and "*#4*2*0##" not in _queued(handler)


async def test_a_restored_setpoint_is_shown_at_once_and_still_read(hass) -> None:
    """After a restart the card has the saved target immediately; the poll then confirms it."""
    handler = _handler(hass, "H4684")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_restore_last_state(
        State("climate.zone_2", "heat", {"temperature": 20.5, "current_temperature": 19.6})
    )
    assert zone.target_temperature == 20.5
    await zone.async_update()
    assert _queued(handler) == ["*#4*2*0##", "*#4*2*13##"]
    for frame in (TEMPERATURE, KNOB):
        zone.handle_event(OWNHeatingEvent(frame))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler)[2:] == ["*#4*2*14##", "*#4*2*12##"]
    zone.handle_event(OWNHeatingEvent("*#4*2*14*0200*3##"))
    assert zone.target_temperature == 20.0


async def test_an_added_zone_schedules_the_poll(hass, monkeypatch) -> None:
    handler = _handler(hass, "MH200")
    handler._initial_discovery_done.set()
    zone = _zone(hass, handler)
    names: list[str] = []
    task = MagicMock()

    def _capture(coro, name=None, **_kwargs):
        names.append(name)
        coro.close()
        return task

    monkeypatch.setattr(hass, "async_create_background_task", _capture)
    zone._poll_on_add = False
    handler.watch_zone_actuators = MagicMock(return_value=lambda: None)
    await zone.async_added_to_hass()
    assert names == ["myhome_climate.zone_2_silent_zone_poll"]
    for remove in list(zone._on_remove or []):
        remove()
    task.cancel.assert_called_once()


@pytest.mark.parametrize(
    ("model", "where", "asked"),
    [
        # The actuator form, *#4*Z#N*20##: forwarded by the four gateways that drop only the zone form.
        ("MH202", "7#1", ["*#4*7#1*20##"]),
        ("F453", "7#1", ["*#4*7#1*20##"]),
        ("F453AV", "7#1", ["*#4*7#1*20##"]),
        ("MH200N", "7#1", ["*#4*7#1*20##"]),
        ("F455", "7#1", ["*#4*7#1*20##"]),
        ("H4684", "7#1", []),
        ("MH200", "7#1", []),
        # The zone form, *#4*Z*20##: dropped by all six, and refused at once by the F455.
        ("MH202", "7", []),
        ("MH200N", "7", []),
        ("H4684", "7", []),
        ("F455", "7", []),
        ("F454", "7", ["*#4*7*20##"]),
    ],
)
async def test_a_thermo_switch_reads_its_actuator_by_the_form_the_gateway_forwards(hass, model, where, asked) -> None:
    """``homeassistant.update_entity`` on a WHO 4 thermo switch: the read goes by the form of its WHERE."""
    from custom_components.myhome.switch import MyHOMEThermoSwitch

    handler = _handler(hass, model)
    _ready(handler)
    zone, _, actuator = where.partition("#")
    switch = MyHOMEThermoSwitch(
        hass=hass, name="Towel warmer", entity_name=None, icon=None, icon_on=None, device_id=f"4-{where}",
        where=where, zone=zone, actuator=actuator or None, interface=None, device_class=None,
        manufacturer="BTicino", model="F430", turn_on_command=None, turn_off_command=None, gateway=handler,
    )
    await switch.async_update()
    assert _queued(handler) == asked


@pytest.mark.parametrize(
    ("model", "sent"),
    [("MH202", ["*#4*7#1*20##"]), ("F453", ["*#4*7#1*20##"]), ("MH200N", ["*#4*7#1*20##"]), ("H4684", []), ("MH200", [])],
)
async def test_the_watch_of_an_actuator_is_sent_where_only_the_zone_form_is_dropped(hass, model, sent) -> None:
    from tests.test_f455_zone_polling import _drain

    handler = _handler(hass, model)
    handler._initial_discovery_done.set()
    handler.watch_zone_actuators("7")  # a climate zone: the zone form, dropped by these gateways
    handler.watch_zone_actuators("7#1")  # a thermo actuator switch
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _drain(handler) == sent
