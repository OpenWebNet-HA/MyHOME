"""Tests for OWNLightingEvent and OWNLightingCommand protocol translation."""

import pytest
from OWNd.message import (
    MESSAGE_TYPE_ILLUMINANCE,
    MESSAGE_TYPE_MOTION,
    MESSAGE_TYPE_MOTION_TIMEOUT,
    MESSAGE_TYPE_PIR_SENSITIVITY,
    OWNCommand,
    OWNEvent,
    OWNLightingCommand,
    OWNLightingEvent,
)

# ── Event Parsing ──────────────────────────────────────────────────────────

class TestLightingEventParsing:
    """Validate OWNLightingEvent via OWNEvent.parse factory."""

    def test_light_off(self):
        msg = OWNEvent.parse("*1*0*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.is_on is False
        assert msg.brightness is None

    def test_light_on(self):
        msg = OWNEvent.parse("*1*1*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.is_on is True

    def test_dimmer_preset(self):
        msg = OWNEvent.parse("*1*5*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.is_on is True
        assert msg.brightness_preset == 5

    def test_timer_1m(self):
        msg = OWNEvent.parse("*1*11*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 60

    def test_timer_2m(self):
        msg = OWNEvent.parse("*1*12*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 120

    def test_timer_3m(self):
        msg = OWNEvent.parse("*1*13*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 180

    def test_timer_4m(self):
        msg = OWNEvent.parse("*1*14*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 240

    def test_timer_5m(self):
        msg = OWNEvent.parse("*1*15*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 300

    def test_timer_15m(self):
        msg = OWNEvent.parse("*1*16*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 900

    def test_timer_30s(self):
        msg = OWNEvent.parse("*1*17*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 30

    def test_timer_half_second(self):
        msg = OWNEvent.parse("*1*18*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.timer == 0.5

    def test_blinker(self):
        msg = OWNEvent.parse("*1*20*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.blinker == 0.5

    def test_blinker_fast(self):
        msg = OWNEvent.parse("*1*25*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.blinker == 3.0

    def test_motion_detected(self):
        msg = OWNEvent.parse("*1*34*21##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.message_type == MESSAGE_TYPE_MOTION
        assert msg.motion is True

    def test_brightness_dimension(self):
        msg = OWNEvent.parse("*#1*21*1*150*0##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.brightness == 50
        assert msg.is_on is True

    def test_brightness_zero_means_off(self):
        msg = OWNEvent.parse("*#1*21*1*100*0##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.brightness == 0
        assert msg.is_on is False

    def test_pir_sensitivity(self):
        msg = OWNEvent.parse("*#1*21*5*2##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.message_type == MESSAGE_TYPE_PIR_SENSITIVITY
        assert msg.pir_sensitivity == 2

    def test_illuminance(self):
        msg = OWNEvent.parse("*#1*21*6*500##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.message_type == MESSAGE_TYPE_ILLUMINANCE
        assert msg.illuminance == 500

    def test_motion_timeout(self):
        msg = OWNEvent.parse("*#1*21*7*0*5*15##")
        assert isinstance(msg, OWNLightingEvent)
        assert msg.message_type == MESSAGE_TYPE_MOTION_TIMEOUT
        assert msg.motion_timeout is not None


# ── Command Generation ─────────────────────────────────────────────────────

class TestLightingCommandGeneration:
    """Validate OWNLightingCommand factory methods."""

    def test_status_request(self):
        cmd = OWNLightingCommand.status("21")
        assert str(cmd) == "*#1*21##"

    def test_switch_on(self):
        cmd = OWNLightingCommand.switch_on("21")
        assert str(cmd) == "*1*1*21##"

    def test_switch_on_with_transition(self):
        cmd = OWNLightingCommand.switch_on("21", _transition=100)
        assert str(cmd) == "*1*1#100*21##"

    def test_switch_off(self):
        cmd = OWNLightingCommand.switch_off("21")
        assert str(cmd) == "*1*0*21##"

    def test_switch_off_with_transition(self):
        cmd = OWNLightingCommand.switch_off("21", _transition=50)
        assert str(cmd) == "*1*0#50*21##"

    def test_set_brightness(self):
        cmd = OWNLightingCommand.set_brightness("21", _level=50, _transition=10)
        assert str(cmd) == "*#1*21*#1*150*10##"

    def test_flash(self):
        cmd = OWNLightingCommand.flash("21", _freqency=1.0)
        assert str(cmd) == "*1*21*21##"

    def test_flash_default(self):
        cmd = OWNLightingCommand.flash("21")
        assert str(cmd) == "*1*20*21##"

    def test_get_brightness(self):
        cmd = OWNLightingCommand.get_brightness("21")
        assert str(cmd) == "*#1*21*1##"

    def test_get_pir_sensitivity(self):
        cmd = OWNLightingCommand.get_pir_sensitivity("21")
        assert str(cmd) == "*#1*21*5##"

    def test_get_illuminance(self):
        cmd = OWNLightingCommand.get_illuminance("21")
        assert str(cmd) == "*#1*21*6##"

    def test_get_motion_timeout(self):
        cmd = OWNLightingCommand.get_motion_timeout("21")
        assert str(cmd) == "*#1*21*7##"

    def test_command_parse_who1(self):
        cmd = OWNCommand.parse("*1*1*21##")
        assert isinstance(cmd, OWNLightingCommand)

    @pytest.mark.skipif(
        not hasattr(OWNLightingCommand, "switch_on_timed"),
        reason="Requires OWNd >= 2.0.0b11 (OWNd#93)",
    )
    def test_switch_on_timed(self):
        cmd = OWNLightingCommand.switch_on_timed("21", 11)
        assert str(cmd) == "*1*11*21##"
        assert isinstance(cmd, OWNLightingCommand)

        with pytest.raises(ValueError, match="timer WHAT must be between 11 and 18"):
            OWNLightingCommand.switch_on_timed("21", 10)

    @pytest.mark.skipif(
        not hasattr(OWNLightingCommand, "set_variable_timer"),
        reason="Requires OWNd >= 2.0.0b11 (OWNd#93)",
    )
    def test_set_variable_timer(self):
        cmd = OWNLightingCommand.set_variable_timer("21", 1, 30, 45)
        assert str(cmd) == "*#1*21*#2*1*30*45##"
        assert isinstance(cmd, OWNLightingCommand)

        with pytest.raises(ValueError, match="hours 0..255, minutes and seconds 0..59"):
            OWNLightingCommand.set_variable_timer("21", 256, 0, 0)
        with pytest.raises(ValueError, match="hours 0..255, minutes and seconds 0..59"):
            OWNLightingCommand.set_variable_timer("21", 0, 60, 0)
        with pytest.raises(ValueError, match="hours 0..255, minutes and seconds 0..59"):
            OWNLightingCommand.set_variable_timer("21", 0, 0, 60)

    @pytest.mark.skipif(
        not hasattr(OWNLightingCommand, "get_variable_timer"),
        reason="Requires OWNd >= 2.0.0b11 (OWNd#93)",
    )
    def test_get_variable_timer(self):
        cmd = OWNLightingCommand.get_variable_timer("21")
        assert str(cmd) == "*#1*21*2##"
        assert isinstance(cmd, OWNLightingCommand)

    @pytest.mark.skipif(
        not hasattr(OWNLightingCommand, "set_brightness_preset"),
        reason="Requires OWNd >= 2.0.0b11 (OWNd#93)",
    )
    def test_set_brightness_preset(self):
        cmd = OWNLightingCommand.set_brightness_preset("21", 5)
        assert str(cmd) == "*1*5*21##"
        assert isinstance(cmd, OWNLightingCommand)

        with pytest.raises(ValueError, match="preset must be between 2 and 10"):
            OWNLightingCommand.set_brightness_preset("21", 1)
        with pytest.raises(ValueError, match="preset must be between 2 and 10"):
            OWNLightingCommand.set_brightness_preset("21", 11)

    @pytest.mark.skipif(
        not hasattr(OWNLightingCommand, "step_up"),
        reason="Requires OWNd >= 2.0.0b11 (OWNd#93)",
    )
    def test_step_up(self):
        cmd = OWNLightingCommand.step_up("21")
        assert str(cmd) == "*1*30*21##"
        assert isinstance(cmd, OWNLightingCommand)

        cmd_delta = OWNLightingCommand.step_up("21", delta=20, speed=1)
        assert str(cmd_delta) == "*1*30#20#1*21##"
        assert isinstance(cmd_delta, OWNLightingCommand)

        with pytest.raises(ValueError, match="delta must be 1..100 and speed 0..255"):
            OWNLightingCommand.step_up("21", delta=0)
        with pytest.raises(ValueError, match="delta must be 1..100 and speed 0..255"):
            OWNLightingCommand.step_up("21", delta=101)
        with pytest.raises(ValueError, match="delta must be 1..100 and speed 0..255"):
            OWNLightingCommand.step_up("21", delta=10, speed=256)

    @pytest.mark.skipif(
        not hasattr(OWNLightingCommand, "step_down"),
        reason="Requires OWNd >= 2.0.0b11 (OWNd#93)",
    )
    def test_step_down(self):
        cmd = OWNLightingCommand.step_down("21")
        assert str(cmd) == "*1*31*21##"
        assert isinstance(cmd, OWNLightingCommand)

        cmd_delta = OWNLightingCommand.step_down("21", delta=10, speed=2)
        assert str(cmd_delta) == "*1*31#10#2*21##"
        assert isinstance(cmd_delta, OWNLightingCommand)

        with pytest.raises(ValueError, match="delta must be 1..100 and speed 0..255"):
            OWNLightingCommand.step_down("21", delta=0)
        with pytest.raises(ValueError, match="delta must be 1..100 and speed 0..255"):
            OWNLightingCommand.step_down("21", delta=101)
        with pytest.raises(ValueError, match="delta must be 1..100 and speed 0..255"):
            OWNLightingCommand.step_down("21", delta=10, speed=256)

