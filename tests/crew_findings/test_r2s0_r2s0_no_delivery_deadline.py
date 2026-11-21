# ruff: noqa
import asyncio
from unittest.mock import AsyncMock, patch
from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError
from tests.test_f455_zone_polling import _handler
from tests.test_crew_central_season_restore import _climate, _replay, _entity_base_init, ANTIFREEZE

async def test_terminal_command_worker_refusal_does_not_hold_central_lock_forever(hass):
    gw = _handler(hass, 'F455')
    central = _climate(gw, '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    gw._event_session_ready.set()
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await asyncio.sleep(0)
    with patch('custom_components.myhome.gateway.OWNCommandSession') as session:
        session.return_value.connect = AsyncMock(return_value={'Success': False, 'Message': 'connection_refused'})
        session.return_value.close = AsyncMock()
        await gw._command_pool.sending_loop(1)
    try:
        # Worker has terminated, not merely returned a late ACK. Advance the event-loop
        # clock so any reasonable service-side delivery deadline would fire.
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'time', return_value=loop.time()+3600):
            for _ in range(8):
                await asyncio.sleep(0)
        assert call.done(), 'No sender left, but HEAT still holds the central command lock an hour later'
        assert isinstance(call.exception(), HomeAssistantError)
    finally:
        gw._command_pool.close()
        await asyncio.gather(call, return_exceptions=True)
