"""Tests for unified gateway-aware OpenWebNet frame safety engine."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_NAME
from OWNd.connection import get_gateway_profile
from OWNd.message import OWNMessage

from custom_components.myhome import services
from custom_components.myhome.const import ATTR_GATEWAY, ATTR_MESSAGE, DOMAIN
from custom_components.myhome.frame_safety import (
    FrameSafetyVerdict,
    _declared_callable,
    _handler_display_model,
    _handler_mac,
    _handler_model_key,
    _is_mh202_who1_status_where_crash,
    _is_mh202_who4_crash_frame,
    _is_mh202_who4_status_actuator_hash,
    _is_mh202_who4_status_numeric_invalid,
    _parsed_int,
    active_gateway_protections,
    evaluate_blanket_crash_safety,
    evaluate_frame_safety,
    gateway_crash_frame_reason,
    is_valid_ascii_own_frame,
    resolve_effective_target,
)
from custom_components.myhome.gateway import (
    KNOWN_GATEWAY_MODELS,
    _profile_for_model,
    _resolve_model_key,
)
from custom_components.myhome.gateway_sessions import CommandWorkerPool
from custom_components.myhome.where_grammar import validate_thermo_trigger_command

ALL_13_MODELS: tuple[str, ...] = (
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
)

WHO22_BARE_ALL_WHATS: tuple[int, ...] = (
    0,
    1,
    2,
    19,
    20,
    21,
    22,
    23,
    *range(36, 44),
    *range(46, 54),
    57,
)

WHO22_33_44_45_CRASH_MODELS: tuple[str, ...] = (
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
WHO22_33_44_45_SAFE_MODELS: tuple[str, ...] = tuple(
    m for m in ALL_13_MODELS if m not in WHO22_33_44_45_CRASH_MODELS
)

WHO13_10_11_12_CRASH_MODELS: tuple[str, ...] = ("H4684", "MH200")
WHO13_10_11_12_SAFE_MODELS: tuple[str, ...] = tuple(
    m for m in ALL_13_MODELS if m not in WHO13_10_11_12_CRASH_MODELS
)

WHO13_DIM50_CRASH_MODELS: tuple[str, ...] = ("F453", "F453AV", "MH200N")
WHO13_DIM50_SAFE_MODELS: tuple[str, ...] = tuple(
    m for m in ALL_13_MODELS if m not in WHO13_DIM50_CRASH_MODELS
)

NON_MH202_MODELS: tuple[str, ...] = tuple(m for m in ALL_13_MODELS if m != "MH202")


def _make_handler(
    model: str = "",
    *,
    mac: str = "00:03:50:aa:bb:cc",
    delegated: Any = None,
    failover: Any = None,
) -> SimpleNamespace:
    """Create a lightweight gateway handler stub for safety-engine tests."""
    return SimpleNamespace(
        _model_key=model,
        mac=mac,
        gateway=SimpleNamespace(model_name=model or "Generic", model=model or "Generic"),
        config_entry=None,
        _delegated_target=delegated if callable(delegated) else (lambda _msg: delegated),
        _failover_target=failover if callable(failover) else (lambda _msg: failover),
    )


# ---------------------------------------------------------------------------
# 1. Hard-blocked blanket crash rules (Rules 1-4, 6-7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", (*ALL_13_MODELS, ""))
@pytest.mark.parametrize(
    "frame",
    [
        "*#4*1*22##",
        "*#4*0*22##",
        "*#4*#0*22##",
        "*#4*#1*22##",
        "*#4*2#1*22##",
        "*#4*2*#22*1*0200*1*0##",
        "*#4*#0*#22*1*0200*1*0##",
    ],
)
def test_rule_who4_dim22_no_actuator_blocked_everywhere(model: str, frame: str) -> None:
    """Rule 1: WHO 4 dim 22 without canonical split WHERE is blanket-blocked."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who4_dim22_no_actuator"
    assert verdict.scope == "blanket"
    assert verdict.reason

    blanket = evaluate_blanket_crash_safety(frame)
    assert blanket.blocked is True
    assert blanket.rule_id == "who4_dim22_no_actuator"


@pytest.mark.parametrize("model", (*ALL_13_MODELS, ""))
@pytest.mark.parametrize(
    "frame",
    [
        "*#18*51*52#2026#10##",
        "*#18*51*#52*1##",
        "*#18*51*52#26#13##",
    ],
)
def test_rule_who18_dim52_invalid_year_blocked_everywhere(model: str, frame: str) -> None:
    """Rule 2: WHO 18 dim 52 outside canonical 1-2 digit year / month 1..12 is blanket-blocked."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who18_dim52_invalid_year"
    assert verdict.scope == "blanket"

    blanket = evaluate_blanket_crash_safety(frame)
    assert blanket.blocked is True
    assert blanket.rule_id == "who18_dim52_invalid_year"


def test_rule_who18_dim52_canonical_year_month_allowed() -> None:
    """Canonical 2-digit year + valid month WHO 18 dim 52 request is allowed."""
    verdict = evaluate_frame_safety(_make_handler("F461"), "*#18*51*52#26#10##")
    assert verdict.valid is True
    assert verdict.blocked is False


@pytest.mark.parametrize("model", (*ALL_13_MODELS, ""))
def test_rule_who1_empty_where_blocked_everywhere(model: str) -> None:
    """Rule 3: *#1*## is blanket-blocked with valid=False, severity='crash'."""
    verdict = evaluate_frame_safety(_make_handler(model), "*#1*##")
    assert verdict.valid is False
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who1_empty_where"
    assert verdict.scope == "blanket"

    blanket = evaluate_blanket_crash_safety("*#1*##")
    assert blanket.valid is False
    assert blanket.blocked is True
    assert blanket.severity == "crash"
    assert blanket.rule_id == "who1_empty_where"


@pytest.mark.parametrize("what", WHO22_BARE_ALL_WHATS)
def test_rule_who22_bare_what_all_every_what_blocked(what: int) -> None:
    """Rule 4: Every bare WHO 22 WHAT in {0,1,2,19..23,36..43,46..53,57} is blanket-blocked."""
    frame = f"*22*{what}*3#1#1##"
    blanket = evaluate_blanket_crash_safety(frame)
    assert blanket.blocked is True
    assert blanket.severity == "crash"
    assert blanket.rule_id == "who22_bare_what_all"
    assert blanket.scope == "blanket"


@pytest.mark.parametrize("model", (*ALL_13_MODELS, ""))
@pytest.mark.parametrize("frame", ["*22*0*3#1#1##", "*22*1*1##", "*22*19*3#1#1##", "*22*36*3#1#1##", "*22*57*3#1#1##"])
def test_rule_who22_bare_what_all_blocked_across_all_models(model: str, frame: str) -> None:
    """Rule 4 blocks across all 13 emulated gateways and unknown gateways."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who22_bare_what_all"
    assert verdict.scope == "blanket"


@pytest.mark.parametrize("model", (*ALL_13_MODELS, ""))
@pytest.mark.parametrize("frame", ["*#22*31##", "*#22*31*1##", "*#22*06##", "*#22*1##", "*#22*0##"])
def test_rule_who22_unstructured_where_blocked_everywhere(model: str, frame: str) -> None:
    """Rule 6: Unstructured WHO 22 status/dim WHERE (except '6') is blanket-blocked."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who22_unstructured_where"
    assert verdict.scope == "blanket"

    blanket = evaluate_blanket_crash_safety(frame)
    assert blanket.blocked is True
    assert blanket.rule_id == "who22_unstructured_where"


