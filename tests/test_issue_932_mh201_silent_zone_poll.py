"""#932: an MH201 zone that has not reported its setpoint asks for it once after the startup sweep.

Reported by @SimoneRevool in https://github.com/OpenWebNet-HA/MyHOME/issues/932 (BTicino MH201
firmware 3.6.43, ``bus_topology: standalone``, four heating zones, MyHOME 2.0.0b18, Home Assistant
2026.10.4): the zones showed ``current_temperature`` and ``knob_pos`` but no ``temperature`` until
a thermostat published a change, after every restart.

What stays as it was: the MH201 stays off the zone status poll (``*#4*Z##``) and off the general
request (``*#4*0##``), and each zone still asks for its temperature (``*#4*Z*0##``) when it is added
(#185).

What changes: the MH201 joins ``SILENT_ZONE_POLL``. ``SILENT_ZONE_GRACE`` seconds after the sweep a
zone asks for what it has not reported, one read at a time: its knob (``*#4*Z*13##``), its setpoint
(``*#4*Z*14##``), its setpoint in force (``*#4*Z*12##``) and actuator 1 (``*#4*Z#1*20##``). The knob
read therefore goes out inside this serial poll and no longer when the zone is added, as on the
F455.

The frames below are the ones quoted in the issue; the reporter's plant is the hardware evidence
(the MH201 is catalogued but not emulated in own-firmware-oracle).
"""

from __future__ import annotations

import pytest
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.gateway import SILENT_ZONE_GRACE, SILENT_ZONE_POLL
from tests.test_f455_zone_polling import _handler, _queued, _ready, _zone

# Issue 932, "Bus evidence": the replies of the reporter's MH201 3.6.43 to explicit reads.
REPLY_SETPOINT_IN_FORCE = "*#4*2*12*0200*3##"  # to *#4*2*12##
REPLY_SETPOINT = "*#4*2*14*0200*3##"  # to *#4*#2*14##
# Issue 932: frames the same zone published by itself after a change on the touchscreen.
PUSHED_TEMPERATURE = "*#4*2*0*0196##"
PUSHED_SETPOINT_IN_FORCE = "*#4*2*12*0205*3##"

MH201_READS = ["*#4*2*13##", "*#4*2*14##", "*#4*2*12##", "*#4*2#1*20##"]


def test_mh201_is_a_silent_zone_gateway_and_keeps_its_other_gates(hass) -> None:
    handler = _handler(hass, "MH201")
    assert SILENT_ZONE_POLL == {"F455", "MH201"} and SILENT_ZONE_GRACE == 120.0
    assert handler.silent_zone_poll_needed is True
    assert handler.supports_zone_status is False
    assert handler.zone_temperature_poll_needed is True
    assert handler.general_zone_request_refused is True
    assert "*#4*0##" not in handler._discovery_frames()


async def test_mh201_zone_still_asks_its_temperature_on_add_and_nothing_else(hass) -> None:
    """The temperature poll of #185 stays; the knob read moves into the serial poll."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    assert zone._poll_on_add is True
    assert zone._asks_local_offset is False
    await zone.async_update()
    assert _queued(handler) == ["*#4*2*0##"]


async def test_mh201_zone_with_a_temperature_asks_knob_setpoints_and_actuator(hass) -> None:
    """The reporter's zones 1, 3 and 4: a temperature, and no setpoint since the restart."""
    handler = _handler(hass, "MH201")
    _ready(handler)
    zone = _zone(hass, handler)
    zone.handle_event(OWNHeatingEvent(PUSHED_TEMPERATURE))
    assert zone.current_temperature == 19.6 and zone.target_temperature is None
    await zone._poll_if_silent(grace=0)
    assert _queued(handler) == MH201_READS


async def test_mh201_zone_without_a_temperature_asks_for_it_first(hass) -> None:
    handler = _handler(hass, "MH201")
    _ready(handler)
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == ["*#4*2*0##", *MH201_READS]


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


@pytest.mark.parametrize("model", ["F450", "F453", "F453AV", "F459", "H4684", "MH200", "MH200N"])
async def test_the_other_scs_only_gateways_are_unchanged(hass, model) -> None:
    """Only the MH201 was reported and verified (#932); the other SCS-only gateways keep asking nothing here."""
    handler = _handler(hass, model)
    _ready(handler)
    assert handler.silent_zone_poll_needed is False
    await _zone(hass, handler)._poll_if_silent(grace=0)
    assert _queued(handler) == []
