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
from homeassistant.exceptions import ServiceValidationError
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


def _zone(hass, where="2", gateway=None, standalone=True, **kwargs):
    if gateway is None:
        gateway = MagicMock()
        gateway.mac = "00:03:50:00:04:97"
        gateway.send = AsyncMock()
        gateway.send_status_request = AsyncMock()
        gateway.has_central_unit = False
    zone = MyHOMEClimate(
        hass=hass, name=f"Zone {where}", device_id=f"4-{where}", who="4", where=where,
        heating=True, cooling=True, fan=False, standalone=standalone, central=False,
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
    assert zone._target_temperature == 21.0


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


# ------------------------------------------------------------------ a restart while off / in protection (#383)

# Zone 3 of a 3550 (heating actuator only), F461 2.0.11 event session of the test plant,
# 2026-11-17 23:24:58 - 23:25:07: the zone in manual heating at 25.0, then put in antifreeze by
# the central unit (zone 3 `auto` while the 3550 is in antifreeze).
ZONE_3_MANUAL_25 = ("*#4*3*12*0250*3##", "*4*1*3##", "*4*110*#3##", "*#4*#3*14*0250*1##")
ZONE_3_ANTIFREEZE = ("*4*102*#3##", "*4*102*3##", "*#4*3*0*0255##", "*#4*3*12*0070*3##", "*4*102*3##")


def _central_plant_gateway():
    gateway = MagicMock()
    gateway.mac = "00:03:50:00:04:97"
    gateway.send = AsyncMock()
    gateway.send_status_request = AsyncMock()
    gateway.has_central_unit = True
    gateway.supports_zone_status = False  # an F455: the zone is not asked `*#4*3##` after a restart
    return gateway


def _saved(zone):
    return State(
        zone.entity_id,
        zone.hvac_mode.value,
        {"temperature": zone.target_temperature, **zone.extra_state_attributes},
    )


async def test_zone_saved_in_antifreeze_goes_back_to_its_nominal_after_a_restart(hass):
    """HEAT after a restart writes the setpoint the zone had, not the 7.0 it holds in antifreeze.

    The slider of a zone that is off or in protection shows the protection setpoint (#383), and
    that is what Home Assistant saves as `temperature`. Restored as the nominal, HEAT sent
    `*#4*#3*#14*0070*1##` (live F455 plant, 2026-11-18 02:03:28) and zone 3 heated to 7.0; the
    entity that had not restarted sent `*#4*#3*#14*0250*1##` (same plant, 2026-11-17 23:25:15).
    """
    live = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    _feed(live, *ZONE_3_MANUAL_25, *ZONE_3_ANTIFREEZE)
    assert live.hvac_mode == HVACMode.OFF
    assert live.target_temperature == 7.0
    saved = _saved(live)
    assert saved.state == "off"
    assert saved.attributes["temperature"] == 7.0
    assert saved.attributes["nominal_temperature"] == 25.0

    restored = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    await restored.async_restore_last_state(saved)
    assert restored.hvac_mode == HVACMode.OFF
    assert restored.target_temperature == 7.0  # still what the zone holds (#383)
    assert restored.extra_state_attributes["nominal_temperature"] == 25.0
    _feed(restored, "*4*102*3##", "*#4*3*12*0070*3##")  # the zone reports again, still in antifreeze
    assert restored.target_temperature == 7.0
    # The commanded setpoint is the saved one: asked until the zone reports it (a restored value is).
    assert "*#4*3*14##" in restored._silent_zone_reads()
    await restored.async_set_hvac_mode(HVACMode.HEAT)
    restored._gateway_handler.send.assert_awaited_once()
    assert str(restored._gateway_handler.send.await_args.args[0]) == "*#4*#3*#14*0250*1##"
    # the 3550's answer on the live plant (23:25:15.919 - 23:25:16.824)
    _feed(restored, "*4*1*3##", "*#4*3*0*0255##", "*4*110*#3##", "*#4*#3*14*0250*1##", "*#4*3*12*0250*3##")
    assert restored.hvac_mode == HVACMode.HEAT
    assert restored.target_temperature == 25.0


async def test_zone_saved_in_antifreeze_shows_its_nominal_when_it_leaves_protection(hass):
    """A zone that leaves protection by itself after a restart shows the nominal, as on the live path."""
    live = _zone(hass)
    _feed(live, *F461_POLL_REPLY, "*4*102*2##", "*#4*2*12*0070*3##")
    restored = _zone(hass)
    await restored.async_restore_last_state(_saved(live))
    assert restored.target_temperature == 7.0
    _feed(restored, "*4*1*2##")
    assert restored.hvac_mode == HVACMode.HEAT
    assert restored.target_temperature == 21.0  # the commanded setpoint, until dimension 12 says more
    _feed(restored, "*#4*2*12*0235*3##")
    assert restored.target_temperature == 23.5


async def test_saved_nominal_carries_no_knob_offset(hass):
    """`nominal_temperature` is the commanded value; the shown one has the knob offset on top."""
    live = _zone(hass)
    _feed(live, "*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*13*02##")
    assert live.target_temperature == 23.0
    assert live.extra_state_attributes["nominal_temperature"] == 21.0
    _feed(live, "*4*303*2##")
    restored = _zone(hass)
    await restored.async_restore_last_state(_saved(live))
    assert restored._target_temperature == 21.0
    assert restored._local_offset == 2
    _feed(restored, "*4*1*2##")
    assert restored.target_temperature == 23.0


@pytest.mark.parametrize(
    "attributes",
    [
        {"temperature": 7.0, "local_offset": 0},
        {"temperature": 7.0, "local_offset": 0, "nominal_temperature": None},
        {"temperature": 7.0, "local_offset": 0, "nominal_temperature": "warm"},
    ],
    ids=["saved-before-the-attribute", "nominal-none", "unreadable-nominal"],
)
async def test_zone_saved_off_without_a_nominal_does_not_write_the_saved_value(hass, attributes, caplog):
    """A state saved while off by an older version has no nominal: nothing is written, the call fails.

    The value shown while off cannot be told from a protection setpoint, so it is not written as
    a heating setpoint; the zone's next dimension 14 brings the nominal.
    """
    restored = _zone(hass, where="3", gateway=_central_plant_gateway(), standalone=None)
    await restored.async_restore_last_state(State("climate.zone_3", "off", attributes))
    assert restored.target_temperature == 7.0
    assert restored._target_temperature is None
    assert "nominal_temperature" not in restored.extra_state_attributes
    _feed(restored, "*4*102*3##", "*#4*3*12*0070*3##")
    with pytest.raises(ServiceValidationError, match="has not reported a setpoint yet"):
        await restored.async_set_hvac_mode(HVACMode.HEAT)
    restored._gateway_handler.send.assert_not_awaited()
    # what the zone said while off is its protection setpoint: the silent-zone poll still asks
    assert "*#4*3*14##" in restored._silent_zone_reads()
    _feed(restored, "*#4*#3*14*0250*1##")  # the 3550 reports the zone's setpoint
    assert "*#4*3*14##" not in restored._silent_zone_reads()
    assert restored.target_temperature == 7.0  # still in antifreeze
    await restored.async_set_hvac_mode(HVACMode.HEAT)
    assert str(restored._gateway_handler.send.await_args.args[0]) == "*#4*#3*#14*0250*1##"


@pytest.mark.parametrize("state", ["heat", "unknown"], ids=["running", "mode-not-known"])
async def test_zone_not_saved_off_keeps_the_restore_rule_of_820(hass, state):
    """Only a state saved as `off` is read that way: a running zone's value is its setpoint in force."""
    restored = _zone(hass)
    await restored.async_restore_last_state(
        State("climate.zone_2", state, {"temperature": 23.5, "local_offset": 2, "nominal_temperature": 20.0})
    )
    assert restored.target_temperature == 23.5
    assert restored._target_temperature == 21.5


async def test_central_unit_has_no_nominal_attribute_and_restores_its_setpoint_when_off(hass):
    gateway = _central_plant_gateway()
    central = MyHOMEClimate(
        hass=hass, name="Central", device_id="4-#0", who="4", where="#0", heating=True, cooling=True,
        fan=False, standalone=False, central=True, manufacturer="BTicino", model="Central Unit (3550)",
        gateway=gateway,
    )
    await central.async_restore_last_state(State("climate.central", "off", {"temperature": 21.5}))
    assert central.target_temperature == 21.5
    assert central._target_temperature == 21.5
    assert "nominal_temperature" not in central.extra_state_attributes


# ------------------------------------------------------------------ H/L4684 derived local offset when dimension 13 is absent (#466)

async def test_hl4684_zone_88_setpoint_sequence_derives_offset_and_compensates_writes(hass):
    """Replay H/L4684 (FW 6.0.11) + 3550 Zone 88 frames from #466 (09-11-53.json).

    On H/L4684, ``*#4*Z##`` is NACKed and the 3550's quarter-hour sweep only surfaces
    Dimension 0, Dimension 14, and Dimension 20 on the event session (no Dimension 13 in
    the 7 November sweep or write bursts; the 13 November follow-up trace 06-59-18.json
    confirmed H/L4684 answers direct ``*#4*Z*13##`` reads and forwards live knob turns).
    When Dimension 13 has not been reported yet, each ``*#4*#88*#14*TTTT*3##`` write burst
    emits Dimension 0, Dimension 12 (effective setpoint = nominal + knob offset),
    ``*4*1*88##``, optionally Dimension 14 (present on frames 97-102, 118-123, 126-131;
    omitted on frames 104-108, 113-117, 132-136), and ``*4*110*#88##``, deriving the
    knob offset as a fallback.
    """
    zone = _zone(hass, where="88", standalone=False)
    zone._gateway_handler.has_central_unit = True

    # 1. Quarter-hour 3550 sweep on H/L4684 (09-11-53.json frames 36-37, 45): Dimension 0, 14, 20
    _feed(zone, "*#4*88*0*0210##", "*#4*88*14*0170*3##", "*#4*88#1*20*5##")
    assert zone._target_temperature == 17.0
    assert zone._local_offset == 0
    assert zone.extra_state_attributes["knob_pos"] == "UNKNOWN"

    # 2. First write (09-11-53.json frames 97-102): includes Dimension 12 (0075) and Dimension 14 (0105)
    await zone.async_set_temperature(temperature=10.5)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*#88*#14*0105*3##"
    _feed(
        zone,
        "*#4*88*0*0210##",
        "*#4*88*12*0075*3##",
        "*4*1*88##",
        "*#4*88*14*0105*3##",
        "*4*110*#88##",
    )
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 7.5
    assert zone._target_temperature == 10.5
    assert zone.extra_state_attributes["local_offset"] == -3
    assert zone.extra_state_attributes["knob_pos"] == "-3"

    # 3. Second write (09-11-53.json frames 104-108, Dimension 14 omitted):
    #    user sets 24.0 °C -> compensated with -3 offset writes 27.0 °C (0270)
    zone._gateway_handler.send.reset_mock()
    await zone.async_set_temperature(temperature=24.0)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*#88*#14*0270*3##"
    _feed(
        zone,
        "*#4*88*0*0210##",
        "*#4*88*12*0240*3##",
        "*4*1*88##",
        "*4*110*#88##",
    )
    assert zone.target_temperature == 24.0
    assert zone._target_temperature == 27.0
    assert zone.extra_state_attributes["local_offset"] == -3
    assert zone.extra_state_attributes["knob_pos"] == "-3"

    # 4. Third write (09-11-53.json frames 113-117, Dimension 14 omitted):
    #    user sets 23.0 °C -> writes 26.0 °C (0260)
    zone._gateway_handler.send.reset_mock()
    await zone.async_set_temperature(temperature=23.0)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*#88*#14*0260*3##"
    _feed(
        zone,
        "*#4*88*0*0210##",
        "*#4*88*12*0230*3##",
        "*4*1*88##",
        "*4*110*#88##",
    )
    assert zone.target_temperature == 23.0
    assert zone._target_temperature == 26.0
    assert zone.extra_state_attributes["local_offset"] == -3

    # 5. Fifth write (09-11-53.json frames 126-131, Dimension 14 included):
    #    user sets 23.5 °C -> writes 26.5 °C (0265)
    zone._gateway_handler.send.reset_mock()
    await zone.async_set_temperature(temperature=23.5)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*#88*#14*0265*3##"
    _feed(
        zone,
        "*#4*88*0*0210##",
        "*#4*88*12*0235*3##",
        "*4*1*88##",
        "*#4*88*14*0265*3##",
        "*4*110*#88##",
    )
    assert zone.target_temperature == 23.5
    assert zone._target_temperature == 26.5
    assert zone.extra_state_attributes["local_offset"] == -3

    # 6. Sixth write (09-11-53.json frames 132-136, Dimension 14 omitted):
    #    user sets 27.0 °C -> writes 30.0 °C (0300)
    zone._gateway_handler.send.reset_mock()
    await zone.async_set_temperature(temperature=27.0)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*#88*#14*0300*3##"
    _feed(
        zone,
        "*#4*88*0*0210##",
        "*#4*88*12*0270*3##",
        "*4*1*88##",
        "*4*110*#88##",
    )
    assert zone.target_temperature == 27.0
    assert zone._target_temperature == 30.0
    assert zone.extra_state_attributes["local_offset"] == -3


async def test_hl4684_write_omitting_dimension_14_derives_offset_from_written_nominal(hass):
    """When H/L4684 omits Dimension 14 on the first write (09-13-19.json frames 136-141), the written nominal derives the offset."""
    zone = _zone(hass, where="55", standalone=False)
    zone._gateway_handler.has_central_unit = True

    await zone.async_set_temperature(temperature=24.0)
    assert str(zone._gateway_handler.send.await_args.args[0]) == "*#4*#55*#14*0240*3##"
    _feed(
        zone,
        "*#4*55*0*0221##",
        "*#4*55*12*0210*3##",
        "*4*1*55##",
        "*#4*55#1*20*5##",
        "*4*110*#55##",
    )
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 21.0
    assert zone._target_temperature == 24.0
    assert zone.extra_state_attributes["local_offset"] == -3
    assert zone.extra_state_attributes["knob_pos"] == "-3"


@pytest.mark.parametrize(
    ("where", "burst", "expected_local", "expected_nominal", "expected_offset", "expected_knob", "next_set", "expected_tx"),
    [
        (
            "33",
            ("*#4*33*0*0218##", "*#4*33*12*0260*3##", "*4*1*33##", "*#4*33*14*0270*3##", "*4*110*#33##"),
            26.0,
            27.0,
            -1,
            "-1",
            24.0,
            "*#4*#33*#14*0250*3##",
        ),
        (
            "22",
            ("*#4*22*0*0223##", "*#4*22*12*0240*3##", "*4*1*22##", "*#4*22*14*0260*3##", "*4*110*#22##"),
            24.0,
            26.0,
            -2,
            "-2",
            22.0,
            "*#4*#22*#14*0240*3##",
        ),
        (
            "44",
            ("*#4*44*0*0238##", "*#4*44*12*0210*3##", "*4*1*44##", "*#4*44*14*0240*3##", "*4*110*#44##"),
            21.0,
            24.0,
            -3,
            "-3",
            21.0,
            "*#4*#44*#14*0240*3##",
        ),
        (
            "55",
            ("*#4*55*0*0221##", "*#4*55*12*0200*3##", "*4*1*55##", "*#4*55*14*0230*3##", "*4*110*#55##"),
            20.0,
            23.0,
            -3,
            "-3",
            24.0,
            "*#4*#55*#14*0270*3##",
        ),
    ],
    ids=["zone-33-offset-minus-1", "zone-22-offset-minus-2", "zone-44-offset-minus-3", "zone-55-offset-minus-3"],
)
async def test_hl4684_captured_zone_bursts_derive_knob_offsets(
    hass, where, burst, expected_local, expected_nominal, expected_offset, expected_knob, next_set, expected_tx
):
    """Captured H/L4684 bursts for zones 33, 22, 44, and 55 (09-13-19.json) derive each zone's integer knob offset."""
    zone = _zone(hass, where=where, standalone=False)
    zone._gateway_handler.has_central_unit = True
    _feed(zone, *burst)
    assert zone.target_temperature == expected_local
    assert zone._target_temperature == expected_nominal
    assert zone.extra_state_attributes["local_offset"] == expected_offset
    assert zone.extra_state_attributes["knob_pos"] == expected_knob

    await zone.async_set_temperature(temperature=next_set)
    assert str(zone._gateway_handler.send.await_args.args[0]) == expected_tx


@pytest.mark.parametrize(
    ("frames", "expected_offset", "expected_knob", "expected_target", "expected_nominal"),
    [
        (
            ("*4*1*2##", "*#4*2*14*0240*3##", "*#4*2*12*0210*3##"),
            -3,
            "-3",
            21.0,
            24.0,
        ),
        (
            ("*#4*2*14*0240*3##", "*4*1*2##", "*#4*2*12*0210*3##"),
            -3,
            "-3",
            21.0,
            24.0,
        ),
        (
            ("*4*1*2##", "*#4*2*12*0230*3##", "*#4*2*14*0210*3##"),
            2,
            "+2",
            23.0,
            21.0,
        ),
        (
            ("*4*1*2##", "*#4*2*14*0210*3##", "*#4*2*12*0210*3##"),
            0,
            "UNKNOWN",
            21.0,
            21.0,
        ),
    ],
    ids=[
        "dim14-then-dim12-negative",
        "dim14-before-mode-then-dim12-negative",
        "dim12-then-dim14-positive",
        "dim14-then-dim12-zero-leaves-knob-unknown",
    ],
)
async def test_derived_offset_handles_both_frame_orders_and_signs(
    hass, frames, expected_offset, expected_knob, expected_target, expected_nominal
):
    """Paired Dimension 12 and 14 frames derive -3..+3 integer offsets in either arrival order."""
    zone = _zone(hass)
    _feed(zone, *frames)
    assert zone.target_temperature == expected_target
    assert zone._target_temperature == expected_nominal
    assert zone.extra_state_attributes["local_offset"] == expected_offset
    assert zone.extra_state_attributes["knob_pos"] == expected_knob


async def test_derived_nonzero_offset_resets_to_zero_when_knob_returned_to_center(hass):
    """Once a nonzero knob offset was derived, a later 12 == 14 pair resets local_offset to 0 and knob_pos to '0'."""
    zone = _zone(hass)
    _feed(zone, "*4*1*2##", "*#4*2*12*0210*3##", "*#4*2*14*0240*3##")
    assert zone.extra_state_attributes["local_offset"] == -3
    assert zone.extra_state_attributes["knob_pos"] == "-3"

    _feed(zone, "*#4*2*0*0215##", "*#4*2*12*0240*3##", "*#4*2*14*0240*3##")
    assert zone.extra_state_attributes["local_offset"] == 0
    assert zone.extra_state_attributes["knob_pos"] == "0"
    assert zone.target_temperature == 24.0
    assert zone._target_temperature == 24.0


async def test_explicit_dimension_13_prevents_derived_offset_override(hass):
    """Once Dimension 13 is reported, a later integer difference between 12 and 14 does not override it."""
    zone = _zone(hass)
    _feed(zone, "*4*1*2##", "*#4*2*13*00##", "*#4*2*12*0230*3##", "*#4*2*14*0210*3##")
    assert zone.extra_state_attributes["local_offset"] == 0
    assert zone.extra_state_attributes["knob_pos"] == "0"
    assert zone.target_temperature == 23.0


async def test_out_of_range_or_stale_dimension_pairs_do_not_derive_offset(hass, monkeypatch):
    """Differences outside -3..+3 °C or separated beyond the burst window do not derive a knob offset."""
    clock = [1000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass)
    # 1. Difference of -14 °C (e.g. protection 7.0 vs comfort 21.0) is outside -3..+3
    _feed(zone, "*4*1*2##", "*#4*2*12*0070*3##", "*#4*2*14*0210*3##")
    assert zone.extra_state_attributes["local_offset"] == 0
    assert zone.extra_state_attributes["knob_pos"] == "UNKNOWN"

    # 2. Separated beyond _PROTECTION_FRAME_WINDOW (2.0s)
    _feed(zone, "*#4*2*12*0200*3##")
    clock[0] += 5.0
    _feed(zone, "*#4*2*14*0220*3##")
    assert zone.extra_state_attributes["local_offset"] == 0
    assert zone.extra_state_attributes["knob_pos"] == "UNKNOWN"


async def test_n4692_standalone_knob_detents_update_target_without_dimension_12(hass, monkeypatch):
    """N4692 standalone Dimension 13 detents update target_temperature even 0.5s after a paired Dim 13 -> Dim 12 burst."""
    clock = [1000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass, where="1", standalone=False)
    zone._gateway_handler.has_central_unit = True

    # Mode-entry burst: *4*1*1## -> *#4*1*13*12## (-2) -> 50 ms -> *#4*1*12*0060*3## (6.0 °C)
    _feed(zone, "*4*1*1##", "*#4*1*13*12##")
    clock[0] += 0.05
    _feed(zone, "*#4*1*12*0060*3##")
    assert zone.target_temperature == 6.0
    assert zone._target_temperature == 8.0

    # 525 ms later, standalone live knob detent -1 (*#4*1*13*11##), repeated once, then 0 (*#4*1*13*00##)
    clock[0] += 0.525
    _feed(zone, "*#4*1*13*11##")
    assert zone.target_temperature == 7.0
    assert zone.extra_state_attributes["knob_pos"] == "-1"
    _feed(zone, "*#4*1*13*11##")
    assert zone.target_temperature == 7.0

    clock[0] += 0.69
    _feed(zone, "*#4*1*13*00##")
    assert zone.target_temperature == 8.0
    assert zone.extra_state_attributes["knob_pos"] == "0"


async def test_dimension_12_before_initial_dimension_13_reconciles_nominal_without_double_adding(hass):
    """Initial Dim 12 before Dim 13 keeps effective setpoint and reconciles _target_temperature once Dim 13 arrives."""
    zone = _zone(hass, where="1", standalone=False)
    zone._gateway_handler.has_central_unit = True

    _feed(zone, "*4*1*1##", "*#4*1*12*0050*3##", "*#4*1*13*13##")
    assert zone.target_temperature == 5.0
    assert zone._target_temperature == 8.0
    assert zone.extra_state_attributes["local_offset"] == -3

    # Subsequent standalone knob move to centre reaches 8.0 °C
    _feed(zone, "*#4*1*13*00##")
    assert zone.target_temperature == 8.0

    # Protection OFF cycle and return to heating preserves nominal 8.0 °C
    _feed(zone, "*4*102*1##", "*#4*1*13*5##", "*#4*1*12*0070*3##")
    assert zone.hvac_mode == HVACMode.OFF
    _feed(zone, "*4*1*1##", "*#4*1*13*01##")
    assert zone.hvac_mode == HVACMode.HEAT
    assert zone.target_temperature == 9.0


async def test_f461_repeated_poll_with_changed_knob_does_not_double_add_offset(hass, monkeypatch):
    """A second F461 *#4*Z## poll burst after an unmonitored knob change does not double-add the trailing Dim 13."""
    clock = [2000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass)
    _feed(zone, *F461_POLL_REPLY)
    assert zone.target_temperature == 23.5
    assert zone._target_temperature == 21.0
    assert zone.extra_state_attributes["local_offset"] == 0

    # Later poll reply where the knob was moved to +1 (*#4*2*12*0245*3## .. *#4*2*13*01##)
    clock[0] += 60.0
    _feed(
        zone,
        "*#4*2*0*0239##",
        "*#4*2*12*0245*3##",
        "*4*1*2##",
        "*#4*2*14*0210*3##",
        "*#4*2*13*01##",
    )
    assert zone.target_temperature == 24.5
    assert zone._target_temperature == 21.0
    assert zone.extra_state_attributes["local_offset"] == 1
    assert zone.extra_state_attributes["knob_pos"] == "+1"

    # A standalone live knob click to +2 0.5 s later adjusts target_temperature by +1.0 -> 25.5
    clock[0] += 0.5
    _feed(zone, "*#4*2*13*02##")
    assert zone.target_temperature == 25.5
    assert zone._target_temperature == 21.0
    assert zone.extra_state_attributes["local_offset"] == 2
    assert zone.extra_state_attributes["knob_pos"] == "+2"


async def test_paired_dim12_then_dim13_without_dim14_after_prior_knob_reconciles_nominal(hass, monkeypatch):
    """When Dim 12 arrives unpaired and Dim 13 follows within the burst window without Dim 14, _target_temperature is reconciled."""
    clock = [3000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass, where="1", standalone=False)
    zone._gateway_handler.has_central_unit = True

    _feed(zone, "*4*1*1##", "*#4*1*13*00##", "*#4*1*12*0080*3##")
    assert zone.target_temperature == 8.0
    assert zone._target_temperature == 8.0

    clock[0] += 10.0
    _feed(zone, "*#4*1*12*0090*3##")
    clock[0] += 0.05
    _feed(zone, "*#4*1*13*01##")
    assert zone.target_temperature == 9.0
    assert zone._target_temperature == 8.0
    assert zone.extra_state_attributes["local_offset"] == 1


async def test_dual_platform_frame_delivery_keeps_dim12_dim13_pairing_idempotent(hass, monkeypatch):
    """Dual WHO=4 routing (climate + sensor platforms) neither breaks Dim 13 -> Dim 12 pairing nor double-adds F461 Dim 13."""
    clock = [4000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])

    def _feed_twice(entity, *raw_frames):
        for raw in raw_frames:
            entity.handle_event(OWNHeatingEvent(raw))
            entity.handle_event(OWNHeatingEvent(raw))

    # 1. N4692 mode-entry burst (Dim 13 -> Dim 12) delivered twice, followed 0.5 s later by live Dim 13
    n4692 = _zone(hass, where="1", standalone=False)
    n4692._gateway_handler.has_central_unit = True
    _feed_twice(n4692, "*4*1*1##", "*#4*1*13*12##")
    clock[0] += 0.05
    _feed_twice(n4692, "*#4*1*12*0060*3##")
    assert n4692.target_temperature == 6.0
    clock[0] += 0.5
    _feed_twice(n4692, "*#4*1*13*11##")
    assert n4692.target_temperature == 7.0

    # 2. F461 poll burst (Dim 12 -> *4*1*2## -> Dim 14 -> Dim 13) delivered twice, then live Dim 13
    f461 = _zone(hass, where="2")
    _feed_twice(f461, *F461_POLL_REPLY)
    assert f461.target_temperature == 23.5
    clock[0] += 30.0
    _feed_twice(
        f461,
        "*#4*2*0*0239##",
        "*#4*2*12*0245*3##",
        "*4*1*2##",
        "*#4*2*14*0210*3##",
        "*#4*2*13*01##",
    )
    assert f461.target_temperature == 24.5
    clock[0] += 0.5
    _feed_twice(f461, "*#4*2*13*02##")
    assert f461.target_temperature == 25.5


