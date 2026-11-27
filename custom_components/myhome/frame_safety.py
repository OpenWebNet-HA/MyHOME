"""Unified Firmware-Oracle-backed OpenWebNet frame safety engine.

Centralises syntax validation, target gateway resolution (including shared-bus
delegation and warm-standby failover), blanket crash guards, per-gateway crash
guards, and non-blocking caution notices across all transmit paths:

- ``myhome.send_message`` service ([services.py])
- Bus Monitor WebSocket ``check_frame`` and ``send`` ([websocket.py])
- ``thermo_switch`` trigger command validation ([where_grammar.py])
- ``CommandWorkerPool`` pre-wire defense-in-depth ([gateway_sessions.py])

All crash rules are empirically verified against the ``own-firmware-oracle``
13-gateway QEMU fleet (``origin/main`` + ``origin/pr/92`` boundary sweep).
"""
from __future__ import annotations

import inspect
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from homeassistant.const import CONF_MAC, CONF_NAME
from OWNd.message import OWNMessage, OWNSignaling

from .where_grammar import where_param

__all__ = [
    "ALL_EMULATED_GATEWAYS",
    "BLANKET_RULE_COUNT",
    "FrameSafetyVerdict",
    "_MH202_ALL_PROBES_CRASH_RE",
    "_handler_model_key",
    "_is_mh202_who4_crash_frame",
    "active_gateway_protections",
    "evaluate_blanket_crash_safety",
    "evaluate_frame_safety",
    "gateway_crash_frame_reason",
    "is_valid_ascii_own_frame",
    "resolve_effective_target",
]

_MISSING = object()

ALL_EMULATED_GATEWAYS: frozenset[str] = frozenset(
    {
        "3488",
        "F450",
        "F453",
        "F453AV",
        "F454",
        "F459",
        "F460",
        "F461",
        "H4684",
        "MH200",
        "MH200N",
        "MH202",
        "MYHOMESERVER1",
    }
)

_WHO4_DIM22_MODELS: tuple[str, ...] = (
    "3488",
    "F450",
    "F453",
    "F453AV",
    "F454",
    "F459",
    "F460",
    "F461",
    "MH200N",
    "MYHOMESERVER1",
)
_WHO18_DIM52_MODELS: tuple[str, ...] = ("3488", "F460", "F461")
_WHO1_EMPTY_WHERE_MODELS: tuple[str, ...] = ("MH202",)
_WHO22_BARE_ALL_MODELS: tuple[str, ...] = (
    "3488",
    "F453",
    "F453AV",
    "F454",
    "F459",
    "F460",
    "F461",
    "MH200N",
    "MH202",
    "MYHOMESERVER1",
)
_WHO22_BARE_33_44_45_MODELS: tuple[str, ...] = (
    "3488",
    "F453",
    "F453AV",
    "F454",
    "F459",
    "F460",
    "F461",
    "MH200N",
    "MYHOMESERVER1",
)
_WHO22_UNSTRUCTURED_WHERE_MODELS: tuple[str, ...] = (
    "3488",
    "F453",
    "F453AV",
    "F454",
    "F459",
    "F460",
    "F461",
    "MH200N",
    "MYHOMESERVER1",
)
_WHO4_DIM14_5DIGIT_MODELS: tuple[str, ...] = ("3488", "F460", "F461")
_WHO13_NET_DIM_MODELS: tuple[str, ...] = ("H4684", "MH200")
_WHO13_DIM50_MODELS: tuple[str, ...] = ("F453", "F453AV", "MH200N")
_MH202_MODELS: tuple[str, ...] = ("MH202",)
_NOREPLY_WHO1_WHAT19_MODELS: tuple[str, ...] = ("H4684", "MH200")
_NOREPLY_WHO13_DIM27_MODELS: tuple[str, ...] = ("MH200", "MH200N", "MYHOMESERVER1")

# Blanket WHO 22 bare WHAT values (zero gateways forward these without #params).
_WHO22_BARE_WHAT_BLANKET: frozenset[int] = frozenset(
    {0, 1, 2, 19, 20, 21, 22, 23, *range(36, 44), *range(46, 54), 57}
)
# Per-gateway WHO 22 bare WHAT values (MH202 forwards them; 9 other gateways crash).
_WHO22_BARE_WHAT_PER_GATEWAY: frozenset[int] = frozenset({33, 44, 45})

BLANKET_RULE_COUNT = 6

_ASCII_OWN_FRAME_RE = re.compile(r"\*[0-9*#]*##", re.ASCII)
_WHO1_EMPTY_WHERE_RE = re.compile(r"\*#0*1\*##", re.ASCII)
_MH202_ALL_PROBES_CRASH_RE = re.compile(
    r"\*#0*4\*0(?:0[1-9]|[1-9][0-9])(?:[#*][0-9*#]*)?##", re.ASCII
)
_MH202_ALL_PROBES_WHERE_RE = re.compile(
    r"^0(?:0[1-9]|[1-9][0-9])(?:#.*)?$", re.ASCII
)
_WHO18_DIM52_CANONICAL_RE = re.compile(
    r"^\*#18\*[0-9]+(?:#0)?\*52(?:#[0-9]{1,2}#(?:0?[1-9]|1[0-2]))?(?:\*[0-9]+)?##$",
    re.ASCII,
)
_WHO4_DIM22_CANONICAL_PREFIX_RE = re.compile(
    r"^\*#4\*3#(?:[1-9]|[1-9][0-9])#[1-9]\*(?:22|#22\*[^#]*)##$",
    re.ASCII,
)

