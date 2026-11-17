"""The silent-zone poll goes by the result, on every gateway (#466, #932).

Issue 466 was an F455, issue 932 an MH201: zones that showed no setpoint after a restart because
nothing asked for it and the thermostat pushed nothing. Whether a gateway answers the general
request (``*#4*0##``) or the zone status request (``*#4*Z##``) with the state of a zone is known
for the models in the request tables and unknown for any other. So the poll has no model list:
``SILENT_ZONE_GRACE`` seconds after the startup sweep, and after its own startup poll, a zone of
any gateway asks for what it has not reported, read by read, and a refused read ends it.

- A zone that reported its temperature, setpoint and mode sends nothing.
- The knob read (``*#4*Z*13##``) is sent when the zone is added (#466 / #902) and the poll does
  not repeat it. Only the F455 keeps it for the poll (``KNOB_READ_IN_SILENT_ZONE_POLL``, #674).
- Actuator 1 (``*#4*Z#1*20##``) is not asked where the gateway drops the actuator request, and a
  zone that misses nothing else asks for it only where its watch could not (the F455).
- A zone marked unresponsive (``poll_health``) is left alone.

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
from unittest.mock import MagicMock

import pytest
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.gateway import KNOB_READ_IN_SILENT_ZONE_POLL, MyHOMEGatewayHandler
from tests.test_f455_zone_polling import _handler, _queued, _ready, _spin, _zone

TEMPERATURE = "*#4*2*0*0196##"
SETPOINT = "*#4*2*14*0200*3##"
MODE = "*4*1*2##"
KNOB = "*#4*2*13*00##"
# model: (asked when the zone is added, asked by the poll once the zone reported a temperature)
SCS_ONLY = {
    "F450": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F452": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F453": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "F453AV": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "F455": ([], ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F458": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "F459": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
    "H4684": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "BTicino H/L4684": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "MH200": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "MH200N": (["*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##"]),
    "MH201": (["*#4*2*0##", "*#4*2*13##"], ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
}
# model: whether the poll may ask for actuator 1 (the gateway does not drop the actuator request)
ZONE_STATUS = {"F454": True, "F460": True, "F461": True, "MH202": False, "MyHomeServer1": True, "Generic": True}


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
    assert zone.current_temperature == 19.6 and zone.target_temperature is None
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [*on_add, *poll]


@pytest.mark.parametrize("model", ["F453", "MH200", "MH200N"])
async def test_a_zone_without_a_temperature_asks_for_it_first(hass, model) -> None:
    """The temperature read proves the probe: a zone that refuses it is asked nothing more."""
    handler = _handler(hass, model)
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    on_add = _queued(handler)
    assert on_add == SCS_ONLY[model][0]
    await zone._poll_if_silent(grace=0)
    assert _queued(handler)[len(on_add) :] == ["*#4*2*0##", "*#4*2*14##", "*#4*2*12##"]


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
    zone.handle_event(OWNHeatingEvent(TEMPERATURE))
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
