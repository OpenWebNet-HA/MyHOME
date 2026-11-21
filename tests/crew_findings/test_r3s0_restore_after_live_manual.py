# ruff: noqa
import asyncio
import time
from homeassistant.components.climate.const import HVACMode
from tests.test_central_season_pair_delivery import _until
from tests.test_crew_central_season_restore import _entity_base_init, _replay, _gateway, _climate, _sent, ANTIFREEZE, HEAT

async def test_manual_run_confirmed_while_season_waits_for_ack():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    receipt = asyncio.get_running_loop().create_future()
    gw.send.side_effect = [receipt, None]
    call = asyncio.create_task(central.async_set_hvac_mode(HVACMode.HEAT))
    await _until(lambda: _sent(gw) == [HEAT])
    # A local manual selection while HA is waiting: real manual heating report,
    # tests/fixtures/traces/issue_466/README.md:30, not an echo of HEAT.
    _replay(central, ['*4*110#0180*#0##'])
    receipt.set_result(time.monotonic())
    await call
    assert _sent(gw) == [HEAT], 'Must not replace the newly confirmed matching manual setpoint'