_PLACEHOLDER_MODEL_KEYS: frozenset[str] = frozenset(
    {
        "",
        "NONE",
        "NULL",
        "GENERIC",
        "GENERICGATEWAY",
        "GATEWAY",
        "UNKNOWN",
        "UNKNOWNMODEL",
        "CUSTOMIP",
        "CUSTOMIPGATEWAY",
        "MYHOME",
    }
)


@dataclass(frozen=True, slots=True)
class FrameSafetyVerdict:
    """Structured verdict for an OpenWebNet frame evaluated against a target gateway."""

    valid: bool
    blocked: bool
    severity: Literal["crash", "warning", "invalid"] | None = None
    rule_id: str | None = None
    reason: str | None = None
    scope: Literal["blanket", "gateway"] | None = None
    target_model: str = ""
    target_mac: str = ""
    delegated_from: str | None = None
    affected_models: tuple[str, ...] = ()
    oracle_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dictionary representation of this verdict."""
        return {
            "valid": self.valid,
            "blocked": self.blocked,
            "severity": self.severity,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "scope": self.scope,
            "target_model": self.target_model,
            "target_mac": self.target_mac,
            "delegated_from": self.delegated_from,
            "affected_models": list(self.affected_models),
            "oracle_ref": self.oracle_ref,
        }


@dataclass(frozen=True, slots=True)
class _GatewayRuleDescriptor:
    """Static catalog entry for a per-gateway Firmware Oracle crash rule."""

    rule_id: str
    summary: str
    affected_models: tuple[str, ...]
    oracle_ref: str


_GATEWAY_RULE_CATALOG: tuple[_GatewayRuleDescriptor, ...] = (
    _GatewayRuleDescriptor(
        rule_id="who22_bare_what_33_44_45",
        summary="WHO 22 bare WHAT 33/44/45 commands without #parameters crash the sound daemon.",
        affected_models=_WHO22_BARE_33_44_45_MODELS,
        oracle_ref="own-firmware-oracle/sound-who22-what",
    ),
    _GatewayRuleDescriptor(
        rule_id="who13_network_dims_10_11_12",
        summary="WHO 13 network dimensions 10, 11 and 12 crash the gateway daemon.",
        affected_models=_WHO13_NET_DIM_MODELS,
        oracle_ref="own-firmware-oracle/who13-identification",
    ),
    _GatewayRuleDescriptor(
        rule_id="who13_dim50",
        summary="WHO 13 dimension 50 crashes the gateway daemon.",
        affected_models=_WHO13_DIM50_MODELS,
        oracle_ref="own-firmware-oracle/gateway-who13-dimension-range",
    ),
    _GatewayRuleDescriptor(
        rule_id="mh202_who4_all_probes_001_099",
        summary="WHO 4 3-digit all-probes WHERE (001..099) crashes the thermostat daemon on MH202.",
        affected_models=_MH202_MODELS,
        oracle_ref="own-firmware-oracle/ownd-audit-frames",
    ),
    _GatewayRuleDescriptor(
        rule_id="mh202_who4_status_numeric_invalid",
        summary="WHO 4 dimensionless status with WHERE 000, 100, or leading-zero 0001..0099 crashes MH202.",
        affected_models=_MH202_MODELS,
        oracle_ref="own-firmware-oracle/thermo-where-forms",
    ),
    _GatewayRuleDescriptor(
        rule_id="mh202_who4_status_actuator_hash",
        summary="WHO 4 dimensionless status with actuator #-form WHERE (Z#C, A#B#C, A#B#C#D, 1<=act<=9) crashes MH202.",
        affected_models=_MH202_MODELS,
        oracle_ref="own-firmware-oracle/thermo-where-forms",
    ),
    _GatewayRuleDescriptor(
        rule_id="mh202_who1_status_where",
        summary="WHO 1 dimensionless status with WHERE 1111, <addr>#4, or <addr>#4#<IF>=19+ crashes MH202.",
        affected_models=_MH202_MODELS,
        oracle_ref="own-firmware-oracle/lights-where-extended",
    ),
)


def is_valid_ascii_own_frame(frame: str) -> bool:
    """True when ``frame`` is a single ASCII OpenWebNet frame bounded by ``*`` and ``##``."""
    return (
        isinstance(frame, str)
        and _ASCII_OWN_FRAME_RE.fullmatch(frame) is not None
        and "##" not in frame[:-2]
    )


_ADDR_TOKEN_RE = re.compile(
    r"\[[0-9A-Fa-f:]+\](?::[0-9]+)?"
    r"|(?:[0-9A-Fa-f]{0,4}:){2,}[0-9A-Fa-f:]*"
    r"|[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5}"
    r"|[0-9A-Fa-f]+(?:[.:][0-9A-Fa-f]+)+",
    re.ASCII,
)


def _norm_model_token(value: object) -> str:
    """Return the upper-case ASCII alphanumeric representation of ``value``."""
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def _is_placeholder_token(token: str) -> bool:
    """True when an upper-case alphanumeric ``token`` is a known placeholder."""
    if token in _PLACEHOLDER_MODEL_KEYS:
        return True
    while token.endswith("GATEWAY"):
        token = token[: -len("GATEWAY")]
        if token in _PLACEHOLDER_MODEL_KEYS:
            return True
    return False


def _is_placeholder_model_key(value: object, raw_candidate: object = None) -> bool:
    """True when ``value`` normalizes to an onboarding/generic placeholder rather than a model."""
    if _is_placeholder_token(_norm_model_token(value)):
        return True
    if raw_candidate is not None:
        stripped = _ADDR_TOKEN_RE.sub(" ", str(raw_candidate))
        if stripped != str(raw_candidate) and _is_placeholder_token(_norm_model_token(stripped)):
            return True
    return False


