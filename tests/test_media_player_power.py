"""Unit tests for the centralised media_player_power module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.media_player.const import MediaPlayerState
from OWNd.message import OWNSoundEvent

from custom_components.myhome.media_player_power import (
    AUTO_OFF_IDLE_DELAY,
    AUTO_OFF_PAUSED_DELAY,
    DECODER_PLAYING_STATES,
    GROUP_LEAVE_GRACE,
    WAKE_ECHO_WINDOW,
    PowerTransition,
    calculate_auto_off_delay,
    determine_power_transition,
    is_dimension_1_volume_report,
    is_volume_rocker_down,
    is_volume_rocker_up,
)


def test_timing_constants():
    """Verify standard anti-hiss and lifecycle timing constants."""
    assert AUTO_OFF_IDLE_DELAY == 3.0
    assert AUTO_OFF_PAUSED_DELAY == 60.0
    assert GROUP_LEAVE_GRACE == 5.0
    assert WAKE_ECHO_WINDOW == 3.0
    assert "playing" in DECODER_PLAYING_STATES
    assert "buffering" in DECODER_PLAYING_STATES
    assert MediaPlayerState.PLAYING in DECODER_PLAYING_STATES
    assert MediaPlayerState.BUFFERING in DECODER_PLAYING_STATES


@pytest.mark.parametrize("what_val", range(1001, 1016))
def test_is_volume_rocker_up_valid(what_val):
    """Frames *16*1001*WHERE## to *16*1015*WHERE## represent volume-up steps."""
    msg = MagicMock()
    msg.is_off = False
    msg.what = what_val
    assert is_volume_rocker_up(msg) is True


@pytest.mark.parametrize("what_val", [1000, 1016, 1101, 1102, 1, 3, 13, "invalid", None])
def test_is_volume_rocker_up_invalid(what_val):
    """Non-rocker-up frames return False."""
    msg = MagicMock()
    msg.is_off = False
    msg.what = what_val
    assert is_volume_rocker_up(msg) is False


def test_is_volume_rocker_up_when_is_off_is_false():
    """If a message has is_off=True, it cannot be considered a volume up rocker."""
    msg = MagicMock()
    msg.is_off = True
    msg.what = 1001
    assert is_volume_rocker_up(msg) is False


@pytest.mark.parametrize("what_val", range(1101, 1116))
def test_is_volume_rocker_down_valid(what_val):
    """Frames *16*1101*WHERE## to *16*1115*WHERE## represent volume-down steps."""
    msg = MagicMock()
    msg.what = what_val
    assert is_volume_rocker_down(msg) is True


@pytest.mark.parametrize("what_val", [1100, 1116, 1001, 1, 3, 13, "invalid", None])
def test_is_volume_rocker_down_invalid(what_val):
    """Non-rocker-down frames return False."""
    msg = MagicMock()
    msg.what = what_val
    assert is_volume_rocker_down(msg) is False


def test_is_dimension_1_volume_report():
    """Dimension 1 reports volume status (0..31)."""
    # Authentic WHO 16 volume report: *#16*41*1*1##
    msg16 = OWNSoundEvent.parse("*#16*41*1*1##")
    assert is_dimension_1_volume_report(msg16) is True

    # Authentic WHO 16 restored volume report: *#16*41*1*6##
    msg_restored = OWNSoundEvent.parse("*#16*41*1*6##")
    assert is_dimension_1_volume_report(msg_restored) is True

    # Generic mock with dimension=1
    mock_dim1 = MagicMock(dimension=1, volume=10)
    assert is_dimension_1_volume_report(mock_dim1) is True

    # Rocker up command is NOT a dimension 1 volume report
    mock_rocker = MagicMock(dimension=None, volume=None, what=1001, is_off=False)
    assert is_dimension_1_volume_report(mock_rocker) is False

    # Status request is NOT a volume report
    status_msg = OWNSoundEvent.parse("*#16*0*5##")
    assert is_dimension_1_volume_report(status_msg) is False


def test_determine_power_transition_source_events():
    """Source device events (*16*3*10S##) must never modify zone power state."""
    msg = MagicMock(is_source_event=True, is_on=True, is_off=False)
    transition = determine_power_transition(
        message=msg,
        is_parked=False,
        is_wake_echo=False,
    )
    assert transition == PowerTransition.NO_CHANGE


