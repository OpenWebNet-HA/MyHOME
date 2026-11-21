# ruff: noqa
from homeassistant.components.climate.const import HVACMode
from tests.test_crew_central_season_restore import _entity_base_init, _gateway, _climate, _sent, _replay, COOL, RESTORE

async def test_other_controller_season_command_cannot_release_reported_off():
    gw = _gateway(); c = _climate(gw, '#0', standalone=None, central=True)
    # Monitor sequence in tests/fixtures/traces/central_restore_3100_live_2026-11-12/README.md:82-89:
    # another client's season selection appears on the monitoring gateway, but read-back is still OFF.
    _replay(c, ['*4*203*#0##', COOL])
    await c.async_set_hvac_mode(HVACMode.COOL)
    assert _sent(gw) == [COOL, RESTORE]
