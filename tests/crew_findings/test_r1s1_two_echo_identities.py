# ruff: noqa
import time
from unittest.mock import patch
from OWNd.message import OWNEvent
from tests.test_crew_central_season_restore import _climate, _gateway, _entity_base_init, _replay, THERMAL_PROTECTION, RESTORE, HEAT, HVACMode

async def test_same_season_echo_object_stays_an_echo_after_second_slot_consumed():
    c = _climate(_gateway(), '#0',central=True,standalone=None)
    _replay(c,[THERMAL_PROTECTION])
    await c.async_set_hvac_mode(HVACMode.HEAT)
    season_echo = OWNEvent.parse(HEAT)
    c.handle_event(season_echo)
    c.handle_event(OWNEvent.parse(RESTORE))
    assert c.hvac_mode == HVACMode.COOL
    with patch('custom_components.myhome.climate.time.monotonic', return_value=time.monotonic()+1):
        c.handle_event(season_echo)  # duplicate dispatch of same object, not a later identical report
    assert c.hvac_mode == HVACMode.COOL
