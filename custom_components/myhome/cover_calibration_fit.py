"""Fit the basic two tape readings; no residual is an independent accuracy check."""
from __future__ import annotations

import math

import voluptuous as vol


def winding(height: float, roll: float) -> float:
    """Motor fraction from the bottom, excluding the slat phase."""
    return height * (roll + 1) / (math.sqrt(1 + (roll * roll - 1) * height) + 1)


ROLL_RANGE = (1.0, 5.0)


def opening_fit(total: float, lift: float, gap: float, travel: float,
                elapsed: float, height: float, *, clamp: bool = False,
                closing: float | None = None) -> tuple[float, float]:
    """Use the actual lift-off gap to correct slat time jointly with the roll.

    `clamp` (L-9): a reading the rolls 1-5 cannot explain is still accepted; the roll is held at the nearest end of
    the rolls whose slat time is possible (from 0 to below both full times, `closing` included).
    """
    if clamp and 0 <= gap < travel and 0 <= lift < elapsed < total:
        height = min(max(height, gap + 1e-6 * travel), travel * (1 - 1e-9))
    if not 0 <= gap < height < travel or not 0 <= lift < elapsed < total:
        raise vol.Invalid("Inconsistent lift-off or tape reading")

    def prediction(roll: float) -> tuple[float, float]:
        fraction = winding(gap / travel, roll)
        slat = (lift - total * fraction) / (1 - fraction)
        return slat + (total - slat) * winding(height / travel, roll), slat

    low, high = ROLL_RANGE
    if clamp:
        rolls = opening_rolls(total, lift, gap, travel, total if closing is None else closing)
        if rolls is None:
            raise vol.Invalid("Lift-off gap contradicts the measured motor time")
        low, high = rolls
        if elapsed <= prediction(low)[0]:
            return max(0.0, prediction(low)[1]), low
        if elapsed >= prediction(high)[0]:
            return max(0.0, prediction(high)[1]), high
    elif not prediction(low)[0] - 1e-8 <= elapsed <= prediction(high)[0] + 1e-8:
        raise vol.Invalid("Reading outside the supported roll range")
    for _ in range(48):
        middle = (low + high) / 2
        if prediction(middle)[0] < elapsed:
            low = middle
        else:
            high = middle
    roll = (low + high) / 2
    slat = prediction(roll)[1]
    if not -1e-8 <= slat < total:
        raise vol.Invalid("Lift-off gap contradicts the measured motor time")
    return max(0.0, slat), roll


def closing_fit(total: float, slat: float, elapsed: float, height: float, travel: float, *, clamp: bool = False) -> float:
    """One closing point fixes one roll; the full motor time stays unchanged (`clamp`: see `_roll`)."""
    if clamp and 0 < travel:
        height = min(max(height, 1e-6 * travel), travel * (1 - 1e-9))
    if not 0 <= slat < total or not 0 < elapsed < total - slat or not 0 < height < travel:
        raise vol.Invalid("Inconsistent closing reading")
    return _roll(1 - elapsed / (total - slat), height, travel, clamp=clamp)


def opening_roll_fit(total: float, slat: float, elapsed: float, height: float, travel: float, *, clamp: bool = False) -> float:
    """Mirror of closing_fit for an ascent after a known slat phase (0 without slats)."""
    if clamp and 0 < travel:
        height = min(max(height, 1e-6 * travel), travel * (1 - 1e-9))
    if not 0 <= slat < total or not slat < elapsed < total or not 0 < height < travel:
        raise vol.Invalid("Inconsistent opening reading")
    return _roll((elapsed - slat) / (total - slat), height, travel, clamp=clamp)


def height_at(fraction: float, roll: float) -> float:
    """Invert winding() for the height: where this motor fraction ends with this roll."""
    return (2 * fraction + (roll - 1) * fraction * fraction) / (roll + 1)


def closing_range(total: float, slat: float, elapsed: float, travel: float) -> tuple[float, float] | None:
    """The heights closing_fit accepts for this stop: from roll 5 (lowest) to roll 1 (highest)."""
    if not 0 <= slat < total or not 0 < elapsed < total - slat or not 0 < travel:
        return None
    return _span(1 - elapsed / (total - slat), travel)


