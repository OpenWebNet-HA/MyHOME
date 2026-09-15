"""Tests for OWNLightingEvent and OWNLightingCommand protocol translation."""
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