@pytest.mark.parametrize("model", (*ALL_13_MODELS, ""))
@pytest.mark.parametrize(
    "frame",
    [
        "*#4*1*#14*02500*1##",
        "*#4*#1*#14*02150*3##",
        "*#4*1*#14*123456*1##",
    ],
)
def test_rule_who4_dim14_5digit_setpoint_blocked_everywhere(model: str, frame: str) -> None:
    """Rule 7: WHO 4 dim 14 write with >= 5-digit setpoint is blanket-blocked."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who4_dim14_5digit_setpoint"
    assert verdict.scope == "blanket"

    blanket = evaluate_blanket_crash_safety(frame)
    assert blanket.blocked is True
    assert blanket.rule_id == "who4_dim14_5digit_setpoint"


@pytest.mark.parametrize("frame", ["*#4*1*#14*0215*1##", "*#4*1*#14*0250*3##", "*#4*1*14##"])
def test_rule_who4_dim14_valid_4digit_setpoint_allowed(frame: str) -> None:
    """Valid 4-digit WHO 4 dim 14 setpoint writes and dim 14 reads remain allowed."""
    verdict = evaluate_frame_safety(_make_handler("F461"), frame)
    assert verdict.valid is True
    assert verdict.blocked is False


# ---------------------------------------------------------------------------
# 2. Hard-blocked per-gateway crash rules (Rules 5, 8-13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("what", (33, 44, 45))
@pytest.mark.parametrize("model", WHO22_33_44_45_CRASH_MODELS)
def test_rule_who22_bare_what_33_44_45_blocked_on_crashing_models(model: str, what: int) -> None:
    """Rule 5: Bare WHO 22 WHAT 33/44/45 is blocked on all 9 crashing gateways."""
    verdict = evaluate_frame_safety(_make_handler(model), f"*22*{what}*3#1#1##")
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who22_bare_what_33_44_45"
    assert verdict.scope == "gateway"
    assert verdict.target_model == model


@pytest.mark.parametrize("what", (33, 44, 45))
@pytest.mark.parametrize("model", WHO22_33_44_45_SAFE_MODELS)
def test_rule_who22_bare_what_33_44_45_allowed_on_forwarding_models(model: str, what: int) -> None:
    """Rule 5: Bare WHO 22 WHAT 33/44/45 is allowed on MH202, F450, H4684, MH200."""
    verdict = evaluate_frame_safety(_make_handler(model), f"*22*{what}*3#1#1##")
    assert verdict.valid is True
    assert verdict.blocked is False


@pytest.mark.parametrize("dim", (10, 11, 12))
@pytest.mark.parametrize("model", WHO13_10_11_12_CRASH_MODELS)
def test_rule_who13_network_dims_10_11_12_blocked_on_crashing_models(model: str, dim: int) -> None:
    """Rule 8: WHO 13 dims 10/11/12 are blocked on H4684 and MH200."""
    verdict = evaluate_frame_safety(_make_handler(model), f"*#13**{dim}##")
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who13_network_dims_10_11_12"
    assert verdict.scope == "gateway"
    assert verdict.target_model == model


@pytest.mark.parametrize("dim", (10, 11, 12))
@pytest.mark.parametrize("model", WHO13_10_11_12_SAFE_MODELS)
def test_rule_who13_network_dims_10_11_12_allowed_on_other_models(model: str, dim: int) -> None:
    """Rule 8: WHO 13 dims 10/11/12 are safe on the other 11 emulated gateways."""
    verdict = evaluate_frame_safety(_make_handler(model), f"*#13**{dim}##")
    assert verdict.valid is True
    assert verdict.blocked is False


@pytest.mark.parametrize("model", WHO13_DIM50_CRASH_MODELS)
def test_rule_who13_dim50_blocked_on_crashing_models(model: str) -> None:
    """Rule 9: WHO 13 dim 50 is blocked on F453, F453AV, and MH200N."""
    verdict = evaluate_frame_safety(_make_handler(model), "*#13**50##")
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who13_dim50"
    assert verdict.scope == "gateway"
    assert verdict.target_model == model


@pytest.mark.parametrize("model", WHO13_DIM50_SAFE_MODELS)
def test_rule_who13_dim50_allowed_on_other_models(model: str) -> None:
    """Rule 9: WHO 13 dim 50 is safe on the other 10 emulated gateways."""
    verdict = evaluate_frame_safety(_make_handler(model), "*#13**50##")
    assert verdict.valid is True
    assert verdict.blocked is False


@pytest.mark.parametrize(
    "frame",
    [
        "*#4*001##",
        "*#4*001*0##",
        "*#4*001*#14*0215*1##",
        "*#4*001#4#01##",
        "*#4*009##",
        "*#4*099*0##",
    ],
)
def test_rule_mh202_who4_all_probes_001_099_blocked_on_mh202(frame: str) -> None:
    """Rule 10: 3-digit 001..099 WHO 4 requests crash MH202 regardless of dimension."""
    verdict = evaluate_frame_safety(_make_handler("MH202"), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "mh202_who4_all_probes_001_099"
    assert verdict.scope == "gateway"
    assert verdict.target_model == "MH202"


@pytest.mark.parametrize("model", NON_MH202_MODELS)
def test_rule_mh202_who4_all_probes_001_099_allowed_on_other_models(model: str) -> None:
    """Rule 10: 3-digit 001..099 WHO 4 requests are safe on all 12 non-MH202 gateways."""
    for frame in ("*#4*001##", "*#4*001*0##"):
        verdict = evaluate_frame_safety(_make_handler(model), frame)
        assert verdict.valid is True
        assert verdict.blocked is False


@pytest.mark.parametrize("frame", ["*#4*000##", "*#4*100##", "*#4*0001##", "*#4*0099##", "*#4*00005##"])
def test_rule_mh202_who4_status_numeric_invalid_blocked_on_mh202(frame: str) -> None:
    """Rule 11: Dimensionless WHO 4 numeric WHERE in {000, 100} or len>=4 0..99 crashes MH202."""
    verdict = evaluate_frame_safety(_make_handler("MH202"), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "mh202_who4_status_numeric_invalid"
    assert verdict.scope == "gateway"
    assert verdict.target_model == "MH202"


@pytest.mark.parametrize("model", NON_MH202_MODELS)
def test_rule_mh202_who4_status_numeric_invalid_allowed_on_other_models(model: str) -> None:
    """Rule 11: Dimensionless WHO 4 numeric status forms are safe on non-MH202 models."""
    for frame in ("*#4*000##", "*#4*100##", "*#4*0001##"):
        verdict = evaluate_frame_safety(_make_handler(model), frame)
        assert verdict.valid is True
        assert verdict.blocked is False


@pytest.mark.parametrize(
    "frame",
    [
        "*#4*2#1##",
        "*#4*0#1##",
        "*#4*99#9##",
        "*#4*2#4#01##",
        "*#4*3#1#2##",
        "*#4*2#1#4#01##",
        "*#4*1#2#3#4##",
    ],
)
def test_rule_mh202_who4_status_actuator_hash_blocked_on_mh202(frame: str) -> None:
    """Rule 12: Dimensionless WHO 4 non-#-prefixed actuator hash forms (1<=C<=9) crash MH202."""
    verdict = evaluate_frame_safety(_make_handler("MH202"), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "mh202_who4_status_actuator_hash"
    assert verdict.scope == "gateway"
    assert verdict.target_model == "MH202"


@pytest.mark.parametrize("model", NON_MH202_MODELS)
def test_rule_mh202_who4_status_actuator_hash_allowed_on_other_models(model: str) -> None:
    """Rule 12: Actuator hash dimensionless status forms are safe on non-MH202 models."""
    for frame in ("*#4*2#1##", "*#4*2#4#01##"):
        verdict = evaluate_frame_safety(_make_handler(model), frame)
        assert verdict.valid is True
        assert verdict.blocked is False


@pytest.mark.parametrize(
    "frame",
    [
        "*#1*1111##",
        "*#1*11#4##",
        "*#1*0#4##",
        "*#1*11#4#19##",
        "*#1*11#4#99##",
        "*#1*0#4#20##",
    ],
)
def test_rule_mh202_who1_status_where_blocked_on_mh202(frame: str) -> None:
    """Rule 13: Dimensionless WHO 1 status WHERE 1111, <addr>#4, <addr>#4#>=19 crashes MH202."""
    verdict = evaluate_frame_safety(_make_handler("MH202"), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "mh202_who1_status_where"
    assert verdict.scope == "gateway"
    assert verdict.target_model == "MH202"


@pytest.mark.parametrize("model", NON_MH202_MODELS)
def test_rule_mh202_who1_status_where_allowed_on_other_models(model: str) -> None:
    """Rule 13: WHO 1 status WHERE forms are forwarded/safe on all 12 non-MH202 gateways."""
    for frame in ("*#1*1111##", "*#1*11#4##", "*#1*11#4#19##"):
        verdict = evaluate_frame_safety(_make_handler(model), frame)
        assert verdict.valid is True
        assert verdict.blocked is False


# ---------------------------------------------------------------------------
# 3. False-positive regressions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ALL_13_MODELS)
@pytest.mark.parametrize(
    "frame",
    [
        "*#22*6##",
        "*#22*6*12##",
        "*#22*2#1##",
        "*22*36#1*3#1#1##",
        "*22*33#3*2#1##",
        "*22*1#1#1*3#1#1##",
        "*22*0#1#1*3#1#1##",
        "*22*19#1*3#1#1##",
        "*#4*3#1#1*22##",
    ],
)
def test_false_positive_regressions_allowed_across_all_models(model: str, frame: str) -> None:
    """Valid WHO 22 WHERE=6, structured WHERE, parameterized WHATs, and WHO 4 split reads pass."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.valid is True
    assert verdict.blocked is False
    assert verdict.severity is None


@pytest.mark.parametrize(
    "frame",
    [
        "*#4*11#4#00##",
        "*#4*11#4#0##",
        "*#4*2#0##",
        "*#4*2#1#4#00##",
        "*#4*100*15##",
        "*#4*000*0##",
        "*#4*0001*0##",
        "*#4*101##",
        "*#4*0100*0##",
        "*#4*#0##",
        "*#4*#0#1##",
        "*#4*2#1*20##",
        "*#4*2#4#12##",
        "*#4*3#0#2##",
        "*#4*1#2#0#4##",
        "*#1*11#4#18##",
        "*#1*11#4#01##",
        "*#1*9999##",
        "*1*1*11#4#19##",
        "*#1*11#4#19*1##",
    ],
)
def test_false_positive_regressions_allowed_on_mh202(frame: str) -> None:
    """Confirmed-safe MH202 WHO 4 (C==0, dim reads, 101) and WHO 1 (IF<=18, 9999, cmds) pass."""
    verdict = evaluate_frame_safety(_make_handler("MH202"), frame)
    assert verdict.valid is True
    assert verdict.blocked is False
    assert verdict.severity is None


def test_who4_dim22_split_blocked_when_ownd_detector_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When services._is_dimension_22_crash_form is None, split form falls back to blanket refusal."""
    monkeypatch.setattr(services, "_is_dimension_22_crash_form", None)
    verdict = evaluate_frame_safety(_make_handler("F461"), "*#4*3#1#1*22##")
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who4_dim22_no_actuator"


# ---------------------------------------------------------------------------
# 4. Leading-zero bypass closures & embedded '##' / invalid / signaling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "frame", "expected_rule"),
    [
        ("H4684", "*#013**10##", "who13_network_dims_10_11_12"),
        ("MH200", "*#13**010##", "who13_network_dims_10_11_12"),
        ("MH200N", "*#13**050##", "who13_dim50"),
        ("F453", "*#013**050##", "who13_dim50"),
        ("MH202", "*#04*001##", "mh202_who4_all_probes_001_099"),
        ("MH202", "*#01*1111##", "mh202_who1_status_where"),
        ("F461", "*22*01*3#1#1##", "who22_bare_what_all"),
        ("F461", "*022*1*3#1#1##", "who22_bare_what_all"),
        ("F461", "*022*33*3#1#1##", "who22_bare_what_33_44_45"),
        ("F461", "*#04*2*#22*1*0215*2*0##", "who4_dim22_no_actuator"),
        ("F461", "*#4*2*#022*1*0215*2*0##", "who4_dim22_no_actuator"),
        ("F461", "*#04*1*#014*02500*1##", "who4_dim14_5digit_setpoint"),
    ],
)
def test_leading_zero_bypass_closures(model: str, frame: str, expected_rule: str) -> None:
    """Leading zeroes in WHO/WHAT/DIM cannot bypass crash rules."""
    verdict = evaluate_frame_safety(_make_handler(model), frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == expected_rule


@pytest.mark.parametrize(
    "multi_frame",
    [
        "*1*1*11##*#13**10##",
        "*1*1*11##*1*0*11##",
        "*1*1*11##*#4*1*22##",
    ],
)
def test_embedded_double_hash_multi_frame_rejected(multi_frame: str) -> None:
    """Concatenated multi-frame payloads with embedded '##' are rejected everywhere."""
    assert is_valid_ascii_own_frame(multi_frame) is False
    assert services.is_valid_ascii_own_frame(multi_frame) is False

    verdict = evaluate_frame_safety(_make_handler("H4684"), multi_frame)
    assert verdict.valid is False
    assert verdict.blocked is True
    assert verdict.severity == "invalid"
    assert verdict.rule_id == "invalid_frame_format"

    blanket = evaluate_blanket_crash_safety(multi_frame)
    assert blanket.valid is False
    assert blanket.blocked is True
    assert blanket.severity == "invalid"
    assert blanket.rule_id == "invalid_frame_format"


@pytest.mark.parametrize("sig_frame", ["*#*1##", "*#*0##", "*#123456789##"])
def test_signaling_frames_rejected(sig_frame: str) -> None:
    """ACK, NACK, and NONCE signaling frames are rejected as signaling_frame."""
    verdict = evaluate_frame_safety(_make_handler("F461"), sig_frame)
    assert verdict.valid is False
    assert verdict.blocked is True
    assert verdict.severity == "invalid"
    assert verdict.rule_id == "signaling_frame"


@pytest.mark.parametrize("bad_frame", ["", "   ", "not_a_frame", "*1*2##", "*foo*bar##", "*1*1*\u0661\u0662##"])
def test_invalid_frame_formats_rejected(bad_frame: str) -> None:
    """Syntactically invalid or non-ASCII frames are rejected as invalid_frame_format."""
    verdict = evaluate_frame_safety(_make_handler("F461"), bad_frame)
    assert verdict.valid is False
    assert verdict.blocked is True
    assert verdict.severity == "invalid"
    assert verdict.rule_id == "invalid_frame_format"


# ---------------------------------------------------------------------------
# 5. Model normalization for 3488 / HOMETOUCH / 067259 / SMARTDES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_name",
    [
        "3488",
        "BTicino 3488",
        "HOMETOUCH",
        "BTicino HomeTouch",
        "067259",
        "67259",
        "Legrand 067259",
        "SMARTDES",
        "SmartDES",
    ],
)
def test_3488_model_normalization_and_crash_protection(raw_name: str) -> None:
    """3488 and its HOMETOUCH / 067259 / SMARTDES aliases resolve to model key '3488'."""
    assert "3488" in KNOWN_GATEWAY_MODELS
    assert _resolve_model_key(raw_name) == "3488"

    handler = SimpleNamespace(
        gateway=SimpleNamespace(model_name=raw_name, model=raw_name, profile=get_gateway_profile("")),
        config_entry=SimpleNamespace(data={CONF_NAME: raw_name}, title=raw_name),
        mac="00:03:50:34:88:01",
    )
    assert services._handler_model_key(handler) == "3488"

    verdict = evaluate_frame_safety(handler, "*22*33*3#1#1##")
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "who22_bare_what_33_44_45"
    assert verdict.target_model == "3488"


# ---------------------------------------------------------------------------
# 6. Shared-bus delegation, failover routing, WHO 13 locality, _who4_target fallback
# ---------------------------------------------------------------------------


def test_delegated_and_failover_routing_who1_who4_who22() -> None:
    """WHO 1, WHO 4, and WHO 22 follow _delegated_target and _failover_target."""
    mh202 = _make_handler("MH202", mac="00:03:50:00:02:02")
    f461 = _make_handler("F461", mac="00:03:50:00:04:61")

    # Delegation: F461 delegates WHO 1 and WHO 4 to MH202
    f461_delegating = _make_handler(
        "F461",
        mac="00:03:50:00:04:61",
        delegated=lambda msg: mh202 if getattr(msg, "who", None) in (1, 4) else None,
    )
    v_who1 = evaluate_frame_safety(f461_delegating, "*#1*1111##")
    assert v_who1.blocked is True
    assert v_who1.rule_id == "mh202_who1_status_where"
    assert v_who1.target_model == "MH202"
    assert v_who1.delegated_from == "F461"

    v_who4 = evaluate_frame_safety(f461_delegating, "*#4*001##")
    assert v_who4.blocked is True
    assert v_who4.rule_id == "mh202_who4_all_probes_001_099"
    assert v_who4.target_model == "MH202"
    assert v_who4.delegated_from == "F461"

    # Delegation: MH202 delegates WHO 22 to F461
    mh202_delegating = _make_handler(
        "MH202",
        mac="00:03:50:00:02:02",
        delegated=lambda msg: f461 if getattr(msg, "who", None) == 22 else None,
    )
    v_who22 = evaluate_frame_safety(mh202_delegating, "*22*33*3#1#1##")
    assert v_who22.blocked is True
    assert v_who22.rule_id == "who22_bare_what_33_44_45"
    assert v_who22.target_model == "F461"
    assert v_who22.delegated_from == "MH202"

    # Failover: F461 fails over to MH202 when _delegated_target returns None
    f461_failover = _make_handler(
        "F461",
        mac="00:03:50:00:04:61",
        delegated=None,
        failover=lambda _msg: mh202,
    )
    v_fo = evaluate_frame_safety(f461_failover, "*#4*2#1##")
    assert v_fo.blocked is True
    assert v_fo.rule_id == "mh202_who4_status_actuator_hash"
    assert v_fo.target_model == "MH202"
    assert v_fo.delegated_from == "F461"


def test_who13_stays_local_to_primary_ignoring_delegation_and_failover() -> None:
    """WHO 13 / 1013 are gateway-local and never delegate or fail over."""
    f461 = _make_handler("F461", mac="00:03:50:00:04:61")
    h4684 = _make_handler(
        "H4684",
        mac="00:03:50:00:46:84",
        delegated=lambda _msg: f461,
        failover=lambda _msg: f461,
    )
    parsed_13 = OWNMessage.parse("*#13**10##")
    target, delegated_from = resolve_effective_target(h4684, parsed_13)
    assert target is h4684
    assert delegated_from is None

    v_h4684 = evaluate_frame_safety(h4684, "*#13**10##")
    assert v_h4684.blocked is True
    assert v_h4684.rule_id == "who13_network_dims_10_11_12"
    assert v_h4684.target_model == "H4684"
    assert v_h4684.delegated_from is None

    # Reverse: F461 delegating everything to H4684 still evaluates WHO 13 locally on F461 (safe)
    f461_with_h4684 = _make_handler(
        "F461",
        mac="00:03:50:00:04:61",
        delegated=lambda _msg: h4684,
        failover=lambda _msg: h4684,
    )
    v_f461 = evaluate_frame_safety(f461_with_h4684, "*#13**10##")
    assert v_f461.valid is True
    assert v_f461.blocked is False
    assert v_f461.target_model == "F461"
    assert v_f461.delegated_from is None


def test_resolve_effective_target_cycle_guard() -> None:
    """Cyclic delegation links terminate safely without infinite recursion."""
    a = _make_handler("F461", mac="00:03:50:00:00:01")
    b = _make_handler("MH202", mac="00:03:50:00:00:02")
    a._delegated_target = lambda _msg: b
    b._delegated_target = lambda _msg: a

    target, _ = resolve_effective_target(a, OWNMessage.parse("*#4*001##"))
    assert target in (a, b)


def test_mock_handler_who4_target_fallback() -> None:
    """Mock handlers providing only _who4_target() route WHO 4 but leave other WHOs local."""
    mh202_peer = SimpleNamespace(_model_key="MH202", mac="00:03:50:00:02:02")
    f461_stub = SimpleNamespace(
        _model_key="F461",
        mac="00:03:50:00:04:61",
        _who4_target=lambda: mh202_peer,
    )
    v_who4 = evaluate_frame_safety(f461_stub, "*#4*001##")
    assert v_who4.blocked is True
    assert v_who4.rule_id == "mh202_who4_all_probes_001_099"
    assert v_who4.target_model == "MH202"
    assert v_who4.delegated_from == "F461"

    # WHO 1 stays on F461 (not routed via _who4_target)
    v_who1 = evaluate_frame_safety(f461_stub, "*#1*1111##")
    assert v_who1.valid is True
    assert v_who1.blocked is False
    assert v_who1.target_model == "F461"
    assert v_who1.delegated_from is None


# ---------------------------------------------------------------------------
# 7. Non-blocking warning verdicts (4 no-reply rules + unknown gateway)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frame", "warn_models", "clean_model", "expected_rule"),
    [
        ("*1*19*31##", ("H4684", "MH200"), "F461", "noreply_who1_what19"),
        ("*#14*1##", ("MH202",), "F461", "noreply_who14_request"),
        ("*#14*1*0##", ("MH202",), "F461", "noreply_who14_request"),
        ("*22*54*3#1#1##", ("MH202",), "F461", "noreply_who22_high_what"),
        ("*22*60*3#1#1##", ("MH202",), "F461", "noreply_who22_high_what"),
        ("*22*99*3#1#1##", ("MH202",), "F461", "noreply_who22_high_what"),
        ("*22*120*3#1#1##", ("MH202",), "F461", "noreply_who22_high_what"),
        ("*#13**27##", ("MH200", "MH200N", "MYHOMESERVER1"), "F461", "noreply_who13_dim27"),
    ],
)
def test_noreply_warning_verdicts(
    frame: str,
    warn_models: tuple[str, ...],
    clean_model: str,
    expected_rule: str,
) -> None:
    """No-reply frames produce non-blocking amber warnings on affected models and clean verdicts elsewhere."""
    for model in warn_models:
        verdict = evaluate_frame_safety(_make_handler(model), frame)
        assert verdict.valid is True
        assert verdict.blocked is False
        assert verdict.severity == "warning"
        assert verdict.rule_id == expected_rule
        assert verdict.reason

    clean = evaluate_frame_safety(_make_handler(clean_model), frame)
    assert clean.valid is True
    assert clean.blocked is False
    assert clean.severity is None


