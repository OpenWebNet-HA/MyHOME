# ruff: noqa
from unittest.mock import AsyncMock, MagicMock, patch
from tests.test_gateway import gateway_handler, mock_config_entry
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed
from tests.test_dimmer_level_0_verified_2026_11_22 import MH200N_PLAIN_ON, MH200N_LEVEL_0


async def test_bare_ack_is_not_a_deduplicated_on(hass, gateway_handler):
    # Built timing; both frames sourced at tests/test_dimmer_level_0_verified_2026_11_22.py:62-72.
    light, gw = _light(hass, where='55')
    gw.send_status_request = gateway_handler.send_status_request
    gateway_handler._event_session_ready.set()
    session = MagicMock(connect=AsyncMock(return_value={'Success':True}), close=AsyncMock())
    # OWNd's ACK with no reports (gateway_sessions.py:1616-1625).
    session.send = AsyncMock(return_value=True)
    with patch('custom_components.myhome.gateway.OWNCommandSession', return_value=session):
        for raw in (MH200N_PLAIN_ON, MH200N_LEVEL_0):
            gateway_handler.bus_monitor.record_frame(direction='rx', raw=raw)
            _feed(light, raw)
        await gateway_handler.send_buffer.put(None)
        await gateway_handler.sending_loop(0)
        await hass.async_block_till_done()
    assert session.send.await_count == 1
    assert not light.is_on, 'no command-session reply to deduplicate; no event after request either'
