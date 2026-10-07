"""WHAT 1 / 0 (zone operation mode = season) must not flip a running zone's mode.

Runs only with an OWNd that decodes the frame as ``hvac_season`` (OpenWebNet-HA/OWNd#94);
the pinned 2.0.0b10 reports it as ``hvac_mode`` heat / cool and keeps the old path.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode

import OWNd.message as own_message
from OWNd.message import OWNEvent

pytestmark = pytest.mark.skipif(
    not hasattr(own_message, "MESSAGE_TYPE_SEASON"),
    reason="OWNd without the season decoding (OpenWebNet-HA/OWNd#94)",
)


@pytest.fixture
def mock_gateway():
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.unique_id = "00:03:50:00:12:34"
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    return gw


@pytest.fixture
def mock_entity_base_init():
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _climate(mock_gateway, central=False, where="01"):
    from custom_components.myhome.climate import MyHOMEClimate

    hass = MagicMock()
    hass.data = {}
    c = MyHOMEClimate(
        hass=hass,
        name="Climate",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=True,
        cooling=True,
        fan=False,
        standalone=False,
        central=central,
        manufacturer="BTicino",
        model="Thermostat",
        gateway=mock_gateway,
    )
    c.platform = MagicMock()
    c.entity_id = c.entity_id or "climate.test"
    c.async_schedule_update_ha_state = MagicMock()
    c.async_write_ha_state = MagicMock()
    return c


def test_auto_zone_keeps_auto_on_season_frame(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway)
    climate.handle_event(OWNEvent.parse("*4*311*#01##"))
    assert climate.hvac_mode == HVACMode.AUTO
    climate.handle_event(OWNEvent.parse("*4*1*01##"))
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate.extra_state_attributes["season"] == "heating"


def test_manual_zone_keeps_its_mode_on_season_frame(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway)
    climate.handle_event(OWNEvent.parse("*4*110*#01##"))
    assert climate.hvac_mode == HVACMode.HEAT
    climate.handle_event(OWNEvent.parse("*4*0*01##"))
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.extra_state_attributes["season"] == "conditioning"


def test_off_or_unknown_zone_takes_the_season_mode(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway)
    climate.handle_event(OWNEvent.parse("*4*1*01##"))
    assert climate.hvac_mode == HVACMode.HEAT
    climate.handle_event(OWNEvent.parse("*4*303*01##"))
    assert climate.hvac_mode == HVACMode.OFF
    climate.handle_event(OWNEvent.parse("*4*0*01##"))
    assert climate.hvac_mode == HVACMode.COOL


def test_central_unit_shows_the_season_as_its_mode(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway, central=True, where="#0")
    climate.handle_event(OWNEvent.parse("*4*0*#0##"))
    assert climate.hvac_mode == HVACMode.COOL
    climate.handle_event(OWNEvent.parse("*4*1*#0##"))
    assert climate.hvac_mode == HVACMode.HEAT
