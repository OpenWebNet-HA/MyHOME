# ruff: noqa
import time
from unittest.mock import patch
from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent
from tests.test_crew_central_season_restore import _climate, _gateway, _replay, _entity_base_init, ANTIFREEZE, COOL, RESTORE, HEATING_PROGRAM

async def test_each_echo_object_stays_recognized_on_second_delivery():
    central = _climate(_gateway(), '#0', standalone=None, central=True)
    _replay(central, [ANTIFREEZE])
    await central.async_set_hvac_mode(HVACMode.COOL)
    season_echo = OWNEvent.parse(COOL)
    central.handle_event(season_echo)
    central.handle_event(OWNEvent.parse(RESTORE))
    _replay(central, [HEATING_PROGRAM])  # intervening report
    with patch('custom_components.myhome.climate.time.monotonic', return_value=time.monotonic() + 1):
        central.handle_event(season_echo)
    assert central.hvac_mode == HVACMode.HEAT
