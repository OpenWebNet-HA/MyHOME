"""Config subentry flow for WHO=4 probe-less thermo actuator / towel-warmer switches."""

from __future__ import annotations

import typing

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers import selector
from voluptuous import Required

from .const import (
    BUS_ROUTING,
    CONF_BUS_INTERFACE,
    CONF_TURN_OFF_COMMAND,
    CONF_TURN_ON_COMMAND,
    CONF_WHERE,
    CONF_WHO,
    SUBENTRY_TYPE_THERMO_SWITCH,
)
from .data import get_runtime_data
from .thermo_switch import (
    load_thermo_switch_subentries as load_thermo_switch_subentries,
)
from .typing_compat import flow_schema
from .where_grammar import parse_thermo_actuator_where, validate_thermo_trigger_command


class ThermoSwitchSubentryFlowHandler(ConfigSubentryFlow):
    """Handle ConfigSubentryFlow for WHO=4 probe-less thermo actuator switches."""

    def _build_schema(self, defaults: dict[str, typing.Any]) -> typing.Any:
        """Build form schema for adding or reconfiguring a thermo switch subentry."""
        where_raw = str(defaults.get(CONF_WHERE, "") or "")
        iface_raw = str(defaults.get(CONF_BUS_INTERFACE, "") or "")
        where_default = (
            f"{where_raw}{BUS_ROUTING}{iface_raw}"
            if where_raw and iface_raw and BUS_ROUTING not in where_raw
            else where_raw
        )
        name_default = str(defaults.get(CONF_NAME, "") or "")
        on_cmd_default = str(defaults.get(CONF_TURN_ON_COMMAND, "") or "")
        off_cmd_default = str(defaults.get(CONF_TURN_OFF_COMMAND, "") or "")

        schema_dict: dict[typing.Any, typing.Any] = {
            Required(
                CONF_WHERE,
                description={"suggested_value": where_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_NAME,
                description={"suggested_value": name_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_TURN_ON_COMMAND,
                description={"suggested_value": on_cmd_default},
            ): selector.TextSelector(),
            vol.Optional(
                CONF_TURN_OFF_COMMAND,
                description={"suggested_value": off_cmd_default},
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
        raw_where = user_input.get(CONF_WHERE)
        where_str = (
            ""
            if raw_where is None or isinstance(raw_where, bool)
            else str(raw_where).strip()
        )
        norm_where = ""
        norm_if: str | None = None
        if not where_str:
            errors[CONF_WHERE] = "where_required"
        else:
            try:
                norm_where, _zone, _act, norm_if = parse_thermo_actuator_where(
                    where_str, user_input.get(CONF_BUS_INTERFACE)
                )
            except ValueError:
                errors[CONF_WHERE] = "invalid_where"

        raw_name = user_input.get(CONF_NAME)
        name = (
            ""
            if raw_name is None or isinstance(raw_name, bool)
            else str(raw_name).strip()
        )

        raw_on = user_input.get(CONF_TURN_ON_COMMAND)
        raw_off = user_input.get(CONF_TURN_OFF_COMMAND)
        if isinstance(raw_on, bool):
            errors[CONF_TURN_ON_COMMAND] = "invalid_command"
            on_str = ""
        else:
            on_str = str(raw_on or "").strip()
        if isinstance(raw_off, bool):
            errors[CONF_TURN_OFF_COMMAND] = "invalid_command"
            off_str = ""
        else:
            off_str = str(raw_off or "").strip()

        on_cmd: str | None = None
        off_cmd: str | None = None
        if (
            CONF_TURN_ON_COMMAND not in errors
            and CONF_TURN_OFF_COMMAND not in errors
            and bool(on_str) != bool(off_str)
        ):
            missing_field = CONF_TURN_OFF_COMMAND if on_str else CONF_TURN_ON_COMMAND
            errors[missing_field] = "paired_commands_required"
        elif on_str and off_str:
            try:
                on_cmd = validate_thermo_trigger_command(on_str)
            except ValueError:
                errors[CONF_TURN_ON_COMMAND] = "invalid_command"
            try:
                off_cmd = validate_thermo_trigger_command(off_str)
            except ValueError:
                errors[CONF_TURN_OFF_COMMAND] = "invalid_command"

        if errors:
            return {}, errors, ""

        entry = self._get_entry()
        candidate_pair = (norm_where, norm_if)

        subentries = getattr(entry, "subentries", None) or {}
        for sub_id, subentry in subentries.items():
            if exclude_subentry_id and str(sub_id) == exclude_subentry_id:
                continue
            if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_THERMO_SWITCH:
                continue
            sdata = dict(getattr(subentry, "data", None) or {})
            try:
                s_where, _sz, _sa, s_if = parse_thermo_actuator_where(
                    sdata.get(CONF_WHERE), sdata.get(CONF_BUS_INTERFACE)
                )
            except ValueError:
                continue
            if (s_where, s_if) == candidate_pair:
                errors["base"] = "already_configured"
                return {}, errors, ""

        runtime = get_runtime_data(entry)
        if runtime is not None:
            for cfg in runtime.platforms.get("switch", {}).values():
                if not isinstance(cfg, dict) or str(cfg.get(CONF_WHO)) != "4":
                    continue
                if exclude_subentry_id and cfg.get("subentry_id") == exclude_subentry_id:
                    continue
                try:
                    c_where, _cz, _ca, c_if = parse_thermo_actuator_where(
                        cfg.get(CONF_WHERE), cfg.get(CONF_BUS_INTERFACE)
                    )
                except ValueError:
                    continue
                if (c_where, c_if) == candidate_pair:
                    errors["base"] = "already_configured"
                    return {}, errors, ""

        cleaned: dict[str, typing.Any] = {
            CONF_WHO: "4",
            CONF_WHERE: norm_where,
        }
        if norm_if is not None:
            cleaned[CONF_BUS_INTERFACE] = norm_if
        if name:
            cleaned[CONF_NAME] = name
        if on_cmd is not None and off_cmd is not None:
            cleaned[CONF_TURN_ON_COMMAND] = on_cmd
            cleaned[CONF_TURN_OFF_COMMAND] = off_cmd

        addr_label = f"{norm_where}{BUS_ROUTING}{norm_if}" if norm_if else norm_where
        title = (
            f"{name} (WHO 4 / WHERE {addr_label})"
            if name
            else f"Thermo Switch (WHO 4 / WHERE {addr_label})"
        )
        return cleaned, errors, title

    async def async_step_user(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Handle adding a new thermo switch subentry."""
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
        """Handle reconfiguring an existing thermo switch subentry."""
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
