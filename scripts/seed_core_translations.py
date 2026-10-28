"""Seed ``translations/en.json`` for Home Assistant core components installed from git.

A release wheel ships ``homeassistant/components/<domain>/translations/en.json``;
the git tree only carries ``strings.json`` (``en.json`` is generated at release
time by ``script.translations develop``). Without it, entities that take their
name from the device class (``component.sensor.entity_component.power.name``)
have no name at all, so CI's *dev* leg misnames every such sensor.

This writes the English translations from ``strings.json`` for every component
that lacks them, resolving ``[%key:component::<domain>::<path>%]`` references
the way the core script does. Components that already ship ``en.json`` are left
alone; running it against a release install is a no-op.

It also bridges symbol moves between ``homeassistant`` (beta/dev) and the
latest ``pytest-homeassistant-custom-component`` wheel on PyPI, which only
tracks stable Home Assistant releases.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

REFERENCE = re.compile(r"\[%key:component::([a-z0-9_]+)::([a-z0-9_:]+)%\]")

_NEW_RESTORE_BLOCK = (
    "def _mock_restore_cache(\n"
    "    hass: HomeAssistant, states: Sequence[tuple[State, Mapping[str, Any] | None]]\n"
    ") -> None:\n"
    '    """Mock the DATA_RESTORE_CACHE."""\n'
    "    data = rs.async_get(hass)\n"
    "    now = dt_util.utcnow()\n"
    "\n"
    "    stored_states = []\n"
    "    for state, extra_data in states:\n"
    "        restored_state = state.as_dict()\n"
    "        restored_state = {\n"
    "            **restored_state,\n"
    '            "attributes": json.loads(\n'
    '                json.dumps(restored_state["attributes"], cls=JSONEncoder)\n'
    "            ),\n"
    "        }\n"
    "        stored_states.append(\n"
    '            {"state": restored_state, "extra_data": extra_data, "last_seen": now}\n'
    "        )\n"
    "    data._async_load_stored_states(stored_states)\n"
    "    _LOGGER.debug(\n"
    '        "Restore cache: %s and %s",\n'
    "        data.last_states_by_entity_id,\n"
    "        data.last_states_by_entity_registry_id,\n"
    "    )\n"
    "    assert len(data.last_states_by_entity_id) + len(\n"
    "        data.last_states_by_entity_registry_id\n"
    '    ) == len(states), f"Duplicate entity_id? {states}"\n'
    "\n"
    "\n"
    "def mock_restore_cache(hass: HomeAssistant, states: Sequence[State]) -> None:\n"
    '    """Mock the DATA_RESTORE_CACHE."""\n'
    "    _mock_restore_cache(hass, [(state, None) for state in states])\n"
    "\n"
    "\n"
    "def mock_restore_cache_with_extra_data(\n"
    "    hass: HomeAssistant, states: Sequence[tuple[State, Mapping[str, Any]]]\n"
    ") -> None:\n"
    '    """Mock the DATA_RESTORE_CACHE."""\n'
    "    _mock_restore_cache(hass, states)"
)


def _flatten(prefix: str, node: Any, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _flatten(f"{prefix}::{key}" if prefix else key, value, out)
    elif isinstance(node, str):
        out[prefix] = node


def _resolve(value: str, flat: dict[str, str], depth: int = 0) -> str:
    def substitute(match: re.Match[str]) -> str:
        target = flat.get(f"{match.group(1)}::{match.group(2)}")
        if target is None or depth > 10:
            return match.group(0)
        return _resolve(target, flat, depth + 1)

    return REFERENCE.sub(substitute, value)


def _resolved_tree(node: Any, flat: dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {key: _resolved_tree(value, flat) for key, value in node.items()}
    if isinstance(node, str):
        return _resolve(node, flat)
    return node


def _replace_legacy_logger_import(content: str) -> str | None:
    """Rewrite legacy _clear_logger_overwrites root import in phcc common.py if present."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "homeassistant.components.logger"
            and node.end_lineno is not None
            and any(alias.name == "_clear_logger_overwrites" for alias in node.names)
        ):
            kept = [
                f"{alias.name} as {alias.asname}" if alias.asname else alias.name
                for alias in node.names
                if alias.name != "_clear_logger_overwrites"
            ]
            helper_alias = next(
                alias for alias in node.names if alias.name == "_clear_logger_overwrites"
            )
            helper_import = (
                f"from homeassistant.components.logger.helpers import "
                f"_clear_logger_overwrites as {helper_alias.asname}"
                if helper_alias.asname
                else "from homeassistant.components.logger.helpers import _clear_logger_overwrites"
            )
            replacement_lines = (
                [f"from homeassistant.components.logger import {', '.join(kept)}", helper_import]
                if kept
                else [helper_import]
            )
            lines = content.splitlines(keepends=True)
            trailing_nl = "\n" if lines[node.end_lineno - 1].endswith("\n") else ""
            lines[node.lineno - 1 : node.end_lineno] = ["\n".join(replacement_lines) + trailing_nl]
            return "".join(lines)
    return None


