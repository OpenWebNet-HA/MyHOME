"""Pure travel-time position math and echo window checks for MyHome covers."""
from __future__ import annotations

# Timed-cover echo model (issue #302). After a direction/stop frame is written,
# the gateway relays our own command back on the monitor session: a stop status
# within ~0.1 s, the WHAT=1000 translation, and the real direction status once the
# motor starts (~0.55 s on a MyHOMEServer1). Frames for this cover inside the
# window are treated as echoes of our command, not as keypad presses.
ECHO_WINDOW: float = 1.5

# Time from the write of a direction frame to the motor start when the gateway
# never relays the direction status (measured 0.55 s on a MyHOMEServer1, #302).
MOTOR_START_DELAY: float = 0.55

# Upper bound on how long we wait for the send queue to write our frame before
# falling back to "now" as the motion anchor. #302 measured the *queue*: with
# twelve covers the last frame goes out 1.5-12 s after enqueue (the ~1.6 s often
# quoted is the per-frame wait), and a reconnect after the 15 s idle close adds
# the handshake on top.
WRITE_TIMEOUT: float = 30.0

# Shortest motor run we schedule for a timed cover (#466). A tubular motor needs
# a moment after the relay closes before the curtain moves. On an F454 with
# Somfy Ilmo 50 WT motors (20.4 s travel) 1% steps ran 0.18-0.22 s between the
# start and stop status frames and the relay clicked without moving the curtain,
# while 2% steps (0.39-0.41 s) moved it (#466 comment 6038376669). So the
# threshold lies somewhere in 0.22-0.39 s for that motor; the floor is the
# shortest run known to move it. Other motors may differ.
MIN_MOTOR_PULSE: float = 0.4

# Smallest level change sent to an advanced actuator (#466). The actuator turns
# a level change into a timed run of its own, so a 1% change is the same
# too-short run as above. 2% was enough on the 20.4 s motor in that trace; we
# do not know the actuator's travel time, so a faster motor may need more. The
# trace used the step frames (*2*12#1#001*WHERE##), not the level frames
# (*#2*WHERE*#11#001*LEVEL##) we send: their behaviour is inferred, not captured.
MIN_POSITION_DELTA: int = 2


def quantize_position_delta(
    curr_pos: int | None,
    target_pos: int,
    min_delta: int = MIN_POSITION_DELTA,
) -> int:
    """Widen a level change smaller than ``min_delta`` to ``min_delta``.

    If 0 < |target_pos - curr_pos| < min_delta the target moves to min_delta
    from curr_pos in the commanded direction, clamped to [0, 100]. The result
    can be 0 or 100: callers route those to a full run.
    """
    if curr_pos is None:
        return max(0, min(100, target_pos))
    diff = target_pos - curr_pos
    if diff == 0:
        return curr_pos
    if 0 < abs(diff) < min_delta:
        step = min_delta if diff > 0 else -min_delta
        return max(0, min(100, curr_pos + step))
    return max(0, min(100, target_pos))


def compute_run_duration(
    diff: int,
    travel_time: float,
    min_pulse: float = MIN_MOTOR_PULSE,
) -> float:
    """Return the run time for a position change, at least ``min_pulse`` seconds.

    A run shorter than the motor's start-up time clicks the relay without
    moving the curtain while the estimate still advances (see MIN_MOTOR_PULSE).
    """
    if diff == 0 or travel_time <= 0:
        return 0.0
    travel_fraction = abs(diff) / 100.0
    nominal_duration = travel_fraction * travel_time
    return max(nominal_duration, min_pulse)


def travel_for(travel_time_up: float, travel_time_down: float, opening: bool) -> float:
    """Return full-travel seconds for the given direction (motors are often slower going up)."""
    return travel_time_up if opening else travel_time_down


def is_in_echo_window(echo_until: float | None, now: float) -> bool:
    """Return whether timestamp `now` is within the active echo window."""
    return echo_until is not None and now < echo_until


def compute_interpolated_position(
    current_position: int | None,
    start_position: int,
    move_start_time: float | None,
    now: float,
    travel_time: float,
    is_opening: bool,
    is_closing: bool,
) -> int | None:
    """Calculate current cover position interpolated from elapsed motion time."""
    if move_start_time is not None and travel_time > 0 and (is_opening or is_closing):
        elapsed = max(0.0, now - move_start_time)
        delta = (elapsed / travel_time) * 100.0
        if is_opening:
            return min(100, int(round(start_position + delta)))
        if is_closing:
            return max(0, int(round(start_position - delta)))
    return current_position


def compute_freeze_position(
    start_position: int,
    move_start_time: float | None,
    at: float,
    travel_time: float,
    is_opening: bool,
    is_closing: bool,
    current_position: int | None = None,
) -> int:
    """Calculate the final position when motion freezes as of timestamp `at`.

    When stationary (move_start_time is None or neither opening nor closing),
    correctly preserves current_position or start_position.
    """
    pos = compute_interpolated_position(
        current_position=current_position,
        start_position=start_position,
        move_start_time=move_start_time,
        now=at,
        travel_time=travel_time,
        is_opening=is_opening,
        is_closing=is_closing,
    )
    if pos is not None:
        return pos
    return current_position if current_position is not None else start_position

