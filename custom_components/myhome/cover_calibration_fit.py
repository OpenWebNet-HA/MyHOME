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
    fraction = 1 - elapsed / (total - slat)
    h = height / travel
    denominator = h - fraction * fraction
    if denominator <= 0:
        raise vol.Invalid("Reading outside the supported roll range")
    roll = (2 * fraction - fraction * fraction - h) / denominator
    if not 1 - 1e-8 <= roll <= 5 + 1e-8:
        raise vol.Invalid("Reading outside the supported roll range")
    return min(5.0, max(1.0, roll))
