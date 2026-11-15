"""OpenWebNet Golden Corpus Conformance Test Suite.

Tests parser extraction fidelity, direction & typed subclass dispatch,
bidirectional attribute completeness, roundtrip string serialization,
command-builder class coverage, and documentation matrix sync against OWNd.
"""

from __future__ import annotations

import datetime
import inspect
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import OWNd.message as msg_module
import pytest
from OWNd.message import (
    OWNCommand,
    OWNEnergyCommand,
    OWNEvent,
    OWNGatewayCommand,
    OWNMessage,
    OWNSignaling,
    OWNStatusRequest,
    _ensure_all_subsystems_registered,
)
from OWNd.message.base import _COMMAND_DISPATCH, _EVENT_DISPATCH

_ensure_all_subsystems_registered()

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
GOLDEN_FRAMES_DIR = GOLDEN_DIR / "frames"
CORPUS_JSON_PATH = GOLDEN_DIR / "corpus.json"
CONFORMANCE_MATRIX_PATH = REPO_ROOT / "docs" / "protocol_conformance_matrix.md"

DIRECTION_PREDICATES: dict[str, Callable[[OWNMessage], bool]] = {
    "signaling": lambda m: isinstance(m, OWNSignaling),
    "status_request": lambda m: isinstance(m, OWNStatusRequest)
    or (m.is_status_request and m.dimension is None),
    "dimension_request": lambda m: m.is_dimension_request,
    "dimension_write": lambda m: m.is_dimension_writing,
    "dimension_response": lambda m: m.is_dimension_reply,
    "command": lambda m: isinstance(m, (OWNCommand, OWNEvent))
    and m.dimension is None
    and not m.is_status_request,
    "event": lambda m: isinstance(m, OWNEvent)
    and m.dimension is None
    and not m.is_status_request,
}


def coerce_builder_args(cls: type[Any], method_name: str, args: list[Any]) -> list[Any]:
    """Coerce serialized ISO date/time/datetime strings into Python datetime objects."""
    coerced = list(args)
    if cls is OWNGatewayCommand and coerced and isinstance(coerced[0], str):
        if method_name in ("set_datetime", "report_datetime"):
            coerced[0] = datetime.datetime.fromisoformat(coerced[0])
        elif method_name in ("set_date", "report_date"):
            coerced[0] = datetime.date.fromisoformat(coerced[0])
        elif method_name in ("set_time", "report_time"):
            coerced[0] = datetime.time.fromisoformat(coerced[0])
    elif (
        cls is OWNEnergyCommand
        and method_name == "get_hourly_consumption"
        and len(coerced) >= 2
        and isinstance(coerced[1], str)
    ):
        coerced[1] = datetime.date.fromisoformat(coerced[1])
    return coerced


def load_all_golden_fixtures() -> list[dict[str, Any]]:
    """Load all golden frame fixtures from corpus.json or YAML files."""
    if CORPUS_JSON_PATH.is_file():
        with open(CORPUS_JSON_PATH, encoding="utf-8") as f:
            return json.load(f)

    try:
        import yaml
    except ImportError:
        return []

    fixtures: list[dict[str, Any]] = []
    for yf in sorted(GOLDEN_FRAMES_DIR.glob("*.yaml")):
        with open(yf, encoding="utf-8") as f:
            records = yaml.safe_load(f) or []
            for rec in records:
                rec["_file"] = yf.name
                fixtures.append(rec)
    return fixtures


ALL_FIXTURES = load_all_golden_fixtures()
ROUNDTRIP_FIXTURES = [f for f in ALL_FIXTURES if f.get("roundtrip") is True]
BUILDER_FIXTURES = [f for f in ALL_FIXTURES if f.get("builder") is not None]


def test_golden_corpus_schema_validity() -> None:
    """Verify that all golden fixtures strictly conform to schema.json and corpus.json sync."""
    try:
        import jsonschema  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        pytest.skip("jsonschema and pyyaml required for live schema validation")
    from tools.golden.validate_corpus import validate_corpus

    assert validate_corpus(check_only=True) == 0


def test_golden_corpus_loaded() -> None:
    """Verify that golden fixtures are loaded across all configured subsystems."""
    assert len(ALL_FIXTURES) >= 24, f"Corpus contains too few fixtures: {len(ALL_FIXTURES)}"


