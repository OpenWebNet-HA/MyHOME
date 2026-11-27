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


def test_unconfigured_timezone_real_newlines_and_localized_strings() -> None:
    """Verify unconfigured_timezone newlines/localization and fixed PR #1028 keys in nl/fr/it."""
    english = _strings(COMPONENT / "strings.json")
    service_keys = (
        "services.sync_time.fields.gateway.description",
        "services.send_message.name",
        "services.send_message.fields.gateway.description",
        "services.send_message.fields.message.name",
        "services.send_message.fields.message.description",
        "services.start_sending_instant_power.name",
        "services.start_sending_instant_power.description",
        "services.start_sending_instant_power.fields.entity_id.name",
        "services.start_sending_instant_power.fields.entity_id.description",
        "services.stop_sending_instant_power.name",
        "services.stop_sending_instant_power.description",
        "services.stop_sending_instant_power.fields.entity_id.name",
        "services.stop_sending_instant_power.fields.entity_id.description",
    )
    cover_keys = (
        "exceptions.cover_position_unknown.message",
        "device_automation.trigger_type.centralized_shutter_open",
        "device_automation.trigger_type.centralized_shutter_close",
        "device_automation.trigger_type.centralized_shutter_stop",
    )
    expected_localized: dict[str, tuple[str, ...]] = {
        "nl": (
            "config.flow_title",
            "config.step.custom.data.address",
            "config.step.custom.data.port",
            "config.error.password_retry",
            "options.step.user.data.address",
            "options.error.invalid_worker_count",
            *service_keys,
            *cover_keys,
        ),
        "fr": (
            "options.error.invalid_worker_count",
            *cover_keys,
        ),
        "it": (
            *service_keys,
            *cover_keys,
        ),
    }
    cover_wording_rules: dict[str, tuple[tuple[str, ...], str]] = {
        "nl": (("raambedekking",), "rolluik"),
        "fr": (("ouverture", "ouvrant"), "volet"),
        "it": (("copertur",), "tapparell"),
    }

    for language in ("nl", "fr", "it"):
        translated = _strings(COMPONENT / "translations" / f"{language}.json")

        # No literal double-escaped newlines anywhere in the catalog.
        escaped_newlines = sorted(key for key, text in translated.items() if "\\n" in text)
        assert not escaped_newlines, f"{language}: literal \\\\n found in {escaped_newlines}"

        # issues.unconfigured_timezone must use real paragraph breaks and be localized.
        tz_title = translated["issues.unconfigured_timezone.title"]
        tz_desc = translated["issues.unconfigured_timezone.description"]
        assert tz_title != english["issues.unconfigured_timezone.title"], (
            f"{language}: issues.unconfigured_timezone.title is still English"
        )
        assert tz_desc != english["issues.unconfigured_timezone.description"], (
            f"{language}: issues.unconfigured_timezone.description is still English"
        )
        assert "\n\n" in tz_desc, (
            f"{language}: issues.unconfigured_timezone.description missing real paragraph break"
        )
        assert "\\n" not in tz_desc, (
            f"{language}: issues.unconfigured_timezone.description contains literal \\\\n"
        )

        # Fixed PR #1028 keys must be localized (not identical to English).
        untranslated = [key for key in expected_localized[language] if translated[key] == english[key]]
        assert not untranslated, f"{language}: untranslated keys matching English: {untranslated}"

        # PR #1028 cover keys must use generic cover wording instead of shutter-specific wording.
        expected_stems, forbidden_stem = cover_wording_rules[language]
        for key in cover_keys:
            lowered = translated[key].lower()
            assert any(stem in lowered for stem in expected_stems), (
                f"{language}: {key} should contain one of {expected_stems!r}: {translated[key]!r}"
            )
            assert forbidden_stem not in lowered, (
                f"{language}: {key} should not contain {forbidden_stem!r}: {translated[key]!r}"
            )

    assert " and " not in _strings(COMPONENT / "translations" / "nl.json")[
        "options.error.invalid_worker_count"
    ]
