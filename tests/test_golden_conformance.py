"""OpenWebNet Golden Corpus Conformance Test Suite.

Tests parser extraction fidelity, roundtrip string serialization,
and builder factory parity against OWNd.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

try:
    from OWNd.message import (
        OWNAutomationCommand,
        OWNCenCommand,
        OWNCenPlusCommand,
        OWNHeatingCommand,
        OWNLightingCommand,
        OWNMessage,
        OWNSignaling,
    )
except ImportError:
    from OWNd.message import (  # type: ignore[no-redef]
        OWNAutomationCommand,
        OWNHeatingCommand,
        OWNLightingCommand,
        OWNMessage,
        OWNSignaling,
    )
    OWNCenCommand = None  # type: ignore[assignment]
    OWNCenPlusCommand = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
GOLDEN_FRAMES_DIR = GOLDEN_DIR / "frames"
CORPUS_JSON_PATH = GOLDEN_DIR / "corpus.json"


def load_all_golden_fixtures() -> List[Dict[str, Any]]:
    """Load all golden frame fixtures from corpus.json or YAML files."""
    if CORPUS_JSON_PATH.is_file():
        with open(CORPUS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # Fallback to YAML if corpus.json is absent
    try:
        import yaml
    except ImportError:
        return []

    fixtures = []
    for yf in sorted(GOLDEN_FRAMES_DIR.glob("*.yaml")):
        with open(yf, "r", encoding="utf-8") as f:
            records = yaml.safe_load(f) or []
            for rec in records:
                rec["_file"] = yf.name
                fixtures.append(rec)
    return fixtures


ALL_FIXTURES = load_all_golden_fixtures()
ROUNDTRIP_FIXTURES = [f for f in ALL_FIXTURES if f.get("roundtrip") is True]
BUILDER_FIXTURES = [f for f in ALL_FIXTURES if f.get("builder") is not None]


def test_golden_corpus_schema_validity():
    """Verify that all golden fixtures strictly conform to schema.json with unique IDs and frames."""
    try:
        import jsonschema  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        pytest.skip("jsonschema and pyyaml required for live schema validation")
    from tools.golden.validate_corpus import validate_corpus
    assert validate_corpus() == 0


def test_golden_corpus_loaded():
    """Verify that golden fixtures are loaded across all configured subsystems."""
    assert len(ALL_FIXTURES) >= 24, f"Corpus contains too few fixtures: {len(ALL_FIXTURES)}"


@pytest.mark.parametrize("fixture", ALL_FIXTURES, ids=lambda f: f["id"])
def test_golden_frame_parsing(fixture: Dict[str, Any]):
    """Verify that OWNd parses the golden frame and extracts semantic attributes."""
    frame_str = fixture["frame"]
    parsed = OWNMessage.parse(frame_str)

    assert parsed is not None, f"Failed to parse frame {frame_str}"

    # WHO assertion (null for signaling)
    expected_who = fixture.get("who")
    if expected_who is not None:
        assert parsed.who == expected_who, f"WHO mismatch: {parsed.who} != {expected_who}"
    else:
        assert isinstance(parsed, OWNSignaling) or parsed.who is None

    # WHERE assertion
    expected_where = fixture.get("where")
    if expected_where is not None:
        assert parsed.where == expected_where, f"WHERE mismatch: {parsed.where} != {expected_where}"

    # Interface assertion (for private bus routed frames)
    expected_interface = fixture.get("interface")
    if expected_interface is not None:
        assert getattr(parsed, "interface", None) == expected_interface, (
            f"Interface mismatch: {getattr(parsed, 'interface', None)} != {expected_interface}"
        )

    # WHAT assertion (if specified in fixture)
    expected_what = fixture.get("what")
    if expected_what is not None:
        actual_what = getattr(parsed, "what", getattr(parsed, "_what", None))
        actual_what_params = getattr(parsed, "what_param", getattr(parsed, "_what_param", []))
        if actual_what_params:
            actual_full_what = f"{actual_what}#{'#'.join(str(p) for p in actual_what_params)}"
        else:
            actual_full_what = str(actual_what) if actual_what is not None else None

        def _norm(w):
            if w is None:
                return None
            return "#".join(str(int(p)) if p.isdigit() else p for p in str(w).split("#"))

        norm_actual = _norm(actual_full_what or actual_what)
        norm_expected = _norm(expected_what)
        assert (
            str(actual_what) == str(expected_what)
            or actual_full_what == str(expected_what)
            or norm_actual == norm_expected
        ), (
            f"WHAT mismatch: actual={actual_what} (full={actual_full_what}) != expected={expected_what}"
        )

    # Dimension assertion
    expected_dim = fixture.get("dimension")
    if expected_dim is not None:
        assert getattr(parsed, "dimension", None) == expected_dim, (
            f"Dimension mismatch: {getattr(parsed, 'dimension', None)} != {expected_dim}"
        )

    # Dimension values assertion
    expected_dim_values = fixture.get("dimension_values")
    if expected_dim_values is not None:
        actual_dim_values = getattr(parsed, "dimension_value", getattr(parsed, "_dimension_value", []))
        assert actual_dim_values == expected_dim_values, (
            f"Dimension values mismatch: {actual_dim_values} != {expected_dim_values}"
        )

    # Translation frame assertion
    expected_translation = fixture.get("is_translation")
    if expected_translation is not None:
        assert getattr(parsed, "is_translation", False) == expected_translation, (
            f"is_translation mismatch for {frame_str}: {getattr(parsed, 'is_translation', False)} != {expected_translation}"
        )



@pytest.mark.parametrize("fixture", ROUNDTRIP_FIXTURES, ids=lambda f: f["id"])
def test_golden_frame_roundtrip(fixture: Dict[str, Any]):
    """Verify that string serialization of the parsed frame exactly reproduces raw wire frame."""
    frame_str = fixture["frame"]
    parsed = OWNMessage.parse(frame_str)
    assert str(parsed) == frame_str, f"Roundtrip serialization failed: {str(parsed)} != {frame_str}"


@pytest.mark.parametrize("fixture", BUILDER_FIXTURES, ids=lambda f: f["id"])
def test_golden_frame_builder_parity(fixture: Dict[str, Any]):
    """Verify that high-level OWNd command builder methods emit the exact golden frame string."""
    builder = fixture["builder"]
    class_map = {
        "OWNLightingCommand": OWNLightingCommand,
        "OWNAutomationCommand": OWNAutomationCommand,
        "OWNCenCommand": OWNCenCommand,
        "OWNCenPlusCommand": OWNCenPlusCommand,
        "OWNHeatingCommand": OWNHeatingCommand,
    }
    cls = class_map.get(builder["class"])
    if cls is None:
        pytest.skip(f"Builder class {builder['class']} not available in current OWNd package")

    method = getattr(cls, builder["method"], None)
    if method is None:
        pytest.skip(f"Method {builder['method']} not found on {cls.__name__} in current OWNd package")

    built_message = method(*builder["args"])
    assert str(built_message) == fixture["frame"], (
        f"Builder {builder['class']}.{builder['method']}({builder['args']}) produced "
        f"'{str(built_message)}', expected '{fixture['frame']}'"
    )
