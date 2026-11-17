"""Cover / shutter end-of-run behaviour helpers for MyHOME options flow."""

from __future__ import annotations

import typing

import voluptuous as vol
from homeassistant.helpers import selector

from .const import (
    CONF_EXPLICIT_STOP_MARGIN,
    CONF_SEND_EXPLICIT_STOP,
    DEFAULT_EXPLICIT_STOP_MARGIN,
    DEFAULT_SEND_EXPLICIT_STOP,
    MAX_EXPLICIT_STOP_MARGIN,
    MIN_EXPLICIT_STOP_MARGIN,
)


class OptionsFlowCoversMixin:
    """Gateway-wide default end-of-run behaviour for timed WHO=2 covers.

    Individual covers can still override these defaults in YAML with their own
    ``send_explicit_stop:`` / ``explicit_stop_margin:`` keys; the values
    configured here only apply when a cover does not set its own.
    """

    hass: typing.Any
    config_entry: typing.Any
    options: dict[str, typing.Any] | None

    def _persist_covers_options(self, user_input: dict[str, typing.Any]) -> None:
        """Persist the gateway-wide default explicit-stop behaviour for covers."""
        assert self.options is not None
        self.options[CONF_SEND_EXPLICIT_STOP] = bool(
            user_input.get(CONF_SEND_EXPLICIT_STOP, DEFAULT_SEND_EXPLICIT_STOP)
        )
        self.options[CONF_EXPLICIT_STOP_MARGIN] = float(
            user_input.get(CONF_EXPLICIT_STOP_MARGIN, DEFAULT_EXPLICIT_STOP_MARGIN)
        )

    def _append_covers_schema(
        self, schema_dict: dict[typing.Any, typing.Any]
    ) -> None:
        """Append the cover end-of-run explicit-stop fields to the options schema."""
        assert self.options is not None
        schema_dict[
            vol.Optional(
                CONF_SEND_EXPLICIT_STOP,
                description={
                    "suggested_value": self.options.get(
                        CONF_SEND_EXPLICIT_STOP, DEFAULT_SEND_EXPLICIT_STOP
                    )
                },
                default=DEFAULT_SEND_EXPLICIT_STOP,
            )
        ] = selector.BooleanSelector()

        schema_dict[
            vol.Optional(
                CONF_EXPLICIT_STOP_MARGIN,
                description={
                    "suggested_value": self.options.get(
                        CONF_EXPLICIT_STOP_MARGIN, DEFAULT_EXPLICIT_STOP_MARGIN
                    )
                },
                default=DEFAULT_EXPLICIT_STOP_MARGIN,
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_EXPLICIT_STOP_MARGIN,
                max=MAX_EXPLICIT_STOP_MARGIN,
                step=0.5,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
