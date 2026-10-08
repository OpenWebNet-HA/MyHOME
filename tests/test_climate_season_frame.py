"""WHAT 1 / 0 (zone operation mode = season) must not flip a running zone's mode.

Runs with both pinned OWNd 2.0.0b10 (synthetic season events) and newer OWNd
where OWND decodes the frame natively as ``hvac_season`` (OpenWebNet-HA/OWNd#94).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import OWNd.message as own_message
import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from OWNd.message import OWNEvent

from custom_components.myhome.climate import (
    MESSAGE_TYPE_SEASON,
    SEASON_HEATING,
)


def _season_event(raw: str, season: str, where: str = "01"):
    """Return a season event, using native OWNd parser if supported or mock event on 2.0.0b10."""
    if hasattr(own_message, "MESSAGE_TYPE_SEASON"):
        return OWNEvent.parse(raw)
    ev = MagicMock(spec=OWNEvent)
    ev.message_type = MESSAGE_TYPE_SEASON
    ev.season = season
    ev.where = where
    ev.zone = int(where.replace("#", "")) if where.replace("#", "").isdigit() else 0
    ev.human_readable_log = f"Zone {where}'s season is {season}."
    return ev


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
    climate.handle_event(_season_event("*4*1*01##", SEASON_HEATING, "01"))
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate.extra_state_attributes["season"] == SEASON_HEATING


def test_manual_zone_keeps_its_mode_on_season_frame(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway)
    climate.handle_event(OWNEvent.parse("*4*110*#01##"))
    assert climate.hvac_mode == HVACMode.HEAT
    climate.handle_event(_season_event("*4*0*01##", "conditioning", "01"))
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.extra_state_attributes["season"] == "conditioning"


def test_off_or_unknown_zone_takes_the_season_mode(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway)
    climate._target_temperature = 20.0
    climate._attr_hvac_mode = HVACMode.OFF
    climate._attr_hvac_action = HVACAction.OFF
    climate.handle_event(_season_event("*4*1*01##", SEASON_HEATING, "01"))
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.hvac_action == HVACAction.IDLE
    assert climate.target_temperature == 20.0
    climate.handle_event(OWNEvent.parse("*4*303*01##"))
    assert climate.hvac_mode == HVACMode.OFF
    climate.handle_event(_season_event("*4*0*01##", "conditioning", "01"))
    assert climate.hvac_mode == HVACMode.COOL


def test_central_unit_shows_the_season_as_its_mode(mock_gateway, mock_entity_base_init):
    climate = _climate(mock_gateway, central=True, where="#0")
    climate.handle_event(_season_event("*4*0*#0##", "conditioning", "#0"))
    assert climate.hvac_mode == HVACMode.COOL
    climate.handle_event(_season_event("*4*1*#0##", SEASON_HEATING, "#0"))
    assert climate.hvac_mode == HVACMode.HEAT
