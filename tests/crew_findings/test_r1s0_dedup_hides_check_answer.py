# ruff: noqa
from unittest.mock import AsyncMock, MagicMock, patch
from OWNd.message import OWNMessage
from tests.test_gateway import gateway_handler, mock_config_entry
from tests.test_dimmer_on_at_level_0_2026_11_20 import _light, _feed, ON_AT_2, LEVEL_0
from tests.test_dimmer_level_0_verified_2026_11_22 import STATUS_REQUEST

async def test_fast_command_session_status_answer(hass, gateway_handler):
    light, gw = _light(hass)
    gw.send_status_request = gateway_handler.send_status_request
    gateway_handler._event_session_ready.set()
    session = MagicMock(connect=AsyncMock(return_value={'Success':True}),close=AsyncMock())
    session.send = AsyncMock(return_value=[OWNMessage.parse(ON_AT_2)])
    def dispatch(_, signal, msg):
        light.handle_event(msg)
    with patch('custom_components.myhome.gateway.OWNCommandSession',return_value=session), patch('custom_components.myhome.gateway.async_dispatcher_send',side_effect=dispatch):
        for raw in (ON_AT_2, LEVEL_0):
            gateway_handler.bus_monitor.record_frame(direction='rx', raw=raw)
            _feed(light, raw)
        await gateway_handler.send_buffer.put(None)
        await gateway_handler.sending_loop(0)
        await hass.async_block_till_done()
    assert str(session.send.await_args.kwargs['message']) == STATUS_REQUEST
    assert light.is_on, 'worker got ON back, but the entity expired its check as unanswered'