@pytest.mark.parametrize(
    ("frame", "expected_subset"),
    [
        ("*#13**10##", {"H4684", "MH200"}),
        ("*#13**50##", {"F453", "F453AV", "MH200N"}),
        ("*#4*001##", {"MH202"}),
        ("*#4*000##", {"MH202"}),
        ("*#4*2#1##", {"MH202"}),
        ("*#1*1111##", {"MH202"}),
        ("*22*33*3#1#1##", {"3488", "F453", "F453AV", "F454", "F459", "F460", "F461", "MH200N", "MYHOMESERVER1"}),
    ],
)
def test_unknown_gateway_warns_on_model_specific_rules(
    frame: str,
    expected_subset: set[str],
) -> None:
    """Unknown/Generic gateways allow model-specific rules with an amber caution listing affected models."""
    verdict = evaluate_frame_safety(_make_handler(""), frame)
    assert verdict.valid is True
    assert verdict.blocked is False
    assert verdict.severity == "warning"
    assert verdict.rule_id == "unknown_gateway_model_specific"
    assert expected_subset <= set(verdict.affected_models)
    payload = verdict.to_dict()
    assert isinstance(payload["affected_models"], list)
    assert expected_subset <= set(payload["affected_models"])


def test_verdict_to_dict_and_active_gateway_protections() -> None:
    """FrameSafetyVerdict.to_dict() and active_gateway_protections() expose structured metadata."""
    mh202 = _make_handler("MH202", mac="00:03:50:00:02:02")
    verdict = evaluate_frame_safety(mh202, "*#4*001##")
    assert isinstance(verdict, FrameSafetyVerdict)
    as_dict = verdict.to_dict()
    assert as_dict["blocked"] is True
    assert as_dict["severity"] == "crash"
    assert as_dict["rule_id"] == "mh202_who4_all_probes_001_099"
    assert isinstance(as_dict["affected_models"], list)

    prot_mh202 = active_gateway_protections(mh202)
    assert prot_mh202["model_key"] == "MH202"
    assert prot_mh202["model_known"] is True
    assert prot_mh202["blanket_rule_count"] >= 6
    rule_ids = {r["rule_id"] for r in prot_mh202["gateway_rules"]}
    assert {
        "mh202_who4_all_probes_001_099",
        "mh202_who4_status_numeric_invalid",
        "mh202_who4_status_actuator_hash",
        "mh202_who1_status_where",
    } <= rule_ids
    for rule in prot_mh202["gateway_rules"]:
        assert {"rule_id", "summary", "oracle_ref"} <= set(rule)

    prot_unknown = active_gateway_protections(_make_handler(""))
    assert prot_unknown["model_key"] == ""
    assert prot_unknown["model_known"] is False
    assert prot_unknown["blanket_rule_count"] >= 6
    assert prot_unknown["gateway_rules"] == []
    assert isinstance(prot_unknown["delegated_peers"], list)


