#!/usr/bin/env python3
"""Automated Home Assistant Architectural Standards Validator.

This script enforces Home Assistant Integration Quality Scale and Core
architectural standards across custom_components/myhome/ and tests/:
1. Discovery flows (SSDP, Zeroconf, DHCP, USB, etc.) MUST NOT create config
   entries automatically; they must present a confirmation step (e.g. discovery_confirm).
2. Discovery flows must enforce unique ID registration and abort on duplicates.
3. Every step_id used in async_show_form in ConfigFlow and OptionsFlow must have
   complete translations in translations/en.json.
4. No synchronous blocking calls (e.g. time.sleep, requests.*) in async coroutines.
5. No unhandled top-level imports of deprecated homeassistant.const symbols.
6. No deprecated DeviceInfo / ChildDeviceInfo mapping access (_DeviceInfoMapping,
   HA 2026.12+ core#192323) across custom_components/myhome/ and tests/.
"""

import ast
import json
import os
import py_compile
import re
import struct
import sys
import tomllib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_DIR = ROOT_DIR / "custom_components" / "myhome"
TESTS_DIR = ROOT_DIR / "tests"
TYPING_COMPAT_FILE = CUSTOM_COMPONENTS_DIR / "typing_compat.py"

# Modules that read per-config-entry state; audited by the runtime-data check.
RUNTIME_DATA_READERS = [
    "light.py", "switch.py", "cover.py", "climate.py", "binary_sensor.py", "sensor.py",
    "media_player.py", "button.py", "number.py", "alarm_control_panel.py", "lock.py",
    "services.py", "websocket.py", "diagnostics.py", "myhome_device.py", "decoder_pool.py",
    "decoder_allocation.py", "cover_calibration.py", "climate_discovery.py",
]
TRANSLATIONS_DIR = CUSTOM_COMPONENTS_DIR / "translations"

DISCOVERY_STEPS = {
    "async_step_ssdp",
    "async_step_zeroconf",
    "async_step_dhcp",
    "async_step_usb",
    "async_step_bluetooth",
    "async_step_homekit",
    "async_step_mqtt",
}

DEPRECATED_HA_CONSTANTS = {
    "STATE_ALARM_DISARMED",
    "STATE_ALARM_ARMED_HOME",
    "STATE_ALARM_ARMED_AWAY",
    "STATE_ALARM_ARMED_NIGHT",
    "STATE_ALARM_ARMED_VACATION",
    "STATE_ALARM_ARMED_CUSTOM_BYPASS",
    "STATE_ALARM_PENDING",
    "STATE_ALARM_ARMING",
    "STATE_ALARM_DISARMING",
    "STATE_ALARM_TRIGGERED",
    "TEMP_CELSIUS",
    "TEMP_FAHRENHEIT",
    "PRESSURE_BAR",
    "PRESSURE_HPA",
    "PRESSURE_PSI",
    "SPEED_METERS_PER_SECOND",
    "SPEED_KILOMETERS_PER_HOUR",
    "SPEED_MILES_PER_HOUR",
}


class StandardsChecker:
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def log_error(self, rule: str, file_path: Path, line: int, message: str):
        try:
            rel = file_path.relative_to(ROOT_DIR)
        except ValueError:
            rel = file_path
        msg = f"[{rule}] {rel}:{line}: {message}"
        self.errors.append(msg)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error file={rel},line={line},title={rule}::{message}")
        else:
            print(f"\033[91mFAIL\033[0m: {msg}")

    def log_ok(self, message: str):
        if not os.getenv("GITHUB_ACTIONS"):
            print(f"\033[92mPASS\033[0m: {message}")
        else:
            print(f"PASS: {message}")


