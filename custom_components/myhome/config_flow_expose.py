"""Config subentry flow exposing Home Assistant entities on the SCS bus."""

from __future__ import annotations

import copy
import typing

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_ATTRIBUTE, CONF_ENTITY_ID
from homeassistant.helpers import selector
from voluptuous import Required

from . import validate
from .const import (
    CONF_BIDIRECTIONAL,
    CONF_BRIGHTNESS_STEP_PCT,
    CONF_BUS_INTERFACE,
    CONF_COOLDOWN,
    CONF_DIMENSION,
    CONF_MIN_DELTA,
    CONF_ON_STATES,
    CONF_PERIODIC_SEND,
    CONF_RESPOND_TO_READ,
    CONF_STEP_DOWN_ACTION,
    CONF_STEP_UP_ACTION,
    CONF_TURN_OFF_ACTION,
    CONF_TURN_ON_ACTION,
    CONF_VALUE_TEMPLATE,
    CONF_WHERE,
    CONF_WHO,
    SUBENTRY_TYPE_EXPOSE,
)
from .data import get_runtime_data
from .expose import effective_expose_profile, injection_block_reason
from .typing_compat import flow_schema
from .where_grammar import (
    diagnose_apl_where,
    encode_apl_where,
    normalize_expose_where,
    parse_apl_where,
)

CONF_EXPOSE_AREA = "area"
CONF_EXPOSE_POINT = "point"
_IFACE_NONE = "none"
_SELECTOR_WHO = "expose_who"
_SELECTOR_BUS_INTERFACE = "expose_bus_interface"
_SELECTOR_WHO13_DIMENSION = "expose_who13_dimension"
_SELECTOR_WHO18_DIMENSION = "expose_who18_dimension"

_WHO_STEP_ID: dict[int, str] = {
    1: "who_1",
    2: "who_2",
    4: "who_4",
    13: "who_13",
    18: "who_18",
}

_WHO_CODES: tuple[int, ...] = (1, 2, 4, 13, 18)

_WHO1_ACTION_KEYS: tuple[str, ...] = (
    CONF_TURN_ON_ACTION,
    CONF_TURN_OFF_ACTION,
    CONF_STEP_UP_ACTION,
    CONF_STEP_DOWN_ACTION,
)
_WHO2_ACTION_KEYS: tuple[str, ...] = (
    CONF_TURN_ON_ACTION,
    CONF_TURN_OFF_ACTION,
)

_COMMON_CARRIED_KEYS: tuple[str, ...] = (
    CONF_ENTITY_ID,
    CONF_ATTRIBUTE,
    CONF_COOLDOWN,
    CONF_MIN_DELTA,
    CONF_PERIODIC_SEND,
    CONF_RESPOND_TO_READ,
    CONF_BRIGHTNESS_STEP_PCT,
    CONF_ON_STATES,
    CONF_VALUE_TEMPLATE,
)

_AREA_OPTIONS: list[selector.SelectOptionDict] = [
    selector.SelectOptionDict(value=str(i), label=str(i)) for i in range(11)
]
_POINT_OPTIONS: list[selector.SelectOptionDict] = [
    selector.SelectOptionDict(value=str(i), label=str(i)) for i in range(1, 16)
]
_VALID_BUS_INTERFACES: tuple[str, ...] = (
    *(f"{i:02d}" for i in range(1, 10)),
    *(f"{i:02d}" for i in range(11, 16)),
)
_INTERFACE_OPTIONS: list[str] = [_IFACE_NONE, *_VALID_BUS_INTERFACES]
_WHO13_DIM_OPTIONS: list[str] = ["22", "0", "1"]
_WHO18_DIM_OPTIONS: list[str] = ["113", "51", "52", "53", "54"]


def _action_keys_for_who(who: int | str) -> tuple[str, ...]:
    """Return the custom action keys supported by ``who``."""
    who_str = str(who)
    if who_str == "1":
        return _WHO1_ACTION_KEYS
    if who_str == "2":
        return _WHO2_ACTION_KEYS
    return ()


def _opt_float(defaults: typing.Mapping[str, typing.Any], key: str) -> float | None:
    """Return a finite float from ``defaults[key]`` or ``None``."""
    val = defaults.get(key)
    if val is None or val == "":
        return None
    try:
        return validate._finite_float(val)
    except vol.Invalid:
        return None


