# ruff: noqa
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError
from OWNd.exceptions import OWNConnectionError
from tests.test_crew_central_season_restore import _entity_base_init, _climate, _replay, ANTIFREEZE, HEAT, RESTORE, HEATING_PROGRAM
from tests.test_probe_fallback_read import _command_session, _make_handler

async def test_restore_applied_but_command_ack_lost(hass):
    handler = _make_handler(hass, 'F455')
    handler.is_connected = True
    pool = handler._command_pool
    pool._event_session_ready.set()
    central = _climate(handler, '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    session = _command_session()
    async def on_wire(message, is_status_request=False):
        if str(message) == HEAT:
            return True
        assert str(message) == RESTORE
        # Program report on the separate monitor; socket lost before ACK on command session.
        _replay(central, [HEATING_PROGRAM])
        session.last_error = OWNConnectionError('Connection lost before acknowledgement', reason='connection_lost')
        return None
    session.send = AsyncMock(side_effect=on_wire)
    with patch('custom_components.myhome.gateway.OWNCommandSession', return_value=session):
        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            try:
                await central.async_set_hvac_mode(HVACMode.HEAT)
            except HomeAssistantError as error:
                assert central.extra_state_attributes['program'] == 1
                assert 'did not write' not in str(error), 'Lost ACK is not evidence that the program frame was never written'
        finally:
            await pool.send_buffer.put(None)
            await worker
