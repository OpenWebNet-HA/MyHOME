# ruff: noqa
# Reproduction r1s0_candidate_orphaned_suppression of the crew audit of 2026-11-21 (audit/findings/who4-final-crew-audit-2026-11-21.md).
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from OWNd.message import OWNMessage
from custom_components.myhome.climate_discovery import ZoneDiscovery
from custom_components.myhome.discovery import Address, DeviceContext
from tests.test_who4_candidate_kept_2026_11_21 import ZONE_1_TEMPERATURE


async def test_candidate_orphaned_suppression(hass):
    entry = MagicMock(subentries={})
    gateway = MagicMock(read_zone_temperature=AsyncMock(return_value=False))
    tracker = ZoneDiscovery(hass, entry, SimpleNamespace(gateway=gateway, platforms={'climate': {}}), lambda ctx,cfg: MagicMock())
    assert not tracker.accept(DeviceContext(Address('1'), '4', source='bus', message=OWNMessage.parse(ZONE_1_TEMPERATURE)))
    await tracker._confirm_candidates()
    assert tracker._candidates_unanswered == {('1',None)}
    registry = DeviceContext(Address('1'), '4', source='registry')
    assert tracker.accept(registry)
    tracker.build(registry)
    assert tracker._candidates_unanswered <= tracker._candidates.keys()
