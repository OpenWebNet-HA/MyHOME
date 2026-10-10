"""Typing shims for Home Assistant cores that disagree about their own types.

Home Assistant 2026.10 installs ``probatio`` as ``voluptuous`` at import time, so
at runtime every ``voluptuous`` schema below is a probatio schema and works. mypy
still resolves ``voluptuous`` to the real package, and the 2026.10 stubs expect
probatio types, so passing a voluptuous schema to a Home Assistant API is a type
error there and not on earlier cores. These helpers hide that mismatch behind
``Any`` in one place instead of ignoring it at every call site.
"""
from __future__ import annotations

from typing import Any

from voluptuous import Schema


def flow_schema(definition: Any) -> Any:
    """Build a ``Schema`` for ``async_show_form(data_schema=...)``."""
    return Schema(definition)


def as_any(value: Any) -> Any:
    """Return ``value`` unchanged, typed as ``Any`` (service and websocket schemas)."""
    return value