def test_all_exported_command_builder_classes_covered() -> None:
    """Every distinct OWNCommand subclass exported in OWNd.message must have builder coverage."""
    covered_classes = {
        getattr(msg_module, f["builder"]["class"], None) for f in BUILDER_FIXTURES
    }
    exported_classes: dict[type[Any], list[str]] = {}
    for name, obj in sorted(vars(msg_module).items()):
        if inspect.isclass(obj) and issubclass(obj, OWNCommand) and obj is not OWNCommand:
            exported_classes.setdefault(obj, []).append(name)

    missing = [
        "/".join(names)
        for cls, names in sorted(exported_classes.items(), key=lambda item: item[1][0])
        if cls not in covered_classes
    ]
    assert not missing, (
        f"OWNCommand builder class(es) missing from Golden Corpus builder parity: {missing}"
    )


def test_protocol_conformance_matrix_examples_in_corpus() -> None:
    """Every Canonical Frame Example in docs/protocol_conformance_matrix.md must exist in corpus."""
    if not CONFORMANCE_MATRIX_PATH.is_file():
        pytest.skip("docs/protocol_conformance_matrix.md not present")

    corpus_frames = {f["frame"] for f in ALL_FIXTURES}
    missing: list[str] = []
    in_matrix_section = False
    for line in CONFORMANCE_MATRIX_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## 2."):
            in_matrix_section = True
            continue
        if in_matrix_section and line.startswith("## "):
            break
        if not in_matrix_section or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in ("WHO", ":---:"):
            continue
        for frame_token in re.findall(r"`(\*[^`]+##)`", cells[3]):
            if frame_token not in corpus_frames:
                missing.append(frame_token)

    assert not missing, (
        "Canonical Frame Examples in docs/protocol_conformance_matrix.md missing from "
        f"tests/golden/corpus.json: {missing}"
    )


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["id"])
def test_golden_frame_parsing(fixture: dict[str, Any]) -> None:
    """Verify that OWNd parses the golden frame and extracts complete semantic attributes."""
    frame_str = fixture["frame"]
    parsed = OWNMessage.parse(frame_str)

    assert parsed is not None, f"Failed to parse frame {frame_str}"

    # Direction assertion
    expected_direction = fixture["direction"]
    predicate = DIRECTION_PREDICATES.get(expected_direction)
    assert predicate is not None, f"Unknown direction {expected_direction!r}"
    assert predicate(parsed), (
        f"Direction mismatch for {fixture['id']} ({frame_str}): "
        f"expected {expected_direction!r}, got {type(parsed).__name__}"
    )

    # Specialized subclass dispatch assertion
    if isinstance(parsed, OWNEvent) and parsed.who in _EVENT_DISPATCH:
        assert type(parsed) is not OWNEvent, (
            f"Frame {frame_str} (WHO={parsed.who}) fell back to generic OWNEvent"
        )
    if (
        isinstance(parsed, OWNCommand)
        and not isinstance(parsed, OWNStatusRequest)
        and parsed.who in _COMMAND_DISPATCH
    ):
        assert type(parsed) is not OWNCommand, (
            f"Frame {frame_str} (WHO={parsed.who}) fell back to generic OWNCommand"
        )
    if expected_direction == "command" and parsed.who in _COMMAND_DISPATCH:
        cmd_parsed = OWNCommand.parse(frame_str)
        assert cmd_parsed is not None, f"OWNCommand.parse failed on command frame {frame_str}"
        assert type(cmd_parsed) is not OWNCommand, (
            f"Command frame {frame_str} (WHO={parsed.who}) was not specialized by OWNCommand.parse"
        )

    # Signaling frames carry no WHO/WHERE/WHAT/DIMENSION fields
    expected_who = fixture.get("who")
    if isinstance(parsed, OWNSignaling):
        assert expected_who is None, f"Signaling frame {frame_str} must have who=null"
        assert fixture.get("where") is None
        assert fixture.get("where_param") is None
        return

    assert parsed.who == expected_who, f"WHO mismatch: {parsed.who} != {expected_who}"

    # Bidirectional WHERE, interface & where_param assertions
    expected_where = fixture.get("where")
    assert parsed.where == expected_where, (
        f"WHERE mismatch for {frame_str}: {parsed.where!r} != {expected_where!r}"
    )

    actual_interface, actual_where_param = parsed._interface_and_where_params()
    expected_where_param = fixture.get("where_param") or []
    assert actual_where_param == expected_where_param, (
        f"where_param mismatch for {frame_str}: {actual_where_param} != {expected_where_param}"
    )

    expected_interface = fixture.get("interface")
    assert actual_interface == expected_interface, (
        f"Interface mismatch for {frame_str}: {actual_interface!r} != {expected_interface!r}"
    )

    # Scope assertion (WHO 1/2 addressing class: general / area / group / point)
    expected_scope = fixture.get("scope")
    if expected_scope is not None:
        actual_scope = (
            "general"
            if parsed.is_general
            else "area"
            if parsed.is_area
            else "group"
            if parsed.is_group
            else "point"
        )
        assert actual_scope == expected_scope, (
            f"Scope mismatch for {frame_str}: {actual_scope} != {expected_scope}"
        )

    # Bidirectional translation frame assertion (WHAT 1000#... command-translation prefix)
    expected_translation = bool(fixture.get("is_translation", False))
    actual_translation = bool(getattr(parsed, "is_translation", False))
    assert actual_translation is expected_translation, (
        f"is_translation mismatch for {frame_str}: {actual_translation} != {expected_translation}"
    )

    # Bidirectional WHAT & what_param assertion (what_param must never be silently dropped)
    expected_what = fixture.get("what")
    actual_what = getattr(parsed, "what", getattr(parsed, "_what", None))
    actual_what_params = getattr(parsed, "what_param", getattr(parsed, "_what_param", []))
    if actual_what is None:
        assert expected_what is None, (
            f"Unexpected what={expected_what!r} for dimensionless/status frame {frame_str}"
        )
    else:
        assert expected_what is not None, (
            f"Fixture {fixture['id']} ({frame_str}) omitted what={actual_what!r}"
        )
        if actual_what_params:
            actual_full_what = f"{actual_what}#{'#'.join(str(p) for p in actual_what_params)}"
        else:
            actual_full_what = str(actual_what)

        def _norm(val: Any) -> str:
            return "#".join(str(int(p)) if p.isdigit() else p for p in str(val).split("#"))

        assert (
            actual_full_what == str(expected_what)
            or _norm(actual_full_what) == _norm(expected_what)
        ), (
            f"WHAT mismatch for {frame_str}: actual={actual_full_what!r} != expected={expected_what!r}"
        )

    # Bidirectional dimension, dimension_param & dimension_values assertions
    actual_dim = getattr(parsed, "dimension", None)
    expected_dim = fixture.get("dimension")
    assert actual_dim == expected_dim, (
        f"Dimension mismatch for {frame_str}: {actual_dim} != {expected_dim}"
    )

    actual_dim_param = list(
        getattr(parsed, "dimension_param", getattr(parsed, "_dimension_param", []))
    )
    expected_dim_param = fixture.get("dimension_param") or []
    assert actual_dim_param == expected_dim_param, (
        f"dimension_param mismatch for {frame_str}: {actual_dim_param} != {expected_dim_param}"
    )

    actual_dim_values = list(
        getattr(parsed, "dimension_value", getattr(parsed, "_dimension_value", []))
    )
    expected_dim_values = fixture.get("dimension_values") or []
    assert actual_dim_values == expected_dim_values, (
        f"Dimension values mismatch for {frame_str}: {actual_dim_values} != {expected_dim_values}"
    )