# ---------------------------------------------------------------------------
# 8. validate_thermo_trigger_command() blanket crash enforcement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_trigger",
    [
        "*#04*2*#22*1*0215*2*0##",
        "*#4*2*#022*1*0215*2*0##",
        "*22*1*3#1#1##",
        "*22*01*3#1#1##",
        "*22*36*3#1#1##",
        "*#4*1*#14*02500*1##",
        "*#18*51*52#2026#10##",
        "*#22*31##",
        "*1*1*12##*#13**10##",
    ],
)
def test_validate_thermo_trigger_command_rejects_crash_and_bypass_frames(bad_trigger: str) -> None:
    """validate_thermo_trigger_command() refuses leading-zero dim 22, bare WHO 22, 5-digit SP, WHO 18 dim 52."""
    with pytest.raises(ValueError):
        validate_thermo_trigger_command(bad_trigger)


@pytest.mark.parametrize(
    "good_trigger",
    [
        "*#4*3#1#1*#22*0*0215*1*0##",
        "*1*1*12##",
        "*#4*1*#14*0215*1##",
    ],
)
def test_validate_thermo_trigger_command_allows_valid_triggers(good_trigger: str) -> None:
    """Valid split-unit, lighting, and 4-digit setpoint triggers pass validation."""
    assert validate_thermo_trigger_command(good_trigger) == good_trigger


