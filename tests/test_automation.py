"""Tests for OWNAutomationEvent and OWNAutomationCommand protocol translation."""
from OWNd.message import (
    OWNAutomationCommand,
    OWNAutomationEvent,
    OWNCommand,
    OWNEvent,
)


class TestAutomationEventParsing:
    """Validate OWNAutomationEvent via OWNEvent.parse factory."""

    def test_cover_stopped(self):
        msg = OWNEvent.parse("*2*0*21##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_opening is False
        assert msg.is_closing is False

    def test_cover_opening(self):
        msg = OWNEvent.parse("*2*1*21##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_opening is True
        assert msg.is_closing is False

    def test_cover_closing(self):
        msg = OWNEvent.parse("*2*2*21##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_closing is True
        assert msg.is_opening is False

    def test_cover_opening_from_position(self):
        """State 11: opening from initial position."""
        msg = OWNEvent.parse("*#2*21*10*11*50*0*0##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_opening is True
        assert msg.is_closing is False

    def test_cover_closing_from_position(self):
        """State 12: closing from initial position."""
        msg = OWNEvent.parse("*#2*21*10*12*50*0*0##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_closing is True
        assert msg.is_opening is False

    def test_cover_closed_at_zero(self):
        """State 10 with position 0 means fully closed."""
        msg = OWNEvent.parse("*#2*21*10*10*0*0*0##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_closed is True

    def test_cover_open_at_position(self):
        """State 10 with position > 0 means partially open."""
        msg = OWNEvent.parse("*#2*21*10*10*75*0*0##")
        assert isinstance(msg, OWNAutomationEvent)
        assert msg.is_closed is False
        assert msg.current_position == 75


class TestAutomationCommandGeneration:
    """Validate OWNAutomationCommand factory methods."""

    def test_status_request(self):
        cmd = OWNAutomationCommand.status("21")
        assert str(cmd) == "*#2*21##"

    def test_raise_shutter(self):
        cmd = OWNAutomationCommand.raise_shutter("21")
        assert str(cmd) == "*2*1*21##"

    def test_lower_shutter(self):
        cmd = OWNAutomationCommand.lower_shutter("21")
        assert str(cmd) == "*2*2*21##"

    def test_stop_shutter(self):
        cmd = OWNAutomationCommand.stop_shutter("21")
        assert str(cmd) == "*2*0*21##"

    def test_set_shutter_level(self):
        cmd = OWNAutomationCommand.set_shutter_level("21", level=50)
        assert str(cmd) == "*#2*21*#11#001*50##"

    def test_command_parse_who2(self):
        cmd = OWNCommand.parse("*2*1*21##")
        assert isinstance(cmd, OWNAutomationCommand)