async def test_manual_write_echo_does_not_swallow_subsecond_knob_detent_or_corrupt_nominal(
    hass, monkeypatch
):
    """A manual setpoint write echo (*#4*Z*12*TTTT*3## + *4*1*Z##) never pairs with a live Dim 13 knob turn within 2 s."""
    clock = [5000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass, where="1", standalone=False)
    zone._gateway_handler.has_central_unit = True

    _feed(zone, "*4*1*1##", "*#4*1*13*00##", "*#4*1*12*0080*3##")
    assert zone.target_temperature == 8.0
    assert zone._target_temperature == 8.0

    await zone.async_set_temperature(temperature=22.0)
    # Dual-platform delivery of the write echo (*#4*1*12*0220*3## + *4*1*1##)
    for raw in ("*#4*1*12*0220*3##", "*4*1*1##"):
        zone.handle_event(OWNHeatingEvent(raw))
        zone.handle_event(OWNHeatingEvent(raw))
    assert zone.target_temperature == 22.0
    assert zone._target_temperature == 22.0

    # Physical knob turned to +1 just 1.0 s after the write echo
    clock[0] += 1.0
    for _ in range(2):
        zone.handle_event(OWNHeatingEvent("*#4*1*13*01##"))
    assert zone.target_temperature == 23.0
    assert zone._target_temperature == 22.0

    # Returning the knob to 0 restores 22.0 °C (never drifts to 21.0 °C)
    clock[0] += 10.0
    _feed(zone, "*#4*1*13*00##")
    assert zone.target_temperature == 22.0
    assert zone._target_temperature == 22.0


