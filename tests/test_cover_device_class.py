"""A WHO 2 cover takes its device class from myhome.yaml ``class:`` / ``device_class:`` (#39)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant

from custom_components.myhome.const import (
    CONF_DEVICE_CLASS,
    CONF_PLATFORMS,
    CONF_SLAT_TILT,
    CONF_STRATEGY,
    CONF_WHERE,
    DOMAIN,
)
from custom_components.myhome.cover import PLATFORM, async_setup_entry
from custom_components.myhome.cover_scope import MyHOMEScopeCover
from custom_components.myhome.validate import cover_schema
from tests.conftest import attach_runtime


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:00:39"
    gw.unique_id = "00:03:50:00:00:39"
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


async def _setup_from_yaml(hass: HomeAssistant, mock_gateway, yaml_covers: dict) -> list:
    """Validate ``yaml_covers`` as myhome.yaml does and set the cover platform up from it."""
    mac = mock_gateway.mac
    hass.data = {DOMAIN: {mac: {"entity": mock_gateway, CONF_PLATFORMS: {PLATFORM: cover_schema(yaml_covers)}}}}
    config_entry = MagicMock()
    config_entry.data = {"mac": mac}
    config_entry.entry_id = "test_entry"
    added: list = []
    with patch("homeassistant.helpers.entity_registry.async_entries_for_config_entry", return_value=[]), \
         patch("homeassistant.helpers.entity_registry.async_get", return_value=MagicMock()):
        attach_runtime(hass, config_entry)
        await async_setup_entry(hass, config_entry, added.extend)
    return added


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({}, CoverDeviceClass.SHUTTER),
        ({CONF_DEVICE_CLASS: "curtain"}, CoverDeviceClass.CURTAIN),
        ({"device_class": "awning"}, CoverDeviceClass.AWNING),
        ({CONF_DEVICE_CLASS: "garage"}, CoverDeviceClass.GARAGE),
        ({CONF_SLAT_TILT: True}, CoverDeviceClass.BLIND),
        ({CONF_SLAT_TILT: True, CONF_DEVICE_CLASS: "shade"}, CoverDeviceClass.SHADE),
        ({CONF_STRATEGY: "venetian"}, CoverDeviceClass.BLIND),
        ({CONF_STRATEGY: "venetian", CONF_DEVICE_CLASS: "shade"}, CoverDeviceClass.SHADE),
        ({CONF_WHERE: "1", CONF_DEVICE_CLASS: "curtain"}, CoverDeviceClass.CURTAIN),
        ({CONF_WHERE: "0"}, CoverDeviceClass.SHUTTER),
    ],
)
async def test_who2_cover_uses_the_configured_class(hass: HomeAssistant, mock_gateway, cfg, expected) -> None:
    """`class: curtain` reaches the entity; slat tilt picks BLIND only when no class is configured (#39).

    Also covers `strategy: venetian` (implies slat tilt) and an area / general `MyHOMEScopeCover`.
    """
    added = await _setup_from_yaml(
        hass, mock_gateway, {"c": {CONF_WHERE: "41", CONF_NAME: "Cover 41", **cfg}}
    )
    assert len(added) == 1
    assert added[0].device_class == expected
    assert isinstance(added[0], MyHOMEScopeCover) is (cfg.get(CONF_WHERE) in ("0", "1"))
