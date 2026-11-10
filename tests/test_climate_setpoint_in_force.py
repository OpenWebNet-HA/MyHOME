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


# ------------------------------------------------------------------ H/L4684 derived local offset without dimension 13 (#466)

async def test_hl4684_zone_88_setpoint_sequence_derives_offset_and_compensates_writes(hass):
    """Replay H/L4684 (FW 6.0.11) + 3550 Zone 88 frames from #466 (09-11-53.json).

    On H/L4684, ``*#4*Z##`` is NACKed, the 3550's quarter-hour sweep only surfaces
    Dimension 0, Dimension 14, and Dimension 20 on the event session, and Dimension 13
    (``*#4*Z*13*OO##``) is never emitted. On each ``*#4*#88*#14*TTTT*3##`` write, the bus
    emits Dimension 0, Dimension 12 (effective setpoint = nominal + knob offset),
    ``*4*1*88##``, optionally Dimension 14 (present on frames 97-102, 118-123, 126-131;
    omitted on frames 104-108, 113-117, 132-136), and ``*4*110*#88##``.
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