@pytest.mark.parametrize("fixture", ROUNDTRIP_FIXTURES, ids=lambda f: f["id"])
def test_golden_frame_roundtrip(fixture: dict[str, Any]) -> None:
    """Verify that string serialization of the parsed frame reproduces the raw wire frame."""
    frame_str = fixture["frame"]
    parsed = OWNMessage.parse(frame_str)
    assert str(parsed) == frame_str, (
        f"Roundtrip serialization failed: {str(parsed)} != {frame_str}"
    )


@pytest.mark.parametrize("fixture", BUILDER_FIXTURES, ids=lambda f: f["id"])
def test_golden_frame_builder_parity(fixture: dict[str, Any]) -> None:
    """Verify that high-level OWNd command builder methods emit the exact golden frame string."""
    builder = fixture["builder"]
    cls = getattr(msg_module, builder["class"], None)
    assert cls is not None, f"Unknown builder class {builder['class']} in OWNd.message"

    method = getattr(cls, builder["method"], None)
    assert method is not None, f"Method {builder['method']} not found on {cls.__name__}"

    args = coerce_builder_args(cls, builder["method"], builder["args"])
    kwargs = builder.get("kwargs") or {}
    built_message = method(*args, **kwargs)
    assert str(built_message) == fixture["frame"], (
        f"Builder {builder['class']}.{builder['method']}({builder['args']}, {kwargs}) produced "
        f"'{built_message}', expected '{fixture['frame']}'"
    )
