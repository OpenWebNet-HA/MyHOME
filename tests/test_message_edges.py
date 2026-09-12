"""Tests targeting remaining uncovered branches in message.py to maximize coverage."""
import datetime
from unittest.mock import patch

import pytest
from OWNd.message import (
    CLIMATE_MODE_AUTO,
    CLIMATE_MODE_COOL,
    CLIMATE_MODE_HEAT,
    CLIMATE_MODE_OFF,
    MESSAGE_TYPE_ACTION,
    OWNAlarmCommand,
    OWNAlarmEvent,
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNAuxEvent,
    OWNAVCommand,
    OWNCENPlusEvent,
    OWNCommand,
    OWNDryContactCommand,
    OWNDryContactEvent,
    OWNEnergyCommand,
    OWNEnergyEvent,
    OWNEvent,
    OWNGatewayCommand,
    OWNGatewayEvent,
    OWNHeatingCommand,
    OWNHeatingEvent,
    OWNLightingCommand,
    OWNLightingEvent,
    OWNMessage,
    OWNSceneEvent,
    OWNSignaling,
    OWNSoundCommand,
    OWNSoundEvent,
    OWNStatusRequest,
)

# ── OWNMessage Base Class Parsing ──────────────────────────────────────────

class TestOWNMessageBase:
    """Cover all regex paths in OWNMessage.__init__."""

    def test_dimension_request_reply(self):
        """*#WHO*WHERE*DIMENSION*VAL1*VALn## pattern."""
        msg = OWNMessage("*#4*1*0*0225##")
        assert msg.is_valid is True
        assert msg.who == 4

    def test_dimension_writing(self):
        """*#WHO*WHERE*#DIMENSION*VAL1*VALn## pattern."""
        msg = OWNMessage("*#1*21*#1*150*0##")
        assert msg.is_valid is True

    def test_dimension_request(self):
        """*#WHO*WHERE*DIMENSION## pattern."""
        msg = OWNMessage("*#4*1*0##")
        assert msg.is_valid is True

    def test_command_translation(self):
        """WHAT=1000 should set family to COMMAND_TRANSLATION."""
        msg = OWNMessage("*1*1000*21##")
        assert msg.is_valid is True

    def test_invalid_message(self):
        msg = OWNMessage("not_a_valid_message")
        assert msg.is_valid is False

    def test_message_parse_status(self):
        result = OWNMessage.parse("*1*1*21##")
        assert isinstance(result, OWNLightingEvent)

    def test_message_parse_signaling_ack(self):
        result = OWNMessage.parse("*#*1##")
        assert isinstance(result, OWNSignaling)

    def test_message_parse_signaling_nack(self):
        result = OWNMessage.parse("*#*0##")
        assert isinstance(result, OWNSignaling)

    def test_message_parse_unknown(self):
        result = OWNMessage.parse("garbage")
        assert result is None

    def test_message_parse_command_session(self):
        result = OWNMessage.parse("*99*0##")
        assert isinstance(result, OWNSignaling)

    def test_message_parse_event_session(self):
        result = OWNMessage.parse("*99*1##")
        assert isinstance(result, OWNSignaling)

    def test_where_param_parsing(self):
        """Messages with WHERE parameters (e.g. #zone#param)."""
        msg = OWNMessage("*#4*0#1*0*0225##")
        assert msg.is_valid is True


# ── Heating Event Edge Cases ──────────────────────────────────────────────

