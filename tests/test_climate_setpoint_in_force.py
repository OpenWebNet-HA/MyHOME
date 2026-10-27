"""The setpoint in force (dimension 12) of a zone on a live F461 plant.

F461 2.0.11, standalone zone 2 (actuator 1 heating valve, actuator 2 fan coil), reply to
``*#4*2##``: ``*#4*2*0*0239##``, ``*#4*2*12*0235*3##``, ``*4*1*2##``,
``*#4*2*14*0210*3##``, ``*#4*2*13*00##``. Dimension 12 (23.5) is what the thermostat
regulates to; dimension 14 (21.0) is the commanded setpoint. Showing 21.0 hid a 2.5 degree
difference. After a manual write ``*#4*2*#14*0220*3##`` the gateway emits
``*#4*2*12*0220*3##`` and the season frame.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.core import State
from OWNd.message import OWNHeatingEvent

from custom_components.myhome import climate as climate_module
from custom_components.myhome.climate import MyHOMEClimate

F461_POLL_REPLY = (
    "*#4*2*0*0239##",
    "*#4*2*12*0235*3##",
    "*4*1*2##",
    "*#4*2*14*0210*3##",
    "*#4*2*13*00##",
)


def _zone(hass, where="2", gateway=None, **kwargs):
    if gateway is None:
        gateway = MagicMock()
        gateway.mac = "00:03:50:00:04:97"
        gateway.send = AsyncMock()
        gateway.send_status_request = AsyncMock()
        gateway.has_central_unit = False
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=True, central=False,
        manufacturer="BTicino", model="Heating Zone", gateway=gateway, **kwargs,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    return zone


def _feed(zone, *frames):
    for frame in frames:
        zone.handle_event(OWNHeatingEvent(frame))


# ------------------------------------------------------------------ dimension 12 vs 14

async def test_live_f461_poll_reply_shows_the_setpoint_in_force(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    assert zone.current_temperature == 23.9
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 23.5
    assert zone.extra_state_attributes["local_target_temperature"] == 23.5
    assert zone._target_temperature == 21.0  # the commanded setpoint is kept


async def test_dimension_14_before_dimension_12_shows_the_setpoint_in_force(hass):
    zone = _zone(hass)
    _feed(zone, "*#4*2*0*0239##", "*#4*2*14*0210*3##", "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*13*00##")
    assert zone.target_temperature == 23.5


@pytest.mark.parametrize(
    "frames",
    [
        ("*4*1*2##", "*#4*2*12*0235*3##", "*#4*2*14*0210*3##", "*#4*2*13*00##"),
        ("*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*12*0235*3##", "*#4*2*13*00##"),
    ],
    ids=["running-12-then-14", "running-14-then-12"],
)
async def test_running_zone_keeps_dimension_12_in_either_order(hass, frames):
    zone = _zone(hass)
    _feed(zone, *frames)
    assert zone.target_temperature == 23.5


async def test_second_poll_does_not_flip_the_target(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    _feed(zone, *F461_POLL_REPLY)
    assert zone.target_temperature == 23.5


async def test_offset_is_never_added_to_dimension_12(hass):
    """Dimension 12 already includes the knob offset: +2 with 23.5 in force stays 23.5."""
    zone = _zone(hass)
    _feed(zone, "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*02##")
    assert zone._local_offset == 2
    assert zone.target_temperature == 23.5
    _feed(zone, "*#4*2*13*02##", "*#4*2*12*0235*3##", "*#4*2*14*0210*3##")
    assert zone.target_temperature == 23.5


async def test_without_dimension_12_the_commanded_setpoint_plus_offset_is_shown(hass):
    zone = _zone(hass)
    _feed(zone, "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*01##")
    assert zone.target_temperature == 22.0


async def test_manual_write_echo_updates_the_setpoint_in_force(hass):
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    # *#4*2*#14*0220*3## written; the gateway answers with dimension 12 and the season
    _feed(zone, "*#4*2*12*0220*3##", "*4*1*2##")
    assert zone.target_temperature == 22.0
    assert zone._target_temperature == 22.0


async def test_protection_then_back_on_shows_the_nominal_again(hass, monkeypatch):
    """Off with a 7.0 protection setpoint, then on: the nominal comes back (#383), not 7.0."""
    clock = [1000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    _feed(zone, "*#4*2*12*0070*3##", "*4*102*2##")
    assert zone.hvac_mode == HVACMode.OFF
    assert zone.target_temperature == 7.0
    _feed(zone, "*#4*2*14*0210*3##", "*#4*2*13*00##")
    assert zone.target_temperature == 7.0
    _feed(zone, "*4*1*2##")
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 21.0
    # the next poll reports what the thermostat regulates to again
    _feed(zone, "*#4*2*12*0235*3##", "*#4*2*14*0210*3##")
    assert zone.target_temperature == 23.5


async def test_nothing_is_shown_before_any_setpoint_is_known(hass):
    """Leaving OFF before the zone ever reported a setpoint shows no target rather than an offset."""
    zone = _zone(hass)
    _feed(zone, "*#4*2*13*01##")
    zone._show_nominal()
    assert zone.target_temperature is None
    assert zone._local_target_reported is False


# ------------------------------------------------------------------ the write and the OFF -> on order

async def test_set_temperature_writes_dimension_14_and_shows_the_echo(hass):
    """``climate.set_temperature`` 22.0 writes ``*#4*2*#14*0220*3##``; the echo decides the display.

    The bus monitor of the live plant showed ``*#4*2*12*0220*3##`` then ``*4*1*2##`` after the
    write. A gateway that echoes only dimension 14 must also update the display: the write
    clears the dimension 12 hold, so neither echo leaves the old value in force on screen.
    """
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    assert zone.target_temperature == 23.5
    await zone.async_set_temperature(temperature=22.0)
    zone._gateway_handler.send.assert_awaited_once()
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*2*#14*0220*3##"
    assert zone.target_temperature == 23.5  # nothing echoed yet
    assert zone._local_target_reported is False
    _feed(zone, "*#4*2*14*0220*3##")  # a dimension 14-only echo is not stale any more
    assert zone.target_temperature == 22.0
    _feed(zone, "*#4*2*12*0220*3##", "*4*1*2##")  # the F461 echo
    assert zone.target_temperature == 22.0
    assert zone._local_target_reported is True


async def test_dimension_12_just_before_leaving_off_is_treated_as_protection(hass, monkeypatch):
    """The live reply order puts dimension 12 before the season frame.

    While the zone is still OFF that value may be the protection setpoint (#383), so leaving
    OFF shows the nominal setpoint; the next dimension 12 report of the running zone wins.
    """
    clock = [1000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    _feed(zone, "*#4*2*12*0070*3##", "*4*102*2##")
    assert zone.hvac_mode == HVACMode.OFF
    # next poll: the zone was switched on at the thermostat; dimension 12 precedes the season
    _feed(zone, "*#4*2*0*0239##", "*#4*2*12*0235*3##")
    assert zone.target_temperature == 23.5
    _feed(zone, "*4*1*2##")
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 21.0  # nominal until the running zone reports again
    assert zone._local_target_reported is False
    _feed(zone, "*#4*2*14*0210*3##", "*#4*2*13*00##")
    assert zone.target_temperature == 21.0
    _feed(zone, "*#4*2*12*0235*3##")
    assert zone.target_temperature == 23.5
    assert zone._local_target_reported is True


# ------------------------------------------------------------------ after a restart

async def test_restored_setpoint_in_force_does_not_get_the_offset_added_again(hass):
    """The saved `temperature` already carries the knob offset: dimension 13 after a restart leaves it alone.

    Restoring 23.5 into the nominal slot made the first `*#4*2*13*02##` show 25.5 (23.5 + 2 on
    top of a value that has the 2) until the next dimension 12 report; the live path showed 23.5.
    """
    zone = _zone(hass)
    _feed(zone, "*#4*2*12*0235*3##", "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*02##")
    saved = State("climate.zone_2", "heat", {"temperature": zone.target_temperature, **zone.extra_state_attributes})
    assert saved.attributes["temperature"] == 23.5 and saved.attributes["local_offset"] == 2
    restored = _zone(hass)
    await restored.async_restore_last_state(saved)
    assert restored.target_temperature == 23.5
    assert restored.extra_state_attributes["local_target_temperature"] == 23.5
    _feed(restored, "*#4*2*13*02##")  # the first report after the restart is the knob offset
    assert restored.target_temperature == 23.5
    _feed(restored, *F461_POLL_REPLY[:4], "*#4*2*13*02##")  # the next poll
    assert restored.target_temperature == 23.5
    assert restored._target_temperature == 21.0


async def test_restored_setpoint_follows_the_live_rule_before_dimension_12(hass):
    """Until the zone reports dimension 12 again, a dimension 14 shows the commanded setpoint plus the offset."""
    restored = _zone(hass)
    await restored.async_restore_last_state(State("climate.zone_2", "heat", {"temperature": 23.5, "local_offset": 2}))
    assert restored._local_target_reported is False
    _feed(restored, "*4*1*2##", "*#4*2*14*0210*3##")
    assert restored.target_temperature == 23.0  # as a fresh entity would show it
    _feed(restored, "*#4*2*12*0235*3##")
    assert restored.target_temperature == 23.5


@pytest.mark.parametrize(
    ("attributes", "after_offset_report"),
    [
        ({"temperature": 22.0}, 23.0),
        ({"temperature": 22.0, "local_offset": "knob"}, 23.0),
        ({"temperature": 22.0, "local_offset": None}, 23.0),
        ({"temperature": 22.0, "local_offset": "1.0"}, 22.0),
    ],
    ids=["no-offset-saved", "unreadable-offset", "offset-none", "offset-as-text"],
)
async def test_restored_setpoint_without_a_usable_offset_is_taken_as_the_nominal(hass, attributes, after_offset_report):
    """Without a saved offset nothing says the value carries one: it is the nominal, as before."""
    restored = _zone(hass)
    await restored.async_restore_last_state(State("climate.zone_2", "heat", attributes))
    assert restored.target_temperature == 22.0
    _feed(restored, "*#4*2*13*01##")
    assert restored.target_temperature == after_offset_report


async def test_unreadable_restored_setpoint_is_ignored(hass):
    restored = _zone(hass)
    await restored.async_restore_last_state(State("climate.zone_2", "heat", {"temperature": "warm", "local_offset": 2}))
    assert restored.target_temperature is None
    assert restored._local_offset == 0
