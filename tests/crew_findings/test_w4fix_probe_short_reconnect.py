# ruff: noqa
# Reproduction r1s0_probe_short_reconnect of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from unittest.mock import AsyncMock, MagicMock
from tests.test_f455_zone_polling import _handler
from tests.test_probe_fallback_read import _sensor, _refused, _settle, _sent, READ


async def test_probe_short_reconnect(hass):
    gw = _handler(hass, 'F455')
    gw._on_event_connection_state_change(True)
    p = _sensor(hass, gw)
    p.async_write_ha_state = MagicMock()
    p._register_availability_listener()
    gw.send_status_request = AsyncMock(return_value=_refused(hass))
    await p.async_update()
    await _settle()
    assert p._fallback_read_refused
    gw._on_event_connection_state_change(False)
    gw._on_event_connection_state_change(True)  # before sustained unavailability, no reload
    await _settle()
    await p.async_update()
    p._call_on_remove_callbacks()
    assert _sent(gw) == [READ, READ]
