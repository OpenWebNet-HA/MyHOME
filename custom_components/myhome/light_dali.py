"""DALI DT8 feature lock and color mode mutual exclusion rules for MyHOME lights."""

from __future__ import annotations

from homeassistant.components.light.const import (
    ColorMode,
    LightEntityFeature,
)

from .const import LOGGER


class DaliFeatureLock:
    """Manages DALI DT8 feature lock and color mode mutual exclusion rules.

    With lock_features the configuration is authoritative: the light supports
    exactly the modes declared (rgb / color_temp / dimmable) and never learns
    another one from the bus or from a restored state. This is the answer to
    DALI gateways that keep replaying an HSV or tunable white value that was
    once written to a fixture that cannot use it (issue #288).
    """

    def __init__(
        self,
        *,
        lock_features: bool,
        dimmable: bool = False,
        color_temp: bool = False,
        rgb: bool = False,
    ) -> None:
        """Initialize the DALI feature lock manager."""
        self.lock_features: bool = bool(lock_features)
        self.allowed_color_modes: set[ColorMode] = set()

        if self.lock_features:
            if rgb:
                self.allowed_color_modes.add(ColorMode.HS)
            if color_temp:
                self.allowed_color_modes.add(ColorMode.COLOR_TEMP)
            if dimmable or rgb or color_temp:
                # A colour mode implies brightness in the HA light model, and
                # the level arrives on Dimension 1 whatever the colour mode.
                self.allowed_color_modes.add(ColorMode.BRIGHTNESS)
            if not self.allowed_color_modes:
                self.allowed_color_modes.add(ColorMode.ONOFF)

    def is_mode_forbidden(self, mode: ColorMode) -> bool:
        """Return whether lock_features keeps this light from adopting ``mode``."""
        return self.lock_features and mode not in self.allowed_color_modes

    def log_locked_out(
        self,
        log_id: str,
        full_where: str,
        message: str,
        dimension: str,
    ) -> None:
        """Log that an incoming frame is ignored because the mode is locked out."""
        LOGGER.debug(
            "%s light %s is locked to %s; ignoring Dimension %s frame %s",
            log_id,
            full_where,
            sorted(mode.value for mode in self.allowed_color_modes),
            dimension,
            message,
        )

    def promote_color_mode(
        self, current_modes: set[ColorMode], mode: ColorMode
    ) -> tuple[set[ColorMode], ColorMode | None, LightEntityFeature, LightEntityFeature]:
        """Add a color capability learned from the bus without dropping others.

        DALI DT8 drivers report both HSV (dimension 12) and tunable white
        (dimension 14); HS and COLOR_TEMP therefore coexist. BRIGHTNESS and
        ONOFF are subsumed by any color mode per the HA light model.

        Returns:
            (new_supported_modes, new_color_mode, features_to_add, features_to_remove)
        """
        if self.is_mode_forbidden(mode):
            return current_modes, None, LightEntityFeature(0), LightEntityFeature(0)

        new_modes = current_modes.copy()
        if mode in (ColorMode.HS, ColorMode.COLOR_TEMP):
            new_modes.discard(ColorMode.BRIGHTNESS)
            new_modes.discard(ColorMode.ONOFF)
        elif mode == ColorMode.BRIGHTNESS:
            new_modes.discard(ColorMode.ONOFF)

        new_modes.add(mode)
        return new_modes, mode, LightEntityFeature.TRANSITION, LightEntityFeature.FLASH
