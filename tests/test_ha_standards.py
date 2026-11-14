"""Automated test suite enforcing Home Assistant architectural standards."""

import json

from scripts.verify_ha_standards import (
    StandardsChecker,
    check_deprecated_constants,
    check_discovery_flows,
    check_future_annotations_and_syntax,
    check_manifest_requirements_rule,
    check_marketplace_manifest_rule,
    check_no_blocking_calls,
    check_no_update_listener_reload_conflict,
    check_ruff_standards,
    check_supported_domains_rule,
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


def test_supported_domains_readme_calibrated():
    """Verify README.md Supported Entity Domains table is calibrated and in sync."""
    checker = StandardsChecker()
    check_supported_domains_rule(checker)
    assert not checker.errors, f"Supported domains README violations found: {checker.errors}"


def test_future_annotations_and_syntax_standards():
    """Verify all Python files compile and follow strict __future__ positioning."""
    checker = StandardsChecker()
    check_future_annotations_and_syntax(checker)
    assert not checker.errors, f"Future annotations/syntax violations found: {checker.errors}"


def test_future_annotations_catches_misplaced_import(tmp_path):
    """Verify rule catches misplaced statements preceding from __future__ import annotations."""
    bad_file = tmp_path / "bad_module.py"
    bad_file.write_text(
        "import sys\nfrom __future__ import annotations\n\nx = 1\n",
        encoding="utf-8",
    )
    checker = StandardsChecker()
    check_future_annotations_and_syntax(checker, target_dir=tmp_path)
    assert any("from __future__ imports must occur at the beginning of the file" in err for err in checker.errors), (
        f"Expected future annotations syntax error, but got: {checker.errors}"
    )





def test_no_update_listener_reload_conflict():
    """Verify the integration never registers an update listener or reloads directly in a flow (#510)."""
    checker = StandardsChecker()
    check_no_update_listener_reload_conflict(checker)
    assert not checker.errors, f"Update listener / reload violations found: {checker.errors}"


def test_update_listener_rule_catches_violations(tmp_path):
    """The rule flags add_update_listener anywhere and async_reload inside config_flow.py."""
    (tmp_path / "__init__.py").write_text(
        "def setup(entry):\n    entry.async_on_unload(entry.add_update_listener(cb))\n"
    )
    (tmp_path / "config_flow.py").write_text(
        "async def step(self):\n    await self.hass.config_entries.async_reload('x')\n"
    )
    (tmp_path / "repairs.py").write_text(
        "async def fix(self):\n    await self.hass.config_entries.async_reload('x')\n"
    )
    checker = StandardsChecker()
    check_no_update_listener_reload_conflict(checker, target_dir=tmp_path)
    assert len(checker.errors) == 2


def test_marketplace_manifest_and_workflow_standards():
    """Verify hacs.json and validate.yml satisfy Home Assistant 2026.11 Marketplace standards."""
    checker = StandardsChecker()
    check_marketplace_manifest_rule(checker)
    assert not checker.errors, f"Marketplace manifest/workflow violations found: {checker.errors}"


def test_marketplace_manifest_rule_catches_violations(tmp_path):
    """Verify check_marketplace_manifest_rule flags missing, invalid, and legacy hacs.json / validate.yml configs."""
    # 1. Missing hacs.json
    checker_missing = StandardsChecker()
    check_marketplace_manifest_rule(checker_missing, root_dir=tmp_path)
    assert any("hacs.json not found" in err for err in checker_missing.errors)

    # 2. Invalid JSON in hacs.json
    hacs_path = tmp_path / "hacs.json"
    hacs_path.write_text("{not-json", encoding="utf-8")
    checker_invalid = StandardsChecker()
    check_marketplace_manifest_rule(checker_invalid, root_dir=tmp_path)
    assert any("Failed to parse hacs.json" in err for err in checker_invalid.errors)

    # 3. Non-object JSON in hacs.json
    hacs_path.write_text("[]", encoding="utf-8")
    checker_non_dict = StandardsChecker()
    check_marketplace_manifest_rule(checker_non_dict, root_dir=tmp_path)
    assert any("hacs.json root must be a JSON object" in err for err in checker_non_dict.errors)

    # 4. Unsupported keys, name mismatch, missing .zip filename, invalid HA version, and validate.yml ignore
    manifest_dir = tmp_path / "custom_components" / "myhome"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "manifest.json").write_text(
        json.dumps({"domain": "myhome", "name": "MyHOME"}),
        encoding="utf-8",
    )
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "validate.yml").write_text(
        "jobs:\n"
        "  validate:\n"
        "    steps:\n"
        "      - uses: hacs/action@d556e736723344f83838d08488c983a15381059a\n"
        "        with:\n"
        "          category: integration\n"
        "          ignore: topics issues\n",
        encoding="utf-8",
    )
    hacs_path.write_text(
        json.dumps(
            {
                "name": "MyHome",
                "render_readme": True,
                "country": ["GB", "FR"],
                "zip_release": True,
                "filename": "sub/myhome.tar.gz",
                "homeassistant": "latest",
            }
        ),
        encoding="utf-8",
    )
    checker_bad = StandardsChecker()
    check_marketplace_manifest_rule(checker_bad, root_dir=tmp_path)
    assert any("unsupported by Home Assistant 2026.11 Marketplace" in err for err in checker_bad.errors)
    assert any("must match manifest.json name" in err for err in checker_bad.errors)
    assert any("zip_release=true must specify a valid .zip filename" in err for err in checker_bad.errors)
    assert any("must be a valid Home Assistant version string" in err for err in checker_bad.errors)
    assert any("validate.yml must not bypass default-catalog checks" in err for err in checker_bad.errors)
