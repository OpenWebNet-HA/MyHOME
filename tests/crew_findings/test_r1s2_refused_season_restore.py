# ruff: noqa
import asyncio

from homeassistant.components.climate.const import HVACMode
from homeassistant.exceptions import HomeAssistantError
from OWNd.message import OWNMessage

from tests.test_crew_central_season_restore import COOL, HEATING_PROGRAM, _climate, _gateway, _sent
from tests.test_crew_central_season_restore import _entity_base_init as _entity_base_init


async def test_refused_season_restore():
    gw = _gateway()
    cu = _climate(gw, "#0", standalone=None, central=True)
    cu.handle_event(OWNMessage.parse(HEATING_PROGRAM))

    async def send(cmd):
        delivery = asyncio.get_running_loop().create_future()
        if str(cmd) == COOL:
            delivery._gateway_nack = True  # gateway_sessions.py:1605-1615
            delivery.cancel()
        else:
            delivery.set_result(asyncio.get_running_loop().time())
        return delivery

    gw.send.side_effect = send
    try:
        await cu.async_set_hvac_mode(HVACMode.COOL)
    except HomeAssistantError:
        pass
    assert _sent(gw) == [COOL], "must not start the last program of the wrong season after refusal"
