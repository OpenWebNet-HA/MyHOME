# ruff: noqa
# Reproduction r1s0_four_zone_signal_bleed of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from unittest.mock import MagicMock
from homeassistant.components.climate import HVACMode
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from tests.test_crew_central_season_restore import _gateway, _climate, _entity_base_init, _replay


async def test_four_zone_signal_bleed(hass):
    gw = _gateway()
    central = _climate(gw, '#0#1', standalone=None, central=True)
    central.hass = hass
    unrelated = _climate(gw, '3', standalone=False)
    _replay(unrelated, ['*4*110*#3##'])  # tests/test_climate_zone_operation.py:82
    assert unrelated.hvac_mode == HVACMode.HEAT
    # Registration from climate.py:1588-1595
    disconnect = async_dispatcher_connect(hass, f'myhome_central_mode_{gw.mac}', unrelated._handle_central_mode_update)
    try:
        _replay(central, ['*4*103*#0#1##'])  # tests/test_climate_central_discovery.py:1203
        assert central.hvac_mode == HVACMode.OFF
        assert unrelated.hvac_mode == HVACMode.HEAT
    finally:
        disconnect()
