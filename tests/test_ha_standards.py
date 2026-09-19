"""Automated test suite enforcing Home Assistant architectural standards."""

from scripts.verify_ha_standards import (
    StandardsChecker,
    check_deprecated_constants,
    check_discovery_flows,
    check_manifest_requirements_rule,
    check_no_blocking_calls,
    check_ruff_standards,
    check_translation_coverage,
)


def test_discovery_flow_confirmation_enforced():
    """Verify that discovery steps never auto-create entries and always require confirmation."""
    checker = StandardsChecker()
    check_discovery_flows(checker)
    assert not checker.errors, f"Discovery flow violations found: {checker.errors}"


def test_config_flow_translation_completeness():
    """Verify all step_id arguments in async_show_form are translated in en.json."""
    checker = StandardsChecker()
    check_translation_coverage(checker)
    assert not checker.errors, f"Translation step violations found: {checker.errors}"


def test_no_deprecated_constants_imported():
    """Verify no unhandled top-level imports of deprecated homeassistant.const symbols."""
    checker = StandardsChecker()
    check_deprecated_constants(checker)
    assert not checker.errors, f"Deprecated constant import violations found: {checker.errors}"


def test_no_blocking_calls_in_async_code():
    """Verify no synchronous blocking calls in async coroutines."""
    checker = StandardsChecker()
    check_no_blocking_calls(checker)
    assert not checker.errors, f"Blocking call violations found: {checker.errors}"


def test_ruff_static_analysis_standards():
    """Verify codebase satisfies Ruff static analysis standards."""
    checker = StandardsChecker()
    check_ruff_standards(checker)
    assert not checker.errors, f"Ruff static analysis violations found: {checker.errors}"


def test_manifest_requirements_consistency():
    """Verify manifest.json version matches const.py and pins exact matching OWNd."""
    checker = StandardsChecker()
    check_manifest_requirements_rule(checker)
    assert not checker.errors, f"Manifest requirements violations found: {checker.errors}"

