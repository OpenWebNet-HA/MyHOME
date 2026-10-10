"""Power-state decision engine and frame classification for MyHOME media players.

The WHO 16 sound system features multiple interacting power-state pathways:
- Explicit ON / OFF OpenWebNet frames
- Physical wall control volume-up rocker commands (*16*1001*WHERE## .. *16*1015*WHERE##)
- Dimension 1 volume status reports (*#16*WHERE*1*<vol>##, *#22*3#A#P*1*<vol>##)
- Anti-hiss auto-off timers for active, unowned (stray), and grouped zones
- Temporary parking of multi-room groups during pauses or track transitions
- Wake-echo suppression to prevent self-induced shutdown loops

This module centralises all power-state classifications, transitions, and delay
calculations into pure, deterministic functions to prevent power state regressions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from homeassistant.components.media_player.const import MediaPlayerState

# Anti-hiss auto-off delays (seconds).
# When the decoder an amplifier hears stops playing or goes idle, the amplifier's
# analog power stage is switched off to eliminate background line hiss.
AUTO_OFF_IDLE_DELAY = 3.0  # seconds after idle, standby or off
AUTO_OFF_PAUSED_DELAY = 60.0  # seconds after pause

# A departing member of a group has a short grace period before being turned off,
# allowing Music Assistant's sequential handover to reclaim it without audio dropouts.
GROUP_LEAVE_GRACE = 5.0  # seconds

# Window (seconds) during which an incoming OFF frame is presumed to be the gateway's
# echo of our own OFF -> ON wake sequence rather than an external power-down.
WAKE_ECHO_WINDOW = 3.0  # seconds

# Decoder states that indicate audio playback is active or imminent.
DECODER_PLAYING_STATES = frozenset({
    MediaPlayerState.PLAYING,
    MediaPlayerState.BUFFERING,
    "playing",
    "buffering",
})

_PLAYING_STATE_STRINGS = frozenset({"playing", "buffering"})


class PowerTransition(Enum):
    """Categorisation of power-state effect for an incoming OpenWebNet event."""

    NO_CHANGE = "no_change"
    WAKE_ON = "wake_on"
    WAKE_ECHO_OFF = "wake_echo_off"
    PARKED_CONFIRM_OFF = "parked_confirm_off"
    REAL_OFF = "real_off"


def is_volume_rocker_up(message: Any) -> bool:
    """Return True if message is a physical volume-up rocker command.

    Physical wall switches (such as H4651/2, L4651/2) emit OpenWebNet frames
    *16*1001*WHERE## to *16*1015*WHERE## (step +1 to +15).
    In BTicino systems, tapping volume-up on a wall control powers on an unpowered
    amplifier (#579).
    """
    if getattr(message, "is_off", False) is True:
        return False
    what = getattr(message, "what", getattr(message, "_what", None))
    if not isinstance(what, (int, str)):
        return False
    try:
        val = int(what)
        return 1001 <= val <= 1015
    except (ValueError, TypeError):
        return False


def is_volume_rocker_down(message: Any) -> bool:
    """Return True if message is a physical volume-down rocker command.

    Frames *16*1101*WHERE## to *16*1115*WHERE## (step -1 to -15).
    Volume-down steps adjust the volume level but MUST NOT power on an amplifier.
    """
    if getattr(message, "is_off", False) is True:
        return False
    what = getattr(message, "what", getattr(message, "_what", None))
    if not isinstance(what, (int, str)):
        return False
    try:
        val = int(what)
        return 1101 <= val <= 1115
    except (ValueError, TypeError):
        return False


def is_dimension_1_volume_report(message: Any) -> bool:
    """Return True if message is a Dimension 1 volume status report.

    Dimension 1 frames (*#16*WHERE*1*<vol>##, *#22*3#A#P*1*<vol>##) report the
    current hardware volume level (0..31).
    BTicino amplifiers retain their configured volume in hardware registers even
    when turned OFF. During video intercom door entry calls, the matrix broadcasts
    hardware audio ducking and volume restoration frames for all zones in the house.
    These reports MUST NEVER change the amplifier power state (#669, #682).
    """
    dimension = getattr(message, "dimension", getattr(message, "_dimension", None))
    if isinstance(dimension, (int, str)):
        try:
            return int(dimension) == 1
        except (ValueError, TypeError):
            return False

    # Fallback for synthetic test fixtures / partial mock events where `volume`
    # is populated on an event object that omits the dimension attribute.
    # To guard against suppressing legitimate transitions on future event models:
    # 1. Do not match if the message explicitly signals ON or OFF.
    # 2. Do not match if it is a volume rocker command.
    vol = getattr(message, "volume", None)
    if (
        isinstance(vol, (int, float, str))
        and not getattr(message, "is_on", False)
        and not getattr(message, "is_off", False)
        and not is_volume_rocker_up(message)
        and not is_volume_rocker_down(message)
    ):
        return True
    return False


def determine_power_transition(
    message: Any,
    is_parked: bool,
    is_wake_echo: bool,
) -> PowerTransition:
    """Determine the power state transition for an incoming bus message.

    Rules:
    1. Dimension 1 volume status reports (*#16*WHERE*1*<vol>##, *#22*3#A#P*1*<vol>##)
       report volume levels and must NEVER modify amplifier power state (#669, #682).
    2. Volume-down rocker commands (*16*1101..1115*WHERE##) step volume down only
       and must never wake an amplifier.
    3. Source device events (*16*3*10S##, *16*13*10S##) relate to external source
       interfaces and never modify room amplifier power state.
    4. Explicit OFF frames (*16*13*WHERE## / *16*10*WHERE##):
       - If within the wake echo window: WAKE_ECHO_OFF (ignored).
       - If the zone is currently parked: PARKED_CONFIRM_OFF (physical confirmation,
         preserves group membership).
       - Otherwise: REAL_OFF (powers off, initiates coordinated teardown or handover).
    5. Explicit ON frames (*16*3*WHERE## / *16*0*WHERE##):
       - WAKE_ON (sets state ON, cancels pending off, triggers auto-join).
    6. Physical volume-up rocker commands (*16*1001*WHERE## .. *16*1015*WHERE##):
       - WAKE_ON (wakes an unpowered or parked amplifier, triggers auto-join).
    7. All other frames — including matrix routing frames (*16*3*1ES##), Dimension 5
       status responses, and unrecognised frames:
       - NO_CHANGE (must NEVER turn an off amplifier on).
    """
    # 1. Dimension 1 volume status reports never alter power state (#669, #682)
    if is_dimension_1_volume_report(message):
        return PowerTransition.NO_CHANGE

    # 2. Volume-down rocker commands step volume only and must never wake an amplifier
    if is_volume_rocker_down(message):
        return PowerTransition.NO_CHANGE

    # 3. Source device events never affect amplifier zone power
    if getattr(message, "is_source_event", False):
        return PowerTransition.NO_CHANGE

    # 4. Explicit OFF frames
    if getattr(message, "is_off", False):
        if is_wake_echo:
            return PowerTransition.WAKE_ECHO_OFF
        if is_parked:
            return PowerTransition.PARKED_CONFIRM_OFF
        return PowerTransition.REAL_OFF

    # 5. Explicit ON frames
    if getattr(message, "is_on", False):
        return PowerTransition.WAKE_ON

    # 6. Physical volume-up rocker command wakes amplifier
    if is_volume_rocker_up(message):
        return PowerTransition.WAKE_ON

    # 7. Non-power frames (routing, status queries, high/low tones, etc.)
    return PowerTransition.NO_CHANGE


def calculate_auto_off_delay(new_state_val: str | MediaPlayerState | None) -> float | None:
    """Calculate the anti-hiss auto-off delay for a decoder state change.

    Returns:
        float delay in seconds if the timer should be armed, or None if the
        timer should be cancelled (audio playing/buffering) or ignored (unknown/unavailable).
    """
    if new_state_val is None:
        return None

    state_str = str(new_state_val).lower()
    if state_str in _PLAYING_STATE_STRINGS:
        return None
    if state_str in ("off", "idle", "standby"):
        return AUTO_OFF_IDLE_DELAY
    if state_str == "paused":
        return AUTO_OFF_PAUSED_DELAY
    return None
