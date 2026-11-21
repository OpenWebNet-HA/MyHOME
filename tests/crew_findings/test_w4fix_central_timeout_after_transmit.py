# ruff: noqa
# Reproduction r1s0_central_timeout_after_transmit of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError
from tests.test_crew_central_season_restore import _entity_base_init, _climate, _replay, ANTIFREEZE, HEAT, HEATING_PROGRAM
from tests.test_probe_fallback_read import _command_session, _make_handler


async def test_central_timeout_after_transmit(hass, monkeypatch):
    handler = _make_handler(hass, 'F455')
    handler.is_connected = True
    pool = handler._command_pool
    pool._event_session_ready.set()
    central = _climate(handler, '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    session = _command_session()
    transmitted = []
    ack = asyncio.Event()
    async def on_wire(message, is_status_request=False):
        transmitted.append(str(message))
        await ack.wait()  # command was sent; only ACK is outstanding
        return True
    session.send = AsyncMock(side_effect=on_wire)
    monkeypatch.setattr('custom_components.myhome.climate.SETPOINT_DELIVERY_TIMEOUT', 0.1)
    with patch('custom_components.myhome.gateway.OWNCommandSession', return_value=session):
        worker = asyncio.create_task(pool.sending_loop(0))
        try:
            with pytest.raises(HomeAssistantError) as error:
                await central.async_set_hvac_mode(HVACMode.HEAT)
            assert transmitted == [HEAT]
            assert error.value.translation_key != 'central_command_not_written'
        finally:
            ack.set()
            await pool.send_buffer.put(None)
            await worker