class ExposeSubentryFlowHandler(ConfigSubentryFlow):
    """Handle 2-step ConfigSubentryFlow for exposing HA entities to the SCS bus."""

    def __init__(self) -> None:
        """Initialize the expose subentry flow handler."""
        super().__init__()
        self._selected_who: int | None = None

    def _gateway_profile_for_who(self, who: int) -> typing.Any:
        """Return the parent gateway's resolved profile for ``who``, or None if unavailable."""
        entry = self._get_entry()
        runtime = get_runtime_data(entry)
        gw = getattr(runtime, "gateway", None) if runtime is not None else None
        if gw is not None:
            return effective_expose_profile(gw, who)
        return None

    def _build_who_schema(
        self,
        defaults: typing.Mapping[str, typing.Any],
        *,
        include_who: str | None = None,
    ) -> typing.Any:
        """Build Step 1 schema selecting the OpenWebNet WHO subsystem."""
        who_default = str(defaults.get(CONF_WHO, include_who or "1"))
        who_options: list[str] = [
            str(code)
            for code in _WHO_CODES
            if str(code) == include_who
            or (
                (prof := self._gateway_profile_for_who(code)) is None
                or injection_block_reason(prof, code) is None
            )
        ]
        if who_default not in who_options and who_options:
            who_default = who_options[0]

        return flow_schema(
            {
                Required(CONF_WHO, default=who_default): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=who_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key=_SELECTOR_WHO,
                    )
                )
            }
        )

    def _validate_who_step(
        self, user_input: typing.Mapping[str, typing.Any]
    ) -> tuple[int | None, dict[str, str]]:
        """Validate Step 1 WHO selection against supported subsystems and gateway profile."""
        try:
            who = validate._non_bool_int(user_input.get(CONF_WHO, 1))
        except vol.Invalid:
            return None, {CONF_WHO: "invalid_who"}
        if who not in _WHO_STEP_ID:
            return None, {CONF_WHO: "invalid_who"}

        profile = self._gateway_profile_for_who(who)
        if profile is not None and injection_block_reason(profile, who) is not None:
            return None, {CONF_WHO: "who_injection_unsupported"}
        return who, {}

    def _initial_step2_defaults(
        self,
        who: int,
        subentry_data: typing.Mapping[str, typing.Any] | None,
    ) -> dict[str, typing.Any]:
        """Compute Step 2 form defaults when reconfiguring an existing expose subentry."""
        if subentry_data is None:
            return {}
        defaults: dict[str, typing.Any] = {}
        try:
            old_who: int | None = validate._non_bool_int(subentry_data.get(CONF_WHO))
        except vol.Invalid:
            old_who = None

        for key in (*_COMMON_CARRIED_KEYS, *_action_keys_for_who(who)):
            if key in subentry_data:
                defaults[key] = subentry_data[key]
        if old_who in (1, 2) and CONF_BIDIRECTIONAL in subentry_data:
            defaults[CONF_BIDIRECTIONAL] = subentry_data[CONF_BIDIRECTIONAL]

        if who in (1, 2):
            if old_who in (1, 2):
                existing_where = str(subentry_data.get(CONF_WHERE) or "").strip()
                parsed = parse_apl_where(existing_where)
                if parsed is not None:
                    area_val, point_val, iface_val = parsed
                    defaults[CONF_EXPOSE_AREA] = str(area_val)
                    defaults[CONF_EXPOSE_POINT] = str(point_val)
                    defaults[CONF_BUS_INTERFACE] = (
                        iface_val if iface_val is not None else _IFACE_NONE
                    )
                    defaults[CONF_WHERE] = ""
                elif existing_where:
                    defaults[CONF_WHERE] = existing_where
        elif who in (4, 18):
            if old_who == who:
                if CONF_WHERE in subentry_data:
                    defaults[CONF_WHERE] = subentry_data[CONF_WHERE]
                if CONF_DIMENSION in subentry_data:
                    defaults[CONF_DIMENSION] = subentry_data[CONF_DIMENSION]
        elif who == 13 and old_who == 13 and CONF_DIMENSION in subentry_data:
            defaults[CONF_DIMENSION] = subentry_data[CONF_DIMENSION]
        return defaults

    def _build_details_schema(
        self,
        who: int,
        defaults: typing.Mapping[str, typing.Any],
    ) -> typing.Any:
        """Build tailored Step 2 schema for the selected ``who``."""
        where_default = str(defaults.get(CONF_WHERE, "") or "")
        entity_default = str(defaults.get(CONF_ENTITY_ID, "") or "")
        attr_default = str(defaults.get(CONF_ATTRIBUTE, "") or "")
        tpl_default = str(defaults.get(CONF_VALUE_TEMPLATE, "") or "")
        cooldown_default = _opt_float(defaults, CONF_COOLDOWN)
        min_delta_default = _opt_float(defaults, CONF_MIN_DELTA)
        periodic_default = _opt_float(defaults, CONF_PERIODIC_SEND)

        schema_dict: dict[typing.Any, typing.Any] = {}

        if who in (1, 2):
            raw_area = defaults.get(CONF_EXPOSE_AREA)
            area_default = (
                str(raw_area)
                if raw_area is not None
                and not isinstance(raw_area, bool)
                and str(raw_area) in {str(i) for i in range(11)}
                else None
            )
            raw_point = defaults.get(CONF_EXPOSE_POINT)
            point_default = (
                str(raw_point)
                if raw_point is not None
                and not isinstance(raw_point, bool)
                and str(raw_point) in {str(i) for i in range(1, 16)}
                else None
            )
            raw_iface = defaults.get(CONF_BUS_INTERFACE)
            iface_default = (
                str(raw_iface)
                if raw_iface is not None
                and not isinstance(raw_iface, bool)
                and str(raw_iface) in _INTERFACE_OPTIONS
                else _IFACE_NONE
            )

            schema_dict[
                vol.Optional(
                    CONF_EXPOSE_AREA,
                    description=(
                        {"suggested_value": area_default}
                        if area_default is not None
                        else None
                    ),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_AREA_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_EXPOSE_POINT,
                    description=(
                        {"suggested_value": point_default}
                        if point_default is not None
                        else None
                    ),
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_POINT_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_BUS_INTERFACE,
                    description={"suggested_value": iface_default},
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_INTERFACE_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=_SELECTOR_BUS_INTERFACE,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_WHERE,
                    description={"suggested_value": where_default},
                )
            ] = selector.TextSelector()
        elif who in (4, 18):
            schema_dict[
                vol.Optional(
                    CONF_WHERE,
                    description={"suggested_value": where_default},
                )
            ] = selector.TextSelector()

        if who == 18:
            dim_raw_18 = defaults.get(CONF_DIMENSION)
            dim_default_18 = (
                str(dim_raw_18)
                if dim_raw_18 is not None and str(dim_raw_18) in _WHO18_DIM_OPTIONS
                else "113"
            )
            schema_dict[
                Required(CONF_DIMENSION, default=dim_default_18)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_WHO18_DIM_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=_SELECTOR_WHO18_DIMENSION,
                )
            )

        if who == 13:
            dim_raw_13 = defaults.get(CONF_DIMENSION)
            dim_default_13 = (
                str(dim_raw_13)
                if dim_raw_13 is not None and str(dim_raw_13) in _WHO13_DIM_OPTIONS
                else "22"
            )
            schema_dict[
                Required(CONF_DIMENSION, default=dim_default_13)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_WHO13_DIM_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key=_SELECTOR_WHO13_DIMENSION,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_PERIODIC_SEND,
                    description=(
                        {"suggested_value": periodic_default}
                        if periodic_default is not None
                        else None
                    ),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0,
                    max=604800.0,
                    step=1.0,
                    unit_of_measurement="s",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            schema_dict[
                Required(
                    CONF_RESPOND_TO_READ,
                    default=bool(defaults.get(CONF_RESPOND_TO_READ, True)),
                )
            ] = selector.BooleanSelector()
            return flow_schema(schema_dict)

        schema_dict[
            vol.Optional(
                CONF_ENTITY_ID,
                description=(
                    {"suggested_value": entity_default} if entity_default else None
                ),
            )
        ] = selector.EntitySelector()
        schema_dict[
            vol.Optional(
                CONF_ATTRIBUTE,
                description={"suggested_value": attr_default},
            )
        ] = selector.TextSelector()
        schema_dict[
            vol.Optional(
                CONF_COOLDOWN,
                default=cooldown_default if cooldown_default is not None else 1.0,
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=3600.0,
                step=0.5,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        )

        if who in (4, 18):
            schema_dict[
                vol.Optional(
                    CONF_MIN_DELTA,
                    description=(
                        {"suggested_value": min_delta_default}
                        if min_delta_default is not None
                        else None
                    ),
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0,
                    max=10000.0,
                    step=0.1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        schema_dict[
            vol.Optional(
                CONF_PERIODIC_SEND,
                description=(
                    {"suggested_value": periodic_default}
                    if periodic_default is not None
                    else None
                ),
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.0,
                max=604800.0,
                step=1.0,
                unit_of_measurement="s",
                mode=selector.NumberSelectorMode.BOX,
            )
        )
        schema_dict[
            Required(
                CONF_RESPOND_TO_READ,
                default=bool(defaults.get(CONF_RESPOND_TO_READ, True)),
            )
        ] = selector.BooleanSelector()

        if who in (1, 2):
            schema_dict[
                Required(
                    CONF_BIDIRECTIONAL,
                    default=bool(defaults.get(CONF_BIDIRECTIONAL, True)),
                )
            ] = selector.BooleanSelector()

        if who == 1:
            try:
                step_default = validate._non_bool_int(
                    defaults.get(CONF_BRIGHTNESS_STEP_PCT, 10)
                )
            except vol.Invalid:
                step_default = 10
            on_states_raw = defaults.get(CONF_ON_STATES)
            if isinstance(on_states_raw, (list, tuple)):
                on_states_default = ", ".join(str(x) for x in on_states_raw)
            else:
                on_states_default = str(on_states_raw or "")

            schema_dict[
                vol.Optional(CONF_BRIGHTNESS_STEP_PCT, default=step_default)
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=100,
                    step=1,
                    unit_of_measurement="%",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )
            schema_dict[
                vol.Optional(
                    CONF_ON_STATES,
                    description={"suggested_value": on_states_default},
                )
            ] = selector.TextSelector()

        schema_dict[
            vol.Optional(
                CONF_VALUE_TEMPLATE,
                description={"suggested_value": tpl_default},
            )
        ] = selector.TextSelector()

        for action_key in _action_keys_for_who(who):
            action_default = defaults.get(action_key)
            schema_dict[
                vol.Optional(
                    action_key,
                    description=(
                        {"suggested_value": action_default}
                        if action_default
                        else None
                    ),
                )
            ] = selector.ActionSelector()

        return flow_schema(schema_dict)

    def _validate_and_clean(
        self,
        who: int,
        user_input: dict[str, typing.Any],
        *,
        exclude_subentry_id: str | None = None,
    ) -> tuple[dict[str, typing.Any], dict[str, str], dict[str, str], str]:
        """Validate Step 2 user_input and return (cleaned_data, errors, placeholders, title)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if who not in _WHO_STEP_ID:
            return {}, {"base": "invalid_expose"}, {}, ""

        profile = self._gateway_profile_for_who(who)
        if profile is not None and injection_block_reason(profile, who) is not None:
            return {}, {"base": "who_injection_unsupported"}, {}, ""

        allowed_actions = _action_keys_for_who(who)
        if any(
            k not in allowed_actions and user_input.get(k) not in (None, "", [], {})
            for k in _WHO1_ACTION_KEYS
        ):
            errors["base"] = "action_not_supported_for_who"
        has_actions = any(
            user_input.get(k) not in (None, "", [], {}) for k in allowed_actions
        )

        dim_val = user_input.get(CONF_DIMENSION)
        dim: int | None = None
        if who in (1, 2):
            if dim_val not in (None, ""):
                errors[CONF_DIMENSION] = "invalid_dimension"
        elif who == 4:
            if dim_val in (None, ""):
                dim = 0
            elif isinstance(dim_val, bool):
                errors[CONF_DIMENSION] = "invalid_dimension"
            else:
                raw_dim = str(dim_val).strip()
                if not (raw_dim.isascii() and raw_dim.isdigit()) or int(raw_dim) != 0:
                    errors[CONF_DIMENSION] = "invalid_dimension"
                else:
                    dim = 0
        elif who == 13:
            if dim_val in (None, ""):
                dim = 22
            elif isinstance(dim_val, bool):
                errors[CONF_DIMENSION] = "invalid_dimension"
            else:
                raw_dim = str(dim_val).strip()
                if not (raw_dim.isascii() and raw_dim.isdigit()) or int(raw_dim) not in (0, 1, 22):
                    errors[CONF_DIMENSION] = "invalid_dimension"
                else:
                    dim = int(raw_dim)
        elif who == 18:
            if dim_val in (None, ""):
                dim = 113
            elif isinstance(dim_val, bool):
                errors[CONF_DIMENSION] = "invalid_dimension"
            else:
                raw_dim = str(dim_val).strip()
                if not (raw_dim.isascii() and raw_dim.isdigit()) or int(raw_dim) not in (51, 52, 53, 54, 113):
                    errors[CONF_DIMENSION] = "invalid_dimension"
                else:
                    dim = int(raw_dim)

        raw_where = user_input.get(CONF_WHERE)
        raw_where_str = (
            ""
            if raw_where is None or isinstance(raw_where, bool)
            else str(raw_where).strip()
        )
        where = ""

        if who == 13:
            where = ""
        elif who in (1, 2):
            if raw_where_str:
                diag = diagnose_apl_where(raw_where_str)
                if diag is not None:
                    err_key, err_placeholders = diag
                    errors[CONF_WHERE] = err_key
                    placeholders.update(err_placeholders)
                else:
                    where = normalize_expose_where(who, raw_where_str)
            else:
                raw_area = user_input.get(CONF_EXPOSE_AREA)
                raw_point = user_input.get(CONF_EXPOSE_POINT)
                raw_iface = user_input.get(CONF_BUS_INTERFACE)

                area_int: int | None = None
                if raw_area in (None, ""):
                    errors[CONF_EXPOSE_AREA] = "area_required"
                else:
                    try:
                        area_cand = validate._non_bool_int(raw_area)
                        if not (0 <= area_cand <= 10):
                            errors[CONF_EXPOSE_AREA] = "invalid_area"
                        else:
                            area_int = area_cand
                    except vol.Invalid:
                        errors[CONF_EXPOSE_AREA] = "invalid_area"

                point_int: int | None = None
                if raw_point in (None, ""):
                    errors[CONF_EXPOSE_POINT] = "point_required"
                else:
                    try:
                        point_cand = validate._non_bool_int(raw_point)
                        if not (1 <= point_cand <= 15):
                            errors[CONF_EXPOSE_POINT] = "invalid_point"
                        else:
                            point_int = point_cand
                    except vol.Invalid:
                        errors[CONF_EXPOSE_POINT] = "invalid_point"

                iface_clean: str | None = None
                if raw_iface not in (None, "", _IFACE_NONE):
                    if isinstance(raw_iface, bool):
                        errors[CONF_BUS_INTERFACE] = "invalid_bus_interface"
                    else:
                        iface_str = str(raw_iface).strip()
                        if not (
                            iface_str.isascii()
                            and iface_str.isdigit()
                            and 1 <= len(iface_str) <= 2
                        ):
                            errors[CONF_BUS_INTERFACE] = "invalid_bus_interface"
                        else:
                            iface_norm = f"{int(iface_str):02d}"
                            if iface_norm not in _VALID_BUS_INTERFACES:
                                errors[CONF_BUS_INTERFACE] = "invalid_bus_interface"
                            else:
                                iface_clean = iface_norm

                if (
                    area_int is not None
                    and point_int is not None
                    and CONF_BUS_INTERFACE not in errors
                ):
                    where = encode_apl_where(area_int, point_int, iface_clean)
        elif who == 4:
            if not raw_where_str:
                errors[CONF_WHERE] = "where_required"
            else:
                cand = normalize_expose_where(4, raw_where_str)
                if "*" in cand or not validate._WHO4_EXPOSE_PROBE_RE.fullmatch(cand):
                    errors[CONF_WHERE] = "invalid_where_who4"
                else:
                    where = cand
        elif who == 18:
            if not raw_where_str:
                errors[CONF_WHERE] = "where_required"
            else:
                cand = normalize_expose_where(18, raw_where_str)
                if "*" in cand or not validate._WHO18_EXPOSE_WHERE_RE.fullmatch(cand):
                    errors[CONF_WHERE] = "invalid_where_who18"
                else:
                    where = cand

        raw_entity = user_input.get(CONF_ENTITY_ID)
        entity_id = (
            ""
            if raw_entity is None or isinstance(raw_entity, bool)
            else str(raw_entity).strip()
        )
        if who in (4, 18) and not entity_id:
            errors[CONF_ENTITY_ID] = "entity_required"
        elif who in (1, 2) and not entity_id and not has_actions:
            errors[CONF_ENTITY_ID] = "entity_required"

        if errors:
            return {}, errors, placeholders, ""

        cleaned: dict[str, typing.Any] = {
            CONF_WHO: who,
            CONF_COOLDOWN: user_input.get(CONF_COOLDOWN, 1.0),
            CONF_RESPOND_TO_READ: bool(user_input.get(CONF_RESPOND_TO_READ, True)),
            CONF_BIDIRECTIONAL: (
                bool(user_input.get(CONF_BIDIRECTIONAL, True))
                if who in (1, 2)
                else False
            ),
            CONF_BRIGHTNESS_STEP_PCT: (
                user_input.get(CONF_BRIGHTNESS_STEP_PCT, 10) if who == 1 else 10
            ),
        }
        if where:
            cleaned[CONF_WHERE] = where
        if entity_id and who != 13:
            cleaned[CONF_ENTITY_ID] = entity_id
        if who != 13:
            attr = str(user_input.get(CONF_ATTRIBUTE) or "").strip()
            if attr:
                cleaned[CONF_ATTRIBUTE] = attr
            tpl = str(user_input.get(CONF_VALUE_TEMPLATE) or "").strip()
            if tpl:
                cleaned[CONF_VALUE_TEMPLATE] = tpl
        if dim is not None:
            cleaned[CONF_DIMENSION] = dim
        if who in (4, 18):
            raw_min_delta = user_input.get(CONF_MIN_DELTA)
            if raw_min_delta is not None and str(raw_min_delta).strip() != "":
                cleaned[CONF_MIN_DELTA] = raw_min_delta
        raw_periodic = user_input.get(CONF_PERIODIC_SEND)
        if raw_periodic is not None and str(raw_periodic).strip() != "":
            cleaned[CONF_PERIODIC_SEND] = raw_periodic
        if who == 1:
            on_states = str(user_input.get(CONF_ON_STATES) or "").strip()
            if on_states:
                cleaned[CONF_ON_STATES] = [
                    s.strip() for s in on_states.split(",") if s.strip()
                ]
        raw_actions: dict[str, list[dict[str, typing.Any]]] = {}
        for action_key in allowed_actions:
            raw_action = user_input.get(action_key)
            if raw_action not in (None, "", [], {}):
                cleaned[action_key] = raw_action
                plain_action = validate._stringify_templates(copy.deepcopy(raw_action))
                if isinstance(plain_action, dict):
                    raw_actions[action_key] = [plain_action]
                elif isinstance(plain_action, list):
                    raw_actions[action_key] = plain_action

        try:
            cleaned = validate.expose_entry_schema(dict(cleaned))
        except vol.Invalid:
            errors["base"] = "invalid_expose"
            return {}, errors, {}, ""

        for action_key, raw_seq in raw_actions.items():
            cleaned[action_key] = raw_seq

        norm_where = str(cleaned.get(CONF_WHERE) or "")
        if (
            profile is not None
            and injection_block_reason(profile, who, norm_where) is not None
        ):
            errors[CONF_WHERE] = "who4_probe_where_unsupported"
            return {}, errors, {}, ""

        entry = self._get_entry()
        candidate_dim = cleaned.get(CONF_DIMENSION)
        if candidate_dim is None:
            candidate_dim = 0 if who == 4 else (22 if who == 13 else None)
        candidate_key = (int(cleaned[CONF_WHO]), norm_where, candidate_dim)

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
                errors["base"] = "already_configured"
                return {}, errors, {}, ""

        subentries = getattr(entry, "subentries", None) or {}
        for sub_id, subentry in subentries.items():
            if exclude_subentry_id and str(sub_id) == exclude_subentry_id:
                continue
            if getattr(subentry, "subentry_type", None) != SUBENTRY_TYPE_EXPOSE:
                continue
            sdata = getattr(subentry, "data", None) or {}
            s_who_raw = sdata.get(CONF_WHO)
            if s_who_raw is None or isinstance(s_who_raw, bool):
                continue
            try:
                s_who = validate._non_bool_int(s_who_raw)
            except vol.Invalid:
                continue
            s_where = normalize_expose_where(s_who, sdata.get(CONF_WHERE))
            s_raw_dim = sdata.get(CONF_DIMENSION)
            if s_raw_dim is not None and (
                isinstance(s_raw_dim, bool) or str(s_raw_dim).strip() != ""
            ):
                try:
                    s_dim: int | None = validate._non_bool_int(s_raw_dim)
                except vol.Invalid:
                    continue
            elif s_who == 4:
                s_dim = 0
            elif s_who == 13:
                s_dim = 22
            elif s_who == 18:
                s_dim = 113
            else:
                s_dim = None
            if (s_who, s_where, s_dim) == candidate_key:
                errors["base"] = "already_configured"
                return {}, errors, {}, ""

        if who == 13:
            title = f"Bus Clock Sync (WHO 13, dim {dim if dim is not None else 22})"
        else:
            target_label = entity_id or "custom actions"
            title = f"WHO {who} / WHERE {norm_where} ({target_label})"
        return cleaned, errors, placeholders, title

    async def _async_step_for_who(self, who: int) -> SubentryFlowResult:
        """Route Step 1 to the matching Step 2 handler for ``who``."""
        if who == 1:
            return await self.async_step_who_1()
        if who == 2:
            return await self.async_step_who_2()
        if who == 4:
            return await self.async_step_who_4()
        if who == 13:
            return await self.async_step_who_13()
        return await self.async_step_who_18()

    async def _async_handle_who_step(
        self,
        *,
        who: int,
        step_id: str,
        user_input: dict[str, typing.Any] | None = None,
    ) -> SubentryFlowResult:
        """Render or submit a tailored Step 2 expose form for ``who``."""
        self._selected_who = who
        is_reconfigure = self.source == SOURCE_RECONFIGURE
        subentry: ConfigSubentry | None = (
            self._get_reconfigure_subentry() if is_reconfigure else None
        )
        exclude_id = subentry.subentry_id if subentry is not None else None

        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            cleaned, errors, placeholders, title = self._validate_and_clean(
                who,
                user_input,
                exclude_subentry_id=exclude_id,
            )
            if not errors:
                entry = self._get_entry()
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
                if is_reconfigure and subentry is not None:
                    return self.async_update_and_abort(
                        entry, subentry, title=title, data=cleaned
                    )
                return self.async_create_entry(title=title, data=cleaned)
            defaults: dict[str, typing.Any] = dict(user_input)
        else:
            defaults = self._initial_step2_defaults(
                who, subentry.data if subentry is not None else None
            )

        return self.async_show_form(
            step_id=step_id,
            data_schema=self._build_details_schema(who, defaults),
            errors=errors,
            description_placeholders=placeholders or None,
            last_step=True,
        )

    async def async_step_user(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (add): choose the OpenWebNet WHO subsystem to expose."""
        errors: dict[str, str] = {}
        if user_input is not None:
            who, errors = self._validate_who_step(user_input)
            if not errors and who is not None:
                self._selected_who = who
                return await self._async_step_for_who(who)

        return self.async_show_form(
            step_id="user",
            data_schema=self._build_who_schema(user_input or {}),
            errors=errors,
            last_step=False,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 1 (reconfigure): confirm or switch the OpenWebNet WHO subsystem."""
        errors: dict[str, str] = {}
        subentry = self._get_reconfigure_subentry()
        current_who = subentry.data.get(CONF_WHO, 1)
        if user_input is not None:
            who, errors = self._validate_who_step(user_input)
            if not errors and who is not None:
                self._selected_who = who
                return await self._async_step_for_who(who)

        defaults = {
            CONF_WHO: (
                user_input.get(CONF_WHO, current_who)
                if user_input is not None
                else current_who
            )
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._build_who_schema(
                defaults, include_who=str(current_who)
            ),
            errors=errors,
            last_step=False,
        )

    async def async_step_who_1(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for WHO 1 — Lighting / Switch / Virtual Actuator."""
        return await self._async_handle_who_step(
            who=1, step_id="who_1", user_input=user_input
        )

    async def async_step_who_2(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for WHO 2 — Automation / Cover."""
        return await self._async_handle_who_step(
            who=2, step_id="who_2", user_input=user_input
        )

    async def async_step_who_4(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for WHO 4 — Temperature Probe (Dimension 0)."""
        return await self._async_handle_who_step(
            who=4, step_id="who_4", user_input=user_input
        )

    async def async_step_who_13(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for WHO 13 — Bus Clock & DST Sync."""
        return await self._async_handle_who_step(
            who=13, step_id="who_13", user_input=user_input
        )

    async def async_step_who_18(
        self, user_input: dict[str, typing.Any] | None = None
    ) -> SubentryFlowResult:
        """Step 2 for WHO 18 — Energy / Power Meter."""
        return await self._async_handle_who_step(
            who=18, step_id="who_18", user_input=user_input
        )
