# ruff: noqa
import asyncio
import contextlib
from tests.test_crew_central_season_restore import _climate, _gateway, _entity_base_init, _sent, _replay, THERMAL_PROTECTION, HEAT, HVACMode

async def test_no_program_activation_after_failed_season():
    gw = _gateway()
    central = _climate(gw, '#0', standalone=None, central=True)
    _replay(central, [THERMAL_PROTECTION])
    # Session NACK: gateway_sessions.py:1604-1615. Season NACK without bus output:
    # results/MH200/020102/oracle/full/thermo-central-mode.tsv:18 (emulator only).
    receipt = asyncio.get_running_loop().create_future()
    receipt._gateway_nack = True
    receipt.cancel()
    gw.send.side_effect = [receipt, None]
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT], 'must not activate the program of an unselected season'
