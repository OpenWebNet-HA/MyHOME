# ruff: noqa
# Reproduction r1s0_candidate_exposed_while_waiting of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from unittest.mock import AsyncMock
import pytest
from tests.test_issue_966_phantom_climate_zone import _plant, _receive, _settle
from tests.test_who4_candidate_kept_2026_11_21 import ZONE_1_TEMPERATURE
from custom_components.myhome import climate_discovery


@pytest.mark.usefixtures('fast_bus_pacing')
async def test_candidate_exposed_while_waiting(hass, monkeypatch):
    exposed = set()
    monkeypatch.setattr(climate_discovery, 'is_exposed_on_bus', lambda h, e, w, addr: addr in exposed)
    read = AsyncMock(return_value=False)
    gw, zones, _ = await _plant(hass, read=read)
    async def quiet(_since):
        exposed.add('1')
    gw._wait_for_bus_quiet = quiet
    await _receive(hass, gw, [ZONE_1_TEMPERATURE])
    await _settle(hass, gw)
    assert not zones
    assert read.await_count == 0
