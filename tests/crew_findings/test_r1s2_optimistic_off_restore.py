# ruff: noqa
import asyncio

from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNMessage

from tests.test_crew_central_season_restore import HEAT, _climate, _gateway, _sent
from tests.test_crew_central_season_restore import _entity_base_init as _entity_base_init


async def test_optimistic_off_restore():
    gw = _gateway()
    cu = _climate(gw, "#0", standalone=None, central=True)
    # Last physical state: manual heating (tests/fixtures/traces/issue_466/README.md:30).
    cu.handle_event(OWNMessage.parse("*4*110#0180*#0##"))
    failed = asyncio.get_running_loop().create_future()
    failed.cancel()  # off never written (gateway_sessions.py:1540-1547)
    gw.send.return_value = failed
    await cu.async_set_hvac_mode(HVACMode.OFF)
    await asyncio.sleep(0)
    gw.send.reset_mock()
    gw.send.return_value = None
    await cu.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT], (
        "an optimistic off replaced a reported manual heating setpoint by the program"
    )