class TestHeatingEdgeCases:
    """Cover remaining branches in OWNHeatingEvent."""

    def test_mode_off_variant_103(self):
        msg = OWNEvent.parse("*4*103*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_OFF

    def test_mode_off_variant_203(self):
        msg = OWNEvent.parse("*4*203*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_OFF

    def test_mode_off_variant_102(self):
        msg = OWNEvent.parse("*4*102*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_OFF

    def test_mode_off_variant_202(self):
        msg = OWNEvent.parse("*4*202*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_OFF

    def test_mode_off_variant_302(self):
        msg = OWNEvent.parse("*4*302*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_OFF

    def test_mode_cool_variant_210(self):
        msg = OWNEvent.parse("*4*210*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_COOL

    def test_mode_cool_variant_211(self):
        msg = OWNEvent.parse("*4*211*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_COOL

    def test_mode_cool_variant_215(self):
        msg = OWNEvent.parse("*4*215*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_COOL

    def test_mode_cool_weekly_2101(self):
        msg = OWNEvent.parse("*4*2101*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_COOL

    def test_mode_cool_scenario_2201(self):
        msg = OWNEvent.parse("*4*2201*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_COOL

    def test_mode_heat_variant_110(self):
        msg = OWNEvent.parse("*4*110*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_HEAT

    def test_mode_heat_variant_111(self):
        msg = OWNEvent.parse("*4*111*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_HEAT

    def test_mode_heat_variant_115(self):
        msg = OWNEvent.parse("*4*115*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_HEAT

    def test_mode_heat_weekly_1101(self):
        msg = OWNEvent.parse("*4*1101*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_HEAT

    def test_mode_heat_scenario_1201(self):
        msg = OWNEvent.parse("*4*1201*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_HEAT

    def test_mode_auto_variant_310(self):
        msg = OWNEvent.parse("*4*310*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_AUTO

    def test_mode_auto_variant_315(self):
        msg = OWNEvent.parse("*4*315*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_AUTO

    def test_mode_auto_weekly_23001(self):
        msg = OWNEvent.parse("*4*23001*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_AUTO

    def test_mode_auto_weekly_13001(self):
        msg = OWNEvent.parse("*4*13001*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode == CLIMATE_MODE_AUTO

    def test_unknown_mode(self):
        msg = OWNEvent.parse("*4*999*1##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.mode is None

    def test_valve_cooling_on(self):
        msg = OWNEvent.parse("*#4*1*19*1*0##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.message_type == MESSAGE_TYPE_ACTION

    def test_valve_cooling_opened(self):
        msg = OWNEvent.parse("*#4*1*19*2*0##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_cooling_closed(self):
        msg = OWNEvent.parse("*#4*1*19*3*0##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_cooling_stopped(self):
        msg = OWNEvent.parse("*#4*1*19*4*0##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_cooling_fan_on(self):
        msg = OWNEvent.parse("*#4*1*19*6*0##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_cooling_fan_off(self):
        msg = OWNEvent.parse("*#4*1*19*5*0##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_heating_on(self):
        msg = OWNEvent.parse("*#4*1*19*0*1##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_heating_opened(self):
        msg = OWNEvent.parse("*#4*1*19*0*2##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_heating_closed(self):
        msg = OWNEvent.parse("*#4*1*19*0*3##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_heating_stopped(self):
        msg = OWNEvent.parse("*#4*1*19*0*4##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_heating_fan_on(self):
        msg = OWNEvent.parse("*#4*1*19*0*6##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_valve_heating_fan_off(self):
        msg = OWNEvent.parse("*#4*1*19*0*5##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_off(self):
        msg = OWNEvent.parse("*#4*1#1*20*0##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.message_type == MESSAGE_TYPE_ACTION

    def test_actuator_on(self):
        msg = OWNEvent.parse("*#4*1#1*20*1##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_opened(self):
        msg = OWNEvent.parse("*#4*1#1*20*2##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_closed(self):
        msg = OWNEvent.parse("*#4*1#1*20*3##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_stopped(self):
        msg = OWNEvent.parse("*#4*1#1*20*4##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_fan_on_speed(self):
        msg = OWNEvent.parse("*#4*1#1*20*6##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_fan_on_speed_high(self):
        msg = OWNEvent.parse("*#4*1#1*20*8##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_fan_on_auto(self):
        """Fan mode >= 4 is auto speed."""
        msg = OWNEvent.parse("*#4*1#1*20*9##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_actuator_fan_off(self):
        msg = OWNEvent.parse("*#4*1#1*20*5##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_fan_speed_auto(self):
        """Fan speed 0 = auto."""
        msg = OWNEvent.parse("*#4*1*11*0##")
        assert isinstance(msg, OWNHeatingEvent)

    def test_local_offset_knob_off(self):
        """Value 4 reports local control off, not a numeric offset."""
        msg = OWNEvent.parse("*#4*1*13*4##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.local_offset is None
        assert msg.local_control_state == "local_off"

    def test_local_offset_knob_5(self):
        msg = OWNEvent.parse("*#4*1*13*5##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.local_offset is None
        assert msg.local_control_state == "local_protection"

    def test_local_offset_zero_single_digit(self):
        msg = OWNEvent.parse("*#4*1*13*0##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.local_offset == 0

    def test_heating_central_local_command(self):
        """Commands with #0#zone format."""
        cmd = OWNHeatingCommand.set_mode("#0#1", CLIMATE_MODE_OFF)
        assert cmd is not None

    def test_heating_temp_standalone(self):
        cmd = OWNHeatingCommand.set_temperature("1", 21.0, CLIMATE_MODE_HEAT, standalone=True)
        assert cmd is not None

    def test_heating_temp_zone_zero_standalone(self):
        cmd = OWNHeatingCommand.set_temperature("#0", 21.0, CLIMATE_MODE_AUTO, standalone=True)
        assert cmd is not None

    def test_heating_set_mode_standalone(self):
        cmd = OWNHeatingCommand.set_mode("1", CLIMATE_MODE_OFF, standalone=True)
        assert cmd is not None

    def test_heating_set_mode_zone_zero(self):
        cmd = OWNHeatingCommand.set_mode("#0", CLIMATE_MODE_OFF, standalone=False)
        assert cmd is not None

    def test_heating_zone_from_param(self):
        """Zone 0 with where_param should use the param as zone."""
        msg = OWNEvent.parse("*#4*0#5*0*0225##")
        assert isinstance(msg, OWNHeatingEvent)
        assert msg.zone == 5


# ── Alarm Event Edge Cases ────────────────────────────────────────────────

class TestAlarmEdgeCases:
    def test_alarm_deactivation(self):
        msg = OWNEvent.parse("*5*2**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_delay_end(self):
        msg = OWNEvent.parse("*5*3**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_battery_fault(self):
        msg = OWNEvent.parse("*5*4**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_battery_ok(self):
        msg = OWNEvent.parse("*5*5**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_no_network(self):
        msg = OWNEvent.parse("*5*6**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_network_present(self):
        msg = OWNEvent.parse("*5*7**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_disengage(self):
        msg = OWNEvent.parse("*5*9**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_battery_unloads(self):
        msg = OWNEvent.parse("*5*10**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_active_zone(self):
        msg = OWNEvent.parse("*5*11*#1##")
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.is_active is True

    def test_alarm_reset_technical(self):
        msg = OWNEvent.parse("*5*13**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_no_reception(self):
        msg = OWNEvent.parse("*5*14**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_tampering(self):
        msg = OWNEvent.parse("*5*16**##")
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.is_alarm is True

    def test_alarm_anti_panic(self):
        msg = OWNEvent.parse("*5*17**##")
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.is_alarm is True

    def test_alarm_non_active_zone(self):
        msg = OWNEvent.parse("*5*18*#1##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_start_programming(self):
        msg = OWNEvent.parse("*5*26**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_stop_programming(self):
        msg = OWNEvent.parse("*5*27**##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_silent(self):
        msg = OWNEvent.parse("*5*31**##")
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.is_alarm is True

    def test_alarm_zone_c(self):
        """Zone #12 maps to 'c'."""
        msg = OWNEvent.parse("*5*1*#12##")
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.zone == "c"

    def test_alarm_zone_f(self):
        """Zone #15 maps to 'f'."""
        msg = OWNEvent.parse("*5*1*#15##")
        assert isinstance(msg, OWNAlarmEvent)
        assert msg.zone == "f"

    def test_alarm_sensor_in_input_zone(self):
        """Zone 0 + sensor ID."""
        msg = OWNEvent.parse("*5*1*01##")
        assert isinstance(msg, OWNAlarmEvent)

    def test_alarm_control_panel(self):
        """Single digit where = control panel."""
        msg = OWNEvent.parse("*5*1*1##")
        assert isinstance(msg, OWNAlarmEvent)


# ── Aux Event Edge Cases ──────────────────────────────────────────────────

class TestAuxEdgeCases:
    def test_aux_stop(self):
        msg = OWNEvent.parse("*9*3*1##")
        assert isinstance(msg, OWNAuxEvent)
        assert msg.state_code == 3

    def test_aux_up(self):
        msg = OWNEvent.parse("*9*4*1##")
        assert isinstance(msg, OWNAuxEvent)

    def test_aux_down(self):
        msg = OWNEvent.parse("*9*5*1##")
        assert isinstance(msg, OWNAuxEvent)

    def test_aux_enabled(self):
        msg = OWNEvent.parse("*9*6*1##")
        assert isinstance(msg, OWNAuxEvent)

    def test_aux_disabled(self):
        msg = OWNEvent.parse("*9*7*1##")
        assert isinstance(msg, OWNAuxEvent)

    def test_aux_reset_gen(self):
        msg = OWNEvent.parse("*9*8*1##")
        assert isinstance(msg, OWNAuxEvent)

    def test_aux_reset_bi(self):
        msg = OWNEvent.parse("*9*9*1##")
        assert isinstance(msg, OWNAuxEvent)

    def test_aux_reset_tri(self):
        msg = OWNEvent.parse("*9*10*1##")
        assert isinstance(msg, OWNAuxEvent)


# ── CEN+ Event Edge Cases ────────────────────────────────────────────────

class TestCENPlusEdgeCases:
    def test_slow_rotated_cw(self):
        msg = OWNEvent.parse("*25*25#1*21##")
        assert isinstance(msg, OWNCENPlusEvent)
        assert msg.is_slowly_turned_cw is True

    def test_quickly_rotated_cw(self):
        msg = OWNEvent.parse("*25*26#1*21##")
        assert isinstance(msg, OWNCENPlusEvent)
        assert msg.is_quickly_turned_cw is True

    def test_slowly_rotated_ccw(self):
        msg = OWNEvent.parse("*25*27#1*21##")
        assert isinstance(msg, OWNCENPlusEvent)
        assert msg.is_slowly_turned_ccw is True

    def test_quickly_rotated_ccw(self):
        msg = OWNEvent.parse("*25*28#1*21##")
        assert isinstance(msg, OWNCENPlusEvent)
        assert msg.is_quickly_turned_ccw is True


# ── Scene Event Edge Cases ────────────────────────────────────────────────

class TestSceneEdgeCases:
    def test_unknown_state(self):
        msg = OWNEvent.parse("*17*99*1##")
        assert isinstance(msg, OWNSceneEvent)
        assert msg.is_on is None
        assert msg.is_enabled is None


# ── Gateway Device Types ──────────────────────────────────────────────────

class TestGatewayDeviceTypes:
    def test_mhserver(self):
        msg = OWNEvent.parse("*#13**15*2##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_f452(self):
        msg = OWNEvent.parse("*#13**15*6##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_f452v(self):
        msg = OWNEvent.parse("*#13**15*7##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_mhserver2(self):
        msg = OWNEvent.parse("*#13**15*11##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_h4684(self):
        msg = OWNEvent.parse("*#13**15*13##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_f454(self):
        msg = OWNEvent.parse("*#13**15*200##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_unknown_device(self):
        msg = OWNEvent.parse("*#13**15*99##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_gateway_time_no_timezone(self):
        """Timezone field empty."""
        msg = OWNEvent.parse("*#13**0*12*30*45*##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_gateway_time_negative_tz(self):
        """Timezone with leading 1 = negative."""
        msg = OWNEvent.parse("*#13**0*12*30*45*105##")
        assert isinstance(msg, OWNGatewayEvent)

    def test_gateway_datetime_no_tz(self):
        msg = OWNEvent.parse("*#13**22*12*14*58**03*15*04*2026##")
        assert isinstance(msg, OWNGatewayEvent)


# ── Energy Event Edge Cases ───────────────────────────────────────────────

class TestEnergyEdgeCases:
    def test_energy_sensor_7x(self):
        """Sensors starting with 7 are also valid."""
        msg = OWNEvent.parse("*#18*71*113*500##")
        assert isinstance(msg, OWNEnergyEvent)
        assert msg.active_power == 500

    def test_energy_invalid_sensor(self):
        """Sensors not starting with 5 or 7 trigger early return in __init__.
        This is an upstream quirk: __init__ returns None which is silently ignored."""
        msg = OWNEvent.parse("*#18*31*113*500##")
        assert isinstance(msg, OWNEnergyEvent)
        # _type attribute is never set due to early return - upstream bug
        assert not hasattr(msg, '_type') or msg._type is None

    def test_energy_command_duration_clamp(self):
        """Duration > 255 should be clamped."""
        cmd = OWNEnergyCommand.start_sending_instant_power("51", 999)
        assert "255" in str(cmd)


# ── Lighting Edge Cases ──────────────────────────────────────────────────

class TestLightingEdgeCases:
    def test_brightness_dim4(self):
        """Dimension 4 is also brightness."""
        msg = OWNEvent.parse("*#1*21*4*150*5##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.brightness == 50

    def test_timer_dimension(self):
        """Dimension 2 = time value."""
        msg = OWNEvent.parse("*#1*21*2*1*30*0##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 5400  # 1*3600 + 30*60

    def test_flash_invalid_frequency(self):
        """Invalid frequency should default to 0.5."""
        cmd = OWNLightingCommand.flash("21", _freqency=-1)
        assert str(cmd) == "*1*20*21##"

    def test_switch_on_invalid_transition(self):
        """Transition out of range should use simple command."""
        cmd = OWNLightingCommand.switch_on("21", _transition=300)
        assert str(cmd) == "*1*1*21##"

    def test_switch_off_invalid_transition(self):
        cmd = OWNLightingCommand.switch_off("21", _transition=-1)
        assert str(cmd) == "*1*0*21##"

    def test_set_brightness_negative_transition(self):
        """Negative transition should default to 0."""
        cmd = OWNLightingCommand.set_brightness("21", _level=50, _transition=-5)
        assert "*0##" in str(cmd)


# ── Automation Edge Cases ─────────────────────────────────────────────────

class TestAutomationEdgeCases:
    def test_cover_opening_state13(self):
        """State 13 is also opening from position."""
        msg = OWNEvent.parse("*#2*21*10*13*50*0*0##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_opening is True

    def test_cover_closing_state14(self):
        """State 14 is also closing from position."""
        msg = OWNEvent.parse("*#2*21*10*14*50*0*0##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_closing is True


# ── AV Command Edge Cases ─────────────────────────────────────────────────

class TestAVEdgeCases:
    def test_receive_video_4xxx(self):
        """Camera ID >= 4000 extracts from where."""
        cmd = OWNAVCommand.receive_video("4005")
        assert cmd is not None

    def test_receive_video_invalid(self):
        """Camera ID in invalid range returns None."""
        cmd = OWNAVCommand.receive_video("5000")
        assert cmd is None


# ── Command Parse Additional WHO Coverage ─────────────────────────────────

class TestCommandParseWHOCoverage:
    """Cover the remaining WHO dispatch branches in OWNCommand.parse."""

    def test_who3_charges(self):
        cmd = OWNCommand.parse("*3*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who5_alarm(self):
        cmd = OWNCommand.parse("*5*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who6_vdes(self):
        cmd = OWNCommand.parse("*6*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who7_av(self):
        cmd = OWNCommand.parse("*7*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who9_aux(self):
        cmd = OWNCommand.parse("*9*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who14(self):
        cmd = OWNCommand.parse("*14*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who15_cen(self):
        cmd = OWNCommand.parse("*15*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who17_scene(self):
        cmd = OWNCommand.parse("*17*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who22(self):
        cmd = OWNCommand.parse("*22*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who24(self):
        cmd = OWNCommand.parse("*24*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who25_cenplus(self):
        cmd = OWNCommand.parse("*25*1*21##")
        assert isinstance(cmd, OWNCommand)

    def test_who_over_1000(self):
        cmd = OWNCommand.parse("*1001*1*21##")
        assert isinstance(cmd, OWNCommand)


# ── Gateway Command Edge Cases ────────────────────────────────────────────

class TestGatewayCommandEdgeCases:
    def test_set_date_to_today(self):
        cmd = OWNGatewayCommand.set_date_to_today("Europe/Brussels")
        assert cmd is not None
        assert "*#13**#1*" in str(cmd)

    def test_set_time_to_now(self):
        cmd = OWNGatewayCommand.set_time_to_now("Europe/Brussels")
        assert cmd is not None
        assert "*#13**#0*" in str(cmd)

    def test_gateway_command_date(self):
        cmd = OWNCommand.parse("*#13**#1*03*15*04*2026##")
        assert isinstance(cmd, OWNGatewayCommand)

    def test_gateway_command_datetime(self):
        cmd = OWNCommand.parse("*#13**#22*12*14*58*000*03*15*04*2026##")
        assert isinstance(cmd, OWNGatewayCommand)

    def test_gateway_command_time_negative_tz(self):
        """Timezone starting with 1 = negative offset."""
        cmd = OWNCommand.parse("*#13**#0*12*30*45*105*##")
        assert isinstance(cmd, OWNGatewayCommand)

    def test_gateway_command_time_no_tz(self):
        cmd = OWNCommand.parse("*#13**#0*12*30*45**##")
        assert isinstance(cmd, OWNGatewayCommand)


# ── Exhaustive Message Edge Coverage ──────────────────────────────────────

class TestMoreEdgeCoverage:
    """Target remaining uncovered branches in ownd/message.py."""

    def test_message_properties_and_family(self):
        msg_event = OWNMessage.parse("*1*1*21##")
        assert msg_event.is_event is True
        assert msg_event.is_command is False
        assert msg_event.is_request is False
        assert msg_event.is_translation is False
        assert msg_event.entity == "1-21"
        assert msg_event.unique_id == "1-21"
        assert repr(msg_event) == "*1*1*21##"

        msg_cmd = OWNMessage("*#1*21*#1*150*0##")
        assert msg_cmd.is_command is True

        msg_req = OWNMessage("*#1*21##")
        assert msg_req.is_request is True

        msg_trans = OWNMessage("*1*1000*21##")
        assert msg_trans.is_translation is True

        # Test unique_id with interface
        msg_iface = OWNMessage.parse("*1*1*21#4#02##")
        assert msg_iface.unique_id == "1-21#4#02"

        # Test event_content
        ec = msg_iface.event_content
        assert ec["who"] == 1
        assert ec["where"] == "21"
        assert ec["interface"] == "02"

        # event_content with dimensions and where_param
        msg_dim = OWNMessage.parse("*#4*1#1#99*1*0215##")
        ec_dim = msg_dim.event_content
        assert ec_dim["dimension"] == 1
        assert "dimension values" in ec_dim

    def test_message_routing_scopes(self):
        # is_general
        gen = OWNMessage.parse("*1*1*0##")
        assert gen.is_general is True
        not_gen = OWNMessage.parse("*1*1*21##")
        assert not_gen.is_general is False
        who4 = OWNMessage.parse("*4*1*0##")
        assert who4.is_general is False

        # is_group and group property
        grp = OWNMessage.parse("*1*1*#3##")
        assert grp.is_group is True
        assert grp.group == 3
        assert not_gen.is_group is False
        assert not_gen.group is None
        assert who4.is_group is False

        # is_area and area property
        area_00 = OWNMessage.parse("*1*1*00##")
        assert area_00.is_area is True
        assert area_00.area == 0

        area_100 = OWNMessage.parse("*1*1*100##")
        assert area_100.is_area is True
        assert area_100.area == 10

        area_single = OWNMessage.parse("*1*1*5##")
        assert area_single.is_area is True
        assert area_single.area == 5

        area_invalid = OWNMessage("*1*1*abc##")
        assert area_invalid.is_area is False
        assert area_invalid.area is None
        assert who4.is_area is False

    def test_lighting_automation_edge_properties(self):
        light = OWNLightingEvent("*1*1*21##")
        assert light.transition is None

        auto = OWNAutomationEvent("*2*1*21##")
        assert auto.state == 1

    def test_heating_edge_properties(self):
        heat_z0 = OWNHeatingEvent("*#4*#0*0*0215##")
        assert heat_z0.unique_id == "4-#0"

        heat_sensor = OWNHeatingEvent("*#4*101*0*0215##")
        assert heat_sensor.unique_id == "4-101"
        assert heat_sensor._sensor == 1

        heat_z1 = OWNHeatingEvent("*#4*1*0*0215##")
        assert heat_z1.unique_id == "4-1"
        assert heat_z1.is_active() is None
        assert heat_z1.is_heating() is None
        assert heat_z1.is_cooling() is None

    def test_alarm_edge_properties(self):
        alarm = OWNAlarmEvent("*5*1*1##")
        assert alarm.sensor is None

    def test_scene_edge_properties(self):
        scen = OWNSceneEvent("*0*1*21##")
        assert scen.scenario == "21"
        assert scen.state == 1

    def test_energy_event_dimensions(self):
        # 511 hourly consumption
        msg_hourly = OWNEnergyEvent("*#18*51#0*511#10#15*1*50##")
        assert msg_hourly.hourly_consumption["value"] == 50
        assert msg_hourly.human_readable_log is not None

        # 511 daily consumption
        msg_daily = OWNEnergyEvent("*#18*51#0*511#10#15*25*500##")
        assert msg_daily.daily_consumption["value"] == 500

        # 513 daily consumption
        msg_513 = OWNEnergyEvent("*#18*51#0*513#10*20*150##")
        assert msg_513.daily_consumption["value"] == 150

        # 514 daily consumption
        msg_514 = OWNEnergyEvent("*#18*51#0*514#10*20*250##")
        assert msg_514.daily_consumption["value"] == 250

        # 52 monthly consumption
        msg_monthly = OWNEnergyEvent("*#18*51#0*52#26#10*100##")
        assert msg_monthly.monthly_consumption["value"] == 100

        # 53 current month consumption
        msg_cur_m = OWNEnergyEvent("*#18*51#0*53*100##")
        assert msg_cur_m.current_month_partial_consumption == 100

    def test_dry_contact_edge_properties(self):
        dry_on = OWNDryContactEvent("*25*31#1*51##")
        assert dry_on.is_on is True
        assert dry_on.is_detection is True
        assert "detected ON" in dry_on.human_readable_log

        dry_off = OWNDryContactEvent("*25*30#0*51##")
        assert dry_off.is_on is False
        assert dry_off.is_detection is False
        assert "reported OFF" in dry_off.human_readable_log

        cmd = OWNDryContactCommand.status("1")
        assert "*#25*1##" in str(cmd)

    def test_cen_plus_edge_properties(self):
        cen = OWNCENPlusEvent("*25*21#1*51##")
        assert cen.human_readable_log is not None

    def test_sound_edge_cases(self):
        snd_src = OWNSoundEvent("*16*5*101##")
        assert "Audio Source 1 received command" in snd_src.human_readable_log

        snd_dim_err = OWNSoundEvent("*#16*1*1*##")
        assert snd_dim_err._volume is None

        cmd_cycle = OWNSoundCommand.source_cycle()
        assert "*16*23*100##" in str(cmd_cycle)

    def test_heating_command_central_local(self):
        cmd = OWNHeatingCommand.set_temperature("#0#2", 21.0, CLIMATE_MODE_HEAT)
        assert "*#4*#0#2*#14*0210*1##" in str(cmd)

    def test_gateway_broadcasting_no_tz(self):
        # Empty tz field in dimension 22
        cmd = OWNGatewayCommand("*#13**#22*12*14*58**03*15*04*2026##")
        assert cmd._timezone == ""

    def test_energy_commands(self):
        now = datetime.date.today()
        c_hourly = OWNEnergyCommand.get_hourly_consumption("51", now)
        assert c_hourly is not None

        c_hourly_old = OWNEnergyCommand.get_hourly_consumption("51", datetime.date(2000, 1, 1))
        assert c_hourly_old is None

        c_part_d = OWNEnergyCommand.get_partial_daily_consumption("51")
        assert c_part_d is not None

        c_daily = OWNEnergyCommand.get_daily_consumption("51", now.year, now.month)
        assert c_daily is not None

        c_daily_future = OWNEnergyCommand.get_daily_consumption("51", now.year + 2, 1)
        assert c_daily_future is None

        c_daily_too_old = OWNEnergyCommand.get_daily_consumption("51", now.year - 5, 1)
        assert c_daily_too_old is None

        c_part_m = OWNEnergyCommand.get_partial_monthly_consumption("51")
        assert c_part_m is not None

        c_month = OWNEnergyCommand.get_monthly_consumption("51", 2026, 3)
        assert c_month is not None

        c_tot = OWNEnergyCommand.get_total_consumption("51")
        assert c_tot is not None

    def test_signaling_methods(self):
        ack = OWNSignaling("*#*1##")
        assert ack.is_ack() is True
        assert ack.is_nack() is False
        assert ack.is_nonce() is False
        assert ack.is_sha() is False
        assert ack.nonce is None
        assert ack.sha_version is None

        nack = OWNSignaling("*#*0##")
        assert nack.is_nack() is True

        nonce = OWNSignaling("*#1234567890##")
        if nonce.is_nonce():
            assert nonce.nonce is not None

        sha = OWNSignaling("*98*1##")
        assert sha.is_sha() is True
        assert sha.is_sha_1() is True
        assert sha.is_sha_256() is False

        sha256 = OWNSignaling("*98*2##")
        assert sha256.is_sha_256() is True
        assert sha256.sha_version == "2"

    def test_remaining_message_edges(self):
        # 1. sha_version on non-sha
        ack = OWNSignaling("*#*1##")
        assert ack.sha_version is None

        # 2. event_content with interface & extra where params (line 248)
        msg1 = OWNEvent.parse("*1*1*21#4#01#99##")
        if msg1:
            d1 = msg1.event_content
            assert "where parameters" in d1

        # 3. event_content with what params (line 254)
        msg2 = OWNEvent.parse("*1*1#10*21##")
        if msg2:
            d2 = msg2.event_content
            assert "what parameters" in d2

        # 4. event_content with dimension params (line 258)
        msg3 = OWNEvent.parse("*#4*1*#14#1*0220##")
        if msg3:
            d3 = msg3.event_content
            assert "dimension parameters" in d3

        # 5. is_area ValueError handling
        msg_va = OWNEvent("*1*1*1##")
        msg_va._where = "A"
        assert msg_va.is_area is False

        # 6. Energy dimension 511 with date in past and leap year ValueError
        past_511 = OWNEnergyEvent.parse("*#18*71*511#1#1*1*100##")
        assert past_511 is not None
        assert past_511._type is not None

        # Feb 29 in leap year 2024 with today Jan 1 -> _raw_message_date in future -> 2023-02-29 raises ValueError
        real_date = datetime.date

        class MockDate(real_date):
            @classmethod
            def today(cls):
                return real_date(2024, 1, 1)

        with patch("OWNd.message.datetime.date", MockDate):
            leap_err = OWNEnergyEvent.parse("*#18*71*511#2#29*1*100##")
            assert leap_err is not None
            assert getattr(leap_err, "_type", None) is None

        # 7. Energy dimension 513 future (1417-1421) and past (1436-1440)
        future_513 = OWNEnergyEvent.parse("*#18*71*513#12*1*100##")
        assert future_513 is not None
        assert future_513._type is not None
        past_513 = OWNEnergyEvent.parse("*#18*71*513#1*1*100##")
        assert past_513 is not None
        assert past_513._type is not None

        # 8. Energy dimension 514 future (1424-1428), past (1430-1434), and invalid day in 513 (1441-1442)
        future_514 = OWNEnergyEvent.parse("*#18*71*514#12*1*100##")
        assert future_514 is not None
        assert future_514._type is not None
        past_514 = OWNEnergyEvent.parse("*#18*71*514#1*1*100##")
        assert past_514 is not None
        assert past_514._type is not None
        invalid_513 = OWNEnergyEvent.parse("*#18*71*513#1*32*100##")
        assert getattr(invalid_513, "_type", None) is None

        # 9. Gateway date dimension 22 without timezone (line 1998)
        gw_cmd_no_tz = OWNGatewayCommand("*#13**#22*12*14*58**03*15*04*2026##")
        assert gw_cmd_no_tz._timezone == ""
        gw_evt_no_tz = OWNGatewayEvent("*#13**22*12*14*58**03*15*04*2026##")
        assert gw_evt_no_tz._timezone == ""

        # 10. OWNEnergyCommand get_daily_consumption branches (lines 2174, 2180, 2182, 2184, 2186)
        now = datetime.date.today()
        # Future date -> None
        assert OWNEnergyCommand.get_daily_consumption("1", now.year + 1, 1) is None
        # Within last year
        cmd_1y = OWNEnergyCommand.get_daily_consumption("71", now.year, now.month)
        if cmd_1y:
            assert "*18*59#" in str(cmd_1y)
        # Between 1 and 2 years ago
        month_15_ago = (now.month - 3) if now.month > 3 else (now.month + 9)
        year_15_ago = now.year - 1 if now.month > 3 else now.year - 2
        cmd_2y = OWNEnergyCommand.get_daily_consumption("1", year_15_ago, month_15_ago)
        assert cmd_2y is not None
        assert "*18*510#" in str(cmd_2y)
        # Older than 2 years -> None
        assert OWNEnergyCommand.get_daily_consumption("1", now.year - 3, 1) is None


class TestProtocolFixesAudit:
    """Tests covering OpenWebNet protocol fixes and enhancements."""

    def test_who25_routing_and_bounds_safety(self):
        # Dry contact with address starting with '2'
        evt_dc_21 = OWNEvent.parse("*25*31*21##")
        assert isinstance(evt_dc_21, OWNDryContactEvent)
        assert evt_dc_21.is_on is True
        assert evt_dc_21.sensor == "21"

        evt_dc_2 = OWNEvent.parse("*25*32*2##")
        assert isinstance(evt_dc_2, OWNDryContactEvent)
        assert evt_dc_2.is_on is False
        assert evt_dc_2.sensor == "2"

        # CEN+ with WHAT between 21 and 28
        evt_cp_short = OWNEvent.parse("*25*21#1*21##")
        assert isinstance(evt_cp_short, OWNCENPlusEvent)
        assert evt_cp_short.is_short_pressed is True
        assert evt_cp_short.push_button == 1

        evt_cp_ccw = OWNEvent.parse("*25*28#2*21##")
        assert isinstance(evt_cp_ccw, OWNCENPlusEvent)
        assert evt_cp_ccw.is_quickly_turned_ccw is True
        assert evt_cp_ccw.push_button == 2

        # Malformed CEN+ without _what_param does not crash with IndexError
        evt_cp_empty_param = OWNCENPlusEvent("*25*21*21##")
        assert evt_cp_empty_param.push_button == 0

        # Dry contact without _what_param does not crash
        evt_dc_empty_param = OWNDryContactEvent("*25*31*21##")
        assert evt_dc_empty_param.is_detection is True

        # Status request for WHO=25
        cmd_dc_status = OWNCommand.parse("*#25*21##")
        assert isinstance(cmd_dc_status, OWNDryContactCommand)
        assert str(cmd_dc_status) == "*#25*21##"

    def test_where_less_status_requests(self):
        # WHO=5 without WHERE
        msg_alarm_global = OWNMessage.parse("*#5##")
        assert isinstance(msg_alarm_global, OWNAlarmCommand)
        assert msg_alarm_global.where is None
        assert msg_alarm_global.unique_id == "5"

        # WHO=9 without WHERE
        msg_aux_global = OWNMessage.parse("*#9##")
        assert isinstance(msg_aux_global, OWNStatusRequest)
        assert msg_aux_global.where is None
        assert msg_aux_global.unique_id == "9"

        # OWNStatusRequest helpers
        req_9 = OWNStatusRequest.request(9)
        assert str(req_9) == "*#9##"
        req_9_1 = OWNStatusRequest.request(9, "1")
        assert str(req_9_1) == "*#9*1##"

    def test_alarm_commands_alignment(self):
        assert str(OWNAlarmCommand.disarm("0")) == "*5*2*0##"
        assert str(OWNAlarmCommand.arm_away("0")) == "*5*1*0##"
        assert str(OWNAlarmCommand.arm_home("0")) == "*5*1*0##"
        assert str(OWNAlarmCommand.trigger("0")) == "*5*17*0##"
        assert str(OWNAlarmCommand.panic("0")) == "*5*17*0##"
        assert str(OWNAlarmCommand.status("0")) == "*#5*0##"
        assert str(OWNAlarmCommand.status("1")) == "*#5*#1##"
        assert str(OWNAlarmCommand.status("#2")) == "*#5*#2##"
        assert str(OWNAlarmCommand.status(None)) == "*#5##"
        assert str(OWNAlarmCommand.status("")) == "*#5##"

        # OWNCommand.parse for WHO=5
        cmd_alarm = OWNCommand.parse("*5*2*0##")
        assert isinstance(cmd_alarm, OWNAlarmCommand)

    def test_gateway_time_and_bounds_safety(self):
        # set_time_to_now should not have trailing asterisk before ##
        cmd_time = OWNGatewayCommand.set_time_to_now("UTC")
        assert str(cmd_time).endswith("##")
        assert not str(cmd_time).endswith("*##")

        # F454 broadcast without timezone in dimension 0
        evt_time_no_tz = OWNGatewayEvent("*#13**0*14*30*45##")
        assert evt_time_no_tz._hour == "14"
        assert evt_time_no_tz._minute == "30"
        assert evt_time_no_tz._second == "45"

        # OWNGatewayCommand dimension 0, 1, 22 bounds safety
        cmd_dim0 = OWNGatewayCommand("*#13**#0*12*00*00##")
        assert cmd_dim0._hour == "12"

        cmd_dim1 = OWNGatewayCommand("*#13**#1*1*15*04*2026##")
        assert cmd_dim1._year == "2026"

    def test_cover_shutter_status_dimension10(self):
        cmd_shutter = OWNAutomationCommand.get_shutter_status("21")
        assert str(cmd_shutter) == "*#2*21*10##"

        # Dim 10 event parsing (state=10, position=75)
        evt_shutter = OWNAutomationEvent("*#2*21*10*10*75##")
        assert evt_shutter.current_position == 75

    def test_energy_telemetry_addressing(self):
        # Stop & Go sensor where starts with 1
        evt_energy = OWNEnergyEvent("*#18*11*51*2500##")
        assert evt_energy.total_consumption == 2500
        assert evt_energy.sensor == "1"
        assert evt_energy.where == "11"

        evt_energy_single = OWNEnergyEvent("*#18*1*51*1200##")
        assert evt_energy_single.total_consumption == 1200
        assert evt_energy_single.sensor == "1"

    def test_gateway_profile_constants(self):
        from OWNd.profiles import (
            DEFAULT_SUPPORTED_WHO,
            WHO_LOAD_CONTROL,
            WHO_SOUND_DIFFUSION,
        )
        assert WHO_LOAD_CONTROL == 3
        assert WHO_SOUND_DIFFUSION == 22
        assert WHO_LOAD_CONTROL in DEFAULT_SUPPORTED_WHO
        assert WHO_SOUND_DIFFUSION in DEFAULT_SUPPORTED_WHO


class TestMessageAuditExhaustiveCoverage:
    """Exhaustive unit tests targeting all remaining edge-case branches in ownd/message.py."""

    def test_status_request_with_where_param(self):
        msg = OWNMessage("*#1*21#4#01##")
        assert msg.is_valid is True
        assert msg.is_request is True
        assert msg.where == "21"
        assert msg._where_param == ["4", "01"]

    def test_own_event_parse_who25_query(self):
        evt = OWNEvent.parse("*#25*21*0*1##")
        assert isinstance(evt, OWNDryContactEvent)

    def test_own_event_parse_who25_non_integer_what(self):
        evt = OWNEvent.parse("*25*bad*21##")
        assert isinstance(evt, OWNDryContactEvent)

    def test_automation_event_dim10_exception(self):
        class BadInt:
            def __int__(self):
                raise ValueError("boom")

        orig_init = OWNMessage.__init__

        def fake_init(self, data):
            orig_init(self, data)
            self._what = None
            self._dimension = 10
            self._dimension_value = [BadInt()]

        with patch.object(OWNMessage, "__init__", fake_init):
            evt = OWNAutomationEvent("*#2*21*10*10*75##")
            assert evt._state is None

    def test_gateway_event_dim0_exception(self):
        class BadList:
            def __len__(self):
                return 4

            def __getitem__(self, idx):
                raise IndexError("simulated")

        orig_init = OWNMessage.__init__

        def fake_init(self, data):
            orig_init(self, data)
            self._dimension = 0
            self._dimension_value = BadList()

        with patch.object(OWNMessage, "__init__", fake_init):
            with pytest.raises(IndexError):
                OWNGatewayEvent("*#13**0*10*20*30##")

    def test_gateway_event_dim1_invalid_date(self):
        with pytest.raises(ValueError):
            OWNGatewayEvent("*#13**1*99*99*2020##")

    def test_gateway_event_dim12_empty_part(self):
        evt = OWNGatewayEvent("*#13**12**1*2*3*4*5##")
        assert evt._mac_address is None

    def test_gateway_event_dim19_empty_part(self):
        evt = OWNGatewayEvent("*#13**19**1*2*3##")
        assert evt._uptime is None

    def test_gateway_event_dim22_invalid_datetime(self):
        with pytest.raises(ValueError):
            OWNGatewayEvent("*#13**22*12*00*00*01*0*01*99*2020##")

    def test_energy_event_dim113_empty_value(self):
        evt = OWNEnergyEvent("*#18*1*113*##")
        assert evt._active_power == 0

    def test_energy_event_dim511_empty_value(self):
        evt = OWNEnergyEvent("*#18*1*511#1#1*##")
        assert evt is not None

    def test_energy_event_dim51_empty_value(self):
        evt = OWNEnergyEvent("*#18*1*51*##")
        assert evt._total_consumption == 0

    def test_energy_event_dim54_empty_value(self):
        evt = OWNEnergyEvent("*#18*1*54*##")
        assert evt._current_day_partial_consumption == 0

    def test_energy_event_dim52_invalid_date(self):
        evt = OWNEnergyEvent("*#18*1*52#24#99*100##")
        assert evt._monthly_consumption.get("date") is None

    def test_energy_event_dim53_empty_value(self):
        evt = OWNEnergyEvent("*#18*1*53*##")
        assert evt._current_month_partial_consumption == 0

    def test_dry_contact_event_param_exception(self):
        class BadInt:
            def __int__(self):
                raise ValueError("boom")

        orig_init = OWNMessage.__init__

        def fake_init(self, data):
            orig_init(self, data)
            self._what = 31
            self._what_param = [BadInt()]
            self._where = "21"

        with patch.object(OWNMessage, "__init__", fake_init):
            evt = OWNDryContactEvent("*25*31*21##")
            assert evt._detection == 1

    def test_cenplus_event_param_exception_and_empty_where(self):
        class BadInt:
            def __int__(self):
                raise ValueError("boom")

        orig_init = OWNMessage.__init__

        def fake_init(self, data):
            orig_init(self, data)
            self._what = 21
            self._what_param = [BadInt()]
            self._where = None

        with patch.object(OWNMessage, "__init__", fake_init):
            evt = OWNCENPlusEvent("*25*21#1*21##")
            assert evt.push_button == 0
            assert evt.object == ""

    def test_cenplus_event_unmapped_state(self):
        evt = OWNCENPlusEvent("*25*99#1*21##")
        assert "state is 99" in evt.human_readable_log

    def test_own_command_parse_who25_branches(self):
        # 1872: CEN+ range (21..28)
        cmd_cen = OWNCommand.parse("*25*21#1*21##")
        assert cmd_cen is not None

        # 1869-1870: ValueError on non-integer what
        cmd_bad = OWNCommand.parse("*25*bad*21##")
        assert isinstance(cmd_bad, OWNDryContactCommand)

    def test_gateway_command_dim0_invalid_time(self):
        with pytest.raises(ValueError):
            OWNGatewayCommand("*#13**#0*99*99*99*01##")

    def test_gateway_command_dim1_invalid_date(self):
        with pytest.raises(ValueError):
            OWNGatewayCommand("*#13**#1*1*99*2020##")

    def test_gateway_command_dim22_invalid_datetime(self):
        with pytest.raises(ValueError):
            OWNGatewayCommand("*#13**#22*12*00*00*01*0*01*99*2020##")
