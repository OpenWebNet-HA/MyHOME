# ruff: noqa
"""Composed event order, not a recorded transaction."""
from OWNd.message import OWNHeatingEvent
from tests.test_f455_zone_polling import _handler, _zone, _ready, _queued

async def test_poll_does_not_repeat_in_force_read_after_both_answers(hass, fast_bus_pacing):
    gw = _handler(hass, 'F455')
    zone = _zone(hass, gw, '3')
    _ready(gw)
    # tests/test_thermo_discovery_at_start.py:34
    zone.handle_event(OWNHeatingEvent('*#4*3*0*0255##'))
    delivered = False
    async def own_requests_first():
        nonlocal delivered
        if delivered:
            return
        delivered = True
        # tests/fixtures/traces/thermo_discovery_at_start_2026_11_18/README.md:61
        for frame in ('*#4*3*12*0265*3##', '*4*1*3##'):
            zone.handle_event(OWNHeatingEvent(frame))
    gw.wait_for_zone_requests = own_requests_first
    await zone._poll_if_silent(grace=0)
    assert zone.native_target_temperature == 26.5
    assert zone.hvac_mode == 'heat'
    assert '*#4*3*12##' not in _queued(gw)
