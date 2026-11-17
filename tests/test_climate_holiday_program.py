"""Test WHO 4 holiday plan and multi-day vacation program handling.

Verifies:
1. Holiday plan parameters (115#1102 / 215#2102) and multi-day vacation
   frames (13DDD#PPPP / 23DDD#PPPP / 33DDD#PPPP) expose `program` and
   `holiday_days` in extra_state_attributes.
2. Holiday parameters are never misinterpreted as negative target temperatures
   (legacy -10.2 °C bug).
3. Normal manual, setpoint, and off frames clear active holiday attributes.
4. Freedompro / Norbert transition: switching plant season while the central unit
   drops into Antifreeze (*4*102*...##) is correctly tracked by zone state.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import OWNd.message as own_message
import pytest
from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.core import State
from OWNd.message import OWNEvent

from custom_components.myhome.climate import (
    CLIMATE_MODE_HEAT,
    MESSAGE_TYPE_MODE_TARGET,
    MESSAGE_TYPE_SEASON,
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


def _parse_or_mock(
    raw: str, mode: str, program: int | None = None, holiday_days: int | None = None
):
    """Parse raw frame if current OWNd supports it, or supply expected typed event."""
    try:
        ev = OWNEvent.parse(raw)
        if (
            getattr(ev, "program", None) is not None
            or getattr(ev, "holiday_days", None) is not None
        ):
            return ev
    except Exception:
        pass
    ev = MagicMock(spec=OWNEvent)
    ev.message_type = "hvac_mode"
    ev.mode = mode
    ev.program = program
    ev.holiday_days = holiday_days
    ev.scenario = None
    ev.where = "#0"
    ev.zone = 0
    ev.human_readable_log = (
        f"Zone #0 mode is {mode}, program={program}, holiday_days={holiday_days}."
    )
    return ev


def test_holiday_plan_heating_program(mock_gateway, mock_entity_base_init):
    """*4*115#1102*#0## sets HEAT mode, exposes program=2, and does NOT corrupt setpoint."""
    climate = _climate(mock_gateway, central=True, where="#0")
    climate._target_temperature = 20.5
    climate._local_target_temperature = 20.5

    event = _parse_or_mock("*4*115#1102*#0##", mode="heat", program=2)
    climate.handle_event(event)

    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.target_temperature == 20.5
    assert climate.extra_state_attributes.get("program") == 2
    assert climate.extra_state_attributes.get("holiday_days") is None


def test_holiday_plan_cooling_program(mock_gateway, mock_entity_base_init):
    """*4*215#2102*#0## sets COOL mode and exposes program=2."""
    climate = _climate(mock_gateway, central=True, where="#0")

    event = _parse_or_mock("*4*215#2102*#0##", mode="cool", program=2)
    climate.handle_event(event)

    assert climate.hvac_mode == HVACMode.COOL
    assert climate.extra_state_attributes.get("program") == 2
    assert climate.extra_state_attributes.get("holiday_days") is None


def test_multi_day_vacation_frames(mock_gateway, mock_entity_base_init):
    """Multi-day vacation frames expose holiday_days and program across seasons."""
    climate = _climate(mock_gateway, central=True, where="#0")

    # Heating vacation 5 days, return program 2
    ev1 = _parse_or_mock("*4*13005#3102*#0##", mode="heat", program=2, holiday_days=5)
    climate.handle_event(ev1)
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.extra_state_attributes.get("holiday_days") == 5
    assert climate.extra_state_attributes.get("program") == 2

    # Conditioning vacation 3 days, return program 1
    ev2 = _parse_or_mock("*4*23003#3101*#0##", mode="cool", program=1, holiday_days=3)
    climate.handle_event(ev2)
    assert climate.hvac_mode == HVACMode.COOL
    assert climate.extra_state_attributes.get("holiday_days") == 3
    assert climate.extra_state_attributes.get("program") == 1

    # Generic vacation 10 days, return program 3
    ev3 = _parse_or_mock("*4*33010#3103*#0##", mode="auto", program=3, holiday_days=10)
    climate.handle_event(ev3)
    assert climate.hvac_mode == HVACMode.AUTO
    assert climate.extra_state_attributes.get("holiday_days") == 10
    assert climate.extra_state_attributes.get("program") == 3


def test_holiday_cleared_on_normal_mode_or_off(mock_gateway, mock_entity_base_init):
    """Normal mode frames clear holiday_days and program."""
    climate = _climate(mock_gateway, central=True, where="#0")

    # Set vacation
    ev_vac = _parse_or_mock("*4*13005#3102*#0##", mode="heat", program=2, holiday_days=5)
    climate.handle_event(ev_vac)
    assert climate.extra_state_attributes.get("holiday_days") == 5
    assert climate.extra_state_attributes.get("program") == 2

    # Switch to manual heat
    ev_manual = OWNEvent.parse("*4*110*#0##")
    climate.handle_event(ev_manual)
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.extra_state_attributes.get("holiday_days") is None
    assert climate.extra_state_attributes.get("program") is None

    # Switch to off
    ev_off = OWNEvent.parse("*4*303*#0##")
    climate.handle_event(ev_off)
    assert climate.hvac_mode == HVACMode.OFF
    assert climate.extra_state_attributes.get("holiday_days") is None
    assert climate.extra_state_attributes.get("program") is None


