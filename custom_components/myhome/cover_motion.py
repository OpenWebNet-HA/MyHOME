"""Pure travel-time position math and echo window checks for MyHome covers."""
from __future__ import annotations

# Timed-cover echo window after socket write (empirical: 1.5 s; #302, #753, #959):
# - Issue #302 (live MyHomeServer1): gateway relays pre-move *2*0*WHERE## within
#   ~0.10 s, *2*1000#<dir>*WHERE## at ~0.15 s, and motor-start *2*<dir>*WHERE##
#   at ~0.55 s after socket write.
# - PR #753 (tests/fixtures/traces/f461_live_2026-10-18/, live F461 2.0.11 + F411/2)
#   and tests/golden/frames/who02_automation.yaml (MH200 2.1.0): gateways whose
#   coso (libplug_automation_tr.so) or bt_luci daemon injects an SCS STOP (12 0A)
#   ahead of UP/DOWN (12 08/12 09) emit *2*0*WHERE## ~0.4-0.6 s before *2*1/*2*2.
# - 1.5 s (~0.55 s motor-start lag + ~0.95 s LAN/SCS arbitration & event-session
#   scheduling margin) covers that full echo burst while bounding how long an
#   unfollowed *2*0*WHERE## (#959 terminal stop when no *2*1/*2*2 follows) waits.
ECHO_WINDOW: float = 1.5

# Time from the write of a direction frame to the motor start when the gateway
# never relays the direction status (measured 0.55 s on a MyHOMEServer1, #302).
MOTOR_START_DELAY: float = 0.55

# Stop status follows the stop write almost immediately on both measured
# gateway families (34-47 ms, median 42 ms on MyHOMEServer1; 64-120 ms on F461;
# #705). Unlike the direction side (~0.5 s), no fixed fallback offset is added
# to the stop write time; gateways that relay the stop status re-anchor on it.
MOTOR_STOP_DELAY: float = 0.0
STOP_LATENCY: float = MOTOR_STOP_DELAY

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

# Smallest level change sent to an advanced actuator (discussion #770, #466).
# The actuator turns a Dimension 11 level write (*#2*WHERE*#11#001*LEVEL##) into
# a timed run of its own: on a 3.75 m F401 shutter behind an F454 (91.3 s up /
# 87.8 s down, #770), captured 2% level steps ran 1.67-1.75 s each — already
# past the ~1.4 s mechanical slat pivot (three H4660M2 keypad pulses at 2%) —
# so per-cover min_position_delta: 1 (~0.9 s per 1%) is used there for fine slat
# angles. On a short-travel shutter (20.4 s Somfy Ilmo 50 WT in #466, where 1%
# is ~0.2 s), a 1% move clicks the relay without moving the curtain, so the
# default stays 2% (configurable in 1..5%).
MIN_POSITION_DELTA: int = 2

# Interlocked relay & motor capacitor settle pause between a pre-move STOP socket
# write and the subsequent direction frame on gateways without SCS pre-STOP (MH201,
# MH202) (empirical: 0.6 s; #959):
# - Native MH201 firmware scenario STOP -> MOVE bus spacing: 0.587 s measured
#   between *2*0*#1## and *2*2*#1## in
#   tests/fixtures/plants/mh201_physical_plant/diagnostic_summary.json.
# - Live MH201 (fw 3.3.6) + 35x BTicino L4671/2 verification in Issue #959
#   (Encyclopedia OWN-DEV-0098/0022/0023/0153 Object 7 interlocked 230 Vac motor
#   relays): waiting 0.6 s after the STOP write made every mid-run reversal and
#   every post-arrival move inside the ~60 s relay hold succeed cleanly.
PRE_MOVE_STOP_SETTLE_DELAY: float = 0.6

# Post-start hold window during which a factory M=0 timed relay actuator's coil
# may remain energized after HA settled the curtain at 0% / 100% (empirical: 65.0 s;
# #319, #565, #959, #961):
# - Encyclopedia OWN-DEV-0098 (H4671/2, L4671/2, AM5851/2), OWN-DEV-0022 (F411/2),
#   OWN-DEV-0023 (F411/4), and OWN-DEV-0153 (F411U2): factory configurator M=0
#   sets internal relay cutoff STOP_TIME = 60 s.
# - Measured M=0 hardware oscillator drift: 60.05-61.5 s on MH200N / MyHomeServer1
#   (const.CALIBRATION_CUTOFFS[0] + CALIBRATION_CUTOFF_LATE_PER_MIN = 65.0 s) and
#   61.38 s on MH201 3.3.6 + L4671/2 in mh201_l4671_reversal_2026-11-18 frame [9].
# - 65.0 s (60.0 s M=0 STOP_TIME + ~1.5 s oscillator drift + ~3.5 s bus/scheduler
#   margin) covers the full energized tail and clears early on rx *2*0*WHERE##.
M0_RELAY_HOLD_WINDOW: float = 65.0