async def test_slow_f455_poll_burst_over_two_seconds_pairs_dim13_via_dim14(hass, monkeypatch):
    """F455 *#4*Z## poll bursts taking >2.0 s from Dim 12 to Dim 13 (with Dim 14 in between) do not double-add offset."""
    clock = [6000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass, where="36", standalone=False)
    zone._gateway_handler.has_central_unit = True

    _feed(zone, "*4*1*36##", "*#4*36*13*00##", "*#4*36*12*0100*3##")
    assert zone.target_temperature == 10.0

    # Unmonitored knob change to +1, followed by a slow F455 poll burst:
    # +0.000 s Dim 12 (11.0 °C), +0.177 s *4*1*36##, +1.712 s Dim 14 (10.0 °C), +2.302 s Dim 13 (+1)
    clock[0] += 60.0
    _feed(zone, "*#4*36*12*0110*3##")
    clock[0] += 0.177
    _feed(zone, "*4*1*36##")
    clock[0] += 1.712 - 0.177
    _feed(zone, "*#4*36*14*0100*3##")
    clock[0] += 2.302 - 1.712
    _feed(zone, "*#4*36*13*01##")
    assert zone.target_temperature == 11.0
    assert zone._target_temperature == 10.0
    assert zone.extra_state_attributes["local_offset"] == 1