# ---------------------------------------------------------------------------
# 9. CommandWorkerPool defense-in-depth pre-wire refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "crash_frame"),
    [
        ("MH202", "*#4*001##"),
        ("MH202", "*#1*1111##"),
        ("H4684", "*#13**10##"),
    ],
)
async def test_command_worker_pool_refuses_crash_frame_before_wire(
    hass: Any,
    model: str,
    crash_frame: str,
) -> None:
    """CommandWorkerPool refuses crash frames on the transmitting handler before record_frame or socket send."""
    ready = asyncio.Event()
    ready.set()

    bus_monitor = MagicMock()
    bus_monitor.record_frame = MagicMock()

    handler = SimpleNamespace(
        hass=hass,
        _model_key=model,
        mac="00:03:50:de:ad:42",
        log_id=f"[{model}]",
        command_session_idle_timeout=5.0,
        bus_monitor=bus_monitor,
        config_entry=None,
        gateway=SimpleNamespace(
            model_name=model,
            model=model,
            profile=SimpleNamespace(max_queue_size=50, command_queue_delay=0.0),
        ),
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
        _on_auth_refused=MagicMock(),
    )

    fake_session = MagicMock()
    fake_session.connect = AsyncMock(return_value={"Success": True, "Message": None})
    fake_session.send = AsyncMock(return_value=True)
    fake_session.close = AsyncMock()
    fake_session.last_error = None

    pool = CommandWorkerPool(handler, event_session_ready=ready)

    with (
        patch("custom_components.myhome.gateway.OWNCommandSession", return_value=fake_session),
        patch("custom_components.myhome.gateway._session_is_open", return_value=True),
    ):
        worker_task = asyncio.create_task(pool.sending_loop(0))
        try:
            written_fut = await pool.send(OWNMessage.parse(crash_frame))
            await pool.send_buffer.put(None)
            await asyncio.wait_for(worker_task, timeout=2.0)
        finally:
            if not worker_task.done():
                worker_task.cancel()

    bus_monitor.record_frame.assert_not_called()
    fake_session.send.assert_not_awaited()
    assert written_fut.cancelled() is True
    assert getattr(written_fut, "_not_written", False) is True


async def test_command_worker_pool_continues_sending_safe_frames_after_refusal(hass: Any) -> None:
    """After refusing a crash frame, CommandWorkerPool keeps running and sends subsequent safe frames."""
    ready = asyncio.Event()
    ready.set()

    bus_monitor = MagicMock()
    bus_monitor.record_frame = MagicMock()

    handler = SimpleNamespace(
        hass=hass,
        _model_key="MH202",
        mac="00:03:50:de:ad:43",
        log_id="[MH202]",
        command_session_idle_timeout=5.0,
        bus_monitor=bus_monitor,
        config_entry=None,
        gateway=SimpleNamespace(
            model_name="MH202",
            model="MH202",
            profile=SimpleNamespace(max_queue_size=50, command_queue_delay=0.0),
        ),
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
        _on_auth_refused=MagicMock(),
    )

    fake_session = MagicMock()
    fake_session.connect = AsyncMock(return_value={"Success": True, "Message": None})
    fake_session.send = AsyncMock(return_value=True)
    fake_session.close = AsyncMock()
    fake_session.last_error = None

    pool = CommandWorkerPool(handler, event_session_ready=ready)

    with (
        patch("custom_components.myhome.gateway.OWNCommandSession", return_value=fake_session),
        patch("custom_components.myhome.gateway._session_is_open", return_value=True),
    ):
        worker_task = asyncio.create_task(pool.sending_loop(0))
        try:
            bad_fut = await pool.send(OWNMessage.parse("*#4*001##"))
            ok_fut = await pool.send(OWNMessage.parse("*1*1*12##"))
            await pool.send_buffer.put(None)
            await asyncio.wait_for(worker_task, timeout=2.0)
        finally:
            if not worker_task.done():
                worker_task.cancel()

    assert bad_fut.cancelled() is True
    assert getattr(bad_fut, "_not_written", False) is True
    assert ok_fut.done() is True
    assert ok_fut.cancelled() is False
    bus_monitor.record_frame.assert_called_once()
    assert bus_monitor.record_frame.call_args.kwargs["raw"] == "*1*1*12##"
    fake_session.send.assert_awaited_once()


# ---------------------------------------------------------------------------
# 10. Edge-case helpers, fallbacks, delegated_peers, and send_message warning
# ---------------------------------------------------------------------------