# Slack after the stored travel time before a timed full run (0% / 100%) is
# reported as arrived. No stop frame is sent for those runs, and the actuator's
# own stop status may come a minute late or never (#466 review, MyHOMEServer1).
END_SETTLE_MARGIN: float = 2.0

# Slack before the stored travel time within which the actuator's own stop
# status (*2*0*WHERE##, no command translation) still counts as the end stop of
# a full run from a known position (discussion #770 comment 19245565: open /
# close from a known position settle at the end like a run from unknown).
# Measured on the live F461 2.0.11 / F411/2 plant (report 20, 2026-10-26):
# with the travel calibrated at 5.82 s down / 5.85 s up, a close from 100 %
# ended with the actuator's stop 5.76 s after the motor start (60 ms early,
# estimate "open 1 %") and an open with the stop at 5.74 s (80 ms early,
# "open 98 %", report 18) while the shutter sat on its limit stop; a run that
# matched the 5.82 s settled at 100 %. The relay of the stop status and the
# actuator's own limit stop account for ~0.1 s; a yaml travel_time is a whole
# second (6 for that 5.82 s shutter), so the stored time can be up to 0.5 s off
# the real travel as well. 0.5 s covers both. Only a plain *2*0*WHERE## on a
# full run to an end gets the tolerance: a stop HA sent, a stop during a
# set_cover_position run HA times itself, and a keypad stop relayed with the
# WHO 2 command translation (*2*1000#0*WHERE##, F461 / MyHomeServer1) freeze
# the timed estimate as before. Known ambiguity: on a gateway that relays no
# command translation (MH200N and the like) a wall-switch or scenario stop in
# the last 0.5 s of a full run is the same plain *2*0*WHERE## as the actuator's
# limit stop and is reported as arrived too; worst case a manual stop there
# shows 100 % / 0 % instead of 92..99 % on the shortest shutters (6 s, where
# 0.5 s is 8 % of the travel) and instead of 99 % on a 60 s one. 0.5 s stays
# inside the 1 s early window of the calibration cutoff rule (#843,
# CALIBRATION_CUTOFF_EARLY) and does not treat any configurator limit as a stop.
END_STOP_TOLERANCE: float = 0.5

# Upper-bound drift added per unconfirmed timed movement segment (#973):
# - Fixed start/stop anchor uncertainty per segment (0.5 s): covers tubular motor
#   start-up lag variance (~0.2-0.4 s, #466), stop relay release (~0.04-0.12 s,
#   #705), and unrelayed write-to-bus arbitration jitter.
# - Proportional non-linear speed / roll-diameter variance (15% of segment motor
#   time): covers roller-shutter roll radius change and slat-gathering slack
#   between endpoints (~15-22% midpoint deviation in docs/configuration/covers.md).
SEGMENT_STOP_DRIFT: float = 0.5
SEGMENT_TRAVEL_DRIFT_FACTOR: float = 0.15


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


def accumulate_drift_bound(
    current_drift: float | None,
    segment_seconds: float,
    max_travel: float,
    *,
    stop_drift: float = SEGMENT_STOP_DRIFT,
    travel_factor: float = SEGMENT_TRAVEL_DRIFT_FACTOR,
) -> float | None:
    """Accumulate position-drift upper bound (seconds) after an unconfirmed segment.

    Returns ``None`` (unbounded: full ``travel_time + END_SETTLE_MARGIN`` required)
    when ``current_drift`` is already ``None``, ``max_travel <= 0``, or the updated
    bound reaches ``max_travel``.
    """
    if current_drift is None or max_travel <= 0:
        return None
    if segment_seconds <= 0:
        return current_drift if current_drift < max_travel else None
    updated = current_drift + max(0.0, stop_drift) + max(0.0, travel_factor) * segment_seconds
    if updated >= max_travel:
        return None
    return updated


def compute_bounded_settle_travel(
    start_position: int | None,
    opening: bool,
    full_travel: float,
    nominal_remaining: float,
    drift_bound: float | None,
) -> float:
    """Return full-run settle duration (before ``END_SETTLE_MARGIN``) for #973.

    Falls back to full ``full_travel`` when ``start_position`` or ``drift_bound`` is
    ``None`` (unknown start, restored state, reconnect, edited travel time, or
    saturated drift) or on endpoint re-homes (opening from ``>= 100`` or closing
    from ``<= 0``).
    """
    if full_travel <= 0:
        return 0.0
    if start_position is None or drift_bound is None:
        return full_travel
    if (opening and start_position >= 100) or (not opening and start_position <= 0):
        return full_travel
    bounded = max(0.0, nominal_remaining) + max(0.0, drift_bound)
    return min(full_travel, bounded)
