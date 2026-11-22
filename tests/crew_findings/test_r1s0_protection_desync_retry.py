# ruff: noqa
from OWNd.message import OWNHeatingEvent
from tests.test_f455_zone_polling import _handler, _zone, _ready, _queued
from tests.test_silent_zone_poll_by_result import _acknowledged, _dropped_by_the_desync_guard

async def test_protection_setpoint_is_not_a_commanded_setpoint_answer(hass, fast_bus_pacing):
    gateway = _handler(hass, 'MyHomeServer1')
    zone = _zone(hass, gateway, '3')
    # tests/test_who4_commit4_review2_2026_11_22.py:86-94
    for raw in ('*4*102*3##','*#4*3*12*0070*3##','*#4*3*0*0270##'):
        zone.handle_event(OWNHeatingEvent(raw))
    zone._local_offset_read_sent = True
    first = True
    def send(frame):
        nonlocal first
        if str(frame) == '*#4*3*14##':
            if first:
                first = False
                return _dropped_by_the_desync_guard(hass)
            zone.handle_event(OWNHeatingEvent('*#4*3*14*0180*3##'))
        return _acknowledged(hass, empty=False)
    _ready(gateway, side_effect=send)
    await zone._poll_if_silent(grace=0)
    assert _queued(gateway).count('*#4*3*14##') == 2, 'desync with no commanded setpoint report should retry'
