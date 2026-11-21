# ruff: noqa
from tests.test_crew_central_season_restore import _climate, _gateway, _entity_base_init, _sent, HEAT, HVACMode

async def test_off_without_report_is_not_proof_of_idle():
    gw = _gateway()
    central = _climate(gw, '#0', central=True, standalone=None)
    # No reports yet. Queue OFF; the session has returned no 3550 report.
    await central.async_set_hvac_mode(HVACMode.OFF)
    gw.send.reset_mock()
    await central.async_set_hvac_mode(HVACMode.HEAT)
    assert _sent(gw) == [HEAT]
