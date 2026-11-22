# ruff: noqa
"""The worker can suppress the ACTUAL status, not just suppress a repeat of the last ON."""
from unittest.mock import AsyncMock, MagicMock, patch
from OWNd.message import OWNMessage
from tests.test_gateway import gateway_handler, mock_config_entry
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, OFF, LEVEL_0
from tests.test_dimmer_level_0_verified_2026_11_22 import STATUS_REQUEST

async def test_dedup_of_off_answer_wins_over_recent_on(hass, gateway_handler):
    # Built short sequence; raw reports from tests/fixtures/traces/dimmer_on_at_level_0_2026_11_20/frames.json:15,30,40.
    light, gw = _light(hass)
    gw.send_status_request = gateway_handler.send_status_request
    gateway_handler._event_session_ready.set()
    session = MagicMock(connect=AsyncMock(return_value={'Success':True}),close=AsyncMock())
    session.send = AsyncMock(return_value=[OWNMessage.parse(OFF)])
    with patch('custom_components.myhome.gateway.OWNCommandSession',return_value=session), patch('custom_components.myhome.gateway.async_dispatcher_send',side_effect=lambda _,signal,msg:light.handle_event(msg)):
        for raw in (OFF,ON_AT_2,LEVEL_0):
            gateway_handler.bus_monitor.record_frame(direction='rx',raw=raw)
            _feed(light,raw)
        await gateway_handler.send_buffer.put(None)
        await gateway_handler.sending_loop(0)
        await hass.async_block_till_done()
    assert str(session.send.await_args.kwargs['message']) == STATUS_REQUEST
    assert light.is_on is False, 'the actuator explicitly answered OFF, yet Home Assistant confirmed ON'