def test_internal_parsing_handler_and_classifier_edge_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover internal coercion, None-handler, display-model, WHO 1/4 helper, and OWNd fallback branches."""
    # _parsed_int across None, bool, int, numeric string, non-numeric string
    assert _parsed_int(None) is None
    assert _parsed_int(True) is None
    assert _parsed_int(False) is None
    assert _parsed_int(4) == 4
    assert _parsed_int(" 13 ") == 13
    assert _parsed_int("abc") is None
    assert _parsed_int("") is None

    # _declared_callable distinguishes explicit MagicMock children from unconfigured auto-mocks
    plain_mock = MagicMock()
    assert _declared_callable(plain_mock, "_delegated_target") is None
    plain_mock._delegated_target.return_value = None
    assert _declared_callable(plain_mock, "_delegated_target") is plain_mock._delegated_target

    # None handler paths
    assert _handler_model_key(None) == ""
    assert _handler_mac(None) == ""
    assert resolve_effective_target(None, OWNMessage.parse("*1*1*12##")) == (None, None)
    v_none_clean = evaluate_frame_safety(None, "*1*1*12##")
    assert v_none_clean.valid is True
    assert v_none_clean.blocked is False
    assert v_none_clean.target_model == ""
    assert v_none_clean.target_mac == ""

    # _handler_mac config_entry fallback & empty fallback
    from homeassistant.const import CONF_MAC

    mac_from_cfg = SimpleNamespace(
        mac=None,
        config_entry=SimpleNamespace(data={CONF_MAC: " 00:03:50:11:22:33 "}),
    )
    assert _handler_mac(mac_from_cfg) == "00:03:50:11:22:33"
    assert _handler_mac(SimpleNamespace(mac="", config_entry=None)) == ""

    # _handler_display_model fallbacks (raw gateway model_name -> mac -> "primary")
    unknown_with_name = SimpleNamespace(
        _model_key="",
        mac="",
        gateway=SimpleNamespace(model_name="Generic", model="Generic"),
        model=None,
        config_entry=None,
    )
    assert _handler_display_model(unknown_with_name) == "Generic"
    unknown_with_mac = SimpleNamespace(
        _model_key="",
        mac="00:03:50:99:88:77",
        gateway=None,
        model=None,
        config_entry=None,
    )
    assert _handler_display_model(unknown_with_mac) == "00:03:50:99:88:77"
    unknown_bare = SimpleNamespace(
        _model_key="",
        mac="",
        gateway=None,
        model=None,
        config_entry=None,
    )
    assert _handler_display_model(unknown_bare) == "primary"

    # Delegation from unknown primary uses _handler_display_model for delegated_from
    mh202 = _make_handler("MH202", mac="00:03:50:00:02:02")
    unknown_with_mac._delegated_target = lambda _msg: mh202
    v_del_unknown = evaluate_frame_safety(unknown_with_mac, "*#4*001##")
    assert v_del_unknown.blocked is True
    assert v_del_unknown.delegated_from == "00:03:50:99:88:77"

    # follow_routing=False evaluates strictly against primary handler
    f461_delegating = _make_handler("F461", delegated=lambda _msg: mh202)
    v_local = evaluate_frame_safety(f461_delegating, "*#4*001##", follow_routing=False)
    assert v_local.valid is True
    assert v_local.blocked is False
    assert v_local.target_model == "F461"
    assert v_local.delegated_from is None

    # WHO 4 numeric/hash & WHO 1 helper edge cases
    assert _is_mh202_who4_status_numeric_invalid("") is False
    assert _is_mh202_who4_status_numeric_invalid("0000") is False
    assert _is_mh202_who4_status_numeric_invalid("12") is False
    assert _is_mh202_who4_status_actuator_hash("2#abc") is False
    assert _is_mh202_who4_status_actuator_hash("1#2#3#4#5") is False
    assert _is_mh202_who1_status_where_crash("") is False
    assert _is_mh202_who1_status_where_crash("#4") is False
    assert _is_mh202_who1_status_where_crash("11#2") is False
    assert evaluate_frame_safety(mh202, "*#1*#4##").blocked is False
    assert evaluate_frame_safety(mh202, "*#1*11#2##").blocked is False
    assert _is_mh202_who4_crash_frame("*1*1*12##") is False
    assert _is_mh202_who4_crash_frame("*#*1##") is False
    assert _is_mh202_who4_crash_frame("*#1*12##") is False
    assert gateway_crash_frame_reason(_make_handler("F461"), "*1*1*12##") is None

    # OWNd destructive_frame_reason fallback rule (ownd_destructive_frame)
    monkeypatch.setattr(
        services,
        "_ownd_destructive_frame_reason",
        lambda _frame: "synthetic OWNd destructive reason",
    )
    v_ownd = evaluate_frame_safety(_make_handler("F461"), "*1*1*12##")
    assert v_ownd.blocked is True
    assert v_ownd.severity == "crash"
    assert v_ownd.rule_id == "ownd_destructive_frame"
    assert v_ownd.scope == "blanket"
    assert "synthetic OWNd destructive reason" in (v_ownd.reason or "")

    # OWNMessage.parse error & None fallback branches (wire-token fallback still protects crash frames)
    monkeypatch.setattr(services, "_ownd_destructive_frame_reason", None)
    with patch("custom_components.myhome.frame_safety.OWNMessage.parse", side_effect=ValueError("boom")):
        v_parse_err = evaluate_frame_safety(_make_handler("F461"), "*1*1*12##")
        assert v_parse_err.valid is False
        assert v_parse_err.blocked is True
        assert v_parse_err.rule_id == "invalid_frame_format"
        assert _is_mh202_who4_crash_frame("*#4*001##") is True
        assert _is_mh202_who4_crash_frame("*#4*1*0##") is False

    with patch("custom_components.myhome.frame_safety.OWNMessage.parse", return_value=None):
        v_fallback_ok = evaluate_frame_safety(_make_handler("F461"), "*999*999*999##")
        assert v_fallback_ok.valid is True
        assert v_fallback_ok.blocked is False

        v_fallback_bad = evaluate_frame_safety(_make_handler("F461"), "*1*2##")
        assert v_fallback_bad.valid is False
        assert v_fallback_bad.blocked is True
        assert v_fallback_bad.rule_id == "invalid_frame_format"

    # Unemulated known model (F455) gets unknown_gateway_model_specific warning on model-specific rule
    v_f455 = evaluate_frame_safety(_make_handler("F455"), "*#13**10##")
    assert v_f455.valid is True
    assert v_f455.blocked is False
    assert v_f455.severity == "warning"
    assert v_f455.rule_id == "unknown_gateway_model_specific"


def test_active_gateway_protections_delegated_and_standby_peers() -> None:
    """active_gateway_protections summarizes delegated secondary peers and standby failover peers."""
    mh202 = _make_handler("MH202", mac="00:03:50:00:02:02")
    standby = _make_handler("H4684", mac="00:03:50:00:46:84")

    primary = SimpleNamespace(
        _model_key="F461",
        mac="00:03:50:00:04:61",
        gateway=SimpleNamespace(model_name="F461", model="F461"),
        config_entry=None,
        delegated_away_whos=[4, "1", "bad", 22, 99],
        failover_active=True,
    )
    primary._get_secondary_for_who = lambda who: {
        1: mh202,
        4: mh202,
        22: None,
        99: primary,
    }.get(who)
    primary._get_standby_gateway = lambda: standby

    prot = active_gateway_protections(primary)
    assert prot["model_key"] == "F461"
    assert prot["model_known"] is True
    assert prot["delegated_peers"] == [
        {
            "role": "secondary",
            "model_key": "MH202",
            "mac": "00:03:50:00:02:02",
            "whos": [1, 4],
        },
        {
            "role": "standby",
            "model_key": "H4684",
            "mac": "00:03:50:00:46:84",
            "active": True,
        },
    ]

    # None standby and None handler produce empty delegated_peers
    primary._get_standby_gateway = lambda: None
    primary.delegated_away_whos = []
    assert active_gateway_protections(primary)["delegated_peers"] == []
    assert active_gateway_protections(None)["model_known"] is False


async def test_send_message_logs_warning_and_sends_noreply_caution_frame(
    hass: Any,
    attach_gateway: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """myhome.send_message logs a warning and still transmits non-blocking caution frames."""
    await services.async_setup_services(hass)
    h4684 = _make_handler("H4684", mac="00:03:50:19:46:84")
    h4684.log_id = "[H4684]"
    h4684.send = AsyncMock()
    h4684.send_status_request = AsyncMock()
    attach_gateway("00:03:50:19:46:84", h4684)

    with caplog.at_level("WARNING"):
        await hass.services.async_call(
            DOMAIN,
            services.SERVICE_SEND_MESSAGE,
            {ATTR_GATEWAY: "00:03:50:19:46:84", ATTR_MESSAGE: "*1*19*31##"},
            blocking=True,
        )

    h4684.send.assert_awaited_once()
    assert any("noreply_who1_what19" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# 11. Audit regressions (Findings 1-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "non_model_raw",
    [
        "MyHOME 192.168.34.88",
        "MyHOME 00:03:50:12:34:88",
        "MyHOME 00:03:50:ab:34:88",
        "MyHOME 00-03-50-12-34-88",
        "MyHOME 00-03-50-f4-61-01",
        "MyHOME 192.168.1.10:3488",
        "MyHOME 192.168.1.10:4890",
        "MyHOME 192.168.1.10:067259",
        "MyHOME 3488:20000",
        "MyHOME [fe80::3488]:20000",
        "MyHOME [fe80::a3488]:20000",
        "MyHOME [fe80::f461]:20000",
        "MyHOME fe80::f454",
        "MyHOME 2001:db8::f453",
        "Port 13488",
        "Port 34880",
        "34.88",
        "34:88",
        "06:72:59",
        "67.259",
        "00:03:50:af:46:10",
    ],
)
def test_numeric_model_resolution_rejects_ips_macs_ports_and_adjacent_digits(
    non_model_raw: str,
) -> None:
    """Finding 1: IPv4/IPv6 addresses, MAC octets, :port numbers, and longer numbers never resolve to a known model."""
    resolved = _resolve_model_key(non_model_raw)
    assert resolved not in KNOWN_GATEWAY_MODELS, f"{non_model_raw!r} falsely resolved to {resolved!r}"

    handler = SimpleNamespace(
        gateway=SimpleNamespace(model_name="Generic", model="Generic", profile=get_gateway_profile("")),
        config_entry=SimpleNamespace(data={CONF_NAME: "Generic"}, title=non_model_raw),
        mac="00:03:50:12:34:88",
    )
    assert _handler_model_key(handler) == ""
    verdict = evaluate_frame_safety(handler, "*22*33*3#1#1##")
    assert verdict.blocked is False
    assert verdict.severity == "warning"
    assert verdict.rule_id == "unknown_gateway_model_specific"


@pytest.mark.parametrize(
    ("valid_raw", "expected_key"),
    [
        ("3488", "3488"),
        ("Legrand 3488", "3488"),
        ("BTicino 3488 HOMETOUCH", "3488"),
        ("BTicino3488", "3488"),
        ("BTicino-3488", "3488"),
        ("BTicino 3488 (192.168.34.88)", "3488"),
        ("BTicino067259", "3488"),
        ("Legrand067292", "H4890"),
        ("BTicinoF461", "F461"),
        ("Home Touch", "3488"),
        ("BTicino Home Touch", "3488"),
        ("067259", "3488"),
        ("67259", "3488"),
        ("067292", "H4890"),
        ("67292", "H4890"),
        ("4890", "H4890"),
        ("H4890", "H4890"),
        ("003598", "F454"),
        ("03565", "MH200N"),
    ],
)
def test_numeric_and_aliased_valid_models_still_resolve(valid_raw: str, expected_key: str) -> None:
    """Finding 1: Standalone and brand-prefixed numeric model names/aliases continue to resolve."""
    from OWNd.profiles import GenericGatewayProfile, H4890Profile

    assert _resolve_model_key(valid_raw) == expected_key
    assert isinstance(_profile_for_model("067292"), H4890Profile)
    assert isinstance(_profile_for_model("06:72:92"), GenericGatewayProfile)
    assert isinstance(_profile_for_model("67.292"), GenericGatewayProfile)
    assert isinstance(_profile_for_model("[fe80::67292]"), GenericGatewayProfile)


@pytest.mark.parametrize(
    ("gw_name", "cfg_name", "cfg_title"),
    [
        ("Custom (IP Gateway)", "Custom (IP Gateway)", "Custom (IP Gateway) Gateway"),
        ("Unknown", "Unknown", "Unknown Gateway"),
        ("Unknown model", "Unknown model Gateway", "Unknown model Gateway"),
        ("MyHOME Gateway", "MyHOME Gateway", "MyHOME Gateway"),
        ("MyHOME", "MyHOME", "MyHOME"),
        ("MyHOME [fe80::3488]:20000", "Generic", "MyHOME [fe80::f461]:20000"),
        ("MyHOME fe80::1234", "Generic", "MyHOME fe80::1234"),
        ("MyHOME 00-03-50-12-34-88", "Generic", "MyHOME 00-03-50-12-34-88"),
        ("Generic Gateway", "Generic", "Basement Gateway"),
        ("Generic", "Basement Gateway", "Living Room Gateway"),
        ("Custom IP", "Custom IP", "Upstairs Hub"),
    ],
)
def test_onboarding_titles_and_custom_labels_do_not_leak_into_model_key(
    gw_name: str,
    cfg_name: str,
    cfg_title: str,
) -> None:
    """Finding 2: Onboarding Gateway suffixes, MyHOME placeholders, and custom room titles yield ''."""
    handler = SimpleNamespace(
        gateway=SimpleNamespace(model_name=gw_name, model=gw_name, profile=get_gateway_profile("")),
        config_entry=SimpleNamespace(data={CONF_NAME: cfg_name}, title=cfg_title),
        mac="00:03:50:aa:bb:cc",
    )
    assert _handler_model_key(handler) == ""
    prot = active_gateway_protections(handler)
    assert prot["model_key"] == ""
    assert prot["model_known"] is False

    # Direct hardware/mock non-placeholder _model_key fallback remains preserved
    exp = SimpleNamespace(_model_key="ExperimentalX99")
    assert _handler_model_key(exp) == "EXPERIMENTALX99"
    assert active_gateway_protections(exp)["model_known"] is False


@pytest.mark.parametrize(
    "crash_frame",
    [
        "*#1*11#04##",
        "*#1*11#004##",
        "*#1*0#04##",
        "*#1*#5#4##",
        "*#1*#5#04##",
        "*#1*11#04#19##",
        "*#1*11#004#019##",
        "*#1*#1#04#19##",
        "*#1*11#3#04##",
        "*#1*11#4#04#4##",
        "*#1*11#04#04#04##",
        "*#1*31#04#01#04##",
        "*#1*31#04#01#04#19##",
        "*#1*31#4#19#4#02##",
        "*#1*11#4#19#1##",
    ],
)
def test_mh202_who1_leading_zero_routing_token_blocked(crash_frame: str) -> None:
    """Finding 3: Leading-zero and multi-hop #4/#04 routing tokens in WHO 1 status WHERE are blocked on MH202."""
    verdict = evaluate_frame_safety(_make_handler("MH202"), crash_frame)
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == "mh202_who1_status_where"
    assert verdict.scope == "gateway"


