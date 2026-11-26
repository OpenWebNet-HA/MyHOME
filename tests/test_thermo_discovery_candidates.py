"""WHO 4 discovery: a candidate zone while the startup sweep waits, and a candidate overtaken by the
registry or by an exposure.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from OWNd.message import OWNMessage

from custom_components.myhome import climate_discovery
from custom_components.myhome.climate_discovery import ZoneDiscovery
from custom_components.myhome.discovery import Address, DeviceContext
from tests.test_issue_966_phantom_climate_zone import _plant, _receive, _settle
from tests.test_thermo_discovery_at_start import (
    _after_the_sweep,
)
from tests.test_thermo_discovery_at_start import (
    _plant as _start_plant,
)
from tests.test_thermo_discovery_at_start import (
    _receive as _start_receive,
)
from tests.test_who4_candidate_kept_2026_11_21 import ZONE_1_TEMPERATURE
from tests.test_who4_commit4_review2_2026_11_22 import ACTUATOR_1_OFF


async def test_already_discovered_thermostat_not_read_by_actuator_only_scan(hass):
    """A physical thermostat can identify itself during the same startup-lock wait as an exposure."""
    read = AsyncMock(return_value=True)
    gw, zones = await _start_plant(hass, read)
    async with gw._who4_startup_poll_lock:
        await _start_receive(hass, gw, [ACTUATOR_1_OFF])
        gw._initial_discovery_done.set()
        for _ in range(30):
            await asyncio.sleep(0)
            if gw._who4_startup_poll_lock._waiters:
                break
        assert gw._who4_startup_poll_lock._waiters
        await _start_receive(
            hass, gw, ["*#4*3*0*0260##"]
        )  # tests/fixtures/traces/thermo_discovery_at_start_2026_11_18/README.md:38
        assert [(zone._where, zone.current_temperature) for zone in zones] == [("3", 26.0)]
    await _after_the_sweep(hass, gw)
    assert not read.await_args_list, (
        f"already discovered before lock release: {read.await_args_list}"
    )


@pytest.mark.usefixtures("fast_bus_pacing")
async def test_candidate_exposed_while_waiting(hass, monkeypatch):
    exposed = set()
    monkeypatch.setattr(
        climate_discovery, "is_exposed_on_bus", lambda h, e, w, addr: addr in exposed
    )
    read = AsyncMock(return_value=False)
    gw, zones, _ = await _plant(hass, read=read)

    async def quiet(_since):
        exposed.add("1")

    gw._wait_for_bus_quiet = quiet
    await _receive(hass, gw, [ZONE_1_TEMPERATURE])
    await _settle(hass, gw)
    assert not zones
    assert read.await_count == 0


async def test_candidate_orphaned_suppression(hass):
    entry = MagicMock(subentries={})
    gateway = MagicMock(read_zone_temperature=AsyncMock(return_value=False))
    tracker = ZoneDiscovery(
        hass,
        entry,
        SimpleNamespace(gateway=gateway, platforms={"climate": {}}),
        lambda ctx, cfg: MagicMock(),
    )
    assert not tracker.accept(
        DeviceContext(Address("1"), "4", source="bus", message=OWNMessage.parse(ZONE_1_TEMPERATURE))
    )
    await tracker._confirm_candidates()
    assert tracker._candidates_unanswered == {("1", None)}
    registry = DeviceContext(Address("1"), "4", source="registry")
    assert tracker.accept(registry)
    tracker.build(registry)
    assert tracker._candidates_unanswered <= tracker._candidates.keys()