def opening_roll_range(total: float, slat: float, elapsed: float, travel: float) -> tuple[float, float] | None:
    """The heights opening_roll_fit accepts for this stop."""
    if not 0 <= slat < total or not slat < elapsed < total or not 0 < travel:
        return None
    return _span((elapsed - slat) / (total - slat), travel)


def opening_range(total: float, lift: float, gap: float, travel: float, elapsed: float,
                  closing: float) -> tuple[float, float] | None:
    """The heights opening_fit accepts, keeping a slat time from 0 to below both full times.

    The fitted slat time falls as the roll grows, so the accepted rolls form one interval;
    its ends, found by bisection where a slat limit cuts the 1-5 range, bound the height.
    """
    if not 0 <= gap < travel or not 0 <= lift < elapsed < total:
        return None
    rolls = opening_rolls(total, lift, gap, travel, closing)
    if rolls is None:
        return None
    highest, lowest = rolls

    def height(roll: float) -> float:
        phase = _slat(total, lift, gap, travel, roll)
        return travel * height_at((elapsed - phase) / (total - phase), roll)

    # A stop after the lift-off and before the full time is always between the gap and the travel.
    return height(lowest), height(highest)


def _slat(total: float, lift: float, gap: float, travel: float, roll: float) -> float:
    fraction = winding(gap / travel, roll)
    return (lift - total * fraction) / (1 - fraction)


def opening_rolls(total: float, lift: float, gap: float, travel: float, closing: float) -> tuple[float, float] | None:
    """The rolls (low, high) within 1-5 whose slat time from the lift-off is from 0 to below both full times.

    The slat time falls as the roll grows, so they form one interval; its ends are found by bisection where a slat
    limit cuts the 1-5 range, each taken on its own side of the limit (a slat time from 0, below the cap), never at a
    midpoint that rounding could put across it. None when no roll gives a possible slat time. Its callers have
    already checked the gap below the travel and the lift-off within the full time.
    """
    def slat(roll: float) -> float:
        return _slat(total, lift, gap, travel, roll)

    def roll_where(limit: float) -> tuple[float, float]:
        """The two rolls 2⁻⁴⁸ apart around `limit`: (slat time at or above it, slat time below it)."""
        low, high = ROLL_RANGE
        for _ in range(48):
            middle = (low + high) / 2
            if slat(middle) >= limit:
                low = middle
            else:
                high = middle
        return low, high

    cap = min(total, closing)
    if slat(ROLL_RANGE[0]) < 0 or slat(ROLL_RANGE[1]) >= cap:
        return None
    high = ROLL_RANGE[1] if slat(ROLL_RANGE[1]) >= 0 else roll_where(0.0)[0]
    low = ROLL_RANGE[0] if slat(ROLL_RANGE[0]) < cap else roll_where(cap)[1]
    return low, high


def _span(fraction: float, travel: float) -> tuple[float, float]:
    return travel * height_at(fraction, 5.0), travel * height_at(fraction, 1.0)


def _roll(fraction: float, height: float, travel: float, *, clamp: bool = False) -> float:
    """Invert winding(): the roll that puts this motor fraction at this height.

    `clamp` (L-9): a reading no roll from 1 to 5 explains is held at the nearest end (above roll 5 when the edge is
    lower than any roll puts it, below roll 1 when it is higher) instead of being refused.
    """
    h = height / travel
    denominator = h - fraction * fraction
    if denominator <= 0:
        if clamp:
            return ROLL_RANGE[1]
        raise vol.Invalid("Reading outside the supported roll range")
    roll = (2 * fraction - fraction * fraction - h) / denominator
    if not clamp and not ROLL_RANGE[0] - 1e-8 <= roll <= ROLL_RANGE[1] + 1e-8:
        raise vol.Invalid("Reading outside the supported roll range")
    return min(ROLL_RANGE[1], max(ROLL_RANGE[0], roll))
