"""Translated strings must be complete, non-empty, and match English placeholders.

Home Assistant validates placeholders at runtime: when a translation's placeholders differ
from strings.json it logs an ERROR and falls back to English for that string. Bundled
locales must also maintain 100% key parity with strings.json with no missing, orphaned,
or empty/whitespace-only values.
"""

import json
import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "myhome"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
SINGLE_QUOTED_PLACEHOLDER = re.compile(r"'\{\w+\}'")
LANGUAGES = sorted(p.stem for p in (COMPONENT / "translations").glob("*.json") if p.stem != "en")


def _flatten(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _flatten(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, str):
        yield prefix, node


def _strings(path: Path) -> dict[str, str]:
    return dict(_flatten(json.loads(path.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("language", LANGUAGES)
def test_translation_placeholders_match_english(language: str) -> None:
    source = _strings(COMPONENT / "strings.json")
    translated = _strings(COMPONENT / "translations" / f"{language}.json")

    mismatches = {
        key: (sorted(PLACEHOLDER.findall(source[key])), sorted(PLACEHOLDER.findall(text)))
        for key, text in translated.items()
        if key in source and set(PLACEHOLDER.findall(source[key])) != set(PLACEHOLDER.findall(text))
    }

    assert not mismatches, f"{language}: placeholders differ from strings.json (en, {language}): {mismatches}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_have_no_keys_missing_from_english(language: str) -> None:
    source = _strings(COMPONENT / "strings.json")
    translated = _strings(COMPONENT / "translations" / f"{language}.json")

    orphaned = sorted(set(translated) - set(source))
    assert not orphaned, f"{language}: keys not in strings.json: {orphaned}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_have_no_missing_keys(language: str) -> None:
    source = _strings(COMPONENT / "strings.json")
    translated = _strings(COMPONENT / "translations" / f"{language}.json")

    missing = sorted(set(source) - set(translated))
    assert not missing, (
        f"{language}: {len(missing)} key(s) missing relative to strings.json: {missing}"
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_translations_have_no_empty_values(language: str) -> None:
    source = _strings(COMPONENT / "strings.json")
    translated = _strings(COMPONENT / "translations" / f"{language}.json")

    empty = sorted(
        key
        for key, text in translated.items()
        if key in source and source[key].strip() and not text.strip()
    )
    assert not empty, (
        f"{language}: {len(empty)} empty/whitespace translation value(s) "
        f"for non-empty English keys: {empty}"
    )


@pytest.mark.parametrize(
    "relative_path",
    ["strings.json", *(f"translations/{p.name}" for p in sorted((COMPONENT / "translations").glob("*.json")))],
)
def test_no_single_quoted_placeholders(relative_path: str) -> None:
    """Hassfest rejects placeholders inside single quotes due to ICU escaping."""
    offending = sorted(
        key for key, text in _strings(COMPONENT / relative_path).items() if SINGLE_QUOTED_PLACEHOLDER.search(text)
    )
    assert not offending, f"{relative_path}: single-quoted placeholders break ICU/hassfest: {offending}"
