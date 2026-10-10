"""Unit tests for climate platform blank-start quality and phantom entity suppression (#681)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from OWNd.message import OWNMessage
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.myhome.climate import (
    _calling_zones,
    _zone_address,
    async_setup_entry,
)
from custom_components.myhome.discovery import Address
from tests.conftest import attach_runtime


def test_climate_calling_zones_rejects_zero_and_double_zero() -> None:
    """_calling_zones must reject 0 and 00 to avoid discovering phantom zone 0 / 00."""
    # Heating broadcast for area 00
    msg_00 = OWNMessage.parse("*#4*00*15*0215##")
    assert msg_00 is not None
    zones_00, iface_00 = _calling_zones(msg_00)
    assert "00" not in zones_00
    assert zones_00 == []

    # Heating general 0
    msg_0 = OWNMessage.parse("*4*0*0##")
    assert msg_0 is not None
    zones_0, iface_0 = _calling_zones(msg_0)
    assert "0" not in zones_0
    assert zones_0 == []

    # Routed area 00 frame behind an F422 interface
    msg_routed_00 = MagicMock(where="00#4#01", zone=None, what=None, what_param=None, interface="01")
    zones_r00, iface_r00 = _calling_zones(msg_routed_00)
    assert zones_r00 == []

    # Routed general 0 frame behind an F422 interface
    msg_routed_0 = MagicMock(where="0#4#01", zone=None, what=None, what_param=None, interface="01")
    zones_r0, iface_r0 = _calling_zones(msg_routed_0)
    assert zones_r0 == []

    # Legitimate zone 1
    msg_zone1 = OWNMessage.parse("*#4*1*14*0215##")
    assert msg_zone1 is not None
    zones_1, iface_1 = _calling_zones(msg_zone1)
    assert zones_1 == ["1"]
    assert _zone_address(msg_zone1) == Address("1")


@pytest.mark.asyncio
async def test_climate_registry_restore_prunes_phantom_zone_00(hass: HomeAssistant) -> None:
    """Existing phantom climate.climate_zone_00 in entity registry is pruned on startup."""
    mac = "00:11:22:33:44:55"
    config_entry = MockConfigEntry(
        domain="myhome",
        data={"mac": mac},
        entry_id="test_entry",
        unique_id=mac,
    )
    config_entry.add_to_hass(hass)

    registry = er.async_get(hass)
    phantom = registry.async_get_or_create(
        domain="climate",
        platform="myhome",
        unique_id=f"{mac}-4-00",
        config_entry=config_entry,
        suggested_object_id="climate_zone_00",
    )
    phantom_routed = registry.async_get_or_create(
        domain="climate",
        platform="myhome",
        unique_id=f"{mac}-4-00#4#01",
        config_entry=config_entry,
        suggested_object_id="climate_zone_00_01",
    )
    legit = registry.async_get_or_create(
        domain="climate",
        platform="myhome",
        unique_id=f"{mac}-4-1",
        config_entry=config_entry,
        suggested_object_id="climate_zone_1",
    )

    hass.data = {
        "myhome": {
            mac: {
                "platforms": {"climate": {}},
                "entity": MagicMock(mac=mac),
            }
        }
    }
    attach_runtime(hass, config_entry)

    mock_add_entities = MagicMock()
    await async_setup_entry(hass, config_entry, mock_add_entities)

    # Phantom zones 00 and routed 00#4#01 were pruned
    assert registry.async_get(phantom.entity_id) is None
    assert registry.async_get(phantom_routed.entity_id) is None
    # Legitimate zone 1 was kept and restored
    assert registry.async_get(legit.entity_id) is not None
