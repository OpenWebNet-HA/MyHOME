# ruff: noqa
import asyncio
import time
from homeassistant.components.climate.const import HVACMode
from tests.test_crew_central_season_restore import (_entity_base_init, _gateway, _climate, _sent, _replay, COOL, HEATING_PROGRAM)

async def test_lost_season_must_not_run_program_in_old_season():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    _replay(central, [HEATING_PROGRAM])
    def send(command):
        future = asyncio.get_running_loop().create_future()
        if str(command) == COOL:
            future.cancel()  # connection unavailable before season write
        else:
            future.set_result(time.monotonic())
        return future
    gw.send.side_effect = send
    try:
        await central.async_set_hvac_mode(HVACMode.COOL)
    except (Exception, asyncio.CancelledError):
        pass
    assert _sent(gw) == [COOL]
