"""Zone status reads: which dimensions the silent poll asks, in which order of events, and under which
spelling of the zone's address.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import OWNHeatingEvent, OWNMessage

from custom_components.myhome import climate
from custom_components.myhome.climate import MyHOMEClimate
from tests.test_f455_zone_polling import _handler, _queued, _ready, _zone
from tests.test_silent_zone_poll_by_result import _acknowledged, _dropped_by_the_desync_guard


# A zone configured as ``#Z`` (``myhome.yaml``) or stored as ``4-Z`` (a registry id of an old version) is one zone.
#
# Every status request and every write of it must be the frame the same zone configured as ``Z`` sends:
# ``*#4*#2##`` is "Zone 2 via Central Unit" (WHO 4 PDF, addressing table), which a live F461 2.0.11 NACKed for a
# thermostat the central unit was not running while ``*#4*2##`` gave the thermostat's five frames (2026-11-17), and
# ``4-2`` is no WHERE at all. The central unit, an actuator address and a probe keep their WHERE.
def _fan_zone(hass, handler, where: str) -> MyHOMEClimate:
    zone = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=True,
        humidity=True,
        standalone=True,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=handler,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_2"
    zone.async_write_ha_state = MagicMock()
    return zone


async def _frames(hass, where: str) -> dict[str, list]:
    """What a zone stored under ``where`` asks and writes."""
    handler = _handler(hass, "F461")
    _ready(handler)
    handler.request_zone_dimension = MagicMock()
    zone = _fan_zone(hass, handler, where)
    zone._fan_coil_sides = {"cooling": True, "heating": True}
    await zone._async_send_zone_polls()
    polls = _queued(handler)
    zone._fan_coil_sides = {}
    zone._ask_fan_coil_sides()
    asked = [call.args for call in handler.request_zone_dimension.call_args_list]
    handler.send = AsyncMock()
    await zone.async_set_temperature(temperature=21.0)
    await zone.async_set_hvac_mode("off")
    await zone.async_set_fan_mode("low")
    writes = [str(call.args[0]) for call in handler.send.await_args_list]
    return {"polls": polls, "asked": asked, "writes": writes}


@pytest.mark.parametrize("where", ["#2", "4-2"])
async def test_a_zone_under_another_spelling_sends_what_zone_2_sends(hass, where: str) -> None:
    reference = await _frames(hass, "2")
    assert reference["polls"] and reference["writes"], reference
    assert all(
        "#2" not in frame.split("*")[2] and "4-2" not in frame for frame in reference["polls"]
    )
    assert await _frames(hass, where) == reference


async def test_the_central_unit_an_actuator_and_a_probe_keep_their_where(hass) -> None:
    handler = _handler(hass, "F461")
    _ready(handler)
    central = _zone(hass, handler, "#0")
    assert central._central is True
    await central._async_send_zone_polls()
    assert _queued(handler) == ["*#4*#0##"]
    assert _zone(hass, handler, "2#1")._request_where == "2#1"
    assert _zone(hass, handler, "105")._request_where == "105"
    assert _zone(hass, handler, "#3", interface="1")._request_where == "3#4#01"


async def test_protection_setpoint_is_not_a_commanded_setpoint_answer(hass, fast_bus_pacing):
    gateway = _handler(hass, "MyHomeServer1")
    zone = _zone(hass, gateway, "3")
    # tests/test_who4_commit4_review2_2026_11_22.py:86-94
    for raw in ("*4*102*3##", "*#4*3*12*0070*3##", "*#4*3*0*0270##"):
        zone.handle_event(OWNHeatingEvent(raw))
    zone._local_offset_read_sent = True
    first = True

    def send(frame):
        nonlocal first
        if str(frame) == "*#4*3*14##":
            if first:
                first = False
                return _dropped_by_the_desync_guard(hass)
            zone.handle_event(OWNHeatingEvent("*#4*3*14*0180*3##"))
        return _acknowledged(hass, empty=False)

    _ready(gateway, side_effect=send)
    await zone._poll_if_silent(grace=0)
    assert _queued(gateway).count("*#4*3*14##") == 2, (
        "desync with no commanded setpoint report should retry"
    )


async def test_in_force_before_its_mode_is_not_confirmed_nominal(hass, fast_bus_pacing):
    gateway = _handler(hass, "MyHomeServer1")
    zone = _zone(hass, gateway, "3")
    zone._local_offset_read_sent = True
    zone.handle_event(OWNHeatingEvent("*#4*3*0*0270##"))
    # Composed event ordering, frames from tests/test_who4_commit4_review2_2026_11_22.py:86-94
    n = 0

    async def own():
        nonlocal n
        n += 1
        if n == 1:
            zone.handle_event(OWNHeatingEvent("*#4*3*12*0070*3##"))
        elif n == 2:
            zone.handle_event(OWNHeatingEvent("*4*102*3##"))

    gateway.wait_for_zone_requests = own

    def send(frame):
        if str(frame) == "*#4*3*14##":
            zone.handle_event(OWNHeatingEvent("*#4*3*14*0180*3##"))

    _ready(gateway, side_effect=send)
    await zone._poll_if_silent(grace=0)
    assert "*#4*3*14##" in _queued(gateway), (
        "mode arrived a turn after its protection setpoint and no nominal was asked"
    )


async def test_poll_does_not_repeat_in_force_read_after_both_answers(hass, fast_bus_pacing):
    """Composed event order, not a recorded transaction."""
    gw = _handler(hass, "F455")
    zone = _zone(hass, gw, "3")
    _ready(gw)
    # tests/test_thermo_discovery_at_start.py:34
    zone.handle_event(OWNHeatingEvent("*#4*3*0*0255##"))
    delivered = False

    async def own_requests_first():
        nonlocal delivered
        if delivered:
            return
        delivered = True
        # tests/fixtures/traces/thermo_discovery_at_start_2026_11_18/README.md:61
        for frame in ("*#4*3*12*0265*3##", "*4*1*3##"):
            zone.handle_event(OWNHeatingEvent(frame))

    gw.wait_for_zone_requests = own_requests_first
    await zone._poll_if_silent(grace=0)
    assert zone.native_target_temperature == 26.5
    assert zone.hvac_mode == "heat"
    assert "*#4*3*12##" not in _queued(gw)


@pytest.mark.usefixtures("fast_bus_pacing")
async def test_no_second_read_of_already_reported_setpoint_in_force(hass):
    """Composed silent poll. All reports and reads: tests/test_f455_zone_polling.py:128-139."""
    handler = _handler(hass, "F455")
    zone = _zone(hass, handler)

    def receive(raw):
        zone.handle_event(OWNMessage.parse(raw, gateway=handler.gateway))

    receive("*#4*2*0*0239##")
    _ready(handler)
    first = True

    async def wait_for_zone_requests():
        nonlocal first
        if first:
            first = False
            for raw in ("*#4*2*14*0220*3##", "*#4*2*12*0220*3##", "*4*0*2##"):
                receive(raw)

    handler.wait_for_zone_requests = wait_for_zone_requests
    await zone._poll_if_silent(grace=0)
    assert zone.native_target_temperature == 22.0
    assert "*#4*2*14##" not in _queued(handler)
    assert "*#4*2*12##" not in _queued(handler), _queued(handler)


@pytest.mark.usefixtures("fast_bus_pacing")
async def test_silent_poll_timeout_keeps_frame(hass, monkeypatch):
    gw = _handler(hass, "F455")
    pending = hass.loop.create_future()
    _ready(gw, side_effect=[pending])
    zone = _zone(hass, gw)
    monkeypatch.setattr(climate, "PACED_READ_TIMEOUT", 0)
    await zone._poll_if_silent(grace=0)
    assert _queued(gw) == ["*#4*2*0##"]  # tests/test_silent_zone_poll_by_result.py:193
    assert pending.cancelled(), (
        "The unsent read can still go out after the startup poll lock is released"
    )
