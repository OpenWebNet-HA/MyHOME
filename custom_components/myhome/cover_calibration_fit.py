"""Fit the basic two tape readings; no residual is an independent accuracy check."""
from __future__ import annotations

import math

import voluptuous as vol


def winding(height: float, roll: float) -> float:
    """Motor fraction from the bottom, excluding the slat phase."""
    return height * (roll + 1) / (math.sqrt(1 + (roll * roll - 1) * height) + 1)


def opening_fit(total: float, lift: float, gap: float, travel: float,
                elapsed: float, height: float) -> tuple[float, float]:
    """Use the actual lift-off gap to correct slat time jointly with the roll."""
    if not 0 <= gap < height < travel or not 0 <= lift < elapsed < total:
        raise vol.Invalid("Inconsistent lift-off or tape reading")

    def prediction(roll: float) -> tuple[float, float]:
        fraction = winding(gap / travel, roll)
        slat = (lift - total * fraction) / (1 - fraction)
        return slat + (total - slat) * winding(height / travel, roll), slat

    low, high = 1.0, 5.0
    if not prediction(low)[0] - 1e-8 <= elapsed <= prediction(high)[0] + 1e-8:
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


def closing_fit(total: float, slat: float, elapsed: float, height: float, travel: float) -> float:
    """One closing point fixes one roll; the full motor time stays unchanged."""
    if not 0 <= slat < total or not 0 < elapsed < total - slat or not 0 < height < travel:
        raise vol.Invalid("Inconsistent closing reading")
    return _roll(1 - elapsed / (total - slat), height, travel)


def opening_roll_fit(total: float, slat: float, elapsed: float, height: float, travel: float) -> float:
    """Mirror of closing_fit for an ascent after a known slat phase (0 without slats)."""
    if not 0 <= slat < total or not slat < elapsed < total or not 0 < height < travel:
        raise vol.Invalid("Inconsistent opening reading")
    return _roll((elapsed - slat) / (total - slat), height, travel)


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

    def slat(roll: float) -> float:
        fraction = winding(gap / travel, roll)
        return (lift - total * fraction) / (1 - fraction)

    def roll_where(limit: float) -> float:
        low, high = 1.0, 5.0
        for _ in range(48):
            middle = (low + high) / 2
            if slat(middle) >= limit:
                low = middle
            else:
                high = middle
        return (low + high) / 2

    cap = min(total, closing)
    if slat(1.0) < 0 or slat(5.0) >= cap:
        return None
    lowest = 5.0 if slat(5.0) >= 0 else roll_where(0.0)
    highest = 1.0 if slat(1.0) < cap else roll_where(cap)

    def height(roll: float) -> float:
        phase = slat(roll)
        return travel * height_at((elapsed - phase) / (total - phase), roll)

    # A stop after the lift-off and before the full time is always between the gap and the travel.
    return height(lowest), height(highest)


def _span(fraction: float, travel: float) -> tuple[float, float]:
    return travel * height_at(fraction, 5.0), travel * height_at(fraction, 1.0)


def _roll(fraction: float, height: float, travel: float) -> float:
    """Invert winding(): the roll that puts this motor fraction at this height."""
    h = height / travel
    denominator = h - fraction * fraction
    if denominator <= 0:
        raise vol.Invalid("Reading outside the supported roll range")
    roll = (2 * fraction - fraction * fraction - h) / denominator
    if not 1 - 1e-8 <= roll <= 5 + 1e-8:
        raise vol.Invalid("Reading outside the supported roll range")
    return min(5.0, max(1.0, roll))
