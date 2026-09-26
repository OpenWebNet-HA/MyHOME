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

