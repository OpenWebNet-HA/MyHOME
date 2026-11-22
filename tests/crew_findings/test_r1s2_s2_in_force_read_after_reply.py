# ruff: noqa
"""Composed silent poll. All reports and reads: tests/test_f455_zone_polling.py:128-139."""
import pytest
from OWNd.message import OWNMessage
from tests.test_f455_zone_polling import _handler, _zone, _ready, _queued

@pytest.mark.usefixtures('fast_bus_pacing')
async def test_no_second_read_of_already_reported_setpoint_in_force(hass):
    handler = _handler(hass, 'F455')
    zone = _zone(hass, handler)
    def receive(raw):
        zone.handle_event(OWNMessage.parse(raw, gateway=handler.gateway))
    receive('*#4*2*0*0239##')
    _ready(handler)
    first = True
    async def wait_for_zone_requests():
        nonlocal first
        if first:
            first = False
            for raw in ('*#4*2*14*0220*3##', '*#4*2*12*0220*3##', '*4*0*2##'):
                receive(raw)
    handler.wait_for_zone_requests = wait_for_zone_requests
    await zone._poll_if_silent(grace=0)
    assert zone.native_target_temperature == 22.0
    assert '*#4*2*14##' not in _queued(handler)
    assert '*#4*2*12##' not in _queued(handler), _queued(handler)