def test_determine_power_transition_off_frames():
    """Explicit OFF frames are classified according to wake-echo and parked status."""
    off_msg = MagicMock(is_source_event=False, is_off=True, is_on=False)

    # 1. Wake echo OFF
    assert (
        determine_power_transition(
            off_msg,
            is_parked=False,
            is_wake_echo=True,
        )
        == PowerTransition.WAKE_ECHO_OFF
    )

    # 2. Parked confirmation OFF
    assert (
        determine_power_transition(
            off_msg,
            is_parked=True,
            is_wake_echo=False,
        )
        == PowerTransition.PARKED_CONFIRM_OFF
    )

    # 3. Real external OFF
    assert (
        determine_power_transition(
            off_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.REAL_OFF
    )


def test_determine_power_transition_on_frames():
    """Explicit ON frames (*16*3*WHERE## / *16*0*WHERE##) wake the amplifier."""
    on_msg = MagicMock(is_source_event=False, is_on=True, is_off=False)
    assert (
        determine_power_transition(
            on_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.WAKE_ON
    )


def test_determine_power_transition_rocker_up():
    """Physical volume-up rocker commands wake the amplifier."""
    rocker_msg = MagicMock(
        is_source_event=False,
        is_on=False,
        is_off=False,
        what=1001,
    )
    assert (
        determine_power_transition(
            rocker_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.WAKE_ON
    )


def test_determine_power_transition_dimension_1_volume_report_never_wakes():
    """Dimension 1 volume reports (ducking/restoration) MUST NEVER change power state (#669)."""
    # Ducking report to volume 1
    duck_msg = OWNSoundEvent.parse("*#16*41*1*1##")
    assert (
        determine_power_transition(
            duck_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )

    # Restoration report to volume 6
    restore_msg = OWNSoundEvent.parse("*#16*41*1*6##")
    assert (
        determine_power_transition(
            restore_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )

    # When amplifier is ON, volume report also does not change power state
    assert (
        determine_power_transition(
            restore_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )


def test_is_dimension_1_volume_report_fallback():
    """Verify fallback where dimension is unset but scalar volume is present."""
    # When dimension is None and not on/off or rocker, scalar volume is treated as volume report
    msg = MagicMock(spec=[], dimension=None, volume=10, what=None, is_off=False, is_on=False)
    assert is_dimension_1_volume_report(msg) is True

    # If dimension is explicitly something else (e.g. 5, 2, 8), presence of volume does NOT make it Dim 1
    msg_other_dim = MagicMock(spec=[], dimension=5, volume=10, what=None, is_off=False, is_on=False)
    assert is_dimension_1_volume_report(msg_other_dim) is False

    # String non-1 dimension does NOT trigger fallback
    msg_other_dim_str = MagicMock(spec=[], dimension="5", volume=10, what=None, is_off=False, is_on=False)
    assert is_dimension_1_volume_report(msg_other_dim_str) is False

    # If dimension is None, but message is explicitly ON or OFF, fallback does not match
    msg_on = MagicMock(spec=[], dimension=None, volume=10, what=None, is_on=True, is_off=False)
    assert is_dimension_1_volume_report(msg_on) is False
    msg_off = MagicMock(spec=[], dimension=None, volume=10, what=None, is_on=False, is_off=True)
    assert is_dimension_1_volume_report(msg_off) is False

    # If dimension is None, but message is a rocker command, fallback does not match
    msg_rocker_up = MagicMock(spec=[], dimension=None, volume=10, what=1001, is_on=False, is_off=False)
    assert is_dimension_1_volume_report(msg_rocker_up) is False
    msg_rocker_down = MagicMock(spec=[], dimension=None, volume=10, what=1101, is_on=False, is_off=False)
    assert is_dimension_1_volume_report(msg_rocker_down) is False


def test_determine_power_transition_routing_and_status_frames():
    """Matrix routing and status frames must never modify power state."""
    # Volume down rocker command
    vol_down = MagicMock(is_source_event=False, is_on=False, is_off=False, what=1101)
    assert (
        determine_power_transition(
            vol_down,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )

    # Status response frame
    status_msg = OWNSoundEvent.parse("*#16*0*5##")
    assert (
        determine_power_transition(
            status_msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )


@pytest.mark.parametrize(
    ("state_val", "expected_delay"),
    [
        (MediaPlayerState.PLAYING, None),
        ("playing", None),
        (MediaPlayerState.BUFFERING, None),
        ("buffering", None),
        (MediaPlayerState.IDLE, 3.0),
        ("idle", 3.0),
        ("standby", 3.0),
        (MediaPlayerState.OFF, 3.0),
        ("off", 3.0),
        (MediaPlayerState.PAUSED, 60.0),
        ("paused", 60.0),
        ("unavailable", None),
        ("unknown", None),
        (None, None),
        ("other_random_state", None),
    ],
)
def test_calculate_auto_off_delay(state_val, expected_delay):
    """Test delay computation across all media player states."""
    assert calculate_auto_off_delay(state_val) == expected_delay


def test_is_dimension_1_volume_report_string_dimension():
    """String dimension '1' (e.g. from JSON trace) must be recognized."""
    msg = MagicMock(spec=[], dimension="1", volume=None, what=None, is_off=False)
    assert is_dimension_1_volume_report(msg) is True

    # Invalid dimension string
    msg_inv = MagicMock(spec=[], dimension="not_an_int", volume=None, what=None, is_off=False)
    assert is_dimension_1_volume_report(msg_inv) is False


def test_is_volume_rocker_down_when_is_off_is_true():
    """If a message has is_off=True, it cannot be considered a volume down rocker."""
    msg = MagicMock()
    msg.is_off = True
    msg.what = 1101
    assert is_volume_rocker_down(msg) is False


def test_determine_power_transition_dimension_1_overrides_spurious_is_on():
    """Even if a malformed/mocked frame carries is_on=True, dimension 1 takes precedence."""
    msg = MagicMock(dimension=1, volume=10, is_on=True, is_off=False, is_source_event=False)
    assert (
        determine_power_transition(
            msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )


@pytest.mark.parametrize(
    "what_val",
    [1000, 1016, 1100, 1116, 2001, 2101, 5000, 5001, 6001, 9999],
)
def test_determine_power_transition_non_power_what_codes(what_val):
    """Non-standard rocker or audio parameter WHAT codes never change power state."""
    msg = MagicMock(
        is_source_event=False,
        is_on=False,
        is_off=False,
        what=what_val,
        dimension=None,
        volume=None,
    )
    assert (
        determine_power_transition(
            msg,
            is_parked=False,
            is_wake_echo=False,
        )
        == PowerTransition.NO_CHANGE
    )

