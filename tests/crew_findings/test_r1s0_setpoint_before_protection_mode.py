# ruff: noqa
from OWNd.message import OWNHeatingEvent
from tests.test_f455_zone_polling import _handler, _zone, _ready, _queued

async def test_in_force_before_its_mode_is_not_confirmed_nominal(hass, fast_bus_pacing):
    gateway = _handler(hass, 'MyHomeServer1')
    zone = _zone(hass, gateway, '3')
    zone._local_offset_read_sent = True
    zone.handle_event(OWNHeatingEvent('*#4*3*0*0270##'))
    # Composed event ordering, frames from tests/test_who4_commit4_review2_2026_11_22.py:86-94
    n = 0
    async def own():
        nonlocal n
        n += 1
        if n == 1:
            zone.handle_event(OWNHeatingEvent('*#4*3*12*0070*3##'))
        elif n == 2:
            zone.handle_event(OWNHeatingEvent('*4*102*3##'))
    gateway.wait_for_zone_requests = own
    def send(frame):
        if str(frame) == '*#4*3*14##':
            zone.handle_event(OWNHeatingEvent('*#4*3*14*0180*3##'))
    _ready(gateway, side_effect=send)
    await zone._poll_if_silent(grace=0)
    assert '*#4*3*14##' in _queued(gateway), 'mode arrived a turn after its protection setpoint and no nominal was asked'