@pytest.mark.parametrize(
    "safe_frame",
    [
        "*#1*#04##",
        "*#1*#4##",
        "*#1*#4#01##",
        "*#1*11#4#04##",
        "*#1*11#04#04##",
        "*#1*11#04#18##",
        "*#1*#1#4#04##",
        "*#1*31#04#01#04#04##",
        "*#1*11#4#01#1##",
    ],
)
def test_mh202_who1_group_04_and_interface_04_allowed(safe_frame: str) -> None:
    """Finding 3: Group #04 and bus interface 04 (<=18) remain allowed on MH202."""
    assert _is_mh202_who1_status_where_crash("##4") is False
    verdict = evaluate_frame_safety(_make_handler("MH202"), safe_frame)
    assert verdict.valid is True
    assert verdict.blocked is False
    assert verdict.severity is None


@pytest.mark.parametrize(
    ("frame", "expected_rule"),
    [
        ("*#4*2*#22##", "who4_dim22_no_actuator"),
        ("*#04*2*#22##", "who4_dim22_no_actuator"),
        ("*#4*2*#022##", "who4_dim22_no_actuator"),
        ("*#04*2*#022##", "who4_dim22_no_actuator"),
        ("*#4*3#1#1*#22##", "who4_dim22_no_actuator"),
        ("*#18*51*#52##", "who18_dim52_invalid_year"),
        ("*#018*51*#52##", "who18_dim52_invalid_year"),
        ("*#18*51*#052##", "who18_dim52_invalid_year"),
    ],
)
async def test_unparseable_ownd_crash_frames_preserve_crash_severity_everywhere(
    hass: Any,
    attach_gateway: Any,
    monkeypatch: pytest.MonkeyPatch,
    frame: str,
    expected_rule: str,
) -> None:
    """Finding 4: Unparseable WHO 4 dim 22 and WHO 18 dim 52 crash frames report severity='crash' across all gates."""
    from homeassistant.exceptions import ServiceValidationError

    await services.async_setup_services(hass)
    f461 = _make_handler("F461", mac="00:03:50:aa:04:61")
    f461.log_id = "[F461]"
    f461.send = AsyncMock()
    f461.send_status_request = AsyncMock()
    attach_gateway("00:03:50:aa:04:61", f461)

    verdict = evaluate_frame_safety(f461, frame)
    assert verdict.valid is False
    assert verdict.blocked is True
    assert verdict.severity == "crash"
    assert verdict.rule_id == expected_rule
    assert verdict.scope == "blanket"

    blanket = evaluate_blanket_crash_safety(frame)
    assert blanket.valid is False
    assert blanket.blocked is True
    assert blanket.severity == "crash"
    assert blanket.rule_id == expected_rule

    # myhome.send_message raises send_message_refused_frame (not send_message_invalid_frame)
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            services.SERVICE_SEND_MESSAGE,
            {ATTR_GATEWAY: "00:03:50:aa:04:61", ATTR_MESSAGE: frame},
            blocking=True,
        )
    assert exc_info.value.translation_key == "send_message_refused_frame"

    # validate_thermo_trigger_command surfaces the crash guard reason rather than generic regex failure
    with pytest.raises(ValueError) as trig_exc:
        validate_thermo_trigger_command(frame)
    if expected_rule == "who4_dim22_no_actuator":
        assert "crashes the gateway's thermostat daemon" in str(trig_exc.value)
    else:
        assert "Refusing OpenWebNet trigger command" in str(trig_exc.value)

    # Fallback paths when OWNd crash-form helpers are absent
    monkeypatch.setattr(services, "_is_dimension_22_crash_form", None)
    monkeypatch.setattr(services, "_ownd_destructive_frame_reason", None)
    fallback_verdict = evaluate_frame_safety(f461, frame)
    assert fallback_verdict.blocked is True
    assert fallback_verdict.severity == "crash"
    assert fallback_verdict.rule_id == expected_rule