async def test_transient_non_numeric_dim13_while_active_preserves_base_on_next_numeric_detent(
    hass, monkeypatch
):
    """A transient non-numeric Dim 13 while active does not strand _local_target_temperature on subsequent numeric detents."""
    clock = [7000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass, where="1", standalone=False)
    zone._gateway_handler.has_central_unit = True

    _feed(zone, "*4*1*1##", "*#4*1*13*13##", "*#4*1*12*0050*3##")
    assert zone.target_temperature == 5.0
    assert zone._target_temperature == 8.0

    clock[0] += 5.0
    _feed(zone, "*#4*1*13*5##")
    assert zone.extra_state_attributes["knob_pos"] == "*"
    clock[0] += 0.3
    _feed(zone, "*#4*1*13*12##")
    assert zone.target_temperature == 6.0
    assert zone._target_temperature == 8.0
    assert zone.extra_state_attributes["knob_pos"] == "-2"


async def test_hl4684_derived_offset_and_3550_closing_frame_track_live_dim13_detents(
    hass, monkeypatch
):
    """H/L4684 zone whose initial offset was derived from Dim 12 + Dim 14 (#466) tracks live Dim 13 knob clicks."""
    clock = [8000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])
    zone = _zone(hass, where="33", standalone=False)
    zone._gateway_handler.has_central_unit = True

    # Real H/L4684 3550 push burst: Dim 12 = 26.0, Dim 14 = 27.0 -> derived offset -1, closed by *4*110*#33##
    _feed(
        zone,
        "*#4*33*0*0218##",
        "*#4*33*12*0260*3##",
        "*4*1*33##",
        "*#4*33*14*0270*3##",
        "*4*110*#33##",
    )
    assert zone.target_temperature == 26.0
    assert zone._target_temperature == 27.0
    assert zone.extra_state_attributes["local_offset"] == -1

    # Sub-second live knob click to -2 (*#4*33*13*12## -> *4*110*#33##) updates target_temperature to 25.0 °C
    clock[0] += 0.5
    _feed(zone, "*#4*33*13*12##", "*4*110*#33##")
    assert zone.target_temperature == 25.0
    assert zone.extra_state_attributes["local_offset"] == -2

    # Next knob click to -3 and back to -1 tracks 24.0 -> 26.0 °C
    clock[0] += 3.0
    _feed(zone, "*#4*33*13*13##", "*4*110*#33##")
    assert zone.target_temperature == 24.0
    clock[0] += 3.0
    _feed(zone, "*#4*33*13*11##", "*4*110*#33##")
    assert zone.target_temperature == 26.0

    # Central mode+target frame (*4*110#0200*#33##) updates nominal to 20.0 + (-1) = 19.0 °C cleanly
    _feed(zone, "*4*110#0200*#33##")
    assert zone.target_temperature == 19.0


