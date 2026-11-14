"""#466: the one-shot WHO 4 Dimension 13 (local knob offset) startup read.

The maintainer welcomed this follow-up to #890 in
https://github.com/OpenWebNet-HA/MyHOME/pull/890#issuecomment-6852196609. A zone that does not
already learn its knob from the dimensionless ``*#4*Z##`` poll (which expands to a Dimension 13
read on the bus) now asks ``*#4*Z*13##`` once, so the first setpoint write after a restart is
compensated without waiting for the Dimension 12/14 echo of a write (the #890 derivation stays as
the fallback).

Evidence the per-zone request is answered and safe: the reporter's H/L4684 trace of 2026-11-13
(tests/fixtures/traces/issue_466/myhome_trace_HL4684_all_2026-11-13T06-59-18.json), the F461 and
F455 plants, and the 13-image firmware oracle (6341ba3) forwarding ``*#4*2*13##`` / ``*#4*88*13##``
on every image with no crash. The central / general forms are dropped on 10 of 13 images and are
never sent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import OWNHeatingEvent

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.discovery import message_has_state

TODAY = Path(__file__).resolve().parent / "fixtures/traces/issue_466/myhome_trace_HL4684_all_2026-11-13T06-59-18.json"


def _gateway(
    *,
    model: str,
    supports_zone_status: bool,
    zone_temperature_poll_needed: bool = False,
    silent_zone_poll_needed: bool = False,
    humidity_request_refused: bool = True,
    interface_zone_request_refused: bool = False,
) -> MagicMock:
    gw = MagicMock()
    gw.mac = "00:03:50:00:11:99"
    gw.model = model
    gw.log_id = f"[{model}]"
    gw.is_connected = True
    gw.has_central_unit = False
    gw.supports_zone_status = supports_zone_status
    gw.zone_temperature_poll_needed = zone_temperature_poll_needed
    gw.silent_zone_poll_needed = silent_zone_poll_needed
    gw.humidity_request_refused = humidity_request_refused
    gw.interface_zone_request_refused = interface_zone_request_refused
    gw.send = AsyncMock(return_value=None)
    gw.send_status_request = AsyncMock(return_value=None)
    return gw


def _zone(hass, gw, where="88", interface=None, fan=False, humidity=False) -> MyHOMEClimate:
    z = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        interface=interface, heating=True, cooling=True, fan=fan, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gw,
    )
    z.hass = hass
    z.entity_id = f"climate.zone_{where}"
    z.async_write_ha_state = MagicMock()
    z._humidity = humidity
    return z


def _sent(gw) -> list[str]:
    return [str(c.args[0]) for c in gw.send_status_request.await_args_list]


def _feed(z, *frames):
    for f in frames:
        z.handle_event(OWNHeatingEvent(f))


# ---- 1. H/L4684 fresh zone: temperature + dim13; first write after the answer is compensated ----

async def test_hl4684_fresh_zone_asks_temperature_and_dim13(hass):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    assert z._poll_on_add is True
    await z._async_send_zone_polls()
    assert _sent(gw) == ["*#4*88*0##", "*#4*88*13##"]


async def test_hl4684_first_write_is_compensated_after_the_dim13_answer(hass):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    _feed(z, "*#4*88*0*0210##", "*#4*88*14*0210*3##", "*#4*88#1*20*7##", "*#4*88*13*13##")
    assert z._local_offset == -3 and z._local_offset_reported is True
    await z.async_set_temperature(temperature=24.0)
    assert str(gw.send.await_args.args[0]) == "*#4*88*#14*0270*3##"


async def test_without_the_dim13_answer_the_890_derivation_still_learns(hass):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    _feed(z, "*#4*88*0*0210##", "*#4*88*14*0170*3##", "*#4*88#1*20*7##")
    await z.async_set_temperature(temperature=10.5)
    assert str(gw.send.await_args.args[0]) == "*#4*88*#14*0105*3##"  # uncompensated, knob unknown
    _feed(z, "*#4*88*0*0210##", "*#4*88*12*0075*3##", "*4*1*88##", "*#4*88*14*0105*3##")
    assert z._local_offset == -3  # #890 fallback derived it from the echo
    await z.async_set_temperature(temperature=24.0)
    assert str(gw.send.await_args.args[0]) == "*#4*88*#14*0270*3##"


# ---- 2. asked once ----

async def test_not_asked_again_after_the_knob_is_reported(hass):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    _feed(z, "*#4*88*13*13##")
    await z._async_send_zone_polls()
    assert "*#4*88*13##" not in _sent(gw)


async def test_an_unrequested_knob_before_the_poll_means_no_request(hass):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="33")
    _feed(z, "*#4*33*13*11##")  # the gateway pushes it while the knob is turned
    assert z._asks_local_offset is False
    await z._async_send_zone_polls()
    assert "*#4*33*13##" not in _sent(gw)


async def test_read_is_sent_once_even_when_the_knob_never_answers(hass):
    # A zone that never answers *#4*Z*13## must not re-ask it on every poll cycle: the
    # _local_offset_read_sent guard retires the read after the first send (handle_event never
    # runs, so _local_offset_reported stays False).
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    await z._async_send_zone_polls()
    await z._async_send_zone_polls()
    await z._async_send_zone_polls()
    assert z._local_offset_reported is False
    assert z._local_offset_read_sent is True
    assert _sent(gw).count("*#4*88*13##") == 1
    assert z._asks_local_offset is False  # retired after the single send


async def test_skip_reported_marks_complete_once_the_knob_has_arrived(hass):
    # An MH200N zone asks only its knob (#466); once that has reported, a skip_reported pass finds
    # nothing left to ask, so it sends nothing and marks the bus state complete.
    gw = _gateway(model="MH200N", supports_zone_status=False, zone_temperature_poll_needed=False)
    z = _zone(hass, gw, where="2")
    _feed(z, "*#4*2*13*00##")
    assert z._asks_local_offset is False
    z._bus_state_complete = False
    await z._async_send_zone_polls(skip_reported=True)
    assert _sent(gw) == []
    assert z._bus_state_complete is True


# ---- 2b. local-OFF (*13*4##) and protection (*13*5##) still count as a reported knob ----

@pytest.mark.parametrize("frame,offset", [("*#4*88*13*4##", 0), ("*#4*88*13*5##", 0)])
async def test_local_off_and_protection_report_the_knob_and_stop_the_read(hass, frame, offset):
    # OWNd leaves local_offset None for the local-OFF / protection codes, but they are valid
    # Dimension 13 reports: handle_event marks the knob reported (offset defaults to 0), so the
    # zone must stop asking *#4*Z*13##.
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    _feed(z, frame)
    assert z._local_offset_reported is True
    assert z._local_offset == offset
    assert z._asks_local_offset is False
    await z._async_send_zone_polls()
    assert "*#4*88*13##" not in _sent(gw)


@pytest.mark.parametrize("frame", ["*#4*2*13*4##", "*#4*2*13*5##"])
async def test_message_has_state_accepts_local_off_and_protection(hass, frame):
    # The SCS-only, non-temperature-poll completeness check keys off the Dimension 13 frame, not
    # a non-None local_offset, so a local-OFF / protection knob still completes the state.
    gw = _gateway(model="MH200N", supports_zone_status=False, zone_temperature_poll_needed=False)
    z = _zone(hass, gw, where="2")
    assert message_has_state(z, OWNHeatingEvent(frame)) is True


# ---- 3. zone-status gateway (F461): *#4*Z## carries dim13, no separate read ----

async def test_zone_status_gateway_does_not_add_a_separate_dim13_read(hass):
    gw = _gateway(model="F461", supports_zone_status=True)
    z = _zone(hass, gw, where="2")
    await z._async_send_zone_polls()
    assert _sent(gw) == ["*#4*2##"]  # the dimensionless status already expands to the dim13 read


async def test_zone_status_gateway_core_reported_still_asks_only_dim13(hass):
    gw = _gateway(model="F461", supports_zone_status=True)
    z = _zone(hass, gw, where="2")
    # core state (temperature, mode, setpoint) already arrived on the sweep, knob did not
    _feed(z, "*#4*2*0*0239##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0210*3##")
    await z._async_send_zone_polls(skip_reported=True)
    assert _sent(gw) == ["*#4*2*13##"]


# ---- 4. SCS-only without temperature poll (MH200N / F453): polled on add, only dim13 ----

async def test_scs_only_no_temperature_poll_asks_only_dim13(hass):
    gw = _gateway(model="MH200N", supports_zone_status=False, zone_temperature_poll_needed=False)
    z = _zone(hass, gw, where="2")
    assert z._poll_on_add is True
    await z._async_send_zone_polls()
    assert _sent(gw) == ["*#4*2*13##"]


# ---- 6. address forms ----

@pytest.mark.parametrize("where", ["#2", "4-2"])
async def test_canonical_zone_where_sends_the_zone_number(hass, where):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where=where)
    await z._async_send_zone_polls()
    assert "*#4*2*13##" in _sent(gw)
    assert "*#4*#2*13##" not in _sent(gw)  # never the central-unit form


async def test_routed_interface_zone_sends_dim13_only_where_allowed(hass):
    allowed = _gateway(model="F461", supports_zone_status=False, zone_temperature_poll_needed=True,
                       interface_zone_request_refused=False)
    z = _zone(hass, allowed, where="2", interface="01")
    await z._async_send_zone_polls()
    assert "*#4*2#4#01*13##" in _sent(allowed)
    refused = _gateway(model="H4684", supports_zone_status=False, zone_temperature_poll_needed=True,
                       interface_zone_request_refused=True)
    zr = _zone(hass, refused, where="2", interface="01")
    await zr._async_send_zone_polls()
    assert not any("*13##" in f for f in _sent(refused))


@pytest.mark.parametrize("where", ["105", "2#1", "#0", "#0#1"])
async def test_probe_actuator_and_central_send_no_dim13(hass, where):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    central = where in ("#0", "#0#1")
    z = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        interface=None, heating=True, cooling=True, fan=False, standalone=not central, central=central,
        manufacturer="BTicino", model="Heating Zone", gateway=gw,
    )
    z.hass = hass
    z.entity_id = f"climate.zone_{where}"
    z.async_write_ha_state = MagicMock()
    assert z._asks_local_offset is False
    await z._async_send_zone_polls()
    assert not any("*13##" in f for f in _sent(gw))


# ---- 7. message_has_state ----

async def test_message_has_state_hl4684_zone_keeps_polling_until_the_knob(hass):
    gw = _gateway(model="H/L4684", supports_zone_status=False, zone_temperature_poll_needed=True)
    z = _zone(hass, gw, where="88")
    temp = OWNHeatingEvent("*#4*88*0*0210##")
    assert message_has_state(z, temp) is False  # temperature alone is not the whole state


async def test_message_has_state_scs_zone_complete_only_on_dim13(hass):
    gw = _gateway(model="MH200N", supports_zone_status=False, zone_temperature_poll_needed=False)
    z = _zone(hass, gw, where="2")
    assert message_has_state(z, OWNHeatingEvent("*#4*2*0*0239##")) is False
    assert message_has_state(z, OWNHeatingEvent("*#4*2*13*11##")) is True
