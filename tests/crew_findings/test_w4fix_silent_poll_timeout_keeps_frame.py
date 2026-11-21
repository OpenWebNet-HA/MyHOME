# ruff: noqa
# Reproduction r1s0_silent_poll_timeout_keeps_frame of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import pytest
from custom_components.myhome import climate
from tests.test_f455_zone_polling import _ready, _zone, _handler, _queued


@pytest.mark.usefixtures('fast_bus_pacing')
async def test_silent_poll_timeout_keeps_frame(hass, monkeypatch):
    gw = _handler(hass, 'F455')
    pending = hass.loop.create_future()
    _ready(gw, side_effect=[pending])
    zone = _zone(hass, gw)
    monkeypatch.setattr(climate, 'PACED_READ_TIMEOUT', 0)
    await zone._poll_if_silent(grace=0)
    assert _queued(gw) == ['*#4*2*0##']  # tests/test_silent_zone_poll_by_result.py:193
    assert pending.cancelled(), 'The unsent read can still go out after the startup poll lock is released'
