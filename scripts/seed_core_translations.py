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

import json
import re
import sys
from pathlib import Path
from typing import Any

REFERENCE = re.compile(r"\[%key:component::([a-z0-9_]+)::([a-z0-9_:]+)%\]")


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


def _patch_pytest_ha_custom_component() -> None:
    """Bridge symbol moves between HA beta/dev and pytest-homeassistant-custom-component.

    ``pytest-homeassistant-custom-component`` on PyPI tracks stable Home Assistant
    releases. In HA 2026.11+ (core#187220), ``homeassistant.components.logger``
    restricted ``__all__`` and stopped re-exporting ``_clear_logger_overwrites``
    from the package root (moving the test import to
    ``homeassistant.components.logger.helpers``). Until a 2026.11-compatible
    ``pytest-homeassistant-custom-component`` release is published, rewrite its
    ``common.py`` import so the pytest11 entrypoint loads cleanly on beta/dev.
    """
    try:
        import pytest_homeassistant_custom_component as phcc
    except ImportError:
        return

    common_py = Path(phcc.__file__).parent / "common.py"
    if not common_py.exists():
        return

    old_block = (
        "from homeassistant.components.logger import (\n"
        "    DOMAIN as LOGGER_DOMAIN,\n"
        "    SERVICE_SET_LEVEL,\n"
        "    _clear_logger_overwrites,\n"
        ")"
    )
    new_block = (
        "from homeassistant.components.logger import DOMAIN as LOGGER_DOMAIN, SERVICE_SET_LEVEL\n"
        "from homeassistant.components.logger.helpers import _clear_logger_overwrites"
    )
    content = common_py.read_text(encoding="utf-8")
    if old_block in content:
        common_py.write_text(content.replace(old_block, new_block, 1), encoding="utf-8")
        print("patched pytest_homeassistant_custom_component/common.py for HA 2026.11+ logger export")


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
