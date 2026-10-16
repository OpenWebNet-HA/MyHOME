"""The off state of a central unit carries the season (#699).

F454 (OpenWebNet-HA/MyHOME#697, comment 6129809756) and F453AV (OpenWebNet-HA/OWNd#97): the season
switch on the central gives ``*4*202*#0##`` (conditioning) and ``*4*102*#0##`` (heating).
The mode stays ``off``; the season is an attribute. The replay tests need an OWNd that
decodes the season on these frames (OWNd#97) and are skipped on older ones.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.climate.const import HVACMode
from OWNd.message import OWNEvent

from custom_components.myhome.climate import MESSAGE_TYPE_MODE, MyHOMEClimate

_NEEDS_OWND_SEASON = pytest.mark.skipif(
    getattr(OWNEvent.parse("*4*202*#0##"), "season", None) != "conditioning",
    reason="OWNd does not decode the season of mode frames (OWNd#97)",
)


@pytest.fixture(autouse=True)
def _entity_init():
    with patch("custom_components.myhome.myhome_device.Entity.__init__", return_value=None):
        yield


def _zone(where="#0", central=True):
    gw = MagicMock()
    gw.mac = "00:03:50:00:12:34"
    gw.unique_id = gw.mac
    gw.log_id = "[Test Gateway]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    hass = MagicMock()
    hass.data = {}
    c = MyHOMEClimate(
        hass=hass, name="Central", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=False, central=central,
        manufacturer="BTicino", model="Central Unit (3550)", gateway=gw,
    )
    c.platform = MagicMock()
    c.entity_id = "climate.test"
    c.async_schedule_update_ha_state = MagicMock()
    c.async_write_ha_state = MagicMock()
    return c


@_NEEDS_OWND_SEASON
@pytest.mark.parametrize(
    ("raw", "season"),
    [("*4*202*#0##", "conditioning"), ("*4*102*#0##", "heating"), ("*4*103*#0##", "heating")],
)
def test_central_off_frame_sets_the_season(raw, season):
    central = _zone()
    central.handle_event(OWNEvent.parse(raw))
    assert central.hvac_mode == HVACMode.OFF
    assert central.extra_state_attributes["season"] == season


@_NEEDS_OWND_SEASON
def test_season_switch_sequence_from_the_f454_plant():
    central = _zone()
    central.handle_event(OWNEvent.parse("*4*202*#0##"))
    assert central.extra_state_attributes["season"] == "conditioning"
    central.handle_event(OWNEvent.parse("*4*102*#0##"))
    assert central.extra_state_attributes["season"] == "heating"
    assert central.hvac_mode == HVACMode.OFF


@_NEEDS_OWND_SEASON
def test_setpoint_frame_keeps_the_season():
    central = _zone()
    central.handle_event(OWNEvent.parse("*4*110#0170*#0##"))
    assert central.hvac_mode == HVACMode.HEAT
    assert central.extra_state_attributes["season"] == "heating"


def _mode_event(mode, season):
    ev = MagicMock()
    ev.message_type = MESSAGE_TYPE_MODE
    ev.mode = mode
    ev.season = season
    ev.where = "#0"
    ev.human_readable_log = "Zone 0 mode"
    return ev


def test_season_of_a_mode_frame_is_kept_with_any_ownd():
    central = _zone()
    central.handle_event(_mode_event("off", "conditioning"))
    assert central.extra_state_attributes["season"] == "conditioning"


def test_mode_frame_without_a_season_keeps_the_last_one():
    central = _zone()
    central.handle_event(_mode_event("off", "heating"))
    central.handle_event(_mode_event("off", None))
    assert central.extra_state_attributes["season"] == "heating"


def test_old_ownd_without_season_attribute_does_not_break():
    central = _zone()
    ev = _mode_event("off", None)
    del ev.season
    central.handle_event(ev)
    assert "season" not in central.extra_state_attributes
