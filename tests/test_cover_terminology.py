"""Tests for generic WHO=2 cover terminology, calibration cutoff wording, and repair flow schemas."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "myhome"
TRANSLATIONS_DIR = COMPONENT / "translations"
LOCALES = ("en", "nl", "fr", "it")
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
SINGLE_QUOTED_PLACEHOLDER_RE = re.compile(r"'\{\w+\}'")

REPAIR_ISSUE_KEYS = (
    "cover_calibration_cutoff",
    "cover_legacy_calibration_cutoff",
)

EXPECTED_FIX_FLOW_STEPS = {
    "init",
    "timed_close_ready",
    "timed_close_stop",
    "timed_open_ready",
    "timed_open_stop",
    "manual_entry",
    "confirm_save",
    "reset_default",
}

EXPECTED_MENU_OPTIONS = {"timed_close_ready", "manual_entry", "reset_default"}
EXPECTED_ABORT_REASONS = {"cover_missing", "save_failed"}
EXPECTED_ERRORS = {"invalid_travel_time"}


def _flatten(node: Any, prefix: str = "") -> dict[str, str]:
    """Flatten a nested translation dict into dot-separated leaf strings."""
    result: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            full_key = f"{prefix}.{key}" if prefix else key
            result.update(_flatten(value, full_key))
    elif isinstance(node, str):
        result[prefix] = node
    return result


def _load_catalog(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_generic_who2_cover_terminology_across_locales_and_code() -> None:
    """Verify generic WHO=2 cover terminology, cutoff hints, and fixable repair schemas across all locales."""
    strings_data = _load_catalog(COMPONENT / "strings.json")
    en_data = _load_catalog(TRANSLATIONS_DIR / "en.json")
    assert strings_data == en_data, "strings.json and translations/en.json must stay synchronized"

    catalogs: dict[str, dict[str, Any]] = {
        "strings": strings_data,
        **{lang: _load_catalog(TRANSLATIONS_DIR / f"{lang}.json") for lang in LOCALES},
    }

    # 1. Zero shutter-only terms in generic WHO=2 translation values per locale.
    forbidden_by_locale: dict[str, re.Pattern[str]] = {
        "strings": re.compile(r"\bshutters?\b", re.IGNORECASE),
        "en": re.compile(r"\bshutters?\b", re.IGNORECASE),
        "nl": re.compile(r"rolluik", re.IGNORECASE),
        "fr": re.compile(r"volet", re.IGNORECASE),
        "it": re.compile(r"tapparell", re.IGNORECASE),
    }

    for name, pattern in forbidden_by_locale.items():
        flat = _flatten(catalogs[name])
        offenders = {k: v for k, v in flat.items() if pattern.search(v)}
        assert not offenders, f"{name}: found stale shutter-only wording: {offenders}"

    # Verify natural generic cover terms are present in non-English locales.
    nl_text = json.dumps(catalogs["nl"], ensure_ascii=False).lower()
    fr_text = json.dumps(catalogs["fr"], ensure_ascii=False).lower()
    it_text = json.dumps(catalogs["it"], ensure_ascii=False).lower()
    assert "raambedekking" in nl_text
    assert "ouverture" in fr_text
    assert "copertura" in it_text

    # 2. Keep centralized_shutter_* JSON keys intact wherever device_automation exists.
    centralized_keys = (
        "centralized_shutter_open",
        "centralized_shutter_close",
        "centralized_shutter_stop",
    )
    for name, data in catalogs.items():
        triggers = data.get("device_automation", {}).get("trigger_type")
        if triggers is not None:
            for key in centralized_keys:
                assert key in triggers, f"{name}: JSON key {key} must remain intact"
                assert triggers[key].strip(), f"{name}: {key} display label must not be empty"

    # 3. Item 1: No stale stopwatch / Stop & Save references in calibration_actuator_cutoff_*.
    stale_cutoff_re = re.compile(r"stop\s*&\s*save|stopwatch|chronom[eè]tre|cronometro", re.IGNORECASE)
    for name, data in catalogs.items():
        exceptions = data["exceptions"]
        for exc_key in ("calibration_actuator_cutoff_open", "calibration_actuator_cutoff_close"):
            msg = exceptions[exc_key]["message"]
            assert not stale_cutoff_re.search(msg), f"{name}.{exc_key}: stale stopwatch wording: {msg}"
            assert "last_run_seconds" in msg, f"{name}.{exc_key}: must mention last_run_seconds"
            assert "myhome.set_cover_travel_time" in msg, (
                f"{name}.{exc_key}: must mention myhome.set_cover_travel_time"
            )

    # 4. Items 3 & 6b: Both new fixable Repair Issues satisfy hassfest schema & placeholder parity.
    en_issue_placeholders: dict[str, dict[str, set[str]]] = {}
    for issue_key in REPAIR_ISSUE_KEYS:
        en_issue = strings_data["issues"][issue_key]
        en_issue_placeholders[issue_key] = {
            k: set(PLACEHOLDER_RE.findall(v)) for k, v in _flatten(en_issue).items()
        }

    for name, data in catalogs.items():
        issues = data.get("issues", {})
        for issue_key in REPAIR_ISSUE_KEYS:
            assert issue_key in issues, f"{name}: missing repair issue {issue_key}"
            issue = issues[issue_key]
            assert "title" in issue and isinstance(issue["title"], str) and issue["title"].strip()
            assert "description" not in issue, (
                f"{name}.issues.{issue_key}: fixable issue must NOT have a top-level 'description' (hassfest)"
            )
            assert "fix_flow" in issue and isinstance(issue["fix_flow"], dict), (
                f"{name}.issues.{issue_key}: fixable issue must define 'fix_flow'"
            )

            fix_flow = issue["fix_flow"]
            steps = fix_flow.get("step", {})
            assert set(steps.keys()) == EXPECTED_FIX_FLOW_STEPS, (
                f"{name}.issues.{issue_key}: unexpected fix_flow steps {sorted(steps.keys())}"
            )
            assert set(steps["init"].get("menu_options", {}).keys()) == EXPECTED_MENU_OPTIONS
            assert "reuse_down_for_up" in steps["timed_open_ready"].get("data", {})
            for form_step in ("manual_entry", "confirm_save"):
                assert {"travel_time_down", "travel_time_up"} <= set(steps[form_step].get("data", {}).keys())
            assert set(fix_flow.get("abort", {}).keys()) == EXPECTED_ABORT_REASONS
            assert set(fix_flow.get("error", {}).keys()) == EXPECTED_ERRORS

            # Verify no single-quoted placeholders and exact placeholder parity with strings.json.
            flat_issue = _flatten(issue)
            for leaf_key, text in flat_issue.items():
                assert not SINGLE_QUOTED_PLACEHOLDER_RE.search(text), (
                    f"{name}.issues.{issue_key}.{leaf_key}: single-quoted placeholder breaks ICU/hassfest"
                )
            actual_placeholders = {k: set(PLACEHOLDER_RE.findall(v)) for k, v in flat_issue.items()}
            assert actual_placeholders == en_issue_placeholders[issue_key], (
                f"{name}.issues.{issue_key}: placeholder mismatch vs strings.json"
            )


def test_travel_time_missing_mentions_slat_time_in_all_locales() -> None:
    """Item A1: exceptions.travel_time_missing must mention slat_time across strings.json and all locales."""
    paths = [COMPONENT / "strings.json", *(TRANSLATIONS_DIR / f"{lang}.json" for lang in LOCALES)]
    for path in paths:
        data = _load_catalog(path)
        message = data["exceptions"]["travel_time_missing"]["message"]
        assert "slat_time" in message, f"{path.name}: exceptions.travel_time_missing missing 'slat_time'"