async def test_startup_dim13_dim14_dim12_before_mode_hydrates_base_and_rounds_tenth_degrees(
    hass, monkeypatch
):
    """Startup Dim 13 + Dim 14 + Dim 12 before *4*1*Z## hydrates _local_target_base, and 0.1 °C restore avoids float drift."""
    clock = [9000.0]
    monkeypatch.setattr(climate_module.time, "monotonic", lambda: clock[0])

    # 1. Startup Dim 13 (-1) + Dim 14 (21.2) + Dim 12 (20.2) while mode is None, then *4*1*2## -> live Dim 13 (-3)
    zone = _zone(hass, where="2")
    _feed(zone, "*#4*2*13*11##", "*#4*2*14*0212*3##", "*#4*2*12*0202*3##", "*4*1*2##")
    assert zone.target_temperature == 20.2
    assert zone._target_temperature == 21.2
    clock[0] += 5.0
    _feed(zone, "*#4*2*13*13##")
    assert zone.target_temperature == 18.2

    # 2. Restored 0.1 °C setpoint with offset +3 has exact 0.1 °C rounding (never 18.099999999999998)
    restored = _zone(hass, where="3")
    await restored.async_restore_last_state(
        State(
            "climate.zone_3",
            HVACMode.HEAT,
            {"temperature": 21.1, "local_offset": 3},
        )
    )
    assert restored._target_temperature == 18.1
    assert restored.target_temperature == 21.1
    _feed(restored, "*4*102*3##")
    _feed(restored, "*4*1*3##")
    assert restored.target_temperature == 21.1
