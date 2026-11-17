"""Typing shims for Home Assistant cores that disagree about their own types.

Home Assistant 2026.10 installs ``probatio`` as ``voluptuous`` at import time, so
at runtime every ``voluptuous`` schema below is a probatio schema and works. mypy
still resolves ``voluptuous`` to the real package, and the 2026.10 stubs expect
probatio types, so passing a voluptuous schema to a Home Assistant API is a type
error there and not on earlier cores. These helpers hide that mismatch behind
``Any`` in one place instead of ignoring it at every call site.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import homeassistant.helpers.typing as _ha_typing
from voluptuous import Schema


def _resolve_undefined(typing_module: Any) -> Any:
    """Resolve Home Assistant's ``Undefined`` sentinel across core versions.

    Home Assistant 2026.12 deprecated ``UNDEFINED`` in favour of ``Undefined``
    (removal in 2027.12). Reading ``vars(typing_module)`` resolves ``Undefined``
    on 2026.12+ without invoking module ``__getattr__`` deprecation checks,
    while falling back cleanly to ``UNDEFINED`` on 2026.8..2026.11.
    """
    return (
        vars(typing_module)["Undefined"]
        if "Undefined" in vars(typing_module)
        else getattr(typing_module, "UNDEFINED")
    )


UNDEFINED: Any = _resolve_undefined(_ha_typing)


def device_info_to_dict(info: Any) -> dict[str, Any]:
    """Return ``DeviceInfo`` fields as a plain ``dict`` across Home Assistant cores.

    Home Assistant 2026.8–2026.10 represents ``DeviceInfo`` as a ``TypedDict``
    (a plain ``dict`` at runtime without ``as_dict()``). Home Assistant 2026.11
    converted ``DeviceInfo`` (and ``ChildDeviceInfo``) into ``@dataclass(slots=True)``
    with ``as_dict()`` and a silent ``_DeviceInfoMapping`` compatibility shim, and
    Home Assistant 2026.12 (``home-assistant/core#192323``) deprecated
    ``_DeviceInfoMapping`` via ``frame.report_usage`` (removal scheduled for
    2027.12.0).
    """
    as_dict = getattr(info, "as_dict", None)
    if callable(as_dict):
        return cast("dict[str, Any]", as_dict())
    if isinstance(info, Mapping):
        return dict(info)
    return {}


def flow_schema(definition: Any) -> Any:
    """Build a ``Schema`` for ``async_show_form(data_schema=...)``."""
    return Schema(definition)


def as_any(value: Any) -> Any:
    """Return ``value`` unchanged, typed as ``Any`` (service and websocket schemas)."""
    return value
