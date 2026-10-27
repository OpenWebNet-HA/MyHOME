"""Config subentry flow exposing Home Assistant entities on the SCS bus."""

from __future__ import annotations

import typing

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import CONF_ATTRIBUTE, CONF_ENTITY_ID
from homeassistant.helpers import selector
from voluptuous import Required

from . import validate
from .const import (
    CONF_BIDIRECTIONAL,
    CONF_BRIGHTNESS_STEP_PCT,
    CONF_COOLDOWN,
    CONF_DIMENSION,
    CONF_MIN_DELTA,
    CONF_ON_STATES,
    CONF_PERIODIC_SEND,
    CONF_RESPOND_TO_READ,
    CONF_VALUE_TEMPLATE,
    CONF_WHERE,
    CONF_WHO,
)
from .data import get_runtime_data
from .typing_compat import flow_schema


class ExposeSubentryFlowHandler(ConfigSubentryFlow):
    """Handle ConfigSubentryFlow for exposing Home Assistant entities to the SCS bus."""

    def _build_schema(self, defaults: dict[str, typing.Any]) -> typing.Any:
        """Build form schema for adding or reconfiguring an expose subentry."""
        who_default = str(defaults.get(CONF_WHO, "1"))
        where_default = str(defaults.get(CONF_WHERE, "") or "")
        entity_default = str(defaults.get(CONF_ENTITY_ID, "") or "")
        attr_default = str(defaults.get(CONF_ATTRIBUTE, "") or "")
        dim_raw = defaults.get(CONF_DIMENSION)
        dim_default = str(dim_raw) if dim_raw is not None else ""
        tpl_default = str(defaults.get(CONF_VALUE_TEMPLATE, "") or "")
        on_states_raw = defaults.get(CONF_ON_STATES)
        if isinstance(on_states_raw, (list, tuple)):
            on_states_default = ", ".join(str(x) for x in on_states_raw)
        else:
            on_states_default = str(on_states_raw or "")

        def _opt_float(key: str) -> float | None:
            val = defaults.get(key)
            if val is None:
                return None
            try:
                return validate._finite_float(val)
            except vol.Invalid:
                return None

        cooldown_default = _opt_float(CONF_COOLDOWN)
        min_delta_default = _opt_float(CONF_MIN_DELTA)
        periodic_default = _opt_float(CONF_PERIODIC_SEND)
        try:
            step_default = validate._non_bool_int(
                defaults.get(CONF_BRIGHTNESS_STEP_PCT, 10)
            )
        except vol.Invalid:
            step_default = 10

        schema_dict: dict[typing.Any, typing.Any] = {
            Required(CONF_WHO, default=who_default): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="1", label="WHO 1 — Lighting / Switch / Virtual Actuator"),
                        selector.SelectOptionDict(value="2", label="WHO 2 — Automation / Cover"),
                        selector.SelectOptionDict(value="4", label="WHO 4 — Temperature Probe (Dimension 0)"),
                        selector.SelectOptionDict(value="13", label="WHO 13 — Bus Clock & DST Sync"),
                        selector.SelectOptionDict(value="18", label="WHO 18 — Energy / Power Meter"),
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_WHERE,
                description={"suggested_value": where_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ENTITY_ID,
                description={"suggested_value": entity_default} if entity_default else None,
            ): selector.EntitySelector(),
            vol.Optional(
                CONF_ATTRIBUTE,
                description={"suggested_value": attr_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_DIMENSION,
                description={"suggested_value": dim_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_COOLDOWN,
                default=cooldown_default if cooldown_default is not None else 1.0,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=3600.0, step=0.5, unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_MIN_DELTA,
                description=(
                    {"suggested_value": min_delta_default}
                    if min_delta_default is not None
                    else None
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=10000.0, step=0.1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_PERIODIC_SEND,
                description=(
                    {"suggested_value": periodic_default}
                    if periodic_default is not None
                    else None
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=604800.0, step=1.0, unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            Required(
                CONF_RESPOND_TO_READ,
                default=bool(defaults.get(CONF_RESPOND_TO_READ, True)),
            ): selector.BooleanSelector(),
            Required(
                CONF_BIDIRECTIONAL,
                default=bool(defaults.get(CONF_BIDIRECTIONAL, True)),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_BRIGHTNESS_STEP_PCT,
                default=step_default,
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=100, step=1, unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_ON_STATES,
                description={"suggested_value": on_states_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_VALUE_TEMPLATE,
                description={"suggested_value": tpl_default},
            ): selector.TextSelector(),
        }
        return flow_schema(schema_dict)

    def _validate_and_clean(
        self,
        user_input: dict[str, typing.Any],
        *,
        exclude_subentry_id: str | None = None,
    ) -> tuple[dict[str, typing.Any], dict[str, str], str]:
        """Validate subentry user_input and return (cleaned_data, errors, title)."""
        errors: dict[str, str] = {}
        try:
            who = validate._non_bool_int(user_input.get(CONF_WHO, 1))
        except vol.Invalid:
            errors[CONF_WHO] = "invalid_who"
            return {}, errors, ""
        if who not in (1, 2, 4, 13, 18):
            errors[CONF_WHO] = "invalid_who"
            return {}, errors, ""

        raw_where = user_input.get(CONF_WHERE)
        where = (
            ""
            if raw_where is None or isinstance(raw_where, bool)
            else str(raw_where).strip()
        )
        raw_entity = user_input.get(CONF_ENTITY_ID)
        entity_id = (
            ""
            if raw_entity is None or isinstance(raw_entity, bool)
            else str(raw_entity).strip()
        )
        dim_val = user_input.get(CONF_DIMENSION)
        if isinstance(dim_val, bool):
            errors[CONF_DIMENSION] = "invalid_dimension"
            return {}, errors, ""
        raw_dim = str(dim_val if dim_val is not None else "").strip()
        dim: int | None = None
        if raw_dim:
            if not raw_dim.isdigit():
                errors[CONF_DIMENSION] = "invalid_dimension"
            else:
                dim = int(raw_dim)

        if who == 13:
            if dim is not None and dim not in (0, 1, 22):
                errors[CONF_DIMENSION] = "invalid_dimension"
            where = ""
        else:
            if not where:
                errors[CONF_WHERE] = "where_required"
            if not entity_id:
                errors[CONF_ENTITY_ID] = "entity_required"
            if who == 4 and dim is not None and dim != 0:
                errors[CONF_DIMENSION] = "invalid_dimension"
            elif who == 18 and dim is not None and dim not in (51, 52, 53, 54, 113):
                errors[CONF_DIMENSION] = "invalid_dimension"

        if errors:
            return {}, errors, ""

        cleaned: dict[str, typing.Any] = {
            CONF_WHO: who,
            CONF_COOLDOWN: user_input.get(CONF_COOLDOWN, 1.0),
            CONF_RESPOND_TO_READ: bool(user_input.get(CONF_RESPOND_TO_READ, True)),
            CONF_BIDIRECTIONAL: bool(user_input.get(CONF_BIDIRECTIONAL, who in (1, 2))),
            CONF_BRIGHTNESS_STEP_PCT: user_input.get(CONF_BRIGHTNESS_STEP_PCT, 10),
        }
        if where:
            cleaned[CONF_WHERE] = where
        if entity_id:
            cleaned[CONF_ENTITY_ID] = entity_id
        attr = str(user_input.get(CONF_ATTRIBUTE) or "").strip()
        if attr:
            cleaned[CONF_ATTRIBUTE] = attr
        if dim is not None:
            cleaned[CONF_DIMENSION] = dim
        if user_input.get(CONF_MIN_DELTA) is not None and str(user_input.get(CONF_MIN_DELTA)).strip() != "":
            cleaned[CONF_MIN_DELTA] = user_input[CONF_MIN_DELTA]
        if user_input.get(CONF_PERIODIC_SEND) is not None and str(user_input.get(CONF_PERIODIC_SEND)).strip() != "":
            cleaned[CONF_PERIODIC_SEND] = user_input[CONF_PERIODIC_SEND]
        on_states = str(user_input.get(CONF_ON_STATES) or "").strip()
        if on_states:
            cleaned[CONF_ON_STATES] = [s.strip() for s in on_states.split(",") if s.strip()]
        tpl = str(user_input.get(CONF_VALUE_TEMPLATE) or "").strip()
        if tpl:
            cleaned[CONF_VALUE_TEMPLATE] = tpl

        try:
            cleaned = validate.expose_entry_schema(cleaned)
        except vol.Invalid:
            errors["base"] = "invalid_expose"
            return {}, errors, ""

        # Check for duplicate binding on the parent entry
        entry = self._get_entry()
        candidate_dim = cleaned.get(CONF_DIMENSION)
        if candidate_dim is None:
            candidate_dim = 0 if who == 4 else (22 if who == 13 else None)
        candidate_key = (int(cleaned[CONF_WHO]), str(cleaned.get(CONF_WHERE) or ""), candidate_dim)

        runtime = get_runtime_data(entry)
        existing_bindings = (
            runtime.expose_manager.bindings
            if runtime is not None and runtime.expose_manager is not None
            else []
        )
        for existing in existing_bindings:
            if exclude_subentry_id and existing.subentry_id == exclude_subentry_id:
                continue
            if existing.key == candidate_key:
                errors["base"] = "invalid_expose"
                return {}, errors, ""

        norm_where = str(cleaned.get(CONF_WHERE) or "")
        if who == 13:
            title = f"Bus Clock Sync (WHO 13, dim {dim if dim is not None else 22})"
        else:
            title = f"WHO {who} / WHERE {norm_where} ({entity_id})"
        return cleaned, errors, title

    async def async_step_user(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new expose subentry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors, title = self._validate_and_clean(user_input)
            if not errors:
                entry = self._get_entry()
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(title=title, data=cleaned)

        return self.async_show_form(
            step_id="user",
            data_schema=self._build_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Handle reconfiguring an existing expose subentry."""
        errors: dict[str, str] = {}
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry()
        if user_input is not None:
            cleaned, errors, title = self._validate_and_clean(
                user_input, exclude_subentry_id=subentry.subentry_id
            )
            if not errors:
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_update_and_abort(
                    entry, subentry, title=title, data=cleaned
                )

        defaults = dict(subentry.data)
        if user_input:
            defaults.update(user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._build_schema(defaults),
            errors=errors,
        )
