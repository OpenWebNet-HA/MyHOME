from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.myhome.const import DOMAIN
from custom_components.myhome.myhome_device import MyHOMEEntity


@pytest.mark.asyncio
async def test_cross_domain_device_collision():
    """Verify that multiple domains using the same address do not merge into the same Device."""

    # Mock Gateway
    gateway_mock = MagicMock()
    gateway_mock.mac = "00:03:50:00:48:71"

    # Simulate adding a Light at address 11
    # Note: Using MyHOMEEntity directly to simulate how domains construct the device attributes
    light_entity = MyHOMEEntity(
        hass=MagicMock(),
        name="Light 11",
        platform="light",
        device_id="11",
        who="1",
        where="11",
        manufacturer="BTicino",
        model="Lighting Device",
        gateway=gateway_mock,
    )

    # Simulate adding a Cover at address 11
    cover_entity = MyHOMEEntity(
        hass=MagicMock(),
        name="Cover 11",
        platform="cover",
        device_id="11",
        who="2",
        where="11",
        manufacturer="BTicino",
        model="Shutter / Cover",
        gateway=gateway_mock,
    )

    # Ensure they generate DIFFERENT unique IDs for the Entity Registry
    assert light_entity.unique_id == "00:03:50:00:48:71-1-11"
    assert cover_entity.unique_id == "00:03:50:00:48:71-2-11"
    assert light_entity.unique_id != cover_entity.unique_id

    # critically ensure they generate DIFFERENT identifiers for the Device Registry (preventing merge)
    light_identifier = list(light_entity.device_info["identifiers"])[0]
    cover_identifier = list(cover_entity.device_info["identifiers"])[0]

    # Identifier format: (DOMAIN, '{mac}-{who}-{device_id}')
    assert light_identifier == (DOMAIN, "00:03:50:00:48:71-1-11")
    assert cover_identifier == (DOMAIN, "00:03:50:00:48:71-2-11")
    assert light_identifier != cover_identifier
    assert light_entity.via_device_id == gateway_mock.unique_id

    # Test base lifecycle methods
    light_entity.async_update = AsyncMock()
    await light_entity.async_added_to_hass()
    light_entity.async_update.assert_awaited_once()
    await light_entity.async_will_remove_from_hass()
