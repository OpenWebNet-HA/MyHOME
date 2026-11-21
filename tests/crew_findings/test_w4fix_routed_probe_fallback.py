# ruff: noqa
# Reproduction r1s0_routed_probe_fallback of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from tests.test_probe_fallback_read import _sensor, _sent, gateway, PROBE_PUSH_FRESH
from OWNd.message import OWNHeatingEvent


async def test_routed_probe_fallback(hass, gateway):
    raw = '*#4*105#4#02*0*0215##'  # tests/test_issue_967_phantom_temperature_probe.py:435
    gateway.interface_zone_request_refused = True
    sensor = _sensor(hass, gateway, '105#4#02')
    sensor.handle_event(OWNHeatingEvent(raw))
    assert sensor.native_value == 21.5
    sensor._last_push_at -= PROBE_PUSH_FRESH + 1
    await sensor.async_update()
    assert not _sent(gateway)