def _is_token_int(token: str, target: int) -> bool:
    """True when ``token`` is an ASCII decimal integer equal to ``target``."""
    return token.isascii() and token.isdigit() and int(token) == target


def _parsed_int(value: Any) -> int | None:
    """Coerce an OWNd numeric attribute to ``int``, returning ``None`` when absent/invalid."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text and text.isascii() and text.isdigit():
        return int(text)
    return None


def _declared_callable(obj: Any, name: str) -> Callable[..., Any] | None:
    """Return ``obj.<name>`` only when declared on ``obj``/its class or explicitly mocked."""
    static = inspect.getattr_static(obj, name, _MISSING)
    if static is _MISSING:
        children = inspect.getattr_static(obj, "_mock_children", None)
        if not isinstance(children, Mapping) or name not in children:
            return None
    attr = getattr(obj, name, None)
    return attr if callable(attr) else None


def _handler_model_key(handler: Any) -> str:
    """Return the normalized gateway model key (``MH202``, ``H4684``, ``3488``, ...) or ``""``."""
    if handler is None:
        return ""
    from .gateway import _ZONE_REQUEST_MODELS, _resolve_model_key

    direct = getattr(handler, "_model_key", None)
    if isinstance(direct, str) and direct in _ZONE_REQUEST_MODELS:
        return direct

    raw_gw = getattr(handler, "gateway", None)
    profile = getattr(raw_gw, "profile", None)
    profile_model = getattr(profile, "model_name", None)
    if not isinstance(profile_model, str):
        profile = None
        profile_model = None
    cfg_entry = getattr(handler, "config_entry", None)
    cfg_data = getattr(cfg_entry, "data", None)
    cfg_name = cfg_data.get(CONF_NAME) if isinstance(cfg_data, Mapping) else None
    cfg_title = getattr(cfg_entry, "title", None)
    fallback_key = ""
    for candidate, cand_profile, allow_fallback in (
        (direct, None, True),
        (profile_model, profile, True),
        (getattr(raw_gw, "model_name", None), None, True),
        (getattr(raw_gw, "model", None), None, True),
        (getattr(handler, "model", None), None, True),
        (cfg_name, None, False),
        (cfg_title, None, False),
    ):
        if isinstance(candidate, str) and candidate.strip():
            resolved = _resolve_model_key(candidate, profile=cand_profile)
            if _is_placeholder_model_key(resolved, candidate):
                continue
            if resolved in _ZONE_REQUEST_MODELS:
                return resolved
            if allow_fallback and not fallback_key:
                fallback_key = resolved
    return fallback_key


def _handler_mac(handler: Any) -> str:
    """Safely extract the gateway MAC string from ``handler``, or ``""``."""
    if handler is None:
        return ""
    mac = getattr(handler, "mac", None)
    if isinstance(mac, str) and mac.strip():
        return mac.strip()
    cfg_entry = getattr(handler, "config_entry", None)
    cfg_data = getattr(cfg_entry, "data", None)
    if isinstance(cfg_data, Mapping):
        raw_mac = cfg_data.get(CONF_MAC)
        if isinstance(raw_mac, str) and raw_mac.strip():
            return raw_mac.strip()
    return ""


def _handler_display_model(handler: Any) -> str:
    """Return a human-readable model label for ``handler`` when its key is empty."""
    model_key = _handler_model_key(handler)
    if model_key:
        return model_key
    raw_gw = getattr(handler, "gateway", None)
    for cand in (
        getattr(raw_gw, "model_name", None),
        getattr(handler, "model", None),
    ):
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    mac = _handler_mac(handler)
    return mac or "primary"


def resolve_effective_target(handler: Any, parsed: Any) -> tuple[Any, str | None]:
    """Resolve the gateway handler that will actually transmit ``parsed``.

    WHO 13 and WHO 1013 are gateway-local and always stay on ``handler``.
    Other WHO subsystems follow ``_delegated_target(parsed)`` or
    ``_failover_target(parsed)`` with a cycle guard. For ``who == 4``, if
    ``handler`` omits both delegation/failover hooks (as in lightweight mocks),
    falls back to ``_who4_target()``.

    Returns ``(effective_handler, delegated_from_model_or_none)``.
    """
    if handler is None:
        return None, None
    who = _parsed_int(getattr(parsed, "who", getattr(parsed, "_who", None)))
    if who in (13, 1013):
        return handler, None

    current = handler
    visited: set[int] = {id(current)}

    while True:
        delegated_fn = _declared_callable(current, "_delegated_target")
        failover_fn = _declared_callable(current, "_failover_target")
        next_target: Any = None
        if delegated_fn is not None or failover_fn is not None:
            if delegated_fn is not None:
                try:
                    next_target = delegated_fn(parsed)
                except Exception:  # pragma: no cover - defensive against partial doubles
                    next_target = None
            if next_target is None and failover_fn is not None:
                try:
                    next_target = failover_fn(parsed)
                except Exception:  # pragma: no cover - defensive against partial doubles
                    next_target = None
        elif who == 4:
            who4_fn = _declared_callable(current, "_who4_target")
            if who4_fn is not None:
                try:
                    next_target = who4_fn()
                except Exception:  # pragma: no cover - defensive against partial doubles
                    next_target = None

        if next_target is None or id(next_target) in visited:
            break
        visited.add(id(next_target))
        current = next_target

    if current is handler:
        return handler, None
    return current, _handler_display_model(handler)


def _is_mh202_who4_status_numeric_invalid(raw_where: str) -> bool:
    """True for WHO 4 dimensionless status numeric WHERE forms that crash MH202.

    Matches ``000``, ``100``, and 4+ digit leading-zero spellings ``0001..0099``
    (``own-firmware-oracle`` ``thermo-where-forms.cases:30-42``).
    """
    if not raw_where or not (raw_where.isascii() and raw_where.isdigit()):
        return False
    if raw_where in ("000", "100"):
        return True
    if len(raw_where) >= 4 and raw_where.startswith("0"):
        val = int(raw_where)
        return 1 <= val <= 99
    return False


def _is_mh202_who4_status_actuator_hash(raw_where: str) -> bool:
    """True for WHO 4 dimensionless status actuator ``#``-forms that crash MH202.

    ``libthermo.so`` classifies ``Z#C`` (``0<=Z<=99, 1<=C<=9``),
    ``A#B#C`` (``1<=B<=99, 1<=C<=9``), and ``A#B#C#D`` (``1<=C<=99, 1<=D<=9``)
    as an actuator and ``libplug_thermo_tr.so`` dereferences a NULL list on
    STATE REQUEST. ``C == 0`` (e.g. ``11#4#00``, ``11#4#0``) is safe.
    """
    if not raw_where or raw_where.startswith("#") or "#" not in raw_where:
        return False
    parts = raw_where.split("#")
    if not all(part and part.isascii() and part.isdigit() for part in parts):  # pragma: no cover - ASCII validated
        return False
    if len(parts) == 2:
        return 0 <= int(parts[0]) <= 99 and 1 <= int(parts[1]) <= 9
    if len(parts) == 3:
        return 1 <= int(parts[1]) <= 99 and 1 <= int(parts[2]) <= 9
    if len(parts) == 4:
        return 1 <= int(parts[2]) <= 99 and 1 <= int(parts[3]) <= 9
    return False


_MH202_WHO4_RULE_IDS: frozenset[str] = frozenset(
    {
        "mh202_who4_all_probes_001_099",
        "mh202_who4_status_numeric_invalid",
        "mh202_who4_status_actuator_hash",
    }
)


@dataclass(frozen=True, slots=True)
class _StaticModelHandler:
    """Minimal handler stub carrying a fixed ``_model_key``."""

    _model_key: str


_MH202_STATIC_HANDLER = _StaticModelHandler(_model_key="MH202")


def _is_mh202_who1_status_where_crash(raw_where: str) -> bool:
    """True for WHO 1 dimensionless status WHERE forms that crash MH202.

    Matches ``1111``, ``<addr>#4`` (including leading-zero ``#04`` and multi-hop
    trailing ``#4``), and any ``#4#<IF>`` hop with ``int(IF) >= 19``
    (``own-firmware-oracle`` ``lights-where-extended.cases``,
    ``lights-where-forms.cases``). Group addresses like ``#4`` / ``#04`` and
    routed interfaces ``0..18`` (including ``<addr>#4#04``) are excluded.
    """
    if raw_where == "1111":
        return True
    if "#" not in raw_where:
        return False
    parts = raw_where.split("#")
    if raw_where.startswith("#"):
        if len(parts) < 3 or not parts[1]:
            return False
        wp = parts[2:]
    else:
        wp = parts[1:]
    idx = 0
    while idx < len(wp):
        if _is_token_int(wp[idx], 4):
            if idx + 1 >= len(wp):
                return True
            iface = wp[idx + 1]
            if not (iface.isascii() and iface.isdigit()) or int(iface) >= 19:
                return True
            idx += 2
        else:
            idx += 1
    return False


def _is_mh202_who4_crash_frame(frame: str) -> bool:
    """True when ``frame`` triggers any WHO 4 daemon crash on MH202 (010024)."""
    verdict = evaluate_frame_safety(_MH202_STATIC_HANDLER, frame, follow_routing=False)
    return bool(
        verdict.blocked
        and verdict.severity == "crash"
        and verdict.rule_id in _MH202_WHO4_RULE_IDS
    )


def _extract_frame_fields(
    clean: str, parsed: Any
) -> tuple[bool, int | None, int | None, tuple[str, ...], int | None, tuple[str, ...], tuple[str, ...], str, str | None]:
    """Extract canonical integer fields and raw wire tokens from a parsed frame."""
    is_dim_frame = clean.startswith("*#")
    who = _parsed_int(getattr(parsed, "who", getattr(parsed, "_who", None)))
    what = _parsed_int(getattr(parsed, "_what", getattr(parsed, "what", None)))
    what_params = tuple(str(p) for p in (getattr(parsed, "_what_param", None) or ()))
    dim = _parsed_int(getattr(parsed, "dimension", getattr(parsed, "_dimension", None)))
    dim_values = tuple(str(v) for v in (getattr(parsed, "dimension_value", None) or ()))
    where_params = tuple(str(p) for p in where_param(parsed))

    if is_dim_frame:
        raw_fields = clean[2:-2].split("*")
        raw_where = raw_fields[1] if len(raw_fields) >= 2 else ""
        raw_dim_token = raw_fields[2] if len(raw_fields) >= 3 else None
    else:
        raw_fields = clean[1:-2].split("*")
        raw_where = raw_fields[2] if len(raw_fields) >= 3 else ""
        raw_dim_token = None

    return (
        is_dim_frame,
        who,
        what,
        what_params,
        dim,
        dim_values,
        where_params,
        raw_where,
        raw_dim_token,
    )


def _who4_dim22_verdict(
    *,
    valid: bool,
    target_model: str,
    target_mac: str,
    delegated_from: str | None = None,
) -> FrameSafetyVerdict:
    """Build the blanket crash verdict for a refused WHO 4 dimension 22 frame."""
    from . import services as _services

    if _services.services_has_dimension_22_detector():
        reason = (
            "WHO 4 dimension 22 in this form crashes the gateway's thermostat daemon "
            "and is blocked for safety"
        )
    else:
        reason = (
            "WHO 4 dimension 22 frames are blocked as a precaution unless they are "
            "one of the published split forms this installation can recognise"
        )
    return FrameSafetyVerdict(
        valid=valid,
        blocked=True,
        severity="crash",
        rule_id="who4_dim22_no_actuator",
        reason=reason,
        scope="blanket",
        target_model=target_model,
        target_mac=target_mac,
        delegated_from=delegated_from,
        affected_models=_WHO4_DIM22_MODELS,
        oracle_ref="own-firmware-oracle/thermo-dim22",
    )


def _who18_dim52_verdict(
    ownd_reason: str | None,
    *,
    valid: bool,
    target_model: str,
    target_mac: str,
    delegated_from: str | None = None,
) -> FrameSafetyVerdict:
    """Build the blanket crash verdict for a refused WHO 18 dimension 52 frame."""
    return FrameSafetyVerdict(
        valid=valid,
        blocked=True,
        severity="crash",
        rule_id="who18_dim52_invalid_year",
        reason=(
            ownd_reason
            or "WHO 18 dimension 52 outside canonical 1-2 digit year and month 1..12 "
            "crashes F460, F461 and 3488 gateways and corrupts bus frames."
        ),
        scope="blanket",
        target_model=target_model,
        target_mac=target_mac,
        delegated_from=delegated_from,
        affected_models=_WHO18_DIM52_MODELS,
        oracle_ref="own-firmware-oracle/ownd-audit-frames",
    )


def _evaluate_blanket_rules(
    clean: str,
    parsed: Any,
    *,
    valid: bool,
    target_model: str,
    target_mac: str,
    delegated_from: str | None,
) -> FrameSafetyVerdict | None:
    """Evaluate all blanket crash rules on a parsed or raw-stubbed single frame."""
    from . import services as _services

    if _WHO1_EMPTY_WHERE_RE.fullmatch(clean) is not None:
        return FrameSafetyVerdict(
            valid=False,
            blocked=True,
            severity="crash",
            rule_id="who1_empty_where",
            reason="Empty WHO 1 WHERE (*#1*##) is invalid and crashes the gateway daemon on MH202.",
            scope="blanket",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_WHO1_EMPTY_WHERE_MODELS,
            oracle_ref="own-firmware-oracle/ownd-audit-frames",
        )

    (
        is_dim_frame,
        who,
        what,
        what_params,
        dim,
        dim_values,
        where_params,
        raw_where,
        raw_dim_token,
    ) = _extract_frame_fields(clean, parsed)

    # 1. WHO 4 dimension 22 without canonical split-unit actuator form
    if _services.is_refused_dimension_22(clean) or (
        is_dim_frame
        and who == 4
        and dim == 22
        and _WHO4_DIM22_CANONICAL_PREFIX_RE.fullmatch(clean) is None
    ):
        return _who4_dim22_verdict(
            valid=valid,
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
        )

    # 2. WHO 18 dimension 52 invalid year / write / non-canonical spelling
    if is_dim_frame and who == 18 and dim == 52:
        ownd_reason = _services.destructive_frame_reason(clean)
        if ownd_reason is not None or _WHO18_DIM52_CANONICAL_RE.fullmatch(clean) is None:
            return _who18_dim52_verdict(
                ownd_reason,
                valid=valid,
                target_model=target_model,
                target_mac=target_mac,
                delegated_from=delegated_from,
            )

    # 3. WHO 22 bare WHAT commands (zero gateways forward these without #params)
    if (
        not is_dim_frame
        and who == 22
        and what is not None
        and bool(raw_where)
        and not what_params
        and what in _WHO22_BARE_WHAT_BLANKET
    ):
        return FrameSafetyVerdict(
            valid=valid,
            blocked=True,
            severity="crash",
            rule_id="who22_bare_what_all",
            reason=(
                f"WHO 22 bare WHAT {what} without #parameters crashes the sound daemon "
                "(use the parameterized form with #params)."
            ),
            scope="blanket",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_WHO22_BARE_ALL_MODELS,
            oracle_ref="own-firmware-oracle/sound-who22-what",
        )

    # 4. WHO 22 status/dimension frame with unstructured WHERE (exempt WHERE == "6")
    if is_dim_frame and who == 22 and bool(raw_where) and not where_params and raw_where != "6":
        return FrameSafetyVerdict(
            valid=valid,
            blocked=True,
            severity="crash",
            rule_id="who22_unstructured_where",
            reason=(
                f"WHO 22 status/dimension frame with unstructured WHERE '{raw_where}' "
                "crashes the sound daemon (use structured WHERE like 2#S, 3#A#P, 4#A, 5#... or 6)."
            ),
            scope="blanket",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_WHO22_UNSTRUCTURED_WHERE_MODELS,
            oracle_ref="own-firmware-oracle/who-forwarding-matrix",
        )

    # 5. WHO 4 dimension 14 write with >= 5-digit setpoint temperature
    is_write = bool(getattr(parsed, "is_dimension_writing", False)) or (
        raw_dim_token is not None and raw_dim_token.startswith("#")
    )
    if (
        is_dim_frame
        and who == 4
        and dim == 14
        and is_write
        and dim_values
        and len(dim_values[0]) >= 5
    ):
        return FrameSafetyVerdict(
            valid=valid,
            blocked=True,
            severity="crash",
            rule_id="who4_dim14_5digit_setpoint",
            reason=(
                f"WHO 4 dimension 14 setpoint write '{dim_values[0]}' has {len(dim_values[0])} digits "
                "(spec mandates 4-digit 0050..0400) and crashes 3488, F460 and F461 gateways."
            ),
            scope="blanket",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_WHO4_DIM14_5DIGIT_MODELS,
            oracle_ref="own-firmware-oracle/thermo-setpoint-writes-high",
        )

    # 6. Fallback hook for any additional OWNd destructive frame classifier
    ownd_reason = _services.destructive_frame_reason(clean)
    if ownd_reason is not None:
        return FrameSafetyVerdict(
            valid=valid,
            blocked=True,
            severity="crash",
            rule_id="ownd_destructive_frame",
            reason=ownd_reason,
            scope="blanket",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=tuple(sorted(ALL_EMULATED_GATEWAYS)),
            oracle_ref="own-firmware-oracle/ownd-audit-frames",
        )

    return None


def _match_per_gateway_crash_rule(
    clean: str, parsed: Any
) -> tuple[str, str, tuple[str, ...], str] | None:
    """Return ``(rule_id, reason_template, affected_models, oracle_ref)`` if a per-gateway crash rule matches."""
    (
        is_dim_frame,
        who,
        what,
        what_params,
        dim,
        _dim_values,
        _where_params,
        raw_where,
        _raw_dim_token,
    ) = _extract_frame_fields(clean, parsed)

    if (
        not is_dim_frame
        and who == 22
        and what is not None
        and bool(raw_where)
        and not what_params
        and what in _WHO22_BARE_WHAT_PER_GATEWAY
    ):
        return (
            "who22_bare_what_33_44_45",
            f"WHO 22 bare WHAT {what} without #parameters crashes the sound daemon",
            _WHO22_BARE_33_44_45_MODELS,
            "own-firmware-oracle/sound-who22-what",
        )

    if is_dim_frame and who == 13 and dim in (10, 11, 12):
        return (
            "who13_network_dims_10_11_12",
            "WHO 13 network dimensions 10, 11 and 12 crash the gateway daemon",
            _WHO13_NET_DIM_MODELS,
            "own-firmware-oracle/who13-identification",
        )

    if is_dim_frame and who == 13 and dim == 50:
        return (
            "who13_dim50",
            "WHO 13 dimension 50 crashes the gateway daemon",
            _WHO13_DIM50_MODELS,
            "own-firmware-oracle/gateway-who13-dimension-range",
        )

    if is_dim_frame and who == 4:
        if _MH202_ALL_PROBES_WHERE_RE.fullmatch(raw_where) is not None:
            return (
                "mh202_who4_all_probes_001_099",
                "WHO 4 3-digit all-probes WHERE (001..099) crashes the thermostat daemon",
                _MH202_MODELS,
                "own-firmware-oracle/ownd-audit-frames",
            )
        if dim is None and _is_mh202_who4_status_numeric_invalid(raw_where):
            return (
                "mh202_who4_status_numeric_invalid",
                f"WHO 4 dimensionless status WHERE '{raw_where}' crashes the thermostat daemon",
                _MH202_MODELS,
                "own-firmware-oracle/thermo-where-forms",
            )
        if dim is None and _is_mh202_who4_status_actuator_hash(raw_where):
            return (
                "mh202_who4_status_actuator_hash",
                f"WHO 4 dimensionless status actuator WHERE '{raw_where}' crashes the thermostat daemon",
                _MH202_MODELS,
                "own-firmware-oracle/thermo-where-forms",
            )

    if is_dim_frame and who == 1 and dim is None and _is_mh202_who1_status_where_crash(raw_where):
        return (
            "mh202_who1_status_where",
            f"WHO 1 dimensionless status WHERE '{raw_where}' crashes the gateway daemon",
            _MH202_MODELS,
            "own-firmware-oracle/lights-where-extended",
        )

    return None


def _evaluate_noreply_warnings(
    clean: str,
    parsed: Any,
    *,
    target_model: str,
    target_mac: str,
    delegated_from: str | None,
) -> FrameSafetyVerdict | None:
    """Evaluate non-blocking no-reply / session-stall caution rules for ``target_model``."""
    if not target_model:
        return None
    (
        is_dim_frame,
        who,
        what,
        _what_params,
        dim,
        _dim_values,
        _where_params,
        _raw_where,
        raw_dim_token,
    ) = _extract_frame_fields(clean, parsed)

    if not is_dim_frame and who == 1 and what == 19 and target_model in _NOREPLY_WHO1_WHAT19_MODELS:
        return FrameSafetyVerdict(
            valid=True,
            blocked=False,
            severity="warning",
            rule_id="noreply_who1_what19",
            reason=(
                f"WHO 1 WHAT 19 produces no ACK/NACK reply on {target_model} and may stall the command session."
            ),
            scope="gateway",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_NOREPLY_WHO1_WHAT19_MODELS,
            oracle_ref="own-firmware-oracle/lights-no-reply",
        )

    is_write = bool(getattr(parsed, "is_dimension_writing", False)) or (
        raw_dim_token is not None and raw_dim_token.startswith("#")
    )
    if is_dim_frame and who == 14 and not is_write and target_model == "MH202":
        return FrameSafetyVerdict(
            valid=True,
            blocked=False,
            severity="warning",
            rule_id="noreply_who14_request",
            reason=(
                "WHO 14 status/dimension requests produce no ACK/NACK reply on MH202 and may stall the command session."
            ),
            scope="gateway",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_MH202_MODELS,
            oracle_ref="own-firmware-oracle/actuator-lock-no-reply",
        )

    if (
        not is_dim_frame
        and who == 22
        and what is not None
        and (what in (54, 60) or what >= 99)
        and target_model == "MH202"
    ):
        return FrameSafetyVerdict(
            valid=True,
            blocked=False,
            severity="warning",
            rule_id="noreply_who22_high_what",
            reason=(
                f"WHO 22 WHAT {what} produces no ACK/NACK reply on MH202 and may stall the command session."
            ),
            scope="gateway",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_MH202_MODELS,
            oracle_ref="own-firmware-oracle/sound-who22-no-reply",
        )

    if is_dim_frame and who == 13 and dim == 27 and target_model in _NOREPLY_WHO13_DIM27_MODELS:
        return FrameSafetyVerdict(
            valid=True,
            blocked=False,
            severity="warning",
            rule_id="noreply_who13_dim27",
            reason=(
                f"WHO 13 dimension 27 can stall the command session on {target_model} "
                "(no ACK when unpopulated)."
            ),
            scope="gateway",
            target_model=target_model,
            target_mac=target_mac,
            delegated_from=delegated_from,
            affected_models=_NOREPLY_WHO13_DIM27_MODELS,
            oracle_ref="own-firmware-oracle/who13-identification",
        )

    return None


@dataclass(frozen=True, slots=True)
class _RawFrameStub:
    """Wire-token fallback representation for ASCII frames rejected by OWNMessage.parse."""

    who: int | None = None
    _what: int | None = None
    _what_param: tuple[str, ...] = ()
    dimension: int | None = None
    dimension_value: tuple[str, ...] = ()
    is_dimension_writing: bool = False
    _where_param: tuple[str, ...] = ()


def _raw_where_params(raw_where: str) -> tuple[str, ...]:
    """Extract ``#``-parameters after the base address or ``#group`` token."""
    body = raw_where[1:] if raw_where.startswith("#") else raw_where
    parts = body.split("#")
    return tuple(parts[1:]) if len(parts) > 1 else ()


def _build_raw_frame_stub(clean: str) -> _RawFrameStub:
    """Extract wire-level integer/parameter tokens from an unparseable ASCII frame."""
    if clean.startswith("*#"):
        fields = clean[2:-2].split("*")
        who = _parsed_int(fields[0]) if fields else None
        raw_where = fields[1] if len(fields) >= 2 else ""
        dim: int | None = None
        is_write = False
        dim_values: tuple[str, ...] = ()
        if len(fields) >= 3:
            is_write = fields[2].startswith("#")
            dim_head = (fields[2][1:] if is_write else fields[2]).split("#", 1)[0]
            dim = _parsed_int(dim_head)
            dim_values = tuple(fields[3:])
        return _RawFrameStub(
            who=who,
            dimension=dim,
            dimension_value=dim_values,
            is_dimension_writing=is_write,
            _where_param=_raw_where_params(raw_where),
        )
    fields = clean[1:-2].split("*")
    if len(fields) < 3:
        return _RawFrameStub(who=_parsed_int(fields[0]) if fields else None)
    what_parts = fields[1].split("#")
    return _RawFrameStub(
        who=_parsed_int(fields[0]),
        _what=_parsed_int(what_parts[0]),
        _what_param=tuple(what_parts[1:]) if len(what_parts) > 1 else (),
        _where_param=_raw_where_params(fields[2]),
    )


def _invalid_format_verdict(clean: str, *, base_model: str, base_mac: str) -> FrameSafetyVerdict:
    """Return the standard invalid_frame_format verdict."""
    return FrameSafetyVerdict(
        valid=False,
        blocked=True,
        severity="invalid",
        rule_id="invalid_frame_format",
        reason=f"Invalid OpenWebNet frame format: {clean}",
        target_model=base_model,
        target_mac=base_mac,
    )


def _parse_or_raw_stub(
    clean: str, *, base_model: str, base_mac: str
) -> tuple[Any, bool, FrameSafetyVerdict | None]:
    """Validate single-frame ASCII syntax and parse via OWNd, falling back to a wire-token stub."""
    if not clean or not is_valid_ascii_own_frame(clean):
        return None, False, _invalid_format_verdict(clean, base_model=base_model, base_mac=base_mac)

    try:
        parsed = OWNMessage.parse(clean)
    except Exception:
        return _build_raw_frame_stub(clean), False, None

    if parsed is None:
        try:
            fallback = OWNMessage(clean)
        except Exception:  # pragma: no cover - defensive against future OWNd changes
            fallback = None
        if (
            fallback is not None
            and not isinstance(fallback, OWNSignaling)
            and getattr(fallback, "is_valid", False)
        ):
            parsed = fallback

    if isinstance(parsed, OWNSignaling):
        return None, False, FrameSafetyVerdict(
            valid=False,
            blocked=True,
            severity="invalid",
            rule_id="signaling_frame",
            reason=f"Signaling frames ({clean}) cannot be sent as commands.",
            target_model=base_model,
            target_mac=base_mac,
        )

    if parsed is not None and getattr(parsed, "is_valid", False):
        return parsed, True, None

    return _build_raw_frame_stub(clean), False, None


def evaluate_blanket_crash_safety(frame: str) -> FrameSafetyVerdict:
    """Evaluate syntax and blanket crash rules without a gateway handler."""
    clean = str(frame).strip()
    parsed, is_valid, hard_verdict = _parse_or_raw_stub(clean, base_model="", base_mac="")
    if hard_verdict is not None:
        return hard_verdict

    blanket = _evaluate_blanket_rules(
        clean,
        parsed,
        valid=is_valid,
        target_model="",
        target_mac="",
        delegated_from=None,
    )
    if blanket is not None:
        return blanket

    if not is_valid:
        return _invalid_format_verdict(clean, base_model="", base_mac="")

    return FrameSafetyVerdict(valid=True, blocked=False)


def evaluate_frame_safety(
    handler: Any,
    frame: str,
    *,
    follow_routing: bool = True,
) -> FrameSafetyVerdict:
    """Evaluate ``frame`` against syntax, blanket crash, per-gateway crash, and caution rules."""
    clean = str(frame).strip()
    base_model = _handler_model_key(handler)
    base_mac = _handler_mac(handler)

    parsed, is_valid, hard_verdict = _parse_or_raw_stub(
        clean, base_model=base_model, base_mac=base_mac
    )
    if hard_verdict is not None:
        return hard_verdict

    if follow_routing and handler is not None:
        target_handler, delegated_from = resolve_effective_target(handler, parsed)
    else:
        target_handler, delegated_from = handler, None

    target_model = _handler_model_key(target_handler)
    target_mac = _handler_mac(target_handler)

    blanket = _evaluate_blanket_rules(
        clean,
        parsed,
        valid=is_valid,
        target_model=target_model,
        target_mac=target_mac,
        delegated_from=delegated_from,
    )
    if blanket is not None:
        return blanket

    gw_match = _match_per_gateway_crash_rule(clean, parsed)
    if gw_match is not None:
        rule_id, base_reason, affected_models, oracle_ref = gw_match
        if target_model in affected_models:
            return FrameSafetyVerdict(
                valid=is_valid,
                blocked=True,
                severity="crash",
                rule_id=rule_id,
                reason=f"{base_reason} on {target_model}.",
                scope="gateway",
                target_model=target_model,
                target_mac=target_mac,
                delegated_from=delegated_from,
                affected_models=affected_models,
                oracle_ref=oracle_ref,
            )
        if is_valid and target_model not in ALL_EMULATED_GATEWAYS:
            model_desc = target_model or "unknown"
            return FrameSafetyVerdict(
                valid=True,
                blocked=False,
                severity="warning",
                rule_id="unknown_gateway_model_specific",
                reason=(
                    f"{base_reason} on {', '.join(affected_models)}; target gateway model "
                    f"({model_desc}) is not emulated in the Firmware Oracle."
                ),
                scope="gateway",
                target_model=target_model,
                target_mac=target_mac,
                delegated_from=delegated_from,
                affected_models=affected_models,
                oracle_ref=oracle_ref,
            )

    if not is_valid:
        return _invalid_format_verdict(clean, base_model=base_model, base_mac=base_mac)

    noreply = _evaluate_noreply_warnings(
        clean,
        parsed,
        target_model=target_model,
        target_mac=target_mac,
        delegated_from=delegated_from,
    )
    if noreply is not None:
        return noreply

    return FrameSafetyVerdict(
        valid=True,
        blocked=False,
        target_model=target_model,
        target_mac=target_mac,
        delegated_from=delegated_from,
    )


def gateway_crash_frame_reason(handler: Any, frame: str) -> str | None:
    """Return a refusal reason when ``frame`` triggers a per-gateway crash rule on ``handler``."""
    verdict = evaluate_frame_safety(handler, frame)
    if verdict.blocked and verdict.severity == "crash" and verdict.scope == "gateway":
        return verdict.reason
    return None


def active_gateway_protections(handler: Any) -> dict[str, Any]:
    """Return a JSON-serializable summary of active crash protections for ``handler``."""
    from .gateway import KNOWN_GATEWAY_MODELS

    model_key = _handler_model_key(handler)
    model_known = bool(model_key and model_key in KNOWN_GATEWAY_MODELS)
    gateway_rules = [
        {
            "rule_id": rule.rule_id,
            "summary": rule.summary,
            "oracle_ref": rule.oracle_ref,
        }
        for rule in _GATEWAY_RULE_CATALOG
        if model_key and model_key in rule.affected_models
    ]

    delegated_peers: list[dict[str, Any]] = []
    if handler is not None:
        delegated_away = getattr(handler, "delegated_away_whos", None)
        get_sec = _declared_callable(handler, "_get_secondary_for_who")
        if (
            isinstance(delegated_away, (set, frozenset, list, tuple))
            and get_sec is not None
        ):
            by_peer: dict[int, tuple[Any, list[int]]] = {}
            for raw_who in delegated_away:
                who_int = _parsed_int(raw_who)
                if who_int is None:  # pragma: no cover - delegated_away_whos holds ints
                    continue
                try:
                    peer = get_sec(who_int)
                except Exception:  # pragma: no cover - defensive against partial doubles
                    peer = None
                if peer is None or peer is handler:  # pragma: no cover - defensive
                    continue
                entry = by_peer.setdefault(id(peer), (peer, []))
                entry[1].append(who_int)
            for peer, whos in by_peer.values():
                peer_key = _handler_model_key(peer)
                delegated_peers.append(
                    {
                        "role": "secondary",
                        "model_key": peer_key if peer_key in KNOWN_GATEWAY_MODELS else "",
                        "mac": _handler_mac(peer),
                        "whos": sorted(set(whos)),
                    }
                )

        get_standby = _declared_callable(handler, "_get_standby_gateway")
        if get_standby is not None:
            try:
                standby = get_standby()
            except Exception:  # pragma: no cover - defensive against partial doubles
                standby = None
            if standby is not None and standby is not handler:
                standby_key = _handler_model_key(standby)
                delegated_peers.append(
                    {
                        "role": "standby",
                        "model_key": standby_key if standby_key in KNOWN_GATEWAY_MODELS else "",
                        "mac": _handler_mac(standby),
                        "active": getattr(handler, "failover_active", False) is True,
                    }
                )

    return {
        "model_key": model_key if model_known else "",
        "model_known": model_known,
        "blanket_rule_count": BLANKET_RULE_COUNT,
        "gateway_rules": gateway_rules,
        "delegated_peers": delegated_peers,
    }