async def test_unparseable_crash_frame_worker_pool_and_destructive_fallback(
    hass: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finding 4: CommandWorkerPool blocks raw OWNMessage('*#4*2*#22##') and unparseable ownd_destructive_frame works."""
    ready = asyncio.Event()
    ready.set()
    bus_monitor = MagicMock()
    bus_monitor.record_frame = MagicMock()
    handler = SimpleNamespace(
        hass=hass,
        _model_key="F461",
        mac="00:03:50:de:ad:44",
        log_id="[F461]",
        command_session_idle_timeout=5.0,
        bus_monitor=bus_monitor,
        config_entry=None,
        gateway=SimpleNamespace(
            model_name="F461",
            model="F461",
            profile=SimpleNamespace(max_queue_size=50, command_queue_delay=0.0),
        ),
        _delegated_target=lambda _msg: None,
        _failover_target=lambda _msg: None,
        _on_auth_refused=MagicMock(),
    )
    fake_session = MagicMock()
    fake_session.connect = AsyncMock(return_value={"Success": True, "Message": None})
    fake_session.send = AsyncMock(return_value=True)
    fake_session.close = AsyncMock()
    fake_session.last_error = None
    pool = CommandWorkerPool(handler, event_session_ready=ready)

    with (
        patch("custom_components.myhome.gateway.OWNCommandSession", return_value=fake_session),
        patch("custom_components.myhome.gateway._session_is_open", return_value=True),
    ):
        worker_task = asyncio.create_task(pool.sending_loop(0))
        try:
            raw_unparsed_msg: Any = OWNMessage("*#4*2*#22##")
            written_fut = await pool.send(raw_unparsed_msg)
            await pool.send_buffer.put(None)
            await asyncio.wait_for(worker_task, timeout=2.0)
        finally:
            if not worker_task.done():
                worker_task.cancel()

    bus_monitor.record_frame.assert_not_called()
    fake_session.send.assert_not_awaited()
    assert written_fut.cancelled() is True
    assert getattr(written_fut, "_not_written", False) is True

    # Short unparseable *# frame, unparseable per-gateway & bare WHO 22 frames, delegation, and ownd fallback
    assert evaluate_frame_safety(handler, "*#4*##").rule_id == "invalid_frame_format"
    assert evaluate_frame_safety(handler, "*22*1*##").rule_id == "invalid_frame_format"
    assert evaluate_frame_safety(handler, "*#22*##").rule_id == "invalid_frame_format"
    assert evaluate_frame_safety(handler, "*22*2*3#1#1*99##").rule_id == "who22_bare_what_all"
    assert evaluate_frame_safety(handler, "*#22*31*#11##").rule_id == "who22_unstructured_where"
    assert evaluate_blanket_crash_safety("*1*2##").rule_id == "invalid_frame_format"

    # Unparseable crash frames also resolve shared-bus delegation & per-gateway crash rules
    mh202 = _make_handler("MH202", mac="00:03:50:00:02:02")
    f461_del_mh202 = _make_handler(
        "F461",
        mac="00:03:50:00:04:61",
        delegated=lambda msg: mh202 if getattr(msg, "who", None) in (1, 4) else None,
    )
    v_del_dim22 = evaluate_frame_safety(f461_del_mh202, "*#4*2*#22##")
    assert v_del_dim22.severity == "crash"
    assert v_del_dim22.rule_id == "who4_dim22_no_actuator"
    assert v_del_dim22.target_model == "MH202"
    assert v_del_dim22.delegated_from == "F461"

    v_del_mh202_unparsed = evaluate_frame_safety(f461_del_mh202, "*#4*001*#20##")
    assert v_del_mh202_unparsed.valid is False
    assert v_del_mh202_unparsed.blocked is True
    assert v_del_mh202_unparsed.severity == "crash"
    assert v_del_mh202_unparsed.rule_id == "mh202_who4_all_probes_001_099"
    assert v_del_mh202_unparsed.target_model == "MH202"
    assert v_del_mh202_unparsed.delegated_from == "F461"
    assert _is_mh202_who4_crash_frame("*#4*001*#20##") is True

    # Same unparseable per-gateway frame on non-affected/unknown model stays blocked as invalid_frame_format
    v_f461_unparsed_probes = evaluate_frame_safety(handler, "*#4*001*#20##")
    assert v_f461_unparsed_probes.blocked is True
    assert v_f461_unparsed_probes.severity == "invalid"
    assert v_f461_unparsed_probes.rule_id == "invalid_frame_format"
    v_unknown_unparsed_probes = evaluate_frame_safety(_make_handler(""), "*#4*001*#20##")
    assert v_unknown_unparsed_probes.blocked is True
    assert v_unknown_unparsed_probes.severity == "invalid"
    assert v_unknown_unparsed_probes.rule_id == "invalid_frame_format"

    v_h4684_unparsed_13 = evaluate_frame_safety(_make_handler("H4684"), "*#13**#10##")
    assert v_h4684_unparsed_13.blocked is True
    assert v_h4684_unparsed_13.severity == "crash"
    assert v_h4684_unparsed_13.rule_id == "who13_network_dims_10_11_12"

    monkeypatch.setattr(services, "_ownd_destructive_frame_reason", None)
    v_dim52_hash_where = evaluate_frame_safety(handler, "*#18*#51*52##")
    assert v_dim52_hash_where.blocked is True
    assert v_dim52_hash_where.severity == "crash"
    assert v_dim52_hash_where.rule_id == "who18_dim52_invalid_year"

    monkeypatch.setattr(
        services,
        "_ownd_destructive_frame_reason",
        lambda _f: "custom unparseable destructive reason",
    )
    v_unparsed_ownd = evaluate_frame_safety(handler, "*1*2##")
    assert v_unparsed_ownd.valid is False
    assert v_unparsed_ownd.blocked is True
    assert v_unparsed_ownd.severity == "crash"
    assert v_unparsed_ownd.rule_id == "ownd_destructive_frame"
