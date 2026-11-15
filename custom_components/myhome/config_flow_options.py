"""Options flow handler for MyHOME."""

from __future__ import annotations

import ipaddress
import typing

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from voluptuous import All, Coerce, In, Range, Required

from .config_flow_audio import OptionsFlowAudioMixin
from .config_flow_topology import OptionsFlowTopologyMixin
from .const import (
    CONF_ADDRESS,
    CONF_AUTO_JOIN_STREAMING,
    CONF_BROADCAST_RESYNC,
    CONF_GENERATE_EVENTS,
    CONF_IGNORED_ADDRESSES,
    CONF_OWN_PASSWORD,
    CONF_TRANSITION_MODE,
    CONF_TUNER_PICTURES,
    CONF_WORKER_COUNT,
    DEFAULT_AUTO_JOIN_STREAMING,
    DEFAULT_TRANSITION_MODE,
    IDENTIFICATION_MANUAL,
    SUPPORTED_GATEWAY_MODELS,
)
from .gateway import command_session_default, command_session_limit
from .ignored import validate_ignored_addresses
from .typing_compat import flow_schema


class MyhomeOptionsFlowHandler(
    OptionsFlowAudioMixin, OptionsFlowTopologyMixin, OptionsFlowWithReload
):
    """Handle MyHome options (general settings + decoder mapping)."""

    def __init__(self, config_entry: ConfigEntry = None):  # type: ignore
        """Initialize MyHome options flow."""
        self._config_entry = config_entry
        self.options: dict[str, typing.Any] | None = None
        self.data: dict[str, typing.Any] | None = None

    @property
    def config_entry(self):  # type: ignore
        """Return the config entry for this options flow."""
        if self._config_entry is not None:
            return self._config_entry
        if hasattr(self, "handler") and self.hass:  # type: ignore
            return self.hass.config_entries.async_get_entry(self.handler)
        return None

    async def async_step_init(self, user_input: dict[str, typing.Any] | None = None) -> typing.Any:  # pylint: disable=unused-argument
        """Keep panel access separate from gateway connection settings."""
        return self.async_show_menu(step_id="init", menu_options=["panel", "user"])

    async def async_step_panel(self, user_input: dict[str, typing.Any] | None = None) -> typing.Any:
        """Link to this gateway's panel and manage its shared sidebar shortcut."""
        from urllib.parse import urlencode

        from .panel import (
            CONF_SHOW_SIDEBAR,
            PANEL_URL,
            async_get_panel_sidebar,
            async_set_panel_sidebar,
        )

        errors = {}
        if user_input is not None:
            try:
                await async_set_panel_sidebar(self.hass, user_input[CONF_SHOW_SIDEBAR])
            except OSError:
                errors["base"] = "panel_save_failed"
            else:
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
        visible = await async_get_panel_sidebar(self.hass)
        return self.async_show_form(
            step_id="panel",
            data_schema=flow_schema({Required(CONF_SHOW_SIDEBAR, default=visible): bool}),
            description_placeholders={
                "panel_url": f"/{PANEL_URL}?{urlencode({'entry_id': self.config_entry.entry_id})}",
            },
            errors=errors,
        )

    def _initialize_options(self) -> None:
        """Prepare gateway settings only when entering their existing form."""
        options = dict(self.config_entry.options)
        options.setdefault(
            CONF_WORKER_COUNT, command_session_default(self.config_entry.data.get(CONF_NAME))
        )
        options.setdefault(CONF_GENERATE_EVENTS, False)
        options.setdefault(CONF_BROADCAST_RESYNC, True)
        options.setdefault(CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE)
        options.setdefault(CONF_AUTO_JOIN_STREAMING, DEFAULT_AUTO_JOIN_STREAMING)
        options.setdefault(CONF_IGNORED_ADDRESSES, [])
        self.options = options
        self.data = dict(self.config_entry.data)

    async def async_step_user(self, user_input=None, errors=None):  # type: ignore
        """Manage general settings and decoder mapping."""
        errors = errors or {}
        limit_model: str | None = None

        if self.options is None:
            self._initialize_options()
        assert self.options is not None and self.data is not None

        if user_input is not None:
            self._validate_audio_decoders(user_input, errors)

            limit_model = user_input.get(CONF_NAME, self.data.get(CONF_NAME))
            session_limit = command_session_limit(limit_model)
            if (
                session_limit is not None
                and int(user_input[CONF_WORKER_COUNT]) > session_limit
            ):
                errors[CONF_WORKER_COUNT] = "worker_count_above_gateway_limit"

            # Home Assistant frontend (ha-selector-text / ha-form) omits non-required
            # fields from user_input when cleared. Defaulting to "" ensures self.options
            # is cleared to [] rather than preserving stale addresses (#612).
            ignored_raw = user_input.get(CONF_IGNORED_ADDRESSES, "")
            parsed_ignored, is_valid = validate_ignored_addresses(ignored_raw)
            if not is_valid:
                errors[CONF_IGNORED_ADDRESSES] = "invalid_ignored_address"
            else:
                self.options[CONF_IGNORED_ADDRESSES] = parsed_ignored

            if not errors:
                self.options.update({CONF_WORKER_COUNT: user_input[CONF_WORKER_COUNT]})
                self.options.update(
                    {CONF_GENERATE_EVENTS: user_input[CONF_GENERATE_EVENTS]}
                )
                self.options.update(
                    {CONF_BROADCAST_RESYNC: user_input.get(CONF_BROADCAST_RESYNC, True)}
                )
                self.options[CONF_TRANSITION_MODE] = user_input.get(
                    CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE
                )
                self.options[CONF_AUTO_JOIN_STREAMING] = user_input.get(
                    CONF_AUTO_JOIN_STREAMING, DEFAULT_AUTO_JOIN_STREAMING
                )

                self._persist_audio_options(user_input)
                self._apply_topology(user_input, errors)

                _model_update = False
                if CONF_NAME in user_input and user_input[CONF_NAME] != self.data.get(
                    CONF_NAME
                ):
                    self.data[CONF_NAME] = user_input[CONF_NAME]
                    # An explicit choice is authoritative: drop any earlier WHO=13 label so
                    # the next device-type reply cannot overwrite it (see gateway.py).
                    self.data["model_source"] = IDENTIFICATION_MANUAL
                    _model_update = True

                _data_update = (
                    not (
                        self.data.get(CONF_HOST) == user_input.get(CONF_ADDRESS)
                        and self.data.get(CONF_PASSWORD)
                        == user_input.get(CONF_OWN_PASSWORD)
                    )
                    or _model_update
                )
                self.data.update({CONF_HOST: user_input.get(CONF_ADDRESS)})
                self.data.update({CONF_PASSWORD: user_input.get(CONF_OWN_PASSWORD)})

                try:
                    self.data[CONF_HOST] = str(
                        ipaddress.IPv4Address(self.data[CONF_HOST])
                    )
                except ipaddress.AddressValueError:
                    errors[CONF_ADDRESS] = "invalid_ip"

                if not errors:
                    if _data_update:
                        update_kwargs: dict[str, typing.Any] = {"data": self.data}
                        if _model_update and self.config_entry.title.endswith("Gateway"):
                            update_kwargs["title"] = f"{user_input[CONF_NAME]} Gateway"
                        self.hass.config_entries.async_update_entry(
                            self.config_entry, **update_kwargs
                        )
                        # OptionsFlowWithReload only schedules a reload when entry.options
                        # change. When only connection data changed (host, password, model)
                        # and options remain identical, schedule reload explicitly so the
                        # integration restarts with the new connection parameters.
                        if self.config_entry.options == self.options:
                            self.hass.config_entries.async_schedule_reload(
                                self.config_entry.entry_id
                            )

                    return self.async_create_entry(title="", data=self.options)

        # ── Build form schema ─────────────────────────────────────────────
        model_options = [m for m in SUPPORTED_GATEWAY_MODELS]
        current_model = self.data.get(CONF_NAME, "MyHomeServer1")
        if current_model not in model_options:
            model_options.insert(0, current_model)
        # An entry that never finished setup since upgrading still stores a count
        # above its gateway's limit; do not offer it back only to reject it.
        suggested_workers = int(self.options.get(CONF_WORKER_COUNT, 1))
        current_limit = command_session_limit(current_model)
        if current_limit is not None:
            suggested_workers = min(suggested_workers, current_limit)

        schema_dict: dict[typing.Any, typing.Any] = {
            Required(
                CONF_ADDRESS,
                description={"suggested_value": self.data.get(CONF_HOST) or ""},
            ): str,
            vol.Optional(
                CONF_NAME,
                default=current_model,
            ): vol.Any(In(model_options), cv.string),
            vol.Optional(
                CONF_OWN_PASSWORD,
                description={"suggested_value": self.data.get(CONF_PASSWORD) or ""},
            ): vol.Maybe(str),
            Required(
                CONF_WORKER_COUNT,
                description={"suggested_value": suggested_workers},
            ): All(Coerce(int), Range(min=1, max=10)),
            Required(
                CONF_GENERATE_EVENTS,
                description={
                    "suggested_value": self.options.get(CONF_GENERATE_EVENTS, False)
                },
            ): bool,
            vol.Optional(
                CONF_BROADCAST_RESYNC,
                description={
                    "suggested_value": self.options.get(CONF_BROADCAST_RESYNC, True)
                },
                default=True,
            ): bool,
            vol.Optional(
                CONF_TRANSITION_MODE,
                description={
                    "suggested_value": self.options.get(
                        CONF_TRANSITION_MODE, DEFAULT_TRANSITION_MODE
                    )
                },
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[  # type: ignore
                        {
                            "value": "software_stepped",
                            "label": "software_stepped (recommended - reliable stepped fades)",
                        },
                        {
                            "value": "native",
                            "label": "native (pass through hardware speed param - only if your dimmers support it)",
                        },
                        {
                            "value": "auto",
                            "label": "auto (alias for software_stepped)",
                        },
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_AUTO_JOIN_STREAMING,
                description={
                    "suggested_value": self.options.get(
                        CONF_AUTO_JOIN_STREAMING, DEFAULT_AUTO_JOIN_STREAMING
                    )
                },
                default=DEFAULT_AUTO_JOIN_STREAMING,
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_IGNORED_ADDRESSES,
                description={
                    "suggested_value": "\n".join(
                        self.options.get(CONF_IGNORED_ADDRESSES, [])
                    )
                    if isinstance(self.options.get(CONF_IGNORED_ADDRESSES), list)
                    else str(self.options.get(CONF_IGNORED_ADDRESSES) or "")
                },
            ): vol.Maybe(
                selector.TextSelector(selector.TextSelectorConfig(multiline=True))
            ),
            vol.Optional(
                CONF_TUNER_PICTURES,
                description={
                    "suggested_value": str(
                        self.options.get(CONF_TUNER_PICTURES) or ""
                    )
                },
            ): vol.Maybe(
                selector.TextSelector(selector.TextSelectorConfig(multiline=True))
            ),
        }

        self._append_audio_schema(schema_dict)
        self._append_topology_schema(schema_dict)

        return self.async_show_form(
            step_id="user",
            data_schema=flow_schema(schema_dict),
            errors=errors,
            description_placeholders={
                "session_limit": str(
                    command_session_limit(limit_model or current_model) or ""
                ),
                "session_default": str(
                    command_session_default(limit_model or current_model)
                ),
                "model": str(limit_model or current_model),
            },
        )