def test_zone_state_off_clears_all_holiday_attributes(mock_gateway, mock_entity_base_init):
    """A dimension-7 OFF/protection zone state clears program, holiday_days and scenario."""
    climate = _climate(mock_gateway, central=False, where="01")
    climate._program = 2
    climate._holiday_days = 5
    climate._scenario = 3

    climate._apply_zone_state(MagicMock(zone_state="off"))

    assert climate.hvac_mode == HVACMode.OFF
    assert climate.extra_state_attributes.get("program") is None
    assert climate.extra_state_attributes.get("holiday_days") is None
    assert climate.extra_state_attributes.get("scenario") is None


async def test_restore_last_state_preserves_holiday_attributes(mock_gateway, mock_entity_base_init):
    """Restoring state restores program and holiday_days."""
    climate = _climate(mock_gateway, central=True, where="#0")

    last_state = State(
        "climate.test",
        "heat",
        attributes={"program": 2, "holiday_days": 5, "scenario": 1, "temperature": 21.0},
    )
    await climate.async_restore_last_state(last_state)

    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.extra_state_attributes.get("program") == 2
    assert climate.extra_state_attributes.get("holiday_days") == 5
    assert climate.extra_state_attributes.get("scenario") == 1


async def test_restore_last_state_invalid_holiday_attributes(mock_gateway, mock_entity_base_init):
    """Restoring invalid holiday attributes handles ValueError/TypeError gracefully."""
    climate = _climate(mock_gateway, central=True, where="#0")

    last_state = State(
        "climate.test",
        "heat",
        attributes={"program": "invalid", "holiday_days": "invalid", "scenario": "invalid"},
    )
    await climate.async_restore_last_state(last_state)

    assert climate.extra_state_attributes.get("program") is None
    assert climate.extra_state_attributes.get("holiday_days") is None
    assert climate.extra_state_attributes.get("scenario") is None


def test_negative_setpoint_rejected_on_mode_target(mock_gateway, mock_entity_base_init):
    """Negative target temperature on mode_target is rejected and nominal kept."""
    climate = _climate(mock_gateway)
    climate._target_temperature = 21.0
    climate._local_target_temperature = 21.0

    ev = MagicMock(spec=OWNEvent)
    ev.message_type = MESSAGE_TYPE_MODE_TARGET
    ev.mode = CLIMATE_MODE_HEAT
    ev.set_temperature = -10.2
    ev.program = None
    ev.holiday_days = None
    ev.scenario = None
    ev.human_readable_log = "Corrupted temperature"

    climate.handle_event(ev)
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.target_temperature == 21.0  # preserved nominal!


def test_season_transition_and_antifreeze_scenario(mock_gateway, mock_entity_base_init):
    """Norbert's transition: central unit switches season, zones enter antifreeze."""
    central = _climate(mock_gateway, central=True, where="#0")
    zone1 = _climate(mock_gateway, central=False, where="01")

    # Zone 1 is initially running in manual heat at 21.0 °C
    zone1._target_temperature = 21.0
    zone1.handle_event(OWNEvent.parse("*4*110*01##"))
    assert zone1.hvac_mode == HVACMode.HEAT

    # Plant season switches to summer (Conditioning)
    if hasattr(own_message, "MESSAGE_TYPE_SEASON"):
        central_season_event = OWNEvent.parse("*4*0*#0##")
    else:
        central_season_event = MagicMock(spec=OWNEvent)
        central_season_event.message_type = MESSAGE_TYPE_SEASON
        central_season_event.season = "conditioning"
        central_season_event.where = "#0"
        central_season_event.zone = 0
        central_season_event.human_readable_log = "Central season is conditioning"

    central.handle_event(central_season_event)
    assert central.hvac_mode == HVACMode.COOL
    assert central.extra_state_attributes["season"] == "conditioning"

    # Central unit puts zone 1 into Antifreeze (*4*102*01##)
    zone1.handle_event(OWNEvent.parse("*4*102*01##"))
    assert zone1.hvac_mode == HVACMode.OFF
    assert zone1.hvac_action == HVACAction.OFF
    assert zone1.target_temperature == 21.0  # nominal setpoint preserved!

    # Central unit puts zone 1 into thermal protection (*4*202*01##)
    zone1.handle_event(OWNEvent.parse("*4*202*01##"))
    assert zone1.hvac_mode == HVACMode.OFF

    # Zone 1 receives summer operating state
    if hasattr(own_message, "MESSAGE_TYPE_SEASON"):
        zone_summer_event = OWNEvent.parse("*4*0*01##")
    else:
        zone_summer_event = MagicMock(spec=OWNEvent)
        zone_summer_event.message_type = MESSAGE_TYPE_SEASON
        zone_summer_event.season = "conditioning"
        zone_summer_event.where = "01"
        zone_summer_event.zone = 1
        zone_summer_event.human_readable_log = "Zone 1 season is conditioning"

    zone1.handle_event(zone_summer_event)
    assert zone1.hvac_mode == HVACMode.COOL
    assert zone1.extra_state_attributes["season"] == "conditioning"
