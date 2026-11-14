"""Translated strings must use exactly the placeholders of the English source.

Home Assistant validates this at runtime: when a translation's placeholders differ
from strings.json it logs an ERROR and falls back to English for that string.
"""

import json
import re
from pathlib import Path

import pytest

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "myhome"
PLACEHOLDER = re.compile(r"\{(\w+)\}")
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

    assert not sorted(set(translated) - set(source)), f"{language}: keys not in strings.json"
