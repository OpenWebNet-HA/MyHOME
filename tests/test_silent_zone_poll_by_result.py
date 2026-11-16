"""The silent-zone poll goes by the result, not by a model list (#466, #932).

A gateway that is not sent the zone status request (``*#4*Z##``) has nothing that asks a zone for
its setpoint. Issue 466 was an F455, issue 932 an MH201; the same gap exists on every such gateway.
So ``SILENT_ZONE_GRACE`` seconds after the startup sweep a zone asks for what it has not reported
by itself, read by read, on every gateway whose zones are not sent ``*#4*Z##``. A zone that
reported everything sends nothing, and the zones of a gateway that is sent ``*#4*Z##`` are not
polled a second time.

What differs by gateway:

- The knob read (``*#4*Z*13##``) is part of the poll on the F455 and the MH201
  (``KNOB_READ_IN_SILENT_ZONE_POLL``); every other gateway sends it when the zone is added
  (#466 / #902) and the poll does not repeat it.
- Actuator 1 (``*#4*Z#1*20##``) is not asked where the gateway drops the actuator request
  (``actuator_request_refused`` of the gateway that serves WHO 4).

Evidence: the F455 1.0.76 capture (``tests/test_f455_zone_polling.py``), the MH201 3.6.43 plant of
#932 (``tests/test_issue_932_mh201_silent_zone_poll.py``), and own-firmware-oracle (main
``3c1634b``), where all thirteen emulated gateways put ``*#4*Z*0##``, ``*#4*Z*12##``,
``*#4*Z*13##`` and ``*#4*Z*14##`` on the bus (verdict ``out``). For the F450, F453, F453AV, F459,
H4684, MH200 and MH200N the poll has not run on a physical plant; the F452 and the F458 are not in
the oracle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent
from OWNd.profiles import get_gateway_profile

from custom_components.myhome.gateway import (
    KNOB_READ_IN_SILENT_ZONE_POLL,
    NO_ZONE_STATUS_REQUEST,
    MyHOMEGatewayHandler,
)
from tests.test_f455_zone_polling import _handler, _queued, _ready, _spin, _zone

TEMPERATURE = "*#4*2*0*0196##"
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
    "MH201": (["*#4*2*0##"], ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]),
}
ZONE_STATUS = ["F454", "F460", "F461", "MH202", "MyHomeServer1", "Generic"]


# ------------------------------------------------------------------ the gate


@pytest.mark.parametrize("model", sorted(SCS_ONLY))
def test_a_gateway_not_sent_the_zone_status_request_runs_the_poll(hass, model) -> None:
    handler = _handler(hass, model)
    assert handler.supports_zone_status is False
    assert handler.silent_zone_poll_needed is True
    assert handler.knob_read_in_silent_zone_poll is (handler._model_key in KNOB_READ_IN_SILENT_ZONE_POLL)


def test_the_table_of_the_zone_status_request_is_the_one_the_poll_reads() -> None:
    assert set(NO_ZONE_STATUS_REQUEST) == {
        "F450", "F452", "F453", "F455", "F458", "F459", "H4684", "HL4684", "MH200", "MH201",
    }  # fmt: skip
    assert KNOB_READ_IN_SILENT_ZONE_POLL == {"F455", "MH201"}


@pytest.mark.parametrize("model", ZONE_STATUS)
async def test_a_gateway_sent_the_zone_status_request_asks_nothing_more(hass, model) -> None:
    handler = _handler(hass, model)
    _ready(handler)
    assert handler.supports_zone_status is True
    assert handler.silent_zone_poll_needed is False
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == []


def test_the_gateway_that_serves_who_4_decides(hass, monkeypatch) -> None:
    """A primary whose WHO 4 goes through an MH200 (delegation, failover) polls; the reverse does not."""
    primary = _handler(hass, "F461")
    other = _handler(hass, "MH200")
    monkeypatch.setattr(primary, "_who4_target", lambda: other)
    assert primary.silent_zone_poll_needed is True
    assert primary.knob_read_in_silent_zone_poll is False
    assert primary.actuator_request_refused is False and primary.silent_zone_actuator_read_refused is True
    monkeypatch.setattr(primary, "_who4_target", lambda: primary)
    monkeypatch.setattr(other, "_who4_target", lambda: primary)
    assert other.silent_zone_poll_needed is False
    assert other.actuator_request_refused is True and other.silent_zone_actuator_read_refused is False


def test_a_passive_follower_polls_nothing(hass, monkeypatch) -> None:
    handler = _handler(hass, "MH200")
    monkeypatch.setattr(MyHOMEGatewayHandler, "is_follower", property(lambda self: True))
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: set()))
    assert handler.silent_zone_poll_needed is False
    monkeypatch.setattr(MyHOMEGatewayHandler, "delegated_whos", property(lambda self: {4}))
    assert handler.silent_zone_poll_needed is True


def test_a_model_corrected_in_band_is_honoured(hass) -> None:
    """The in-band identity (WHO 13 / WHO 1013) can correct the model after the entities were added."""
    labelled_f461 = _handler(hass, "F461")
    assert labelled_f461.silent_zone_poll_needed is False
    labelled_f461.gateway.profile = get_gateway_profile("MH200")
    assert labelled_f461.silent_zone_poll_needed is True

    labelled_mh200 = _handler(hass, "MH200")
    assert labelled_mh200.silent_zone_poll_needed is True
    labelled_mh200.gateway.profile = get_gateway_profile("F454")  # its profile says *#4*Z## is answered
    assert labelled_mh200.silent_zone_poll_needed is False


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
