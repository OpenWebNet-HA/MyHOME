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

    # 4. Unsupported keys, name mismatch, missing categories, missing .zip filename, invalid HA version, and validate.yml ignore
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
    assert any("'categories' must be a list of 1..3 category strings" in err for err in checker_bad.errors)
    assert any("zip_release=true must specify a valid .zip filename" in err for err in checker_bad.errors)
    assert any("must be a valid Home Assistant CalVer string" in err for err in checker_bad.errors)
    assert any("validate.yml must not bypass default-catalog checks" in err for err in checker_bad.errors)

    # 5. Optional key type/semantic checks, empty-stem .zip, package_release.py drift, and missing/misconfigured validate.yml
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "myhome",
                "name": "MyHOME",
                "categories": ["hubs_protocols.building_automation"],
            }
        ),
        encoding="utf-8",
    )
    (wf_dir / "validate.yml").unlink()
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "package_release.py").write_text("# package_release stub\n", encoding="utf-8")
    hacs_path.write_text(
        json.dumps(
            {
                "name": "MyHOME",
                "content_in_root": True,
                "hide_default_branch": "false",
                "hacs": 205,
                "persistent_directory": "../escape",
                "zip_release": False,
                "filename": "myhome.zip",
                "homeassistant": "2026.3.0",
            }
        ),
        encoding="utf-8",
    )
    checker_opt = StandardsChecker()
    check_marketplace_manifest_rule(checker_opt, root_dir=tmp_path)
    assert any("'hide_default_branch' must be a boolean" in err for err in checker_opt.errors)
    assert any("'content_in_root' must not be true" in err for err in checker_opt.errors)
    assert any("'hacs' must be a valid version string" in err for err in checker_opt.errors)
    assert any("'persistent_directory' must be a safe relative subdirectory" in err for err in checker_opt.errors)
    assert any("must set 'zip_release': true to match scripts/package_release.py" in err for err in checker_opt.errors)
    assert any("must not set 'filename'" in err for err in checker_opt.errors)
    assert any("validate.yml not found" in err for err in checker_opt.errors)

    # 6. Empty-stem ".zip" filename, filename mismatch against package_release.py, and validate.yml missing category / hacs/action
    (wf_dir / "validate.yml").write_text(
        "jobs:\n  validate:\n    steps:\n      - run: echo noop\n",
        encoding="utf-8",
    )
    hacs_path.write_text(
        json.dumps(
            {
                "name": "MyHOME",
                "zip_release": True,
                "filename": ".zip",
                "homeassistant": "2026.3.0",
            }
        ),
        encoding="utf-8",
    )
    checker_empty_zip = StandardsChecker()
    check_marketplace_manifest_rule(checker_empty_zip, root_dir=tmp_path)
    assert any("zip_release=true must specify a valid .zip filename" in err for err in checker_empty_zip.errors)
    assert any("validate.yml must define a hacs/action validation step" in err for err in checker_empty_zip.errors)

    (wf_dir / "validate.yml").write_text(
        "jobs:\n"
        "  validate:\n"
        "    steps:\n"
        "      - uses: hacs/action@d556e736723344f83838d08488c983a15381059a\n"
        "        with:\n"
        "          category: plugin\n",
        encoding="utf-8",
    )
    hacs_path.write_text(
        json.dumps(
            {
                "name": "MyHOME",
                "zip_release": True,
                "filename": "other.zip",
                "homeassistant": "2026.3.0",
            }
        ),
        encoding="utf-8",
    )
    checker_drift = StandardsChecker()
    check_marketplace_manifest_rule(checker_drift, root_dir=tmp_path)
    assert any("must be 'myhome.zip' to match scripts/package_release.py" in err for err in checker_drift.errors)
    assert any("must set with.category to 'integration'" in err for err in checker_drift.errors)

    # 7. Edge cases: filename=null without zip_release, renamed validate job ID, non-dict manifest.json, and pathlib OUTPUT_ZIP
    (manifest_dir / "manifest.json").write_text("[]", encoding="utf-8")
    (scripts_dir / "package_release.py").write_text(
        'OUTPUT_ZIP = Path(REPO_ROOT) / "custom_pkg.zip"\n',
        encoding="utf-8",
    )
    (wf_dir / "validate.yml").write_text(
        "jobs:\n"
        "  renamed_job:\n"
        "    steps:\n"
        "      - uses: hacs/action@d556e736723344f83838d08488c983a15381059a\n"
        "        with:\n"
        "          category: integration\n",
        encoding="utf-8",
    )
    hacs_path.write_text(
        json.dumps(
            {
                "name": "MyHOME",
                "filename": None,
                "homeassistant": "2026.13.0",
            }
        ),
        encoding="utf-8",
    )
    checker_edge = StandardsChecker()
    check_marketplace_manifest_rule(checker_edge, root_dir=tmp_path)
    assert any("manifest.json root must be a JSON object" in err for err in checker_edge.errors)
    assert any("must not set 'filename' (None)" in err for err in checker_edge.errors)
    assert any("must be a valid Home Assistant CalVer string" in err for err in checker_edge.errors)
    assert any("must preserve the required 'validate' job ID" in err for err in checker_edge.errors)

    # 8. Unpinned hacs/action, if/continue-on-error bypasses, HA version < 2026.3.0, category duplicates/unknowns/overlaps, brand PNG checks, and unparseable OUTPUT_ZIP
    brand_dir = manifest_dir / "brand"
    brand_dir.mkdir()
    (brand_dir / "icon.png").write_bytes(b"not-a-png")
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "myhome",
                "name": "MyHOME",
                "categories": [
                    "hubs_protocols",
                    "hubs_protocols.building_automation",
                    "nonexistent_cat",
                ],
            }
        ),
        encoding="utf-8",
    )
    (wf_dir / "validate.yml").write_text(
        "jobs:\n"
        "  validate:\n"
        "    continue-on-error: true\n"
        "    steps:\n"
        "      - uses: hacs/action-fork@d556e736723344f83838d08488c983a15381059a\n"
        "        with:\n"
        "          category: integration\n"
        "      - uses: hacs/action@main\n"
        "        if: false\n"
        "        with:\n"
        "          category: integration\n"
        "          ignore: \"\"\n",
        encoding="utf-8",
    )
    hacs_path.write_text(
        json.dumps(
            {
                "name": "MyHOME",
                "zip_release": True,
                "filename": "myhome.zip",
                "homeassistant": "2026.2.0",
            }
        ),
        encoding="utf-8",
    )
    checker_edge2 = StandardsChecker()
    check_marketplace_manifest_rule(checker_edge2, root_dir=tmp_path)
    assert any("must be 'custom_pkg.zip' to match scripts/package_release.py" in err for err in checker_edge2.errors)
    assert any("must be >= '2026.3.0'" in err for err in checker_edge2.errors)
    assert any("unknown Home Assistant categories: ['nonexistent_cat']" in err for err in checker_edge2.errors)
    assert any("is implied by subcategory 'hubs_protocols.building_automation'" in err for err in checker_edge2.errors)
    assert any("Local brand asset 'icon.png' is not a valid PNG file" in err for err in checker_edge2.errors)
    assert any("Required local brand asset 'logo.png' missing from brand/" in err for err in checker_edge2.errors)
    assert any("'validate' job must not set 'if' or 'continue-on-error'" in err for err in checker_edge2.errors)
    assert any("must pin a 40-character commit SHA" in err for err in checker_edge2.errors)
    assert any("hacs/action step must not set 'if' or 'continue-on-error'" in err for err in checker_edge2.errors)
    assert any("validate.yml must not bypass default-catalog checks via ignore: ''" in err for err in checker_edge2.errors)

    # 9. Duplicate categories and dynamic/unresolvable OUTPUT_ZIP in package_release.py
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "domain": "myhome",
                "name": "MyHOME",
                "categories": [
                    "hubs_protocols.building_automation",
                    "hubs_protocols.building_automation",
                ],
            }
        ),
        encoding="utf-8",
    )
    (scripts_dir / "package_release.py").write_text(
        "OUTPUT_ZIP: str = dynamic_zip_path()\n",
        encoding="utf-8",
    )
    checker_edge3 = StandardsChecker()
    check_marketplace_manifest_rule(checker_edge3, root_dir=tmp_path)
    assert any("must not contain duplicates" in err for err in checker_edge3.errors)
    assert any("Could not statically resolve OUTPUT_ZIP" in err for err in checker_edge3.errors)
