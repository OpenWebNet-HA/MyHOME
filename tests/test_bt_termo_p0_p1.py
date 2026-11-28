"""Regression tests for bt_termo P0 and P1 refinements in MyHOME."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.core import HomeAssistant
from OWNd.message import OWNHeatingEvent, OWNMessage

from custom_components.myhome.climate import MyHOMEClimate
from custom_components.myhome.gateway import (
    ALL_ACTUATORS_REQUEST,
    ALL_ZONE_REQUESTS,
    GENERAL_ZONE_REQUEST_REFUSED,
    KNOWN_GATEWAY_MODELS,
    SOUND_STATUS_REQUEST_REFUSED,
    STARTUP_ALL_ACTUATORS_REQUEST,
    ZONE_TEMPERATURE_POLL,
)
from custom_components.myhome.gateway_events import GatewayEventDispatcher
from custom_components.myhome.sensor import MyHOMEActuatorOutputSensor
from custom_components.myhome.switch import MyHOMEThermoSwitch
from custom_components.myhome.where_grammar import validate_thermo_trigger_command
from tests.test_f455_zone_polling import _handler


def _make_zone(
    hass: HomeAssistant,
    where: str = "2",
    *,
    heating: bool = True,
    cooling: bool = True,
    fan: bool = False,
    standalone: bool = True,
    central: bool = False,
) -> MyHOMEClimate:
    gw = MagicMock()
    gw.mac = "00:03:50:00:b7:00"
    gw.log_id = "[bt_termo]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.request_zone_dimension = MagicMock()
    gw.has_central_unit = not standalone or central
    gw.supports_zone_status = True
    zone = MyHOMEClimate(
        hass=hass,
        name=f"Zone {where}",
        device_id=f"4-{where}",
        who="4",
        where=where,
        heating=heating,
        cooling=cooling,
        fan=fan,
        standalone=standalone,
        central=central,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    zone.hass = hass
    zone.entity_id = f"climate.zone_{where}"
    zone.async_write_ha_state = MagicMock()
    zone.async_schedule_update_ha_state = MagicMock()
    return zone


# ── P0-1 & P0-5: Comfort/Eco, CU timed manual, extended 4xx/5xx, DIM 7 manual ──


@pytest.mark.parametrize(
    ("frame", "expected_mode", "expected_op"),
    [
        ("*4*108*2##", HVACMode.HEAT, "comfort"),
        ("*4*107*2##", HVACMode.HEAT, "eco"),
        ("*4*104*#2##", HVACMode.HEAT, "timed_manual"),
        ("*4*208*2##", HVACMode.COOL, "comfort"),
        ("*4*207*2##", HVACMode.COOL, "eco"),
        ("*4*204*#2##", HVACMode.COOL, "timed_manual"),
        ("*4*304*#2##", HVACMode.AUTO, "timed_manual"),
        ("*4*402*2##", HVACMode.OFF, "protection"),
        ("*4*502*2##", HVACMode.OFF, "protection"),
        ("*4*407*2##", HVACMode.AUTO, "eco"),
        ("*4*408*2##", HVACMode.AUTO, "comfort"),
        ("*4*410*2##", HVACMode.AUTO, "manual"),
        ("*4*507*2##", HVACMode.AUTO, "eco"),
        ("*4*508*2##", HVACMode.AUTO, "comfort"),
        ("*4*510*2##", HVACMode.AUTO, "manual"),
        ("*#4*2*7*1*0##", HVACMode.HEAT, "manual"),
        ("*#4*2*7*2*0##", HVACMode.COOL, "manual"),
        ("*#4*2*7*1*3##", HVACMode.HEAT, "comfort"),
        ("*#4*2*7*1*4##", HVACMode.HEAT, "eco"),
    ],
)
def test_p0_1_and_p0_5_zone_modes_and_operations(
    hass: HomeAssistant,
    frame: str,
    expected_mode: HVACMode,
    expected_op: str,
) -> None:
    zone = _make_zone(hass, "2", standalone=False)
    zone.handle_event(OWNHeatingEvent(frame))
    assert zone.hvac_mode == expected_mode
    assert zone.extra_state_attributes["zone_operation"] == expected_op


def test_p0_1_dim7_and_what_comfort_eco_never_fight(hass: HomeAssistant) -> None:
    """F461 emits *#4*2*7*1*3## followed by *4*108*2##; both agree on zone_operation='comfort'.

    Returning to a normal setpoint emits the season frame (*4*1*2##) in place of 107/108
    (f461_presets_live_2026-10-24 run 2), which must clear the stale eco/comfort operation.
    """
    zone = _make_zone(hass, "2")
    zone.handle_event(OWNHeatingEvent("*#4*2*7*1*3##"))
    assert zone.extra_state_attributes["zone_operation"] == "comfort"
    zone.handle_event(OWNHeatingEvent("*4*108*2##"))
    assert zone.extra_state_attributes["zone_operation"] == "comfort"
    zone.handle_event(OWNHeatingEvent("*#4*2*7*1*4##"))
    assert zone.extra_state_attributes["zone_operation"] == "eco"
    zone.handle_event(OWNHeatingEvent("*4*107*2##"))
    assert zone.extra_state_attributes["zone_operation"] == "eco"
    zone.handle_event(OWNHeatingEvent("*4*1*2##"))
    assert "zone_operation" not in zone.extra_state_attributes


# ── P0-3: DIM 19 single-side empty field across climate, sensor, and switch ──


def test_p0_3_dim19_single_side_empty_never_forces_idle_or_retracts_fan(
    hass: HomeAssistant,
) -> None:
    zone = _make_zone(hass, "2", fan=False)
    zone.handle_event(OWNHeatingEvent("*4*1*2##"))
    # Heating active with unmapped cooling side -> HEATING
    zone.handle_event(OWNHeatingEvent("*#4*2*19**1##"))
    assert zone.hvac_action == HVACAction.HEATING

    # Cooling off with unmapped heating side -> must NOT force IDLE!
    zone.handle_event(OWNHeatingEvent("*#4*2*19*0*##"))
    assert zone.hvac_action == HVACAction.HEATING

    # Unmapped cooling + heating fan off (code 5) enables fan mode and does not retract it
    zone.handle_event(OWNHeatingEvent("*#4*2*19**5##"))
    assert zone._fan is True
    assert zone.hvac_action == HVACAction.HEATING

    # Cooling valve off + unmapped heating side does NOT retract fan mode while heating side is known fan
    zone2 = _make_zone(hass, "3", fan=False)
    zone2.handle_event(OWNHeatingEvent("*#4*3#1*20*5##"))  # tentative fan mode from dim 20
    assert zone2._fan is True
    zone2.handle_event(OWNHeatingEvent("*#4*3*19*0*##"))  # only cooling side known non-fan
    assert zone2._fan is True
    zone2.handle_event(OWNHeatingEvent("*#4*3*19*0*0##"))  # both sides known non-fan -> retract
    assert zone2._fan is False


def test_p0_3_dim19_single_side_empty_sensor_and_switch_guards(hass: HomeAssistant) -> None:
    gw = MagicMock()
    gw.mac = "00:03:50:00:b7:01"
    gw.log_id = "[dim19]"
    gw.standalone_detected_zones = set()

    sensor = MyHOMEActuatorOutputSensor(
        hass=hass,
        name="Zone 2 Valve",
        device_id="4-2#1",
        where="2#1",
        zone="2",
        actuator="1",
        interface=None,
        standalone=True,
        manufacturer="BTicino",
        model="Valve",
        gateway=gw,
    )
    sensor.hass = hass
    sensor.async_schedule_update_ha_state = MagicMock()
    sensor.handle_event(OWNHeatingEvent("*#4*2#1*20*150##"))
    assert sensor.native_value == 50
    # Single-side empty with inactive known side -> zone_active is None -> must NOT zero out 50%!
    sensor.handle_event(OWNHeatingEvent("*#4*2*19*0*##"))
    assert sensor.native_value == 50
    # Full both-sides-off -> zeroes out 50% -> 0%
    sensor.handle_event(OWNHeatingEvent("*#4*2*19*0*0##"))
    assert sensor.native_value == 0

    sw = MyHOMEThermoSwitch(
        hass=hass,
        name="Zone 2 Actuator",
        entity_name=None,
        icon=None,
        icon_on=None,
        device_id="4-2",
        where="2",
        zone="2",
        actuator=None,
        interface=None,
        device_class=None,
        manufacturer="BTicino",
        model="Valve",
        turn_on_command=None,
        turn_off_command=None,
        gateway=gw,
    )
    sw.hass = hass
    sw.async_schedule_update_ha_state = MagicMock()
    sw._publish_state = MagicMock()
    sw.handle_event(OWNHeatingEvent("*#4*2*19**1##"))
    assert sw.is_on is True
    # Single-side empty with inactive known side -> must NOT flip switch OFF!
    sw.handle_event(OWNHeatingEvent("*#4*2*19*0*##"))
    assert sw.is_on is True
    sw.handle_event(OWNHeatingEvent("*#4*2*19*0*0##"))
    assert sw.is_on is False


# ── P0-4: Inbound #14 (on !supports_zone_status) and #11 write echoes ──


@pytest.mark.asyncio
async def test_p0_4_inbound_dim14_on_f455_and_dim11_write_echoes(hass: HomeAssistant) -> None:
    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:00:b7:04"
    handler.log_id = "[f455]"
    handler.generate_events = False
    handler.supports_zone_status = False
    handler.interface_zone_request_refused = True
    handler.send_status_request = AsyncMock()
    handler.request_zone_dimension = MagicMock()
    dispatcher = GatewayEventDispatcher(handler)
    dispatcher._is_active_for_who = lambda who: True  # type: ignore[method-assign]

    # Setpoint write echo on !supports_zone_status -> coalesced dimension 14 read, never *#4*Z##
    await dispatcher.process_message(OWNMessage.parse("*#4*2*#14*0215*3##"))
    handler.send_status_request.assert_not_awaited()
    handler.request_zone_dimension.assert_called_once_with("2", 14)

    # Fan-speed write echo -> coalesced dimension 11 read
    handler.request_zone_dimension.reset_mock()
    await dispatcher.process_message(OWNMessage.parse("*#4*2*#11*2##"))
    handler.request_zone_dimension.assert_called_once_with("2", 11)

    # Fan-speed READ request (*#4*2*11##) must NOT trigger a loop!
    handler.request_zone_dimension.reset_mock()
    await dispatcher.process_message(OWNMessage.parse("*#4*2*11##"))
    handler.request_zone_dimension.assert_not_called()


# ── P1-9: validate_thermo_trigger_command blocks bt_termo crash frames ──


@pytest.mark.parametrize(
    "bad_frame",
    [
        "*#4*#*#14*0215*1##",
        "*#4*1*#14*0200##",
        "*#4*1*#14*00215*1##",
        "*#4*1*#7*1*1##",
        "*#4*1234567890*#11*1##",
    ],
)
def test_p1_9_trigger_validator_refuses_bt_termo_crash_frames(bad_frame: str) -> None:
    with pytest.raises(ValueError, match=r"Refusing .*OpenWebNet"):
        validate_thermo_trigger_command(bad_frame)


# ── P1-10 & P0-2: 3488, F460, and Gen B capability tables ──


def test_p1_10_and_p0_2_gateway_capability_tables(hass: HomeAssistant) -> None:
    assert "3488" in KNOWN_GATEWAY_MODELS
    assert "3488" in GENERAL_ZONE_REQUEST_REFUSED
    assert "3488" in SOUND_STATUS_REQUEST_REFUSED
    assert "3488" in STARTUP_ALL_ACTUATORS_REQUEST
    assert ALL_ZONE_REQUESTS["3488"] == {20: ALL_ACTUATORS_REQUEST, 60: "*#4*0*60##"}
    assert ALL_ZONE_REQUESTS["F460"] == {20: ALL_ACTUATORS_REQUEST, 60: "*#4*0*60##"}

    h3488 = _handler(hass, "3488")
    assert h3488.general_zone_request_refused is True
    assert h3488.supports_zone_status is True
    assert h3488.humidity_request_refused is False
    assert h3488.sound_status_request_refused is True

    for gen_b in ("F453", "F453AV", "MH200N"):
        assert gen_b in GENERAL_ZONE_REQUEST_REFUSED
        assert gen_b in ZONE_TEMPERATURE_POLL
        hb = _handler(hass, gen_b)
        assert hb.general_zone_request_refused is True
        assert hb.zone_temperature_poll_needed is True


# ── P1-12: send_paced waits for late replies 9..12 after 8-reply ACK cap ──


@pytest.mark.asyncio
async def test_p1_12_send_paced_drains_late_replies_past_8_reply_cap(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QUIRK-08: *#4*0#0*20## ACKs after reply #8 while replies 9..12 arrive on the event session.

    send_paced resets its bus-quiet timer whenever late frames arrive, so the next command
    does not enter bt_termo's 0x63a0 wait loop until late replies have drained.
    """
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_PERIOD", 0.05)
    monkeypatch.setattr("custom_components.myhome.gateway.BUS_QUIET_CAP", 1.0)
    handler = _handler(hass, "F461")
    sent_times: list[tuple[str, float]] = []
    late_reply_times: list[float] = []

    async def _send_status(cmd):
        frame = str(cmd)
        now = time.monotonic()
        sent_times.append((frame, now))
        fut: asyncio.Future[float] = asyncio.get_running_loop().create_future()
        fut.set_result(now)
        if frame == ALL_ACTUATORS_REQUEST:
            # Simulate replies 9..11 arriving on the event session 20 ms, 40 ms, 60 ms AFTER ACK.
            async def _late_replies() -> None:
                for delay in (0.02, 0.02, 0.02):
                    await asyncio.sleep(delay)
                    ts = time.monotonic()
                    handler._last_event_frame_at = ts
                    late_reply_times.append(ts)

            asyncio.create_task(_late_replies())
        return fut

    handler.send_status_request = AsyncMock(side_effect=_send_status)
    delivered = await handler.send_paced([ALL_ACTUATORS_REQUEST, "*#4*0*60##"])
    assert delivered == [ALL_ACTUATORS_REQUEST, "*#4*0*60##"]
    assert len(late_reply_times) == 3
    # The second command must not be sent until after the last late reply + quiet settle!
    assert sent_times[1][1] >= late_reply_times[-1] + 0.04


# ── Pre-commit audit regression tests (Bugs #1-#7 & Hardening #1-#2) ──


def test_audit_bug1_104_204_recognized_as_central_zone_status(hass: HomeAssistant) -> None:
    """*4*104*#Z## and *4*204*#Z## must register central-unit ownership and cooling."""
    from custom_components.myhome.where_grammar import (
        central_report_zone,
        is_central_unit_report,
    )

    assert is_central_unit_report(OWNMessage.parse("*4*104*#2##")) is True
    assert is_central_unit_report(OWNMessage.parse("*4*204*#2##")) is True
    assert is_central_unit_report(OWNMessage.parse("*4*304*#2##")) is False
    assert central_report_zone(OWNMessage.parse("*4*204*#2##")) == "2"

    # Zero-config zone (standalone=None, cooling=False, has_central_unit=False):
    gw = MagicMock()
    gw.mac = "00:03:50:00:b7:00"
    gw.log_id = "[bt_termo]"
    gw.send = AsyncMock()
    gw.send_status_request = AsyncMock()
    gw.request_zone_dimension = MagicMock()
    gw.has_central_unit = False
    gw.supports_zone_status = True
    zone = MyHOMEClimate(
        hass=hass,
        name="Zone 2",
        device_id="4-2",
        who="4",
        where="2",
        heating=True,
        cooling=None,
        fan=False,
        standalone=None,
        central=False,
        manufacturer="BTicino",
        model="Heating Zone",
        gateway=gw,
    )
    zone.hass = hass
    zone.entity_id = "climate.zone_2"
    zone.async_write_ha_state = MagicMock()

    zone.handle_event(OWNMessage.parse("*4*204*#2##"))
    assert zone._standalone is False
    assert zone._cooling is True
    assert zone.hvac_mode == HVACMode.COOL
    assert zone.extra_state_attributes.get("zone_operation") == "timed_manual"


def test_audit_bug2_seasonless_4xx_5xx_on_standalone_with_and_without_central(
    hass: HomeAssistant,
) -> None:
    """Seasonless 407/408/410/507/508/510 must never leave standalone stuck OFF or trapped in AUTO."""
    # Case A: standalone zone on a plant WITH a central unit (AUTO is not advertised)
    zone_a = _make_zone(hass, where="2", standalone=True)
    zone_a._gateway_handler.has_central_unit = True
    zone_a._refresh_hvac_modes()
    assert HVACMode.AUTO not in zone_a.hvac_modes
    zone_a._handle_event(OWNMessage.parse("*4*1*2##"))
    zone_a._handle_event(OWNMessage.parse("*4*303*2##"))
    assert zone_a.hvac_mode == HVACMode.OFF
    zone_a._handle_event(OWNMessage.parse("*4*408*2##"))
    assert zone_a.hvac_mode == HVACMode.HEAT
    assert zone_a.hvac_action != HVACAction.OFF
    assert zone_a.extra_state_attributes.get("zone_operation") == "comfort"

    # Case B: standalone zone on a plant WITHOUT a central unit (AUTO is advertised)
    zone_b = _make_zone(hass, where="2", standalone=True)
    zone_b._gateway_handler.has_central_unit = False
    zone_b._refresh_hvac_modes()
    zone_b._handle_event(OWNMessage.parse("*4*1*2##"))
    assert zone_b.hvac_mode == HVACMode.HEAT
    zone_b._handle_event(OWNMessage.parse("*4*408*2##"))
    assert zone_b.hvac_mode == HVACMode.HEAT
    assert zone_b.extra_state_attributes.get("zone_operation") == "comfort"
    zone_b._handle_event(OWNMessage.parse("*4*1*2##"))
    assert zone_b.hvac_mode == HVACMode.HEAT
    assert zone_b.extra_state_attributes.get("zone_operation") is None

    # Even if 408 arrived before season was known (setting AUTO), returning *4*1*2## exits AUTO:
    zone_c = _make_zone(hass, where="2", standalone=True)
    zone_c._gateway_handler.has_central_unit = False
    zone_c._refresh_hvac_modes()
    zone_c._handle_event(OWNMessage.parse("*4*408*2##"))
    assert zone_c.hvac_mode == HVACMode.AUTO
    zone_c._handle_event(OWNMessage.parse("*4*1*2##"))
    assert zone_c.hvac_mode == HVACMode.HEAT
    assert zone_c.extra_state_attributes.get("zone_operation") is None


def test_audit_bug3_bug5_bug6_single_empty_dim19_publishes_and_polls_once(
    hass: HomeAssistant,
) -> None:
    """Single-empty inactive DIM 19 must publish state, poll dim 11 only once, and treat unknown side as unknown."""
    zone = _make_zone(hass, where="2", fan=False)
    zone.platform = MagicMock()
    zone.async_schedule_update_ha_state = MagicMock()
    zone._handle_event(OWNMessage.parse("*4*1*2##"))
    zone.async_schedule_update_ha_state.reset_mock()
    zone._gateway_handler.request_zone_dimension.reset_mock()

    # First *#4*2*19**5## (cooling unknown, heating fan coil off -> is_active is None)
    zone._handle_event(OWNMessage.parse("*#4*2*19**5##"))
    assert zone._fan is True
    assert zone.async_schedule_update_ha_state.call_count >= 1
    assert zone._gateway_handler.request_zone_dimension.call_count == 1
    assert zone._gateway_handler.request_zone_dimension.call_args[0] == ("2", 11)

    # Repeated *#4*2*19**5## frames must NOT re-poll dim 11 every time
    zone._handle_event(OWNMessage.parse("*#4*2*19**5##"))
    zone._handle_event(OWNMessage.parse("*#4*2*19**5##"))
    assert zone._gateway_handler.request_zone_dimension.call_count == 1

    # Bug #5: unknown side in _fan_coil_sides is not False
    zone2 = _make_zone(hass, where="3", fan=True)
    zone2._handle_event(OWNMessage.parse("*4*1*3##"))  # heating season
    zone2._handle_event(OWNMessage.parse("*#4*3*19*0*##"))  # cooling=False, heating unknown
    assert zone2._fan_coil_sides == {"cooling": False}
    assert zone2._season_has_fan_coil() is True


@pytest.mark.asyncio
async def test_audit_bug4_single_side_fan_coil_survives_restore(hass: HomeAssistant) -> None:
    """Single-side _fan_coil_sides must only emit/restore the known side, not False for unknown."""
    from homeassistant.core import State

    zone = _make_zone(hass, where="2", fan=False)
    zone._handle_event(OWNMessage.parse("*#4*2#1*20*5##"))  # fan coil detected on heating
    zone._handle_event(OWNMessage.parse("*#4*2*19*0*##"))  # cooling valve only, heating unknown
    attrs = zone.extra_state_attributes
    assert attrs.get("fan_coil_cooling") is False
    assert "fan_coil_heating" not in attrs

    # Simulate HA restart restoring those exact attributes
    restored = _make_zone(hass, where="2", fan=False)
    saved_attrs = dict(attrs)
    saved_attrs["supported_features"] = zone.supported_features
    saved_attrs["fan_mode"] = zone.fan_mode
    await restored.async_restore_last_state(State("climate.zone_2", "heat", saved_attrs))
    assert restored._fan_coil_sides == {"cooling": False}
    assert restored._has_no_fan_coil_sides() is False
    assert restored._fan is True


def test_audit_hardening1_single_empty_active_valve_keeps_running_fan_speed(
    hass: HomeAssistant,
) -> None:
    """Single-empty active valve DIM 19 must not zero running_fan_speed on the unmapped side."""
    zone = _make_zone(hass, where="2", fan=True)
    zone._handle_event(OWNMessage.parse("*#4*2#2*20*6##"))  # cooling fan speed 1 (low)
    assert zone._running_fan_speed == "low"
    # Cooling side unmapped (""), heating valve ON (1): fan side state is unknown!
    zone._handle_event(OWNMessage.parse("*#4*2*19**1##"))
    assert zone._running_fan_speed == "low"


@pytest.mark.asyncio
async def test_audit_bug7_gateway_events_rejects_malformed_where_and_zone_00(
    hass: HomeAssistant,
) -> None:
    """3-digit leading-zero where (001) and zone 00 must not crash or queue invalid reads."""
    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:8a:06:1d"
    handler.log_id = "[f455]"
    handler.supports_zone_status = False
    handler.interface_zone_request_refused = False
    handler.send_status_request = AsyncMock()
    handler.request_zone_dimension = MagicMock()
    handler.owned_whos = None
    dispatcher = GatewayEventDispatcher(handler)

    for bad_frame in (
        "*#4*001*#14*0215*3##",
        "*#4*001*#11*1##",
        "*#4*00*#14*0215*3##",
        "*#4*00*#11*1##",
        "*#4*2#4#123*#14*0215*3##",
    ):
        await dispatcher.process_message(OWNMessage.parse(bad_frame))
    handler.request_zone_dimension.assert_not_called()
    handler.send_status_request.assert_not_called()


def test_live_bus2_3488_who1013_model_128_resolved() -> None:
    """Live BTicino 3488 fw 3.2.5 reports WHO 13=200 + WHO 1013=128 without unknown_model repair."""
    from custom_components.myhome.identity import (
        GatewayIdentityEvidence,
        resolve_gateway_identity,
    )

    res = resolve_gateway_identity(
        GatewayIdentityEvidence(who13_code="200", who1013_code="128")
    )
    assert res.model == "3488"
    assert res.unknown_code is None

    res_manual = resolve_gateway_identity(
        GatewayIdentityEvidence(manual="MyHomeServer1", who13_code="200", who1013_code="128")
    )
    assert res_manual.unknown_code is None


@pytest.mark.parametrize(
    ("season_frame", "prot_frame", "expected_mode", "expected_op"),
    [
        ("*4*1101*#0##", "*4*402*#0##", HVACMode.HEAT, "antifreeze"),
        ("*4*1101*#0##", "*4*502*#0##", HVACMode.HEAT, "antifreeze"),
        ("*4*2101*#0##", "*4*402*#0##", HVACMode.COOL, "thermal_protection"),
        ("*4*2101*#0##", "*4*502*#0##", HVACMode.COOL, "thermal_protection"),
    ],
)
def test_group1a_central_extended_protection_402_502_preserves_season(
    hass: HomeAssistant,
    season_frame: str,
    prot_frame: str,
    expected_mode: HVACMode,
    expected_op: str,
) -> None:
    """Central unit extended protection 402/502 keeps active season + protection instead of OFF."""
    central = _make_zone(hass, "#0", standalone=False, central=True)
    central._handle_event(OWNMessage.parse(season_frame))
    assert central.hvac_mode == expected_mode
    central._handle_event(OWNMessage.parse(prot_frame))
    assert central.hvac_mode == expected_mode
    assert central.extra_state_attributes.get("central_operation") == expected_op


def test_group1b_gen_b_startup_all_actuators_request() -> None:
    """Gen B (F453, F453AV, MH200N) replace *#4*0## with *#4*0#0*20## at startup."""
    from custom_components.myhome.gateway import STARTUP_ALL_ACTUATORS_REQUEST

    for model in ("F453", "F453AV", "MH200N"):
        assert model in STARTUP_ALL_ACTUATORS_REQUEST


@pytest.mark.parametrize("manual_what", ["410", "510"])
@pytest.mark.parametrize(
    ("season_what", "expected_mode"),
    [("1", HVACMode.HEAT), ("0", HVACMode.COOL)],
)
def test_group1c_standalone_clears_manual_on_season_return_while_central_keeps_it(
    hass: HomeAssistant,
    manual_what: str,
    season_what: str,
    expected_mode: HVACMode,
) -> None:
    """Standalone zone returning from 410/510 via *4*1*Z##/*4*0*Z## clears stale zone_operation=manual."""
    standalone = _make_zone(hass, "2", standalone=True)
    standalone._handle_event(OWNMessage.parse(f"*4*{manual_what}*2##"))
    assert standalone.hvac_mode == HVACMode.AUTO
    assert standalone.extra_state_attributes.get("zone_operation") == "manual"
    standalone._handle_event(OWNMessage.parse(f"*4*{season_what}*2##"))
    assert standalone.hvac_mode == expected_mode
    assert standalone.extra_state_attributes.get("zone_operation") is None

    # Central-managed zone must still preserve zone_operation="manual" across *4*110*#3## -> *4*1*3##
    managed = _make_zone(hass, "3", standalone=False)
    managed._handle_event(OWNMessage.parse("*4*110*#3##"))
    assert managed.extra_state_attributes.get("zone_operation") == "manual"
    managed._handle_event(OWNMessage.parse("*4*1*3##"))
    assert managed.extra_state_attributes.get("zone_operation") == "manual"


def test_dim7_automatic_manual_preserved_across_trailing_season_frame(hass: HomeAssistant) -> None:
    """Standalone DIM 7 *#4*2*7*3*0## (automatic+manual) must keep HVACMode.AUTO across *4*1*2##."""
    standalone = _make_zone(hass, "2", standalone=True)
    standalone._handle_event(OWNMessage.parse("*#4*2*7*3*0##"))
    assert standalone.hvac_mode == HVACMode.AUTO
    assert standalone.extra_state_attributes.get("zone_operation") == "manual"
    standalone._handle_event(OWNMessage.parse("*4*1*2##"))
    assert standalone.hvac_mode == HVACMode.AUTO
    assert standalone.extra_state_attributes.get("zone_operation") == "manual"


def test_auto_mode_fallbacks_when_hvac_auto_unavailable(hass: HomeAssistant) -> None:
    """When HVACMode.AUTO is not offered (standalone on central plant), AUTO falls back to season/HEAT/COOL."""
    # Season fallback (heating) via *4*311*2## (generic automatic) when AUTO is not advertised
    zone_season = _make_zone(hass, "2", standalone=True, heating=True, cooling=True)
    zone_season._gateway_handler.has_central_unit = True
    zone_season._refresh_hvac_modes()
    assert HVACMode.AUTO not in zone_season.hvac_modes
    zone_season._handle_event(OWNMessage.parse("*4*1*2##"))
    zone_season._handle_event(OWNMessage.parse("*4*311*2##"))
    assert zone_season.hvac_mode == HVACMode.HEAT

    # Heat fallback (no season known)
    zone_heat = _make_zone(hass, "3", standalone=True, heating=True, cooling=False)
    zone_heat._gateway_handler.has_central_unit = True
    zone_heat._refresh_hvac_modes()
    zone_heat._handle_event(OWNMessage.parse("*4*311*3##"))
    assert zone_heat.hvac_mode == HVACMode.HEAT

    # Cool fallback (no season known, cool-only)
    zone_cool = _make_zone(hass, "4", standalone=True, heating=False, cooling=True)
    zone_cool._gateway_handler.has_central_unit = True
    zone_cool._refresh_hvac_modes()
    zone_cool._handle_event(OWNMessage.parse("*4*311*4##"))
    assert zone_cool.hvac_mode == HVACMode.COOL


@pytest.mark.asyncio
async def test_gateway_events_rejects_non_2digit_who4_interface_token(
    hass: HomeAssistant,
) -> None:
    """A WHO=4 setpoint write carrying a malformed >2-digit interface token is ignored cleanly."""
    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:8a:06:1d"
    handler.log_id = "[f455]"
    handler.supports_zone_status = False
    handler.interface_zone_request_refused = False
    handler.send_status_request = AsyncMock()
    handler.request_zone_dimension = MagicMock()
    handler.owned_whos = None
    dispatcher = GatewayEventDispatcher(handler)
    dispatcher._is_active_for_who = lambda who: True  # type: ignore[method-assign]

    msg = OWNMessage.parse("*#4*2*#14*0215*3##")
    msg._where_param = ["4", "123"]
    await dispatcher.process_message(msg)
    handler.request_zone_dimension.assert_not_called()
    handler.send_status_request.assert_not_called()


def test_follow_season_change_polls_dim11_after_single_empty_dim19(hass: HomeAssistant) -> None:
    """After single-empty DIM 19 (*#4*2*19**0##), switching to the unknown cooling side polls DIM 11."""
    zone = _make_zone(hass, "2", standalone=True)
    zone._fan = True
    zone._gateway_handler.request_zone_dimension = MagicMock(return_value=True)
    zone._handle_event(OWNMessage.parse("*4*1*2##"))
    zone._handle_event(OWNMessage.parse("*#4*2*19**0##"))
    zone._gateway_handler.request_zone_dimension.reset_mock()
    zone._handle_event(OWNMessage.parse("*4*0*2##"))
    zone._gateway_handler.request_zone_dimension.assert_called_once_with("2", 11)


def test_group1d_3488_and_067259_supported_models_and_spelling_alias() -> None:
    """BTicino 3488 and Legrand 067259 are selectable models and resolve cleanly to 3488."""
    from custom_components.myhome.const import SUPPORTED_GATEWAY_MODELS
    from custom_components.myhome.gateway import _MODEL_SPELLINGS, _resolve_model_key
    from custom_components.myhome.topology import (
        DOOR_ENTRY_WHO8_ONLY_MODELS,
        gateway_delegatable_whos,
        gateway_supported_whos,
        gateway_tier,
    )

    assert "3488" in SUPPORTED_GATEWAY_MODELS
    assert "067259" in SUPPORTED_GATEWAY_MODELS
    assert _MODEL_SPELLINGS.get("067259") == "3488"
    assert _resolve_model_key("3488") == "3488"
    assert _resolve_model_key("067259") == "3488"
    assert _resolve_model_key("BTicino 3488") == "3488"
    assert _resolve_model_key("BTicino3488") == "3488"
    assert _resolve_model_key("Legrand067259") == "3488"
    assert _resolve_model_key("BTicinoHometouch") == "3488"
    assert _resolve_model_key("BTicinoF461") == "F461"
    # Bare 3488 must never false-match IP/MAC/host strings
    assert _resolve_model_key("192.168.34.88") != "3488"
    assert _resolve_model_key("00:03:50:34:88:12") != "3488"
    assert _resolve_model_key("Gateway 192.168.1.1:3488") != "3488"
    assert _resolve_model_key("34.88") == ""
    assert _resolve_model_key("UnknownModel") == "UNKNOWNMODEL"
    # 3488 topology: Tier 2 (Touch), WHO 8 only (no WHO 6)
    assert "3488" in DOOR_ENTRY_WHO8_ONLY_MODELS
    assert gateway_tier("3488") == 2
    assert gateway_tier("BTicino 3488") == 2
    assert gateway_tier("067259") == 2
    assert gateway_tier("HOMETOUCH") == 2
    assert gateway_tier("192.168.34.88") == 3
    whos_3488 = gateway_supported_whos("3488")
    assert 8 in whos_3488
    assert 6 not in whos_3488
    assert 6 not in gateway_delegatable_whos("3488")
    # IP string containing 3488 stays Generic in topology lookup
    assert 6 not in gateway_supported_whos("192.168.34.88")


def test_resolve_model_key_and_topology_on_generic_profile_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3488 / 067259 still resolve when OWNd returns GenericGatewayProfile (e.g. OWNd 2.0.2)."""
    from OWNd.profiles import GenericGatewayProfile

    from custom_components.myhome.gateway import _resolve_model_key
    from custom_components.myhome.topology import _profile_and_model

    monkeypatch.setattr(
        "custom_components.myhome.gateway.get_gateway_profile",
        lambda _model: GenericGatewayProfile(),
    )
    monkeypatch.setattr(
        "OWNd.profiles.get_gateway_profile",
        lambda _model: GenericGatewayProfile(),
    )
    assert _resolve_model_key("Legrand 067259") == "3488"
    assert _resolve_model_key("BTicino 3488") == "3488"
    assert _resolve_model_key("Legrand067259") == "3488"
    assert _resolve_model_key("BTicino3488") == "3488"
    assert _resolve_model_key("192.168.34.88") != "3488"
    _prof, model_name = _profile_and_model("Legrand 067259")
    assert model_name == "3488"


def test_supports_zone_status_delegates_through_who4_target(hass: HomeAssistant) -> None:
    """supports_zone_status follows _who4_target() during failover/delegation (F461 -> F455)."""
    f461 = _handler(hass, "F461")
    f455 = _handler(hass, "F455")
    assert f461.supports_zone_status is True
    assert f455.supports_zone_status is False
    f461._who4_target = lambda: f455  # type: ignore[method-assign]
    assert f461.supports_zone_status is False


def test_seasonless_auto_cleared_across_dim7_off_mode_target_auto_and_central_mode(
    hass: HomeAssistant,
) -> None:
    """_seasonless_auto never leaks across DIM 7 OFF, explicit MODE_TARGET AUTO, or central updates."""
    # 1. DIM 7 OFF / protection clears _seasonless_auto
    z1 = _make_zone(hass, "2", standalone=True)
    z1._handle_event(OWNMessage.parse("*4*410*2##"))
    assert z1._seasonless_auto is True
    z1._handle_event(OWNMessage.parse("*#4*2*7*1*5##"))
    assert z1.hvac_mode == HVACMode.OFF
    assert z1._seasonless_auto is False

    # 2. Explicit MODE_TARGET AUTO (*4*310#0210*2##) clears _seasonless_auto so *4*1*2## keeps AUTO
    z2 = _make_zone(hass, "2", standalone=True)
    z2._handle_event(OWNMessage.parse("*4*410*2##"))
    assert z2._seasonless_auto is True
    z2._handle_event(OWNMessage.parse("*4*310#0210*2##"))
    assert z2._seasonless_auto is False
    z2._handle_event(OWNMessage.parse("*4*1*2##"))
    assert z2.hvac_mode == HVACMode.AUTO
    assert z2._zone_operation == "manual"

    # 3. _handle_central_mode_update clears _seasonless_auto on OFF and on HEAT/COOL
    z3 = _make_zone(hass, "2", standalone=False)
    z3._seasonless_auto = True
    z3._handle_central_mode_update(HVACMode.HEAT)
    assert z3._seasonless_auto is False
    z3._seasonless_auto = True
    z3._handle_central_mode_update(HVACMode.OFF)
    assert z3._seasonless_auto is False


@pytest.mark.asyncio
async def test_restored_auto_manual_zone_clears_manual_on_season_frame(hass: HomeAssistant) -> None:
    """Restoring AUTO + zone_operation='manual' and receiving *4*1*2## clears manual operation."""
    from homeassistant.core import State

    z = _make_zone(hass, "2", standalone=True)
    await z.async_restore_last_state(State("climate.zone_2", "auto", {"zone_operation": "manual"}))
    assert z._hvac_mode_restored is True
    assert z._seasonless_auto is False
    z._handle_event(OWNMessage.parse("*4*1*2##"))
    assert z.hvac_mode == HVACMode.HEAT
    assert z._zone_operation is None


def test_dim7_protection_generic_and_automatic_and_on_fallback(hass: HomeAssistant) -> None:
    """DIM 7 0*2 / 3*2 preserves zone_operation='protection'; 0*3 and 3*0 never stick in OFF."""
    # Protection 0*2 and 3*2 preserve "protection"
    z = _make_zone(hass, "2", standalone=True)
    z._handle_event(OWNMessage.parse("*4*402*2##"))
    assert z._zone_operation == "protection"
    z._handle_event(OWNMessage.parse("*#4*2*7*3*2##"))
    assert z.hvac_mode == HVACMode.OFF
    assert z._zone_operation == "protection"
    z._handle_event(OWNMessage.parse("*#4*2*7*0*2##"))
    assert z.hvac_mode == HVACMode.OFF
    assert z._zone_operation == "protection"

    # Generic 0*3 from OFF falls back to active mode (season-aware, never stuck OFF)
    z._handle_event(OWNMessage.parse("*#4*2*7*0*3##"))
    assert z.hvac_mode == HVACMode.HEAT
    assert z.hvac_action == HVACAction.IDLE
    assert z._zone_operation == "comfort"

    # Generic 0*3 while already in COOL keeps COOL (never flips to HEAT)
    z._handle_event(OWNMessage.parse("*4*0*2##"))
    assert z.hvac_mode == HVACMode.COOL
    z._handle_event(OWNMessage.parse("*#4*2*7*0*3##"))
    assert z.hvac_mode == HVACMode.COOL
    assert z._zone_operation == "comfort"

    # Standalone zone on a central plant (no AUTO advertised) receiving 3*0 from OFF
    z_cu = _make_zone(hass, "3", standalone=True)
    z_cu._gateway_handler.has_central_unit = True
    z_cu._cooling = True
    z_cu._refresh_hvac_modes()
    assert HVACMode.AUTO not in z_cu.hvac_modes
    z_cu._attr_hvac_mode = HVACMode.OFF
    z_cu._attr_hvac_action = HVACAction.OFF
    z_cu._season = "conditioning"
    z_cu._handle_event(OWNMessage.parse("*#4*3*7*3*0##"))
    assert z_cu.hvac_mode == HVACMode.COOL
    assert z_cu.hvac_action == HVACAction.IDLE
    assert z_cu._zone_operation == "manual"


@pytest.mark.asyncio
async def test_restore_does_not_overwrite_live_fan_coil_sides(hass: HomeAssistant) -> None:
    """Live DIM 19 fan_coil_sides stays intact when async_restore_last_state runs."""
    from homeassistant.core import State

    z = _make_zone(hass, "2", standalone=True)
    z._handle_event(OWNMessage.parse("*#4*2*19**5##"))
    assert z._fan_coil_sides == {"heating": True}
    await z.async_restore_last_state(
        State(
            "climate.zone_2",
            "heat",
            {"fan_coil_heating": False, "fan_coil_cooling": False},
        )
    )
    assert z._fan_coil_sides == {"heating": True, "cooling": False}


def test_single_side_empty_dim19_clears_pending_and_probes_missing_side(
    hass: HomeAssistant,
) -> None:
    """Single-side non-fan DIM 19 clears pending immediately and allows one probe before DIM 11."""
    # A7a: solicited *#4*3*19*0*## clears _fan_coil_sides_asked_at immediately (no 30 s stall)
    z1 = _make_zone(hass, "3", standalone=True)
    z1._season = "heating"
    z1._gateway_handler.request_zone_dimension = MagicMock(return_value=True)
    z1._ask_fan_coil_sides()
    assert z1._fan_coil_sides_pending() is True
    assert z1._season_has_fan_coil() is False
    z1._handle_event(OWNMessage.parse("*#4*3*19*0*##"))
    assert z1._fan_coil_sides == {"cooling": False}
    assert z1._fan_coil_sides_asked_at is None
    assert z1._fan_coil_sides_pending() is False
    assert z1._season_has_fan_coil() is True
    # Subsequent DIM 20 fan frame does not re-probe in a loop once probed
    z1._gateway_handler.request_zone_dimension.reset_mock()
    z1._handle_event(OWNMessage.parse("*#4*3#1*20*5##"))
    assert all(c.args != ("3", 19) for c in z1._gateway_handler.request_zone_dimension.call_args_list)

    # A7b: unsolicited *#4*3*19*0*## followed by tentative DIM 20 *5## probes DIM 19, not DIM 11
    z2 = _make_zone(hass, "3", standalone=True)
    z2._season = None
    z2._gateway_handler.request_zone_dimension = MagicMock(return_value=True)
    z2._handle_event(OWNMessage.parse("*#4*3*19*0*##"))
    z2._gateway_handler.request_zone_dimension.reset_mock()
    z2._handle_event(OWNMessage.parse("*#4*3#1*20*5##"))
    assert [c.args for c in z2._gateway_handler.request_zone_dimension.call_args_list] == [("3", 19)]
    assert z2._season_has_fan_coil() is False
    # Answering *#4*3*19*0*0## retracts tentative fan_mode with zero DIM 11 sent
    z2._handle_event(OWNMessage.parse("*#4*3*19*0*0##"))
    assert z2._fan is False
    assert [c.args for c in z2._gateway_handler.request_zone_dimension.call_args_list] == [("3", 19)]


@pytest.mark.asyncio
async def test_gateway_events_normalizes_leading_zero_and_bounds_f422_interface(
    hass: HomeAssistant,
) -> None:
    """Inbound #14/#11 echoes normalize 02 -> 2 and reject out-of-range F422 interfaces 00 / 16."""
    handler = MagicMock()
    handler.hass = hass
    handler.mac = "00:03:50:8a:06:1d"
    handler.log_id = "[f455]"
    handler.supports_zone_status = False
    handler.interface_zone_request_refused = False
    handler.send_status_request = AsyncMock()
    handler.request_zone_dimension = MagicMock()
    handler.owned_whos = None
    dispatcher = GatewayEventDispatcher(handler)
    dispatcher._is_active_for_who = lambda who: True  # type: ignore[method-assign]

    await dispatcher.process_message(OWNMessage.parse("*#4*02*#14*0215*3##"))
    handler.request_zone_dimension.assert_called_once_with("2", 14)

    handler.request_zone_dimension.reset_mock()
    for bad_iface in ("00", "16", "99"):
        msg = OWNMessage.parse("*#4*2*#14*0215*3##")
        msg._where_param = ["4", bad_iface]
        await dispatcher.process_message(msg)
    handler.request_zone_dimension.assert_not_called()


