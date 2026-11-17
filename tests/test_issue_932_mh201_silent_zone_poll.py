"""#932: an MH201 zone that has not reported its setpoint asks for it once after the startup sweep.

Reported by @SimoneRevool in https://github.com/OpenWebNet-HA/MyHOME/issues/932 (BTicino MH201
firmware 3.6.43, ``bus_topology: standalone``, four heating zones, MyHOME 2.0.0b18, Home Assistant
2026.10.4): the zones showed ``current_temperature`` and ``knob_pos`` but no ``temperature`` until
a thermostat published a change, after every restart.

What stays as it was: the MH201 stays off the zone status poll (``*#4*Z##``) and off the general
request (``*#4*0##``), and each zone still asks for its temperature (``*#4*Z*0##``) when it is added
(#185).

What changes: MH201 zones run the silent-zone poll, as the zones of every gateway now do
(``tests/test_silent_zone_poll_by_result.py``): it is no longer keyed on a model list.
``SILENT_ZONE_GRACE`` seconds after the sweep a zone asks for what it has not reported, one read at
a time: its setpoint (``*#4*Z*14##``), its setpoint in force (``*#4*Z*12##``) and actuator 1
(``*#4*Z#1*20##``). The knob read (``*#4*Z*13##``) still goes out when the zone is added, as before
#932; the reporter's zones showed their ``knob_pos``, so the poll has no reason to ask for it again.
A knob read that brought nothing is repeated once, as the last read of the poll.

The frames below are the ones quoted in the issue; the reporter's plant is the hardware evidence
(the MH201 is catalogued but not emulated in own-firmware-oracle).
"""

from __future__ import annotations

import pytest
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.gateway import KNOB_READ_IN_SILENT_ZONE_POLL, SILENT_ZONE_GRACE
from tests.test_f455_zone_polling import _handler, _queued, _ready, _zone

# The poll waits for the bus to be quiet after every read; the mock gateways answer at once.
pytestmark = pytest.mark.usefixtures("fast_bus_pacing")

# Issue 932, "Bus evidence": the replies of the reporter's MH201 3.6.43 to explicit reads.
REPLY_SETPOINT_IN_FORCE = "*#4*2*12*0200*3##"  # to *#4*2*12##
REPLY_SETPOINT = "*#4*2*14*0200*3##"  # to *#4*#2*14##
# Issue 932: frames the same zone published by itself after a change on the touchscreen.
PUSHED_TEMPERATURE = "*#4*2*0*0196##"
PUSHED_SETPOINT_IN_FORCE = "*#4*2*12*0205*3##"
KNOB = "*#4*2*13*00##"  # the zones of the issue showed ``knob_pos``

ON_ADD = ["*#4*2*0##", "*#4*2*13##"]
MH201_READS = ["*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


def test_mh201_runs_the_silent_zone_poll_and_keeps_its_other_gates(hass) -> None:
    handler = _handler(hass, "MH201")
    assert "MH201" not in KNOB_READ_IN_SILENT_ZONE_POLL and SILENT_ZONE_GRACE == 30.0
    assert handler.silent_zone_poll_needed is True
    assert handler.knob_read_in_silent_zone_poll is False
    assert handler.supports_zone_status is False
    assert handler.zone_temperature_poll_needed is True
    assert handler.general_zone_request_refused is True
    assert "*#4*0##" not in handler._discovery_frames()


async def test_mh201_zone_asks_on_add_what_it_asked_before(hass) -> None:
    """The temperature poll of #185 and the knob read of #466 stay as they were."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    assert zone._poll_on_add is True
    assert zone._asks_local_offset is True
    await zone.async_update()
    assert _queued(handler) == ON_ADD


async def test_mh201_zone_with_a_temperature_asks_setpoints_and_actuator(hass) -> None:
    """The reporter's zones 1, 3 and 4: a temperature, and no setpoint since the restart."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    for frame in (PUSHED_TEMPERATURE, KNOB):
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.current_temperature == 19.6 and zone.target_temperature is None
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [*ON_ADD, *MH201_READS]


async def test_mh201_knob_read_that_brought_nothing_is_repeated_last(hass) -> None:
    """The knob read on add went unanswered: a zone that is still silent asks once more, after the setpoints."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    zone.handle_event(OWNHeatingEvent(PUSHED_TEMPERATURE))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [*ON_ADD, *MH201_READS, "*#4*2*13##"]


async def test_mh201_zone_without_a_temperature_asks_for_it_first(hass) -> None:
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    await zone.async_update()
    zone.handle_event(OWNHeatingEvent(KNOB))
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == [*ON_ADD, "*#4*2*0##", *MH201_READS]


def test_the_replies_of_issue_932_give_the_target_temperature(hass) -> None:
    zone = _zone(hass, _handler(hass, "MH201"))
    assert zone.target_temperature is None
    zone.handle_event(OWNHeatingEvent(REPLY_SETPOINT))
    assert zone.target_temperature == 20.0
    assert zone.extra_state_attributes["local_target_temperature"] is None
    zone.handle_event(OWNHeatingEvent(REPLY_SETPOINT_IN_FORCE))
    assert zone.target_temperature == 20.0
    assert zone.extra_state_attributes["local_target_temperature"] == 20.0


async def test_mh201_zone_that_published_its_setpoint_is_not_asked_for_it_again(hass) -> None:
    """The reporter's zone 2 had published a change: its setpoint is not read a second time."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    for frame in (PUSHED_TEMPERATURE, PUSHED_SETPOINT_IN_FORCE):
        zone.handle_event(OWNHeatingEvent(frame))
    assert zone.target_temperature == 20.5
    await zone._poll_if_silent(grace=0)
    assert "*#4*2*14##" not in _queued(handler)
    assert "*#4*2*0##" not in _queued(handler)