def check_discovery_flows(checker: StandardsChecker):
    """Rule 1 & 2: Enforce discovery flows confirmation and unique ID verification."""
    config_flow_file = CUSTOM_COMPONENTS_DIR / "config_flow.py"
    if not config_flow_file.exists():
        checker.log_error("RULE_DISCOVERY", config_flow_file, 1, "config_flow.py not found")
        return

    for flow_file in sorted(CUSTOM_COMPONENTS_DIR.glob("config_flow*.py")):
        with open(flow_file, "r", encoding="utf-8") as f:
            content = f.read()

        tree = ast.parse(content, filename=str(flow_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name in DISCOVERY_STEPS:
                step_name = node.name

                # Check 1: Must not call async_create_entry directly
                has_create_entry = False
                has_abort_unique_id = False
                delegates_to_confirm_or_form = False

                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        attr = child.func.attr
                        if attr == "async_create_entry":
                            has_create_entry = True
                        elif attr in ("_abort_if_unique_id_configured", "async_set_unique_id"):
                            has_abort_unique_id = True
                        elif attr in ("async_step_discovery_confirm", "async_show_form"):
                            delegates_to_confirm_or_form = True

                    # Check if it calls test_connection directly without user confirmation
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        if child.func.attr == "async_step_test_connection":
                            checker.log_error(
                                "RULE_DISCOVERY_NO_AUTO_CREATE",
                                flow_file,
                                child.lineno,
                                f"{step_name} must NOT directly call async_step_test_connection. "
                                f"It must route to async_step_discovery_confirm to allow user [Configure]/[Ignore].",
                            )

                if has_create_entry:
                    checker.log_error(
                        "RULE_DISCOVERY_NO_AUTO_CREATE",
                        flow_file,
                        node.lineno,
                        f"{step_name} directly invokes async_create_entry! "
                        f"Home Assistant architectural rules require discovery flows to show confirmation.",
                    )

                if not has_abort_unique_id:
                    checker.log_error(
                        "RULE_DISCOVERY_UNIQUE_ID",
                        flow_file,
                        node.lineno,
                        f"{step_name} does not verify unique ID or call _abort_if_unique_id_configured.",
                    )

                if not delegates_to_confirm_or_form:
                    checker.log_error(
                        "RULE_DISCOVERY_CONFIRMATION",
                        flow_file,
                        node.lineno,
                        f"{step_name} does not delegate to async_step_discovery_confirm or async_show_form.",
                    )

    checker.log_ok("Discovery flows require user confirmation and unique ID verification.")


def check_translation_coverage(checker: StandardsChecker):
    """Rule 3: Verify all config flow step_ids have matching translations."""
    config_flow_file = CUSTOM_COMPONENTS_DIR / "config_flow.py"
    en_json_file = TRANSLATIONS_DIR / "en.json"

    if not config_flow_file.exists() or not en_json_file.exists():
        return

    with open(en_json_file, "r", encoding="utf-8") as f:
        en_data = json.load(f)

    config_translated = set(en_data.get("config", {}).get("step", {}).keys())
    options_translated = set(en_data.get("options", {}).get("step", {}).keys())
    subentry_translated = {
        step_id
        for sub_cfg in en_data.get("config_subentries", {}).values()
        if isinstance(sub_cfg, dict)
        for step_id in sub_cfg.get("step", {}).keys()
    }
    all_translated = config_translated | options_translated | subentry_translated

    for flow_file in sorted(CUSTOM_COMPONENTS_DIR.glob("config_flow*.py")):
        with open(flow_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(flow_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "async_show_form":
                    for kw in node.keywords:
                        if kw.arg == "step_id" and isinstance(kw.value, ast.Constant):
                            step_id = kw.value.value
                            if step_id not in all_translated:
                                checker.log_error(
                                    "RULE_TRANSLATION_STEP_MISSING",
                                    flow_file,
                                    node.lineno,
                                    f"async_show_form(step_id='{step_id}') has no translation in translations/en.json.",
                                )

    checker.log_ok("Config flow step translations are complete in en.json.")


def check_deprecated_constants(checker: StandardsChecker):
    """Rule 4: Verify no top-level unhandled imports of deprecated HA constants."""
    for root, _, files in os.walk(CUSTOM_COMPONENTS_DIR):
        for file in files:
            if not file.endswith(".py"):
                continue
            py_path = Path(root) / file
            with open(py_path, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=str(py_path))
                except SyntaxError:
                    continue

            # Inspect top-level statements (body of Module)
            for stmt in tree.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module == "homeassistant.const":
                    for alias in stmt.names:
                        if alias.name in DEPRECATED_HA_CONSTANTS:
                            checker.log_error(
                                "RULE_DEPRECATED_CONSTANTS",
                                py_path,
                                stmt.lineno,
                                f"Direct top-level import of deprecated constant '{alias.name}' from homeassistant.const. "
                                f"Use the modern component enum or wrap in try/except ImportError fallback.",
                            )

    checker.log_ok("No unhandled top-level imports of deprecated homeassistant.const symbols.")


def check_future_annotations_and_syntax(checker: StandardsChecker, target_dir: Path | None = None):
    """Rule: Verify all Python files compile cleanly and 'from __future__ import annotations' is the first statement."""
    base_dir = target_dir or CUSTOM_COMPONENTS_DIR
    checked_files = 0

    for root, _, files in os.walk(base_dir):
        for file in files:
            if not file.endswith(".py"):
                continue
            py_path = Path(root) / file
            checked_files += 1

            # 1. Bytecode compilation check
            try:
                py_compile.compile(str(py_path), doraise=True)
            except py_compile.PyCompileError as err:
                checker.log_error(
                    "RULE_PYTHON_SYNTAX",
                    py_path,
                    getattr(err, "lineno", 1) or 1,
                    f"SyntaxError in Python source: {err}",
                )
                continue

            # 2. AST parsing & __future__ position check
            with open(py_path, "r", encoding="utf-8") as f:
                content = f.read()

            try:
                tree = ast.parse(content, filename=str(py_path))
            except SyntaxError as err:
                checker.log_error(
                    "RULE_PYTHON_SYNTAX",
                    py_path,
                    err.lineno or 1,
                    f"SyntaxError parsing AST: {err}",
                )
                continue

            seen_non_future_stmt = False
            for stmt in tree.body:
                # Allow module docstring
                if (
                    isinstance(stmt, ast.Expr)
                    and isinstance(stmt.value, ast.Constant)
                    and isinstance(stmt.value.value, str)
                    and stmt == tree.body[0]
                ):
                    continue

                if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
                    if seen_non_future_stmt:
                        checker.log_error(
                            "RULE_FUTURE_ANNOTATIONS",
                            py_path,
                            stmt.lineno,
                            "from __future__ imports must occur at the beginning of the file (before other statements/imports).",
                        )
                else:
                    seen_non_future_stmt = True

    checker.log_ok(f"All {checked_files} Python source files verified for valid syntax and correct __future__ positioning.")


def check_no_update_listener_reload_conflict(checker: StandardsChecker, target_dir: Path | None = None):
    """Rule 5: A config entry update listener must not coexist with flow reload helpers (#510).

    Home Assistant 2026.12 turns the combination of ``add_update_listener`` and
    reload helpers in config flows into an error (duplicate reloads / races).
    The integration therefore uses no update listener at all, and config flows
    must not call ``async_reload`` directly: use ``async_update_reload_and_abort``,
    ``OptionsFlowWithReload`` or ``async_schedule_reload`` instead.
    """
    base = target_dir or CUSTOM_COMPONENTS_DIR
    for py_path in sorted(base.rglob("*.py")):
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        except SyntaxError:
            continue
        is_flow = py_path.name.startswith("config_flow") and py_path.name.endswith(".py")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr == "add_update_listener":
                checker.log_error(
                    "RULE_NO_UPDATE_LISTENER",
                    py_path,
                    node.lineno,
                    "add_update_listener() conflicts with config-flow reload helpers and breaks in "
                    "Home Assistant 2026.12. Use OptionsFlowWithReload / async_update_reload_and_abort "
                    "and apply changes on the coordinated reload instead (see issue #510).",
                )
            elif is_flow and node.func.attr == "async_reload":
                checker.log_error(
                    "RULE_NO_FLOW_ASYNC_RELOAD",
                    py_path,
                    node.lineno,
                    "Direct async_reload() in a config flow can double-reload. Use "
                    "async_update_reload_and_abort(), OptionsFlowWithReload or async_schedule_reload().",
                )

    checker.log_ok("No update listener / direct flow reload conflicts (HA 2026.12).")


DEPRECATED_DEVICE_INFO_METHODS = frozenset(
    {
        "get",
        "update",
        "keys",
        "items",
        "values",
        "pop",
        "popitem",
        "setdefault",
        "clear",
        "copy",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__contains__",
        "__iter__",
        "__len__",
        "__required_keys__",
        "__optional_keys__",
        "__annotations__",
        "__total__",
    }
)

DEPRECATED_DEVICE_INFO_CLASS_ATTRS = frozenset(
    {
        "__required_keys__",
        "__optional_keys__",
        "__annotations__",
        "__total__",
    }
)


_DEVICE_INFO_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_COMPREHENSION_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_DEVICE_INFO_ITER_BUILTINS = frozenset({"len", "iter", "list", "tuple", "set", "frozenset"})


def _is_device_info_class(expr: ast.AST, class_names: set[str]) -> bool:
    """Return True when ``expr`` names ``DeviceInfo`` or ``ChildDeviceInfo``."""
    return (isinstance(expr, ast.Name) and expr.id in class_names) or (
        isinstance(expr, ast.Attribute) and expr.attr in {"DeviceInfo", "ChildDeviceInfo"}
    )


def _is_self_attr(expr: ast.AST) -> str | None:
    """Return ``attr`` when ``expr`` is ``self.<attr>``, else ``None``."""
    if (
        isinstance(expr, ast.Attribute)
        and isinstance(expr.value, ast.Name)
        and expr.value.id == "self"
    ):
        return expr.attr
    return None


def _unwrap_cast_expr(expr: ast.AST, cast_names: set[str]) -> ast.AST:
    """Unwrap ``typing.cast(T, value)`` calls to their runtime ``value`` operand."""
    while (
        isinstance(expr, ast.Call)
        and len(expr.args) == 2
        and not expr.keywords
        and (
            (isinstance(expr.func, ast.Name) and expr.func.id in cast_names)
            or (isinstance(expr.func, ast.Attribute) and expr.func.attr == "cast")
        )
    ):
        expr = expr.args[1]
    return expr


def _is_getattr_device_info(expr: ast.AST) -> bool:
    """Return True when ``expr`` is ``getattr(obj, 'device_info' | '_attr_device_info', ...)``."""
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        and expr.func.id == "getattr"
        and 2 <= len(expr.args) <= 3
        and not expr.keywords
        and isinstance(expr.args[1], ast.Constant)
        and expr.args[1].value in {"device_info", "_attr_device_info"}
    )


def _is_device_info_type_annotation(ann: ast.AST | None, class_names: set[str]) -> bool:
    """Return True when ``ann`` annotates a ``DeviceInfo`` / ``ChildDeviceInfo`` value."""
    if ann is None:
        return False
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            parsed = ast.parse(ann.value, mode="eval").body
        except SyntaxError:
            return False
        return _is_device_info_type_annotation(parsed, class_names)
    if _is_device_info_class(ann, class_names):
        return True
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return _is_device_info_type_annotation(
            ann.left, class_names
        ) or _is_device_info_type_annotation(ann.right, class_names)
    if isinstance(ann, ast.Subscript):
        wrapper_name = (
            ann.value.id
            if isinstance(ann.value, ast.Name)
            else ann.value.attr
            if isinstance(ann.value, ast.Attribute)
            else None
        )
        if wrapper_name == "Optional":
            return _is_device_info_type_annotation(ann.slice, class_names)
        if wrapper_name == "Union":
            elts = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            return any(_is_device_info_type_annotation(e, class_names) for e in elts)
        if wrapper_name == "Annotated":
            first = ann.slice.elts[0] if isinstance(ann.slice, ast.Tuple) and ann.slice.elts else ann.slice
            return _is_device_info_type_annotation(first, class_names)
    return False


def _is_device_info_expr(
    expr: ast.AST,
    device_info_vars: set[str],
    self_device_info_attrs: set[str],
    class_names: set[str],
    plain_dict_vars: set[str],
    cast_names: set[str],
) -> bool:
    """Return True when ``expr`` evaluates to a ``DeviceInfo`` / ``ChildDeviceInfo`` value."""
    expr = _unwrap_cast_expr(expr, cast_names)
    if isinstance(expr, ast.Attribute):
        if expr.attr in {"device_info", "_attr_device_info"}:
            return True
        self_attr = _is_self_attr(expr)
        if self_attr is not None and self_attr in self_device_info_attrs:
            return True
    if isinstance(expr, ast.Name) and (
        (expr.id in {"device_info", "_attr_device_info"} and expr.id not in plain_dict_vars)
        or expr.id in device_info_vars
    ):
        return True
    if isinstance(expr, ast.Call) and (
        _is_device_info_class(expr.func, class_names) or _is_getattr_device_info(expr)
    ):
        return True
    if isinstance(expr, ast.BoolOp):
        return any(
            _is_device_info_expr(
                v,
                device_info_vars,
                self_device_info_attrs,
                class_names,
                plain_dict_vars,
                cast_names,
            )
            for v in expr.values
        )
    if isinstance(expr, ast.IfExp):
        return _is_device_info_expr(
            expr.body,
            device_info_vars,
            self_device_info_attrs,
            class_names,
            plain_dict_vars,
            cast_names,
        ) or _is_device_info_expr(
            expr.orelse,
            device_info_vars,
            self_device_info_attrs,
            class_names,
            plain_dict_vars,
            cast_names,
        )
    if isinstance(expr, ast.NamedExpr):
        return _is_device_info_expr(
            expr.value,
            device_info_vars,
            self_device_info_attrs,
            class_names,
            plain_dict_vars,
            cast_names,
        )
    return False


def _is_dict_call(expr: ast.AST, dict_helper_names: set[str]) -> bool:
    """Return True when ``expr`` is ``dict(...)``, ``device_info_to_dict(...)``, or ``.as_dict()``."""
    if not isinstance(expr, ast.Call):
        return False
    if isinstance(expr.func, ast.Name) and (
        expr.func.id == "dict" or expr.func.id in dict_helper_names
    ):
        return True
    if isinstance(expr.func, ast.Attribute):
        if expr.func.attr == "device_info_to_dict":
            return True
        if expr.func.attr == "as_dict" and not expr.args and not expr.keywords:
            return True
        if (
            isinstance(expr.func.value, ast.Name)
            and expr.func.value.id == "builtins"
            and expr.func.attr == "dict"
        ):
            return True
    return False


def _is_dict_expr(
    expr: ast.AST,
    plain_dict_vars: set[str],
    dict_helper_names: set[str],
    cast_names: set[str],
) -> bool:
    """Return True when ``expr`` evaluates to a plain ``dict`` value."""
    expr = _unwrap_cast_expr(expr, cast_names)
    if isinstance(expr, ast.Dict):
        return True
    if _is_dict_call(expr, dict_helper_names):
        return True
    if isinstance(expr, ast.Name) and expr.id in plain_dict_vars:
        return True
    if isinstance(expr, ast.IfExp):
        return _is_dict_expr(
            expr.body, plain_dict_vars, dict_helper_names, cast_names
        ) and _is_dict_expr(expr.orelse, plain_dict_vars, dict_helper_names, cast_names)
    if isinstance(expr, ast.BoolOp):
        return all(
            _is_dict_expr(v, plain_dict_vars, dict_helper_names, cast_names) for v in expr.values
        )
    if isinstance(expr, ast.NamedExpr):
        return _is_dict_expr(expr.value, plain_dict_vars, dict_helper_names, cast_names)
    return False


def _is_device_info_sequence_expr(
    expr: ast.AST,
    device_info_vars: set[str],
    device_info_seq_vars: set[str],
    self_device_info_attrs: set[str],
    class_names: set[str],
    plain_dict_vars: set[str],
    cast_names: set[str],
) -> bool:
    """Return True when ``expr`` is an iterable of ``DeviceInfo`` items."""
    expr = _unwrap_cast_expr(expr, cast_names)
    if isinstance(expr, ast.Name) and expr.id in device_info_seq_vars:
        return True
    if isinstance(expr, (ast.Tuple, ast.List, ast.Set)) and expr.elts:
        return any(
            _is_device_info_expr(
                elt,
                device_info_vars,
                self_device_info_attrs,
                class_names,
                plain_dict_vars,
                cast_names,
            )
            for elt in expr.elts
        )
    if isinstance(expr, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return _is_device_info_expr(
            expr.elt,
            device_info_vars,
            self_device_info_attrs,
            class_names,
            plain_dict_vars,
            cast_names,
        )
    return False


def _iter_assign_pairs(target: ast.AST, value: ast.AST) -> Iterator[tuple[ast.AST, ast.AST]]:
    """Yield ``(target, value)`` pairs, destructuring matching tuple/list assignments."""
    if (
        isinstance(target, (ast.Tuple, ast.List))
        and isinstance(value, (ast.Tuple, ast.List))
        and len(target.elts) == len(value.elts)
        and not any(isinstance(t, ast.Starred) for t in target.elts)
        and not any(isinstance(v, ast.Starred) for v in value.elts)
    ):
        for t_elt, v_elt in zip(target.elts, value.elts):
            yield from _iter_assign_pairs(t_elt, v_elt)
        return
    yield target, value


def _split_device_info_scope(
    stmts: list[ast.stmt],
) -> tuple[list[ast.AST], list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]]:
    """Return AST nodes evaluated directly in ``stmts`` plus immediate child scope definitions."""
    direct_nodes: list[ast.AST] = []
    child_scopes: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef] = []
    stack: list[ast.AST] = list(reversed(stmts))
    while stack:
        cur = stack.pop()
        if isinstance(cur, _DEVICE_INFO_SCOPE_NODES):
            child_scopes.append(cur)
            body_ids = {id(s) for s in cur.body}
            for ch in reversed(list(ast.iter_child_nodes(cur))):
                if id(ch) not in body_ids:
                    stack.append(ch)
            continue
        direct_nodes.append(cur)
        stack.extend(reversed(list(ast.iter_child_nodes(cur))))
    return direct_nodes, child_scopes


def _extract_scope_params(
    scope_node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None,
    class_names: set[str],
) -> tuple[set[str], set[str]]:
    """Return ``(all_param_names, annotated_device_info_params)`` for ``scope_node``."""
    if not isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set(), set()
    args = scope_node.args
    positional_and_kw = (*args.posonlyargs, *args.args, *args.kwonlyargs)
    names = {a.arg for a in positional_and_kw}
    annotated_di = {
        a.arg
        for a in positional_and_kw
        if _is_device_info_type_annotation(a.annotation, class_names)
    }
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names, annotated_di


def _resolve_device_info_scope_bindings(
    direct_nodes: list[ast.AST],
    child_scopes: list[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef],
    param_names: set[str],
    annotated_di_params: set[str],
    inherited_di_vars: set[str],
    inherited_plain_dict_vars: set[str],
    inherited_di_seq_vars: set[str],
    self_di_attrs: set[str],
    class_names: set[str],
    dict_helper_names: set[str],
    cast_names: set[str],
) -> tuple[set[str], set[str], set[str]]:
    """Resolve ``DeviceInfo`` local vars, plain-dict vars, and sequence vars within one scope.

    Updates ``self_di_attrs`` in place when ``self.<attr>`` is assigned a ``DeviceInfo``
    expression so class scopes can share ``self.<attr>`` tracking across methods.
    """
    rebound_outer: set[str] = set()
    assign_pairs: list[tuple[ast.AST, ast.AST]] = []
    for_iter_pairs: list[tuple[ast.AST, ast.AST]] = []
    annotated_di_locals: set[str] = set()
    simple_store_name_ids: set[int] = set()
    type_only_ann_name_ids: set[int] = set()
    non_plain_bound_names: set[str] = {ch.name for ch in child_scopes}

    for node in direct_nodes:
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            rebound_outer.update(node.names)
        elif isinstance(node, ast.Assign):
            for raw_target in node.targets:
                for target, value in _iter_assign_pairs(raw_target, node.value):
                    assign_pairs.append((target, value))
                    if isinstance(target, ast.Name):
                        simple_store_name_ids.add(id(target))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and _is_device_info_type_annotation(
                node.annotation, class_names
            ):
                annotated_di_locals.add(node.target.id)
            if node.value is not None:
                for target, value in _iter_assign_pairs(node.target, node.value):
                    assign_pairs.append((target, value))
                    if isinstance(target, ast.Name):
                        simple_store_name_ids.add(id(target))
            elif isinstance(node.target, ast.Name):
                type_only_ann_name_ids.add(id(node.target))
        elif isinstance(node, ast.NamedExpr):
            assign_pairs.append((node.target, node.value))
            if isinstance(node.target, ast.Name):
                simple_store_name_ids.add(id(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for_iter_pairs.append((node.target, node.iter))
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            non_plain_bound_names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                non_plain_bound_names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    non_plain_bound_names.add(alias.asname or alias.name)

    for node in direct_nodes:
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and id(node) not in simple_store_name_ids
            and id(node) not in type_only_ann_name_ids
        ):
            non_plain_bound_names.add(node.id)

    assigned_values_by_name: dict[str, list[ast.AST]] = {}
    for target, value in assign_pairs:
        if isinstance(target, ast.Name) and target.id not in rebound_outer:
            assigned_values_by_name.setdefault(target.id, []).append(value)

    locally_bound = set(param_names) | (
        (set(assigned_values_by_name) | non_plain_bound_names | annotated_di_locals)
        - rebound_outer
    )

    plain_dict_vars: set[str] = set(inherited_plain_dict_vars - locally_bound)
    candidate_plain_names = {
        name: values
        for name, values in assigned_values_by_name.items()
        if values
        and name not in param_names
        and name not in rebound_outer
        and name not in non_plain_bound_names
        and name not in annotated_di_locals
    }
    changed = True
    while changed:
        changed = False
        for name, values in candidate_plain_names.items():
            if name not in plain_dict_vars and all(
                _is_dict_expr(val, plain_dict_vars, dict_helper_names, cast_names)
                for val in values
            ):
                plain_dict_vars.add(name)
                changed = True

    di_vars: set[str] = set(
        (inherited_di_vars - locally_bound)
        | annotated_di_params
        | (annotated_di_locals - rebound_outer)
    )
    for name in di_vars:
        plain_dict_vars.discard(name)
    di_seq_vars: set[str] = set(inherited_di_seq_vars - locally_bound)

    changed = True
    while changed:
        changed = False
        for target, value in assign_pairs:
            if _is_device_info_expr(
                value, di_vars, self_di_attrs, class_names, plain_dict_vars, cast_names
            ):
                if isinstance(target, ast.Name) and target.id not in rebound_outer:
                    if target.id not in di_vars:
                        di_vars.add(target.id)
                        plain_dict_vars.discard(target.id)
                        changed = True
                else:
                    self_attr = _is_self_attr(target)
                    if self_attr is not None and self_attr not in self_di_attrs:
                        self_di_attrs.add(self_attr)
                        changed = True
            elif (
                isinstance(target, ast.Name)
                and target.id not in rebound_outer
                and target.id not in di_seq_vars
                and _is_device_info_sequence_expr(
                    value,
                    di_vars,
                    di_seq_vars,
                    self_di_attrs,
                    class_names,
                    plain_dict_vars,
                    cast_names,
                )
            ):
                di_seq_vars.add(target.id)
                changed = True

        for target, iter_expr in for_iter_pairs:
            if (
                isinstance(target, ast.Name)
                and target.id not in rebound_outer
                and target.id not in di_vars
                and _is_device_info_sequence_expr(
                    iter_expr,
                    di_vars,
                    di_seq_vars,
                    self_di_attrs,
                    class_names,
                    plain_dict_vars,
                    cast_names,
                )
            ):
                di_vars.add(target.id)
                plain_dict_vars.discard(target.id)
                changed = True

    return di_vars, plain_dict_vars, di_seq_vars


def _resolve_device_info_scan_files(
    target_dir: Path | Sequence[Path] | None,
) -> list[Path]:
    """Return sorted Python files to audit for ``DeviceInfo`` mapping operations."""
    excluded_paths: set[Path] = {TYPING_COMPAT_FILE.resolve()}
    if target_dir is None:
        roots = [d for d in (CUSTOM_COMPONENTS_DIR, TESTS_DIR) if d.exists()]
    elif isinstance(target_dir, Path):
        roots = [target_dir] if target_dir.exists() else []
        excluded_paths.add((target_dir / "typing_compat.py").resolve())
        excluded_paths.add(
            (target_dir / "custom_components" / "myhome" / "typing_compat.py").resolve()
        )
    else:
        roots = [d for d in target_dir if d.exists()]
        for root in roots:
            excluded_paths.add((root / "typing_compat.py").resolve())
            excluded_paths.add(
                (root / "custom_components" / "myhome" / "typing_compat.py").resolve()
            )

    py_files: list[Path] = []
    seen_resolved: set[Path] = set()
    for root in roots:
        for candidate in sorted(root.rglob("*.py")):
            resolved = candidate.resolve()
            if resolved in excluded_paths or resolved in seen_resolved:
                continue
            seen_resolved.add(resolved)
            py_files.append(candidate)
    return py_files


def check_no_deprecated_device_info_mapping(
    checker: StandardsChecker,
    target_dir: Path | Sequence[Path] | None = None,
) -> None:
    """Forbid deprecated mapping operations on ``DeviceInfo`` / ``ChildDeviceInfo`` (#930).

    Home Assistant Core timeline:
    - ``2026.8–2026.10``: ``DeviceInfo`` is a ``TypedDict`` (a plain ``dict`` at runtime).
    - ``2026.11``: ``DeviceInfo`` and ``ChildDeviceInfo`` became ``@dataclass(slots=True)``
      with ``.as_dict()`` and a silent ``_DeviceInfoMapping`` compatibility shim.
    - ``2026.12`` (``home-assistant/core#192323``): ``_DeviceInfoMapping`` emits
      ``frame.report_usage`` deprecations (removal scheduled for ``2027.12.0``).

    Because MyHOME supports Home Assistant 2026.8+, code across both
    ``custom_components/myhome/`` and ``tests/`` (excluding ``typing_compat.py``)
    must pass fields directly to ``DeviceInfo(...)`` at construction time and read
    existing instances via ``typing_compat.device_info_to_dict()``.

    Note: ``.device_info`` and ``._attr_device_info`` attributes always count as
    ``DeviceInfo`` expressions, and bare ``device_info`` / ``_attr_device_info`` names
    count conservatively as ``DeviceInfo`` variables unless locally bound exclusively to
    plain-dict expressions (``{...}``, ``dict(...)``, ``device_info_to_dict(...)``, or
    ``.as_dict()``).
    """
    py_files = _resolve_device_info_scan_files(target_dir)
    errors_before = len(checker.errors)

    for py_path in py_files:
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        except SyntaxError:
            continue

        class_names: set[str] = {"DeviceInfo", "ChildDeviceInfo"}
        dict_helper_names: set[str] = {"device_info_to_dict"}
        cast_names: set[str] = {"cast"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in {"DeviceInfo", "ChildDeviceInfo"}:
                        class_names.add(alias.asname or alias.name)
                    elif alias.name == "device_info_to_dict":
                        dict_helper_names.add(alias.asname or alias.name)
                    elif node.module == "typing" and alias.name == "cast":
                        cast_names.add(alias.asname or alias.name)

        def _audit_scope(
            stmts: list[ast.stmt],
            scope_node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | None,
            inherited_di_vars: set[str],
            inherited_plain_dict_vars: set[str],
            inherited_di_seq_vars: set[str],
            inherited_self_attrs: set[str],
        ) -> None:
            direct_nodes, child_scopes = _split_device_info_scope(stmts)

            if isinstance(scope_node, ast.ClassDef):
                active_self_attrs: set[str] = set()
                method_splits = [
                    (ch, *_split_device_info_scope(ch.body))
                    for ch in child_scopes
                    if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                for _ in range(2):
                    _resolve_device_info_scope_bindings(
                        direct_nodes,
                        child_scopes,
                        set(),
                        set(),
                        inherited_di_vars,
                        inherited_plain_dict_vars,
                        inherited_di_seq_vars,
                        active_self_attrs,
                        class_names,
                        dict_helper_names,
                        cast_names,
                    )
                    for method_node, method_direct, method_children in method_splits:
                        m_params, m_ann_di = _extract_scope_params(method_node, class_names)
                        _resolve_device_info_scope_bindings(
                            method_direct,
                            method_children,
                            m_params,
                            m_ann_di,
                            inherited_di_vars,
                            inherited_plain_dict_vars,
                            inherited_di_seq_vars,
                            active_self_attrs,
                            class_names,
                            dict_helper_names,
                            cast_names,
                        )
            else:
                active_self_attrs = set(inherited_self_attrs)

            s_params, s_ann_di = _extract_scope_params(scope_node, class_names)
            di_vars, plain_dict_vars, di_seq_vars = _resolve_device_info_scope_bindings(
                direct_nodes,
                child_scopes,
                s_params,
                s_ann_di,
                inherited_di_vars,
                inherited_plain_dict_vars,
                inherited_di_seq_vars,
                active_self_attrs,
                class_names,
                dict_helper_names,
                cast_names,
            )

            # Track comprehension-scoped DeviceInfo loop targets without leaking into outer scope.
            comp_di_vars_by_node: dict[int, set[str]] = {}
            for node in direct_nodes:
                if not isinstance(node, _COMPREHENSION_NODES) or not node.generators:
                    continue
                comp_extra = set(comp_di_vars_by_node.get(id(node), set()))
                for gen in node.generators:
                    if isinstance(gen.target, ast.Name) and _is_device_info_sequence_expr(
                        gen.iter,
                        di_vars | comp_extra,
                        di_seq_vars,
                        active_self_attrs,
                        class_names,
                        plain_dict_vars - comp_extra,
                        cast_names,
                    ):
                        comp_extra.add(gen.target.id)
                if comp_extra:
                    outermost_iter_ids = {id(sub) for sub in ast.walk(node.generators[0].iter)}
                    comp_subnodes, _ = _split_device_info_scope([ast.Expr(value=node)])
                    for sub in comp_subnodes:
                        if id(sub) != id(node) and id(sub) not in outermost_iter_ids:
                            comp_di_vars_by_node.setdefault(id(sub), set()).update(comp_extra)

            for node in direct_nodes:
                extra_di = comp_di_vars_by_node.get(id(node))
                eff_di = di_vars | extra_di if extra_di else di_vars
                eff_plain = plain_dict_vars - extra_di if extra_di else plain_dict_vars

                if isinstance(node, ast.Subscript) and _is_device_info_expr(
                    node.value, eff_di, active_self_attrs, class_names, eff_plain, cast_names
                ):
                    checker.log_error(
                        "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                        py_path,
                        node.lineno,
                        "Deprecated mapping subscript '[...]' on DeviceInfo (HA 2026.12+, core#192323). "
                        "Pass fields to DeviceInfo(...) at construction or read via typing_compat.device_info_to_dict().",
                    )
                elif isinstance(node, ast.Attribute):
                    if (
                        _is_device_info_class(node.value, class_names)
                        and node.attr in DEPRECATED_DEVICE_INFO_CLASS_ATTRS
                    ):
                        checker.log_error(
                            "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                            py_path,
                            node.lineno,
                            f"Deprecated TypedDict attribute '.{node.attr}' on DeviceInfo class "
                            "(HA 2026.12+, core#192323).",
                        )
                    elif (
                        _is_device_info_expr(
                            node.value,
                            eff_di,
                            active_self_attrs,
                            class_names,
                            eff_plain,
                            cast_names,
                        )
                        and node.attr in DEPRECATED_DEVICE_INFO_METHODS
                    ):
                        checker.log_error(
                            "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                            py_path,
                            node.lineno,
                            f"Deprecated mapping access '.{node.attr}' on DeviceInfo "
                            "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                        )
                elif isinstance(node, ast.Compare):
                    for lhs, op, comparator in zip(
                        [node.left, *node.comparators], node.ops, node.comparators
                    ):
                        if isinstance(op, (ast.In, ast.NotIn)) and _is_device_info_expr(
                            comparator,
                            eff_di,
                            active_self_attrs,
                            class_names,
                            eff_plain,
                            cast_names,
                        ):
                            checker.log_error(
                                "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                                py_path,
                                node.lineno,
                                "Deprecated membership check ('in' / 'not in') on DeviceInfo "
                                "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                            )
                        elif isinstance(op, (ast.Eq, ast.NotEq)) and (
                            (
                                _is_device_info_expr(
                                    lhs,
                                    eff_di,
                                    active_self_attrs,
                                    class_names,
                                    eff_plain,
                                    cast_names,
                                )
                                and _is_dict_expr(
                                    comparator, eff_plain, dict_helper_names, cast_names
                                )
                            )
                            or (
                                _is_dict_expr(lhs, eff_plain, dict_helper_names, cast_names)
                                and _is_device_info_expr(
                                    comparator,
                                    eff_di,
                                    active_self_attrs,
                                    class_names,
                                    eff_plain,
                                    cast_names,
                                )
                            )
                        ):
                            checker.log_error(
                                "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                                py_path,
                                node.lineno,
                                "Deprecated mapping equality comparison ('==' / '!=') on DeviceInfo "
                                "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                            )
                elif isinstance(node, ast.Dict):
                    for key, val in zip(node.keys, node.values):
                        if (
                            key is None
                            and val is not None
                            and _is_device_info_expr(
                                val,
                                eff_di,
                                active_self_attrs,
                                class_names,
                                eff_plain,
                                cast_names,
                            )
                        ):
                            checker.log_error(
                                "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                                py_path,
                                node.lineno,
                                "Deprecated mapping unpacking '**' on DeviceInfo "
                                "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                            )
                elif isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg is None and _is_device_info_expr(
                            kw.value,
                            eff_di,
                            active_self_attrs,
                            class_names,
                            eff_plain,
                            cast_names,
                        ):
                            checker.log_error(
                                "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                                py_path,
                                node.lineno,
                                "Deprecated mapping unpacking '**' on DeviceInfo "
                                "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                            )
                    is_dict_ctor = (
                        isinstance(node.func, ast.Name) and node.func.id == "dict"
                    ) or (
                        isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "builtins"
                        and node.func.attr == "dict"
                    )
                    if (
                        is_dict_ctor
                        and len(node.args) == 1
                        and _is_device_info_expr(
                            node.args[0],
                            eff_di,
                            active_self_attrs,
                            class_names,
                            eff_plain,
                            cast_names,
                        )
                    ):
                        checker.log_error(
                            "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                            py_path,
                            node.lineno,
                            "Direct dict(...) conversion on DeviceInfo invokes deprecated mapping methods "
                            "on HA 2026.12+ (core#192323). Use typing_compat.device_info_to_dict() instead.",
                        )
                    elif (
                        isinstance(node.func, ast.Name)
                        and node.func.id in _DEVICE_INFO_ITER_BUILTINS
                        and len(node.args) == 1
                        and not node.keywords
                        and _is_device_info_expr(
                            node.args[0],
                            eff_di,
                            active_self_attrs,
                            class_names,
                            eff_plain,
                            cast_names,
                        )
                    ):
                        checker.log_error(
                            "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                            py_path,
                            node.lineno,
                            f"Deprecated mapping iteration/length '{node.func.id}(...)' on DeviceInfo "
                            "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                        )
                elif isinstance(node, (ast.For, ast.AsyncFor)) and _is_device_info_expr(
                    node.iter,
                    eff_di,
                    active_self_attrs,
                    class_names,
                    eff_plain,
                    cast_names,
                ):
                    checker.log_error(
                        "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                        py_path,
                        node.lineno,
                        "Deprecated mapping iteration ('for ... in') on DeviceInfo "
                        "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                    )
                elif isinstance(node, ast.comprehension) and _is_device_info_expr(
                    node.iter,
                    eff_di,
                    active_self_attrs,
                    class_names,
                    eff_plain,
                    cast_names,
                ):
                    checker.log_error(
                        "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                        py_path,
                        node.iter.lineno,
                        "Deprecated mapping iteration ('for ... in') on DeviceInfo "
                        "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                    )
                elif (
                    isinstance(node, ast.Match)
                    and _is_device_info_expr(
                        node.subject,
                        eff_di,
                        active_self_attrs,
                        class_names,
                        eff_plain,
                        cast_names,
                    )
                    and any(
                        isinstance(sub, ast.MatchMapping)
                        for case in node.cases
                        for sub in ast.walk(case.pattern)
                    )
                ):
                    checker.log_error(
                        "RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING",
                        py_path,
                        node.lineno,
                        "Deprecated mapping pattern match ('case {...}') on DeviceInfo "
                        "(HA 2026.12+, core#192323). Use typing_compat.device_info_to_dict() instead.",
                    )

            child_inherited_vars = (
                inherited_di_vars if isinstance(scope_node, ast.ClassDef) else di_vars
            )
            child_inherited_plain = (
                inherited_plain_dict_vars
                if isinstance(scope_node, ast.ClassDef)
                else plain_dict_vars
            )
            child_inherited_seq = (
                inherited_di_seq_vars
                if isinstance(scope_node, ast.ClassDef)
                else di_seq_vars
            )
            for child in child_scopes:
                _audit_scope(
                    child.body,
                    child,
                    child_inherited_vars,
                    child_inherited_plain,
                    child_inherited_seq,
                    set() if isinstance(child, ast.ClassDef) else active_self_attrs,
                )

        _audit_scope(tree.body, None, set(), set(), set(), set())

    if len(checker.errors) == errors_before:
        checker.log_ok(
            f"No deprecated DeviceInfo mapping operations across {len(py_files)} Python files "
            "(HA 2026.12+, core#192323)."
        )


def check_no_blocking_calls(checker: StandardsChecker):
    """Rule 5: Verify no synchronous sleep or blocking calls in async functions."""
    for root, _, files in os.walk(CUSTOM_COMPONENTS_DIR):
        for file in files:
            if not file.endswith(".py"):
                continue
            py_path = Path(root) / file
            with open(py_path, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=str(py_path))
                except SyntaxError:
                    continue

            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            # Check time.sleep
                            if isinstance(child.func, ast.Attribute):
                                if (
                                    isinstance(child.func.value, ast.Name)
                                    and child.func.value.id == "time"
                                    and child.func.attr == "sleep"
                                ):
                                    checker.log_error(
                                        "RULE_NO_BLOCKING_CALLS",
                                        py_path,
                                        child.lineno,
                                        f"Synchronous time.sleep() called inside async function '{node.name}'. "
                                        f"Use 'await asyncio.sleep()' instead.",
                                    )
                                elif (
                                    isinstance(child.func.value, ast.Name)
                                    and child.func.value.id == "requests"
                                ):
                                    checker.log_error(
                                        "RULE_NO_BLOCKING_CALLS",
                                        py_path,
                                        child.lineno,
                                        f"Synchronous requests.{child.func.attr}() called inside async function '{node.name}'. "
                                        f"Use aiohttp or wrap in hass.async_add_executor_job.",
                                    )

    checker.log_ok("No blocking calls (time.sleep, requests.*) detected in async coroutines.")


def check_ruff_standards(checker: StandardsChecker):
    """Rule 6: Verify codebase satisfies Ruff static analysis standards."""
    import shutil
    import subprocess

    ruff_bin = shutil.which("ruff")
    cmd = [ruff_bin, "check", "."] if ruff_bin else [sys.executable, "-m", "ruff", "check", "."]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT_DIR))
    except FileNotFoundError:
        checker.log_ok("Ruff not found in PATH; skipping static analysis check.")
        return

    if res.returncode != 0 and "No module named ruff" in res.stderr:
        checker.log_ok("Ruff module not installed; skipping static analysis check.")
        return

    if res.returncode != 0:
        checker.log_error(
            "RULE_RUFF_STATIC_ANALYSIS",
            ROOT_DIR,
            1,
            f"Ruff static analysis checks failed:\n{res.stdout.strip()}",
        )
    else:
        checker.log_ok("Ruff static analysis checks passed with 0 violations.")


def check_manifest_requirements_rule(checker: StandardsChecker):
    """Rule 6: Enforce manifest.json version matches const.py and requirements pins OWNd exactly."""
    manifest_file = CUSTOM_COMPONENTS_DIR / "manifest.json"
    const_file = CUSTOM_COMPONENTS_DIR / "const.py"

    if not manifest_file.exists():
        checker.log_error("RULE_MANIFEST", manifest_file, 1, "manifest.json not found")
        return
    if not const_file.exists():
        checker.log_error("RULE_MANIFEST", const_file, 1, "const.py not found")
        return

    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        checker.log_error("RULE_MANIFEST", manifest_file, 1, f"Failed to parse manifest.json: {e}")
        return

    with open(const_file, "r", encoding="utf-8") as f:
        const_content = f.read()

    match = re.search(r'INTEGRATION_VERSION\s*=\s*["\']([^"\']+)["\']', const_content)
    const_version = match.group(1) if match else None
    manifest_version = manifest.get("version")

    if manifest_version != const_version:
        checker.log_error(
            "RULE_MANIFEST",
            manifest_file,
            1,
            f"Version mismatch: manifest.json ({manifest_version}) != const.py ({const_version})",
        )

    # manifest.json is the single source of truth for the OWNd pin: Home Assistant
    # installs it, so it must be an exact ``==`` pin (no runtime self-installer).
    requirements = manifest.get("requirements", [])
    ownd_pins = [r for r in requirements if re.fullmatch(r"OWNd==[0-9][0-9A-Za-z.]*", r)]
    if len(ownd_pins) != 1:
        checker.log_error(
            "RULE_MANIFEST",
            manifest_file,
            1,
            f"manifest.json requirements must contain exactly one exact 'OWNd==<version>' pin. Found: {requirements}",
        )
    else:
        checker.log_ok(f"manifest.json requirements pin verified ({ownd_pins[0]}).")


MARKETPLACE_MANIFEST_ALLOWED_KEYS = frozenset(
    {
        "content_in_root",
        "filename",
        "hacs",
        "hide_default_branch",
        "homeassistant",
        "name",
        "persistent_directory",
        "zip_release",
    }
)

KNOWN_INTEGRATION_CATEGORIES = frozenset(
    {
        "ai_voice",
        "ai_voice.ai_models",
        "ai_voice.image_recognition",
        "ai_voice.speech",
        "ai_voice.voice_assistants",
        "appliances_cleaning",
        "appliances_cleaning.cleaning",
        "appliances_cleaning.cooking",
        "appliances_cleaning.laundry",
        "blinds_curtains",
        "buttons_remotes",
        "climate",
        "climate.air_purifiers_humidifiers",
        "climate.fans_ventilation",
        "climate.heat_pumps_air_conditioning",
        "climate.thermostats_heating",
        "climate.water_heaters",
        "computers_network",
        "computers_network.cloud_developer_tools",
        "computers_network.monitoring",
        "computers_network.nas_servers",
        "computers_network.printers",
        "computers_network.routers_network",
        "diy_makers",
        "diy_makers.boards_hardware",
        "diy_makers.web_data_connectors",
        "energy",
        "energy.backup_power",
        "energy.energy_prices",
        "energy.ev_charging",
        "energy.meters_monitoring",
        "energy.solar_batteries",
        "finance",
        "garden_plants",
        "health_wellbeing",
        "helpers",
        "helpers.calculate_value",
        "helpers.store_value",
        "hubs_protocols",
        "hubs_protocols.building_automation",
        "hubs_protocols.smart_home_hubs",
        "hubs_protocols.wireless_protocols",
        "lighting",
        "lighting.bulbs_lamps",
        "lighting.led_strips_controllers",
        "lighting.lighting_systems",
        "local_services",
        "media_entertainment",
        "media_entertainment.gaming",
        "media_entertainment.music_radio_services",
        "media_entertainment.speakers_audio",
        "media_entertainment.tv_streaming",
        "messaging_notifications",
        "messaging_notifications.chat_apps",
        "messaging_notifications.email_sms_calls",
        "messaging_notifications.push_notifications",
        "news_social_media",
        "other_ecosystems",
        "pets",
        "plugs_switches",
        "presence_location",
        "presence_location.network_presence",
        "presence_location.phones_companion_apps",
        "presence_location.trackers",
        "productivity_information",
        "productivity_information.calendars_todos",
        "productivity_information.documents_photos",
        "productivity_information.shopping_groceries",
        "security_safety",
        "security_safety.alarm_systems",
        "security_safety.cameras",
        "security_safety.doorbells_intercoms",
        "security_safety.locks_access",
        "security_safety.smoke_co_alarms",
        "sensors",
        "sensors.air_quality",
        "sensors.door_window",
        "sensors.motion_presence",
        "sensors.temperature_humidity",
        "vehicles_transport",
        "vehicles_transport.bikes_scooters",
        "vehicles_transport.cars_evs",
        "vehicles_transport.flights",
        "vehicles_transport.public_transport",
        "water",
        "water.meters_leak_detection",
        "water.pools_spas",
        "water.water_treatment",
        "weather_alerts",
        "weather_alerts.air_quality_pollen",
        "weather_alerts.forecasts",
        "weather_alerts.public_warnings",
    }
)

PNG_MAGIC_BYTES = b"\x89PNG\r\n\x1a\n"
PNG_IHDR_START = b"\x00\x00\x00\x0dIHDR"
PNG_HEADER_SIZE = 33
ALLOWED_BRAND_IMAGES = frozenset(
    {
        "icon.png",
        "icon@2x.png",
        "dark_icon.png",
        "dark_icon@2x.png",
        "logo.png",
        "logo@2x.png",
        "dark_logo.png",
        "dark_logo@2x.png",
    }
)
BRAND_ICON_SIZES = {"icon.png": 256, "icon@2x.png": 512}
BRAND_LOGO_SHORTEST_SIDE = {"logo.png": (128, 256), "logo@2x.png": (256, 512)}
MIN_SUPPORTED_HA_VERSION = (2026, 8, 0)
HA_CALVER_RE = re.compile(
    r"^(?P<year>[1-9]\d{3})\.(?P<month>[1-9]|1[0-2])\.(?P<patch>0|[1-9]\d*)(?:(?:a|b|rc)(?:0|[1-9]\d*))?$"
)


def _extract_path_leaf(expr: ast.AST) -> str | None:
    """Extract a static trailing filename from a string, Path/os.path.join call, or `/` expression."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        leaf = Path(expr.value.replace("\\", "/")).name
        return leaf or None
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
        return _extract_path_leaf(expr.right)
    if isinstance(expr, ast.Call) and expr.args:
        func = expr.func
        func_name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if func_name in ("join", "Path", "PurePath", "PurePosixPath"):
            return _extract_path_leaf(expr.args[-1])
    return None


def _extract_output_zip_name(package_script: Path) -> str | None:
    """Extract OUTPUT_ZIP basename from scripts/package_release.py via AST (None if unparseable)."""
    try:
        tree = ast.parse(package_script.read_text(encoding="utf-8"), filename=str(package_script))
    except SyntaxError:
        return None

    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id == "OUTPUT_ZIP":
                    return _extract_path_leaf(stmt.value)
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == "OUTPUT_ZIP"
            and stmt.value is not None
        ):
            return _extract_path_leaf(stmt.value)

    return "myhome.zip"


def _validate_manifest_categories_and_branding(
    checker: StandardsChecker,
    manifest_file: Path,
    manifest_data: dict,
    *,
    require_brand_dir: bool,
) -> None:
    """Validate Marketplace categories in manifest.json and local brand/ PNG assets."""
    categories = manifest_data.get("categories")
    if (
        not isinstance(categories, list)
        or not (1 <= len(categories) <= 3)
        or not all(isinstance(c, str) for c in categories)
    ):
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            manifest_file,
            1,
            f"manifest.json 'categories' must be a list of 1..3 category strings for Marketplace catalog indexing (got {categories!r})",
        )
    else:
        if len(set(categories)) != len(categories):
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                manifest_file,
                1,
                f"manifest.json 'categories' must not contain duplicates: {categories!r}",
            )
        unknown_cats = [c for c in categories if c not in KNOWN_INTEGRATION_CATEGORIES]
        if unknown_cats:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                manifest_file,
                1,
                f"manifest.json 'categories' contains unknown Home Assistant categories: {unknown_cats!r}",
            )
        for cat in categories:
            parent, _, sub = cat.partition(".")
            if sub and parent in categories:
                checker.log_error(
                    "RULE_MARKETPLACE_MANIFEST",
                    manifest_file,
                    1,
                    f"manifest.json category {parent!r} is implied by subcategory {cat!r}; remove {parent!r}",
                )

    brand_dir = manifest_file.parent / "brand"
    if require_brand_dir and not brand_dir.is_dir():
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            brand_dir,
            1,
            "Local brand/ directory not found under custom_components/myhome/",
        )
    elif brand_dir.is_dir():
        _validate_brand_dir(checker, brand_dir)


def _validate_brand_dir(checker: StandardsChecker, brand_dir: Path) -> None:
    """Validate local brand/ assets against Home Assistant hassfest brand_images rules."""
    present_files: dict[str, Path] = {}
    for entry in sorted(brand_dir.iterdir()):
        if entry.is_file():
            present_files[entry.name] = entry
        else:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                entry,
                1,
                f"Local brand entry 'brand/{entry.name}' is not a file",
            )

    for extra_name in sorted(present_files.keys() - ALLOWED_BRAND_IMAGES):
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            present_files[extra_name],
            1,
            f"Unexpected file 'brand/{extra_name}' in brand/; allowed are: {', '.join(sorted(ALLOWED_BRAND_IMAGES))}",
        )

    asset_bytes: dict[str, bytes] = {}
    for asset_name in sorted(ALLOWED_BRAND_IMAGES, key=lambda name: (name.startswith("dark_"), name)):
        asset_path = present_files.get(asset_name)
        if asset_path is None:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                brand_dir / asset_name,
                1,
                f"Required local brand asset {asset_name!r} missing from brand/",
            )
            continue

        raw_bytes = asset_path.read_bytes()
        asset_bytes[asset_name] = raw_bytes
        if (
            len(raw_bytes) < PNG_HEADER_SIZE
            or raw_bytes[:8] != PNG_MAGIC_BYTES
            or raw_bytes[8:16] != PNG_IHDR_START
        ):
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                asset_path,
                1,
                f"Local brand asset {asset_name!r} is not a valid PNG file",
            )
            continue

        width, height = struct.unpack(">II", raw_bytes[16:24])
        base_name = asset_name.removeprefix("dark_")
        if base_name in BRAND_ICON_SIZES:
            expected = BRAND_ICON_SIZES[base_name]
            if (width, height) != (expected, expected):
                checker.log_error(
                    "RULE_MARKETPLACE_MANIFEST",
                    asset_path,
                    1,
                    f"Local brand asset {asset_name!r} is {width}x{height}, expected {expected}x{expected}",
                )
        else:
            low, high = BRAND_LOGO_SHORTEST_SIDE[base_name]
            if not (low <= min(width, height) <= high):
                checker.log_error(
                    "RULE_MARKETPLACE_MANIFEST",
                    asset_path,
                    1,
                    f"Local brand asset {asset_name!r} is {width}x{height}, shortest side must be {low}..{high} px",
                )

        if asset_name.startswith("dark_") and base_name in asset_bytes and asset_bytes[base_name] == raw_bytes:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                asset_path,
                1,
                f"Dark brand asset {asset_name!r} is byte-identical to {base_name!r}; provide distinct dark-theme artwork",
            )


def check_marketplace_manifest_rule(checker: StandardsChecker, root_dir: Path | None = None):
    """Enforce Home Assistant 2026.11 Marketplace & HACS hacs.json, manifest.json, brand/, and validate.yml compliance."""
    base_root = root_dir or ROOT_DIR
    hacs_file = base_root / "hacs.json"
    manifest_file = base_root / "custom_components" / "myhome" / "manifest.json"
    package_script = base_root / "scripts" / "package_release.py"
    validate_workflow = base_root / ".github" / "workflows" / "validate.yml"
    errors_before = len(checker.errors)

    if not hacs_file.exists():
        checker.log_error("RULE_MARKETPLACE_MANIFEST", hacs_file, 1, "hacs.json not found at repository root")
        return

    try:
        hacs_data = json.loads(hacs_file.read_text(encoding="utf-8"))
    except Exception as err:
        checker.log_error("RULE_MARKETPLACE_MANIFEST", hacs_file, 1, f"Failed to parse hacs.json: {err}")
        return

    if not isinstance(hacs_data, dict):
        checker.log_error("RULE_MARKETPLACE_MANIFEST", hacs_file, 1, "hacs.json root must be a JSON object")
        return

    unknown_keys = sorted(set(hacs_data.keys()) - MARKETPLACE_MANIFEST_ALLOWED_KEYS)
    if unknown_keys:
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            hacs_file,
            1,
            f"hacs.json contains keys unsupported by Home Assistant 2026.11 Marketplace: {unknown_keys}. "
            f"Allowed keys: {sorted(MARKETPLACE_MANIFEST_ALLOWED_KEYS)}",
        )

    hacs_name = hacs_data.get("name")
    valid_hacs_name = isinstance(hacs_name, str) and bool(hacs_name.strip())
    if not valid_hacs_name:
        checker.log_error("RULE_MARKETPLACE_MANIFEST", hacs_file, 1, "hacs.json must define a non-empty string 'name'")

    if manifest_file.exists():
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(manifest_data, dict):
                checker.log_error("RULE_MARKETPLACE_MANIFEST", manifest_file, 1, "manifest.json root must be a JSON object")
            else:
                manifest_name = manifest_data.get("name")
                if valid_hacs_name and isinstance(manifest_name, str) and manifest_name and hacs_name != manifest_name:
                    checker.log_error(
                        "RULE_MARKETPLACE_MANIFEST",
                        hacs_file,
                        1,
                        f"hacs.json name ({hacs_name!r}) must match manifest.json name ({manifest_name!r})",
                    )
                _validate_manifest_categories_and_branding(
                    checker,
                    manifest_file,
                    manifest_data,
                    require_brand_dir=(root_dir is None),
                )
        except Exception as err:
            checker.log_error("RULE_MARKETPLACE_MANIFEST", manifest_file, 1, f"Failed to parse manifest.json: {err}")

    for bool_key in ("content_in_root", "hide_default_branch", "zip_release"):
        if bool_key in hacs_data and not isinstance(hacs_data[bool_key], bool):
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                f"hacs.json {bool_key!r} must be a boolean (got {hacs_data[bool_key]!r})",
            )

    if (
        hacs_data.get("content_in_root") is True
        and manifest_file.exists()
        and not (base_root / "manifest.json").exists()
    ):
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            hacs_file,
            1,
            "hacs.json 'content_in_root' must not be true when integration lives under custom_components/myhome/",
        )

    if "hacs" in hacs_data:
        hacs_min = hacs_data["hacs"]
        if not isinstance(hacs_min, str) or not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", hacs_min):
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                f"hacs.json 'hacs' must be a valid version string (got {hacs_min!r})",
            )

    if "persistent_directory" in hacs_data:
        pdir = hacs_data["persistent_directory"]
        if (
            not isinstance(pdir, str)
            or not pdir.strip()
            or "\\" in pdir
            or pdir.startswith("/")
            or pdir.endswith("/")
            or any(seg in ("", ".", "..") for seg in pdir.split("/"))
        ):
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                f"hacs.json 'persistent_directory' must be a safe relative subdirectory path (got {pdir!r})",
            )

    expected_zip_name: str | None = "myhome.zip"
    if package_script.exists():
        expected_zip_name = _extract_output_zip_name(package_script)
        if expected_zip_name is None:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                package_script,
                1,
                "Could not statically resolve OUTPUT_ZIP filename from scripts/package_release.py",
            )
        if hacs_data.get("zip_release") is not True:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                "hacs.json must set 'zip_release': true to match scripts/package_release.py",
            )

    if hacs_data.get("zip_release") is True:
        filename = hacs_data.get("filename")
        if (
            not isinstance(filename, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.zip", filename)
            or ".." in filename
        ):
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                f"hacs.json with zip_release=true must specify a valid .zip filename (got {filename!r})",
            )
        elif package_script.exists() and expected_zip_name is not None and filename != expected_zip_name:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                f"hacs.json filename ({filename!r}) must be {expected_zip_name!r} to match scripts/package_release.py",
            )
    elif "filename" in hacs_data:
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            hacs_file,
            1,
            f"hacs.json must not set 'filename' ({hacs_data['filename']!r}) unless zip_release=true",
        )

    ha_min = hacs_data.get("homeassistant")
    ha_match = HA_CALVER_RE.fullmatch(ha_min) if isinstance(ha_min, str) else None
    if ha_match is None:
        checker.log_error(
            "RULE_MARKETPLACE_MANIFEST",
            hacs_file,
            1,
            f"hacs.json 'homeassistant' must be a valid Home Assistant CalVer string YYYY.M.P (got {ha_min!r})",
        )
    else:
        ha_tuple = (
            int(ha_match.group("year")),
            int(ha_match.group("month")),
            int(ha_match.group("patch")),
        )
        if ha_tuple < MIN_SUPPORTED_HA_VERSION:
            checker.log_error(
                "RULE_MARKETPLACE_MANIFEST",
                hacs_file,
                1,
                f"hacs.json 'homeassistant' ({ha_min!r}) must be >= '2026.8.0'",
            )

    if not validate_workflow.exists():
        checker.log_error(
            "RULE_MARKETPLACE_VALIDATE",
            validate_workflow,
            1,
            "validate.yml not found under .github/workflows/",
        )
    else:
        try:
            import yaml

            wf_data = yaml.safe_load(validate_workflow.read_text(encoding="utf-8")) or {}
            raw_jobs = wf_data.get("jobs") if isinstance(wf_data, dict) else None
            jobs = raw_jobs if isinstance(raw_jobs, dict) else {}
            validate_job = jobs.get("validate")
            if not isinstance(validate_job, dict):
                checker.log_error(
                    "RULE_MARKETPLACE_VALIDATE",
                    validate_workflow,
                    1,
                    "validate.yml must preserve the required 'validate' job ID",
                )
            else:
                if "if" in validate_job or validate_job.get("continue-on-error"):
                    checker.log_error(
                        "RULE_MARKETPLACE_VALIDATE",
                        validate_workflow,
                        1,
                        "validate.yml 'validate' job must not set 'if' or 'continue-on-error'",
                    )
                raw_steps = validate_job.get("steps")
                steps = raw_steps if isinstance(raw_steps, list) else []
                found_hacs_action = False
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    uses = str(step.get("uses", ""))
                    if uses == "hacs/action" or uses.startswith("hacs/action@"):
                        found_hacs_action = True
                        if not re.fullmatch(r"hacs/action@[0-9a-f]{40}", uses):
                            checker.log_error(
                                "RULE_MARKETPLACE_VALIDATE",
                                validate_workflow,
                                1,
                                f"validate.yml hacs/action step must pin a 40-character commit SHA (got {uses!r})",
                            )
                        if "if" in step or step.get("continue-on-error"):
                            checker.log_error(
                                "RULE_MARKETPLACE_VALIDATE",
                                validate_workflow,
                                1,
                                "validate.yml hacs/action step must not set 'if' or 'continue-on-error'",
                            )
                        with_args = step.get("with")
                        if not isinstance(with_args, dict) or with_args.get("category") != "integration":
                            checker.log_error(
                                "RULE_MARKETPLACE_VALIDATE",
                                validate_workflow,
                                1,
                                "validate.yml hacs/action step must set with.category to 'integration'",
                            )
                        if isinstance(with_args, dict) and "ignore" in with_args:
                            checker.log_error(
                                "RULE_MARKETPLACE_VALIDATE",
                                validate_workflow,
                                1,
                                f"validate.yml must not bypass default-catalog checks via ignore: {with_args.get('ignore')!r}",
                            )
                if not found_hacs_action:
                    checker.log_error(
                        "RULE_MARKETPLACE_VALIDATE",
                        validate_workflow,
                        1,
                        "validate.yml must define a hacs/action validation step",
                    )
        except Exception as err:
            checker.log_error(
                "RULE_MARKETPLACE_VALIDATE",
                validate_workflow,
                1,
                f"Failed to parse validate.yml: {err}",
            )

    if len(checker.errors) == errors_before:
        checker.log_ok("Home Assistant Marketplace / HACS manifest (hacs.json) and validate.yml verified.")


def check_quality_scale_rules(checker: StandardsChecker):
    """Enforce Home Assistant Integration Quality Scale (Bronze, Silver, Gold, Platinum)."""
    # 1. [BRONZE] Runtime Data
    init_file = CUSTOM_COMPONENTS_DIR / "__init__.py"
    if init_file.exists():
        init_content = init_file.read_text(encoding="utf-8")
        if "runtime_data" not in init_content:
            checker.log_error(
                "RULE_IQS_BRONZE",
                init_file,
                1,
                "Quality Scale Bronze rule 'runtime-data': entry.runtime_data must be implemented in __init__.py",
            )
        else:
            checker.log_ok("[BRONZE] runtime_data entry mapping implemented.")

    # Platforms, services, WebSocket API and diagnostics must read per-entry state from
    # entry.runtime_data, never from the deprecated hass.data[DOMAIN][mac] alias.
    per_entry_reader = re.compile(
        r"hass\.data(?:\.get\(DOMAIN|\[DOMAIN\])\S*\b(?:CONF_ENTITY|CONF_PLATFORMS|CONF_ENTITIES|mac|CONF_MAC)\b"
    )
    offenders = []
    for module in RUNTIME_DATA_READERS:
        module_file = CUSTOM_COMPONENTS_DIR / module
        if not module_file.exists():
            continue
        for lineno, line in enumerate(module_file.read_text(encoding="utf-8").splitlines(), 1):
            if per_entry_reader.search(line):
                offenders.append((module_file, lineno, line.strip()))
    if offenders:
        for module_file, lineno, line in offenders:
            checker.log_error(
                "RULE_IQS_BRONZE",
                module_file,
                lineno,
                f"Quality Scale Bronze rule 'runtime-data': read entry.runtime_data instead of hass.data: {line}",
            )
    else:
        checker.log_ok(f"[BRONZE] runtime-data: {len(RUNTIME_DATA_READERS)} modules read per-entry state from entry.runtime_data only.")

    # 2. [BRONZE] Services Extraction
    services_file = CUSTOM_COMPONENTS_DIR / "services.py"
    if not services_file.exists():
        checker.log_error(
            "RULE_IQS_BRONZE",
            services_file,
            1,
            "Quality Scale Bronze rule 'action-setup': services must be extracted to services.py",
        )
    else:
        services_content = services_file.read_text(encoding="utf-8")
        init_content = (CUSTOM_COMPONENTS_DIR / "__init__.py").read_text(encoding="utf-8")
        setup_body = re.search(r"async def async_setup\(.*?(?=\nasync def |\ndef |\Z)", init_content, re.S)
        registered_in_setup = bool(setup_body and "async_setup_services(" in setup_body.group(0))
        if "async_setup_services" not in services_content or not registered_in_setup:
            checker.log_error(
                "RULE_IQS_BRONZE",
                services_file,
                1,
                "Quality Scale Bronze rule 'action-setup': services.py must define async_setup_services and "
                "__init__.async_setup must call it (services stay registered independent of config entries)",
            )
        else:
            checker.log_ok("[BRONZE] action-setup: services registered once in async_setup.")

    # 3. [SILVER] Concurrency: PARALLEL_UPDATES = 0 across all platform files
    platform_files = [
        "alarm_control_panel.py",
        "binary_sensor.py",
        "button.py",
        "climate.py",
        "cover.py",
        "light.py",
        "lock.py",
        "media_player.py",
        "number.py",
        "sensor.py",
        "switch.py",
    ]
    missing_parallel = []
    for pf_name in platform_files:
        pf = CUSTOM_COMPONENTS_DIR / pf_name
        if pf.exists():
            content = pf.read_text(encoding="utf-8")
            if not re.search(r"^PARALLEL_UPDATES\s*(:\s*int)?\s*=\s*0", content, re.MULTILINE):
                missing_parallel.append(pf_name)
    if missing_parallel:
        checker.log_error(
            "RULE_IQS_SILVER",
            CUSTOM_COMPONENTS_DIR,
            1,
            f"Quality Scale Silver rule 'parallel-updates': PARALLEL_UPDATES = 0 missing in: {', '.join(missing_parallel)}",
        )
    else:
        checker.log_ok(f"[SILVER] parallel-updates: PARALLEL_UPDATES = 0 verified across all {len(platform_files)} platform files.")

    # 4. [GOLD] Reconfiguration Flow
    config_flow_file = CUSTOM_COMPONENTS_DIR / "config_flow.py"
    if config_flow_file.exists():
        has_reconfigure = any(
            "async_step_reconfigure" in flow_file.read_text(encoding="utf-8")
            for flow_file in sorted(CUSTOM_COMPONENTS_DIR.glob("config_flow*.py"))
        )
        if not has_reconfigure:
            checker.log_error(
                "RULE_IQS_GOLD",
                config_flow_file,
                1,
                "Quality Scale Gold rule 'reconfiguration-flow': async_step_reconfigure must be implemented in config_flow.py",
            )
        else:
            checker.log_ok("[GOLD] reconfiguration-flow: async_step_reconfigure implemented.")

    # 5. [GOLD] Translations & Strings Sync
    strings_file = CUSTOM_COMPONENTS_DIR / "strings.json"
    en_file = TRANSLATIONS_DIR / "en.json"
    if not strings_file.exists() or not en_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            strings_file,
            1,
            "Quality Scale Gold rule 'entity-translations': strings.json and translations/en.json must exist",
        )
    else:
        try:
            with open(strings_file, "r", encoding="utf-8") as f:
                strings_data = json.load(f)
            with open(en_file, "r", encoding="utf-8") as f:
                en_data = json.load(f)
            if strings_data != en_data:
                checker.log_error(
                    "RULE_IQS_GOLD",
                    strings_file,
                    1,
                    "Quality Scale Gold rule 'entity-translations': strings.json and translations/en.json do not match. Run 'python scripts/manage_translations.py sync-en' to synchronize.",
                )
            else:
                checker.log_ok("[GOLD] entity-translations: strings.json and translations/en.json synchronized.")

            # Validate Home Assistant issues schema (hassfest fixable exclusion group)
            issues = strings_data.get("issues", {})
            for issue_key, issue_val in issues.items():
                if isinstance(issue_val, dict):
                    has_desc = "description" in issue_val
                    has_flow = "fix_flow" in issue_val
                    if has_desc and has_flow:
                        checker.log_error(
                            "RULE_IQS_GOLD",
                            strings_file,
                            1,
                            f"Issue '{issue_key}' violates hassfest exclusion group 'fixable': cannot have both 'description' and 'fix_flow'",
                        )
        except Exception as e:
            checker.log_error("RULE_IQS_GOLD", strings_file, 1, f"Failed parsing strings/translations JSON: {e}")

    # 6. [GOLD] Icon Translations
    icons_file = CUSTOM_COMPONENTS_DIR / "icons.json"
    if not icons_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            icons_file,
            1,
            "Quality Scale Gold rule 'icon-translations': icons.json must exist",
        )
    else:
        checker.log_ok("[GOLD] icon-translations: icons.json registered.")

    # 7. [GOLD] Repairs Platform
    repairs_file = CUSTOM_COMPONENTS_DIR / "repairs.py"
    if not repairs_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            repairs_file,
            1,
            "Quality Scale Gold rule 'repair-issues': repairs.py platform must exist",
        )
    else:
        checker.log_ok("[GOLD] repair-issues: repairs.py platform implemented.")

    # 8. [GOLD] Diagnostics
    diag_file = CUSTOM_COMPONENTS_DIR / "diagnostics.py"
    if not diag_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            diag_file,
            1,
            "Quality Scale Gold rule 'diagnostics': diagnostics.py platform must exist",
        )
    else:
        checker.log_ok("[GOLD] diagnostics: diagnostics.py platform implemented.")

    # 9. [PLATINUM] Async Dependency (No blocking libraries)
    manifest_file = CUSTOM_COMPONENTS_DIR / "manifest.json"
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
            reqs = manifest_data.get("requirements", [])
            blocking_libs = ["requests", "urllib3", "urllib"]
            found_blocking = [r for r in reqs if any(b in r.lower() for b in blocking_libs)]
            if found_blocking:
                checker.log_error(
                    "RULE_IQS_PLATINUM",
                    manifest_file,
                    1,
                    f"Quality Scale Platinum rule 'async-dependency': blocking library found in requirements: {found_blocking}",
                )
            else:
                checker.log_ok("[PLATINUM] async-dependency: zero blocking network libraries in manifest requirements.")
        except Exception as e:
            checker.log_error("RULE_IQS_PLATINUM", manifest_file, 1, f"Failed parsing manifest.json: {e}")

    # 10. [PLATINUM] Quality Scale Manifest Audit
    qs_file = CUSTOM_COMPONENTS_DIR / "quality_scale.yaml"
    if not qs_file.exists():
        checker.log_error(
            "RULE_IQS_PLATINUM",
            qs_file,
            1,
            "Quality Scale audit manifest quality_scale.yaml must exist in custom_components/myhome/",
        )
    else:
        try:
            import yaml
            with open(qs_file, "r", encoding="utf-8") as f:
                qs_data = yaml.safe_load(f)
            rules = qs_data.get("rules", {})
            valid_statuses = {"done", "todo", "in_progress", "exempt"}
            invalid_rules = [k for k, v in rules.items() if isinstance(v, dict) and v.get("status") not in valid_statuses]
            if invalid_rules:
                checker.log_error(
                    "RULE_IQS_PLATINUM",
                    qs_file,
                    1,
                    f"quality_scale.yaml has invalid status for rules: {invalid_rules}",
                )
            else:
                done_count = sum(1 for v in rules.values() if isinstance(v, dict) and v.get("status") in ("done", "exempt"))
                checker.log_ok(f"[PLATINUM] quality_scale.yaml validated ({done_count}/{len(rules)} rules satisfied/exempt).")
        except Exception as e:
            checker.log_error("RULE_IQS_PLATINUM", qs_file, 1, f"Failed parsing quality_scale.yaml: {e}")


def check_ownd_library_standards(checker: StandardsChecker):
    """Enforce OWNd client library PyPI & async decoupling standards."""
    try:
        import OWNd
        ownd_dir = Path(OWNd.__file__).resolve().parent
        has_ha_import = False
        for py_file in ownd_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*(import homeassistant|from homeassistant)", text, re.MULTILINE):
                has_ha_import = True
                checker.log_error(
                    "RULE_OWND_DECOUPLING",
                    py_file,
                    1,
                    "OWNd protocol library must be completely decoupled and MUST NOT import homeassistant",
                )
                break
        if not has_ha_import:
            checker.log_ok("[OWND] Client library clean decoupling verified (0 homeassistant imports).")
    except Exception as err:
        checker.log_ok(f"[OWND] Client library verification skipped (OWNd not in path: {err}).")


def check_supported_domains_rule(checker: StandardsChecker):
    """Rule: Verify README.md Supported Entity Domains table is calibrated and in sync."""
    try:
        try:
            from scripts.update_supported_domains import check_readme_in_sync
        except ImportError:
            from update_supported_domains import check_readme_in_sync

        in_sync, msg = check_readme_in_sync()
        if not in_sync:
            checker.log_error(
                "RULE_DOCS_SUPPORTED_DOMAINS",
                ROOT_DIR / "README.md",
                1,
                f"Supported Entity Domains table in README.md is out of sync: {msg}. "
                f"Run 'python scripts/update_supported_domains.py' to update.",
            )
        else:
            checker.log_ok("README.md Supported Entity Domains & Automations table is calibrated and in sync.")
    except Exception as err:
        checker.log_error(
            "RULE_DOCS_SUPPORTED_DOMAINS",
            ROOT_DIR / "README.md",
            1,
            f"Failed verifying supported domains: {err}",
        )


def check_documentation_anti_drift_rule(checker: StandardsChecker):
    """Rule: Verify all documentation (README.md and docs/ MkDocs site) is in sync with codebase."""
    try:
        try:
            from scripts.sync_documentation import check_all_documentation
        except ImportError:
            from sync_documentation import check_all_documentation

        in_sync, messages = check_all_documentation(update=False)
        if not in_sync:
            drift_details = "\n".join(
                f"  - {m}"
                for m in messages
                if not any(
                    ok_kw in m
                    for ok_kw in (
                        "in sync",
                        "verified",
                        "validated cleanly",
                        "already up to date",
                        "All documentation pages are referenced",
                        "platforms are documented",
                        "services have documented",
                        "All service parameter fields are documented",
                        "repair issues are documented",
                    )
                )
            )
            if not drift_details.strip():
                drift_details = "\n".join(f"  - {m}" for m in messages)
            checker.log_error(
                "RULE_DOCS_ANTI_DRIFT",
                ROOT_DIR / "docs",
                1,
                f"Documentation drift detected across support website / README:\n{drift_details}\n"
                f"Run 'python scripts/sync_documentation.py --update' to synchronize.",
            )
        else:
            checker.log_ok("Complete documentation suite (README.md & docs/ site) is calibrated and in sync.")
    except Exception as err:
        checker.log_error(
            "RULE_DOCS_ANTI_DRIFT",
            ROOT_DIR / "docs",
            1,
            f"Failed verifying documentation synchronization: {err}",
        )


def check_service_and_icon_parity(
    checker: StandardsChecker, root_dir: Path | None = None
) -> None:
    """Gate 3: Enforce 5-way service/field parity, zero schema=None, and entity translation_key icon parity."""
    base_root = root_dir if root_dir is not None else ROOT_DIR
    comp_dir = base_root / "custom_components" / "myhome"
    services_py = comp_dir / "services.py"
    services_yaml = comp_dir / "services.yaml"
    strings_json = comp_dir / "strings.json"
    icons_json = comp_dir / "icons.json"
    services_doc = base_root / "docs" / "configuration" / "services.md"

    for required_file in (services_py, services_yaml, strings_json, icons_json, services_doc):
        if not required_file.exists():
            checker.log_error(
                "RULE_SERVICE_ICON_PARITY",
                required_file,
                1,
                f"Required file missing for service/icon parity check: {required_file.name}",
            )
            return

    import yaml

    before_errs = len(checker.errors)
    sy_data = yaml.safe_load(services_yaml.read_text(encoding="utf-8")) or {}
    st_root = json.loads(strings_json.read_text(encoding="utf-8"))
    ic_root = json.loads(icons_json.read_text(encoding="utf-8"))
    st_services = st_root.get("services", {})
    ic_services = ic_root.get("services", {})
    doc_text = services_doc.read_text(encoding="utf-8")

    # Resolve SERVICE_* string constants across const.py and module files
    service_consts: dict[str, str] = {}
    for const_candidate in (comp_dir / "const.py", services_py):
        if const_candidate.exists():
            c_tree = ast.parse(const_candidate.read_text(encoding="utf-8"))
            for node in c_tree.body:
                if (
                    isinstance(node, ast.Assign)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            service_consts[target.id] = node.value.value

    registered_services: set[str] = set()
    for py_file in sorted(comp_dir.glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        local_consts = dict(service_consts)
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        local_consts[target.id] = node.value.value

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            if attr == "async_register" and len(node.args) >= 2:
                svc_node = node.args[1]
                svc_name = (
                    svc_node.value
                    if isinstance(svc_node, ast.Constant) and isinstance(svc_node.value, str)
                    else local_consts.get(svc_node.id)
                    if isinstance(svc_node, ast.Name)
                    else None
                )
                if svc_name:
                    registered_services.add(svc_name)
                schema_arg: ast.expr | None = node.args[3] if len(node.args) >= 4 else None
                for kw in node.keywords:
                    if kw.arg == "schema":
                        schema_arg = kw.value
                if schema_arg is None or (isinstance(schema_arg, ast.Constant) and schema_arg.value is None):
                    checker.log_error(
                        "RULE_SERVICE_ICON_PARITY",
                        py_file,
                        node.lineno,
                        f"Service registration for {svc_name or '<unknown>'!r} must specify a non-None schema",
                    )
            elif attr == "async_register_entity_service" and len(node.args) >= 1:
                svc_node = node.args[0]
                svc_name = (
                    svc_node.value
                    if isinstance(svc_node, ast.Constant) and isinstance(svc_node.value, str)
                    else local_consts.get(svc_node.id)
                    if isinstance(svc_node, ast.Name)
                    else None
                )
                if svc_name:
                    registered_services.add(svc_name)
                schema_arg = node.args[1] if len(node.args) >= 2 else None
                for kw in node.keywords:
                    if kw.arg == "schema":
                        schema_arg = kw.value
                if schema_arg is None or (isinstance(schema_arg, ast.Constant) and schema_arg.value is None):
                    checker.log_error(
                        "RULE_SERVICE_ICON_PARITY",
                        py_file,
                        node.lineno,
                        f"Entity service registration for {svc_name or '<unknown>'!r} must specify a non-None schema",
                    )

    yaml_services = set(sy_data.keys())
    strings_service_set = set(st_services.keys())
    icons_service_set = set(ic_services.keys())

    # Slice services.md into per-service sections
    matches = list(
        re.finditer(
            r"^##\s+(?:\d+\.\s+)?`?myhome\.([a-zA-Z0-9_]+)`?[^\n]*$",
            doc_text,
            re.MULTILINE,
        )
    )
    doc_sections: dict[str, str] = {}
    for idx, m in enumerate(matches):
        svc = m.group(1)
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(doc_text)
        doc_sections[svc] = doc_text[start:end]
    doc_service_set = set(doc_sections.keys())

    for label, candidate_set, target_file in (
        ("AST service registrations", registered_services, services_py),
        ("strings.json['services']", strings_service_set, strings_json),
        ("icons.json['services']", icons_service_set, icons_json),
        ("docs/configuration/services.md", doc_service_set, services_doc),
    ):
        missing = sorted(yaml_services - candidate_set)
        extra = sorted(candidate_set - yaml_services)
        if missing or extra:
            checker.log_error(
                "RULE_SERVICE_ICON_PARITY",
                target_file,
                1,
                f"5-way service parity mismatch between services.yaml and {label}: "
                f"missing={missing}, extra={extra}",
            )

    for svc_name, svc_spec in sorted(sy_data.items()):
        yaml_fields = set(((svc_spec or {}).get("fields") or {}).keys())
        str_fields = set(((st_services.get(svc_name) or {}).get("fields") or {}).keys())
        if yaml_fields != str_fields:
            checker.log_error(
                "RULE_SERVICE_ICON_PARITY",
                strings_json,
                1,
                f"Service field mismatch for 'myhome.{svc_name}' in strings.json: "
                f"services.yaml={sorted(yaml_fields)}, strings.json={sorted(str_fields)}",
            )
        icon_entry = ic_services.get(svc_name)
        if not isinstance(icon_entry, dict) or not str(icon_entry.get("service", "")).startswith("mdi:"):
            checker.log_error(
                "RULE_SERVICE_ICON_PARITY",
                icons_json,
                1,
                f"Service 'myhome.{svc_name}' in icons.json must define a valid 'mdi:...' service icon",
            )
        sec_body = doc_sections.get(svc_name, "")
        for field_name in sorted(yaml_fields):
            if f"`{field_name}`" not in sec_body and f"| {field_name} |" not in sec_body:
                checker.log_error(
                    "RULE_SERVICE_ICON_PARITY",
                    services_doc,
                    1,
                    f"Service field '{field_name}' for 'myhome.{svc_name}' is missing in docs/configuration/services.md",
                )

    # Entity translation_key parity across platform modules, strings.json["entity"], and icons.json["entity"]
    entity_platforms = (
        "light",
        "switch",
        "cover",
        "climate",
        "binary_sensor",
        "sensor",
        "media_player",
        "button",
        "number",
        "alarm_control_panel",
        "lock",
    )
    excluded_call_names = {
        "HomeAssistantError",
        "ServiceValidationError",
        "CalibrationError",
        "async_create_issue",
    }
    st_entities = st_root.get("entity", {})
    ic_entities = ic_root.get("entity", {})

    for plat in entity_platforms:
        plat_file = comp_dir / f"{plat}.py"
        if not plat_file.exists():
            continue
        p_tree = ast.parse(plat_file.read_text(encoding="utf-8"))
        found_keys: set[str] = set()
        for node in ast.walk(p_tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    t_name = (
                        target.id
                        if isinstance(target, ast.Name)
                        else target.attr
                        if isinstance(target, ast.Attribute)
                        else None
                    )
                    if t_name in ("_attr_translation_key", "_translation_key"):
                        found_keys.add(node.value.value)
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                t_name = (
                    node.target.id
                    if isinstance(node.target, ast.Name)
                    else node.target.attr
                    if isinstance(node.target, ast.Attribute)
                    else None
                )
                if t_name in ("_attr_translation_key", "_translation_key"):
                    found_keys.add(node.value.value)
            elif isinstance(node, ast.Call):
                fn_name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                if fn_name in excluded_call_names:
                    continue
                for kw in node.keywords:
                    if kw.arg == "translation_key" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        found_keys.add(kw.value.value)

        st_plat_keys = set((st_entities.get(plat) or {}).keys())
        ic_plat_keys = set((ic_entities.get(plat) or {}).keys())
        for tkey in sorted(found_keys):
            if tkey not in st_plat_keys:
                checker.log_error(
                    "RULE_SERVICE_ICON_PARITY",
                    strings_json,
                    1,
                    f"Entity translation_key '{plat}.{tkey}' in {plat_file.name} missing from strings.json['entity']['{plat}']",
                )
            if tkey not in ic_plat_keys:
                checker.log_error(
                    "RULE_SERVICE_ICON_PARITY",
                    icons_json,
                    1,
                    f"Entity translation_key '{plat}.{tkey}' in {plat_file.name} missing from icons.json['entity']['{plat}']",
                )
        if st_plat_keys != ic_plat_keys:
            checker.log_error(
                "RULE_SERVICE_ICON_PARITY",
                icons_json,
                1,
                f"Entity translation_key set mismatch for platform '{plat}': "
                f"strings.json={sorted(st_plat_keys)}, icons.json={sorted(ic_plat_keys)}",
            )

    all_st_entity_keys = {
        k
        for plat_map in st_entities.values()
        if isinstance(plat_map, dict)
        for k in plat_map
    }
    all_ic_entity_keys = {
        k
        for plat_map in ic_entities.values()
        if isinstance(plat_map, dict)
        for k in plat_map
    }
    platform_filenames = {f"{plat}.py" for plat in entity_platforms}
    for helper_py in sorted(comp_dir.glob("*.py")):
        if helper_py.name in platform_filenames:
            continue
        h_tree = ast.parse(helper_py.read_text(encoding="utf-8"))
        for node in ast.walk(h_tree):
            helper_tkey: str | None = None
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                if any(
                    (
                        t.id
                        if isinstance(t, ast.Name)
                        else t.attr
                        if isinstance(t, ast.Attribute)
                        else None
                    )
                    in ("_attr_translation_key", "_translation_key")
                    for t in node.targets
                ):
                    helper_tkey = node.value.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                t_nm = (
                    node.target.id
                    if isinstance(node.target, ast.Name)
                    else node.target.attr
                    if isinstance(node.target, ast.Attribute)
                    else None
                )
                if t_nm in ("_attr_translation_key", "_translation_key"):
                    helper_tkey = node.value.value
            if helper_tkey:
                if helper_tkey not in all_st_entity_keys:
                    checker.log_error(
                        "RULE_SERVICE_ICON_PARITY",
                        strings_json,
                        1,
                        f"Entity translation_key '{helper_tkey}' in {helper_py.name} missing from strings.json['entity']",
                    )
                if helper_tkey not in all_ic_entity_keys:
                    checker.log_error(
                        "RULE_SERVICE_ICON_PARITY",
                        icons_json,
                        1,
                        f"Entity translation_key '{helper_tkey}' in {helper_py.name} missing from icons.json['entity']",
                    )

    if len(checker.errors) == before_errs:
        checker.log_ok(
            f"5-way service parity ({len(yaml_services)} services, 0 schema=None) and entity icon parity verified."
        )


def check_packaging_metadata_rule(
    checker: StandardsChecker, root_dir: Path | None = None
) -> None:
    """Gate 7: Enforce pyproject.toml and CONTRIBUTING.md parity with manifest.json."""
    base_root = root_dir if root_dir is not None else ROOT_DIR
    manifest_file = base_root / "custom_components" / "myhome" / "manifest.json"
    pyproject_file = base_root / "pyproject.toml"
    contributing_file = base_root / "CONTRIBUTING.md"

    if not manifest_file.exists() or not pyproject_file.exists():
        checker.log_error(
            "RULE_PACKAGING_METADATA",
            pyproject_file if not pyproject_file.exists() else manifest_file,
            1,
            "manifest.json and pyproject.toml must both exist",
        )
        return

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_version = manifest_data.get("version", "")
    reqs = manifest_data.get("requirements", [])
    ownd_req = next((r for r in reqs if isinstance(r, str) and r.startswith("OWNd==")), "")

    py_data = tomllib.loads(pyproject_file.read_text(encoding="utf-8"))
    project = py_data.get("project", {})
    py_version = project.get("version", "")
    req_python = project.get("requires-python", "")
    classifiers = list(project.get("classifiers", []))
    test_deps = list(project.get("optional-dependencies", {}).get("test", []))

    before_errs = len(checker.errors)
    if py_version != manifest_version:
        checker.log_error(
            "RULE_PACKAGING_METADATA",
            pyproject_file,
            1,
            f"pyproject.toml project.version ({py_version!r}) must match manifest.json version ({manifest_version!r})",
        )
    if req_python != ">=3.14":
        checker.log_error(
            "RULE_PACKAGING_METADATA",
            pyproject_file,
            1,
            f"pyproject.toml requires-python must be '>=3.14' (got {req_python!r})",
        )
    if "Programming Language :: Python :: 3.13" in classifiers:
        checker.log_error(
            "RULE_PACKAGING_METADATA",
            pyproject_file,
            1,
            "pyproject.toml classifiers must not advertise 'Programming Language :: Python :: 3.13'",
        )
    if ownd_req and ownd_req not in test_deps:
        checker.log_error(
            "RULE_PACKAGING_METADATA",
            pyproject_file,
            1,
            f"pyproject.toml optional-dependencies.test must pin {ownd_req!r} matching manifest.json",
        )

    if contributing_file.exists():
        contrib_text = contributing_file.read_text(encoding="utf-8")
        if "Python 3.14" not in contrib_text or re.search(r"Python\s+3\.1[0-3]\b", contrib_text):
            checker.log_error(
                "RULE_PACKAGING_METADATA",
                contributing_file,
                1,
                "CONTRIBUTING.md must reference Python 3.14 and not stale Python 3.10–3.13 versions",
            )

    if len(checker.errors) == before_errs:
        checker.log_ok(
            f"Packaging metadata parity verified (pyproject.toml v{py_version}, Python >=3.14, {ownd_req})."
        )


def main():
    print("=" * 70)
    print("Running Home Assistant Architectural Standards Validator")
    print("=" * 70)

    checker = StandardsChecker()
    check_discovery_flows(checker)
    check_translation_coverage(checker)
    check_deprecated_constants(checker)
    check_no_blocking_calls(checker)
    check_no_update_listener_reload_conflict(checker)
    check_no_deprecated_device_info_mapping(checker)
    check_ruff_standards(checker)
    check_manifest_requirements_rule(checker)
    check_packaging_metadata_rule(checker)
    check_marketplace_manifest_rule(checker)
    check_service_and_icon_parity(checker)
    check_future_annotations_and_syntax(checker)
    check_quality_scale_rules(checker)
    check_ownd_library_standards(checker)
    check_supported_domains_rule(checker)
    check_documentation_anti_drift_rule(checker)

    print("=" * 70)
    if checker.errors:
        print(f"\nFAILED: {len(checker.errors)} architectural standards violation(s) found.\n")
        for err in checker.errors:
            print(f"  * {err}")
        print("\nPlease fix the above violations to satisfy Home Assistant standards.")
        sys.exit(1)
    else:
        print("\nSUCCESS: All Home Assistant architectural standards passed!\n")
        sys.exit(0)



if __name__ == "__main__":
    main()