def _replace_legacy_restore_cache(content: str) -> str | None:
    """Rewrite legacy .last_states mock_restore_cache helpers in phcc common.py if present."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return None

    top_funcs = {
        node.name: (idx, node)
        for idx, node in enumerate(tree.body)
        if isinstance(node, ast.FunctionDef)
    }
    if "_mock_restore_cache" in top_funcs:
        return None
    plain_entry = top_funcs.get("mock_restore_cache")
    extra_entry = top_funcs.get("mock_restore_cache_with_extra_data")
    if plain_entry is None or extra_entry is None:
        return None

    plain_idx, plain_node = plain_entry
    extra_idx, extra_node = extra_entry
    if (
        abs(extra_idx - plain_idx) != 1
        or plain_node.decorator_list
        or extra_node.decorator_list
        or plain_node.end_lineno is None
        or extra_node.end_lineno is None
    ):
        return None

    start_line = min(plain_node.lineno, extra_node.lineno)
    end_line = max(plain_node.end_lineno, extra_node.end_lineno)
    lines = content.splitlines(keepends=True)
    span = "".join(lines[start_line - 1 : end_line])
    if "_async_load_stored_states" in span or re.search(r"\.last_states\b", span) is None:
        return None

    trailing_nl = "\n" if lines[end_line - 1].endswith("\n") else ""
    lines[start_line - 1 : end_line] = [_NEW_RESTORE_BLOCK + trailing_nl]
    return "".join(lines)


def _patch_pytest_ha_custom_component() -> None:
    """Bridge symbol/storage moves between HA beta/dev and pytest-homeassistant-custom-component.

    ``pytest-homeassistant-custom-component`` on PyPI tracks stable Home Assistant
    releases:
    1. HA 2026.11+ (core#187220): ``homeassistant.components.logger`` restricted
       ``__all__`` and stopped re-exporting ``_clear_logger_overwrites`` from the
       package root (moving the test import to
       ``homeassistant.components.logger.helpers``).
    2. HA 2026.11+ (core#183906): ``RestoreStateData`` replaced ``last_states``
       (indexed by ``entity_id``) with ``last_states_by_entity_registry_id`` and
       ``last_states_by_entity_id`` populated via ``_async_load_stored_states``.
       Unpatched ``mock_restore_cache`` writes the dead ``data.last_states``
       attribute so ``RestoreEntity.async_get_last_state()`` returns ``None``.

    Until a 2026.11-compatible ``pytest-homeassistant-custom-component`` release
    is published, rewrite its ``common.py`` so beta/dev runs match upstream core.
    """
    try:
        import pytest_homeassistant_custom_component as phcc
    except ImportError:
        return

    common_py = Path(phcc.__file__).parent / "common.py"
    if not common_py.exists():
        return

    has_registry_indexed_restore = False
    try:
        from homeassistant.helpers import restore_state as rs

        has_registry_indexed_restore = hasattr(
            rs.RestoreStateData, "_async_load_stored_states"
        )
    except ImportError:
        pass

    content = common_py.read_text(encoding="utf-8")
    modified = False
    patched_logger = _replace_legacy_logger_import(content)
    if patched_logger is not None:
        content = patched_logger
        modified = True
        print("patched pytest_homeassistant_custom_component/common.py for HA 2026.11+ logger export")
    if has_registry_indexed_restore:
        patched_restore = _replace_legacy_restore_cache(content)
        if patched_restore is not None:
            content = patched_restore
            modified = True
            print(
                "patched pytest_homeassistant_custom_component/common.py for HA 2026.11+ restore_state indexing"
            )
    if modified:
        common_py.write_text(content, encoding="utf-8")


def main() -> int:
    import homeassistant

    components = Path(homeassistant.__file__).parent / "components"
    strings: dict[str, Any] = {}
    for path in sorted(components.glob("*/strings.json")):
        strings[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))

    flat: dict[str, str] = {}
    for domain, tree in strings.items():
        _flatten(domain, tree, flat)

    written = 0
    for domain, tree in strings.items():
        target = components / domain / "translations" / "en.json"
        if target.exists():
            continue
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(_resolved_tree(tree, flat), indent=2) + "\n", encoding="utf-8")
        written += 1
    print(f"seeded translations/en.json for {written} core component(s) (of {len(strings)} with strings.json)")
    _patch_pytest_ha_custom_component()
    return 0


if __name__ == "__main__":
    sys.exit(main())
