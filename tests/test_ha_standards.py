"""Automated test suite enforcing Home Assistant architectural standards."""

import json
import struct
import zlib
from pathlib import Path

from scripts.verify_ha_standards import (
    ALLOWED_BRAND_IMAGES,
    CUSTOM_COMPONENTS_DIR,
    TESTS_DIR,
    TYPING_COMPAT_FILE,
    StandardsChecker,
    _resolve_device_info_scan_files,
    check_deprecated_constants,
    check_discovery_flows,
    check_future_annotations_and_syntax,
    check_manifest_requirements_rule,
    check_marketplace_manifest_rule,
    check_no_blocking_calls,
    check_no_deprecated_device_info_mapping,
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


def test_no_deprecated_device_info_mapping():
    """Verify neither custom_components/myhome/ nor tests/ uses deprecated DeviceInfo mapping access (#930)."""
    checker = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker)
    assert not checker.errors, f"DeviceInfo mapping violations found: {checker.errors}"


def test_deprecated_device_info_mapping_rule_catches_violations(tmp_path):
    """Verify AST rule flags all deprecated DeviceInfo mapping/TypedDict operations and allows clean idioms."""
    clean_file = tmp_path / "clean.py"
    clean_file.write_text(
        "from homeassistant.helpers.device_registry import DeviceInfo\n"
        "from .typing_compat import device_info_to_dict\n\n"
        "def fn_a(entity, gw):\n"
        "    info = entity.device_info\n"
        "    entity._attr_device_info = DeviceInfo(\n"
        "        identifiers={('myhome', '1')},\n"
        "        via_device_id=gw.device_registry_id,\n"
        "    )\n"
        "    assert device_info_to_dict(info) == {'identifiers': {('myhome', '1')}}\n"
        "    return device_info_to_dict(entity.device_info).get('identifiers')\n\n"
        "def fn_b():\n"
        "    info = {'k': 1}\n"
        "    return info['k']\n\n"
        "class CleanHolder:\n"
        "    def __init__(self):\n"
        "        self.info = {'k': 1}\n"
        "        _ = self.info['k']\n",
        encoding="utf-8",
    )
    checker_clean = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_clean, target_dir=tmp_path)
    assert not checker_clean.errors, f"Unexpected errors on clean patterns: {checker_clean.errors}"

    bad_file = tmp_path / "bad_device_info.py"
    bad_file.write_text(
        "from homeassistant.helpers import device_registry as dr\n"
        "from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo, DeviceInfo as DI\n\n"
        "def bad_ops(self, switch, e):\n"
        "    self._attr_device_info['via_device_id'] = 'gw'\n"
        "    _ = switch.device_info['name']\n"
        "    _ = (switch.device_info or {}).get('identifiers')\n"
        "    self._attr_device_info.update({'model': 'X'})\n"
        "    _ = switch.device_info.keys()\n"
        "    _ = switch.device_info.items()\n"
        "    _ = 'via_device' in switch.device_info\n"
        "    _ = DI.__annotations__\n"
        "    _ = ChildDeviceInfo.__required_keys__\n"
        "    _ = dr.DeviceInfo.__optional_keys__\n"
        "    local_di = DI(identifiers={('myhome', '1')})\n"
        "    _ = local_di['identifiers']\n"
        "    _ = dict(switch.device_info)\n"
        "    _ = {**switch.device_info}\n"
        "    _ = e.device_info == {'name': 'x'}\n"
        "    _ = e.device_info != dict(name='x')\n"
        "    self.info = DeviceInfo(identifiers={('myhome', '2')})\n"
        "    self.info['x'] = 'y'\n\n"
        "class BadHolder:\n"
        "    def __init__(self):\n"
        "        self.cached = DeviceInfo(identifiers={('myhome', '3')})\n\n"
        "    def read_cached(self):\n"
        "        return self.cached.get('name')\n",
        encoding="utf-8",
    )
    checker_bad = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_bad, target_dir=tmp_path)
    assert len(checker_bad.errors) == 17, f"Expected 17 violations, got {len(checker_bad.errors)}: {checker_bad.errors}"
    assert all("RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING" in err for err in checker_bad.errors)


def test_deprecated_device_info_mapping_scope_isolation(tmp_path):
    """Verify local variable and self.<attr> tracking do not leak across scopes or plain-dict shadows."""
    scoped_file = tmp_path / "scoped.py"
    scoped_file.write_text(
        "from homeassistant.helpers.device_registry import DeviceInfo\n\n"
        "def fn_a(e):\n"
        "    info = e.device_info\n"
        "    return info\n\n"
        "def fn_b():\n"
        "    info = {'k': 1}\n"
        "    _ = info['k']\n"
        "    return info.get('k')\n\n"
        "def outer(e):\n"
        "    info = e.device_info\n"
        "    def inner_shadow():\n"
        "        info = {'k': 2}\n"
        "        return info['k']\n"
        "    return info, inner_shadow()\n\n"
        "class DeviceOwner:\n"
        "    def __init__(self):\n"
        "        self.info = DeviceInfo(identifiers={('myhome', '1')})\n\n"
        "    def unrelated_local(self):\n"
        "        info = {'k': 3}\n"
        "        return info['k']\n\n"
        "class UnrelatedOwner:\n"
        "    def __init__(self):\n"
        "        self.info = {'k': 4}\n"
        "        _ = self.info['k']\n",
        encoding="utf-8",
    )
    checker = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker, target_dir=tmp_path)
    assert not checker.errors, f"Unexpected cross-scope leakage errors: {checker.errors}"


def test_deprecated_device_info_mapping_bare_name_plain_dict_vs_param(tmp_path):
    """Verify bare `device_info` bound exclusively to plain dicts passes, while unshadowed params/attributes fail."""
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    # One-function snippet matching @gdluck's exact review test (even without explicit imports)
    (clean_dir / "one_func.py").write_text(
        "def read_ids(entity):\n"
        "    device_info = device_info_to_dict(entity.device_info)\n"
        "    _ = device_info.get('identifiers')\n"
        "    return device_info['identifiers']\n",
        encoding="utf-8",
    )
    (clean_dir / "dict_variants.py").write_text(
        "from typing import Any\n"
        "from . import typing_compat\n"
        "from .typing_compat import device_info_to_dict as to_plain_dict\n\n"
        "def literal_and_qualified(entity, cond: bool):\n"
        "    device_info = {'identifiers': set()}\n"
        "    _ = device_info['identifiers']\n"
        "    _ = device_info.get('identifiers')\n"
        "    _attr_device_info: dict[str, Any]\n"
        "    if cond:\n"
        "        _attr_device_info = typing_compat.device_info_to_dict(entity.device_info)\n"
        "    else:\n"
        "        _attr_device_info = to_plain_dict(entity.device_info) or dict(identifiers=set())\n"
        "    copied = _attr_device_info\n"
        "    def inner():\n"
        "        return _attr_device_info.get('identifiers'), copied['identifiers']\n"
        "    return inner()\n",
        encoding="utf-8",
    )
    checker_clean = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_clean, target_dir=clean_dir)
    assert not checker_clean.errors, f"Unexpected errors on plain-dict device_info bindings: {checker_clean.errors}"

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "bad_cases.py").write_text(
        "def bad_param(device_info):\n"
        "    return device_info.get('identifiers')\n\n"
        "def bad_unbound():\n"
        "    return device_info['identifiers']\n\n"
        "def bad_mixed(entity, cond):\n"
        "    if cond:\n"
        "        device_info = entity.device_info\n"
        "    else:\n"
        "        device_info = {'identifiers': set()}\n"
        "    return device_info.get('identifiers')\n\n"
        "def bad_attr_despite_local(entity, other):\n"
        "    device_info = device_info_to_dict(entity.device_info)\n"
        "    _ = device_info.get('identifiers')\n"
        "    _ = entity.device_info == device_info_to_dict(other.device_info)\n"
        "    return entity.device_info['identifiers']\n",
        encoding="utf-8",
    )
    checker_bad = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_bad, target_dir=bad_dir)
    assert len(checker_bad.errors) == 5, (
        f"Expected 5 violations, got {len(checker_bad.errors)}: {checker_bad.errors}"
    )
    assert all("RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING" in err for err in checker_bad.errors)


def test_device_info_scan_covers_both_component_and_tests_excluding_typing_compat(tmp_path):
    """Default scan covers both custom_components/myhome/ and tests/ while excluding typing_compat.py."""
    default_files = {p.resolve() for p in _resolve_device_info_scan_files(None)}
    assert (CUSTOM_COMPONENTS_DIR / "myhome_device.py").resolve() in default_files
    assert (CUSTOM_COMPONENTS_DIR / "sensor.py").resolve() in default_files
    assert (TESTS_DIR / "test_ha_standards.py").resolve() in default_files
    assert (TESTS_DIR / "test_platforms.py").resolve() in default_files
    assert TYPING_COMPAT_FILE.resolve() not in default_files

    comp_dir = tmp_path / "custom_components" / "myhome"
    tests_dir = tmp_path / "tests"
    comp_dir.mkdir(parents=True)
    tests_dir.mkdir(parents=True)

    # typing_compat.py intentionally converts Mapping -> dict and must be excluded.
    (comp_dir / "typing_compat.py").write_text(
        "from collections.abc import Mapping\n"
        "from typing import Any\n\n"
        "def device_info_to_dict(device_info: Any) -> dict[str, Any]:\n"
        "    if isinstance(device_info, Mapping):\n"
        "        return dict(device_info)\n"
        "    return {}\n",
        encoding="utf-8",
    )
    (comp_dir / "sensor.py").write_text(
        "from .typing_compat import device_info_to_dict\n\n"
        "def read_sensor_ids(entity):\n"
        "    return device_info_to_dict(entity.device_info).get('identifiers')\n",
        encoding="utf-8",
    )
    # Exact PR #952 HA Compatibility (dev) failure patterns inside tests/:
    (tests_dir / "test_pr952_regression.py").write_text(
        "def test_shared_device_ids(act3_sensor, zone1, living_act3):\n"
        "    assert act3_sensor.device_info['identifiers'] == zone1.device_info['identifiers']\n"
        "    dev_info = living_act3.device_info\n"
        "    assert dev_info['identifiers'] == {('myhome', 'mac-4-#1')}\n",
        encoding="utf-8",
    )

    for target in (tmp_path, [comp_dir, tests_dir]):
        checker = StandardsChecker()
        check_no_deprecated_device_info_mapping(checker, target_dir=target)
        assert len(checker.errors) == 3, (
            f"Expected 3 test-suite violations for target={target!r}, got: {checker.errors}"
        )
        assert all("RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING" in err for err in checker.errors)
        assert all("test_pr952_regression.py" in err for err in checker.errors)


def test_deprecated_device_info_mapping_aliased_walrus_annotated_and_unpacking(tmp_path):
    """Verify AST rule across passing idioms, direct mapping ops, and aliased/walrus/annotated/unpacked vars."""
    clean_dir = tmp_path / "clean_ext"
    clean_dir.mkdir()
    (clean_dir / "clean_patterns.py").write_text(
        "from typing import Any\n"
        "from homeassistant.helpers.device_registry import DeviceInfo\n"
        "from custom_components.myhome.typing_compat import device_info_to_dict\n\n"
        "def clean_usages(entity, dev_reg, entities) -> None:\n"
        "    assert entity.device_info is not None\n"
        "    _ = device_info_to_dict(entity.device_info)['identifiers']\n"
        "    built = DeviceInfo(identifiers={('myhome', '1')}, name='Zone 1', model='F459')\n"
        "    assert built is not None\n"
        "    info_dict = entity.device_info.as_dict()\n"
        "    _ = info_dict['identifiers']\n"
        "    device_info = entity.device_info.as_dict()\n"
        "    _ = device_info['identifiers']\n"
        "    dev_entry = dev_reg.async_get('dev-id')\n"
        "    _ = (dev_entry.identifiers, dev_entry.name)\n"
        "    plain_cfg: dict[str, Any] = {'identifiers': 'abc', 'model': 'F411'}\n"
        "    _ = (plain_cfg['identifiers'], plain_cfg.get('model'), 'model' in plain_cfg)\n"
        "    by_id: dict[str, DeviceInfo] = {}\n"
        "    _ = by_id.get('k')\n"
        "    info = {'items': entities}\n"
        "    _ = [info.device_info for info in info['items']]\n",
        encoding="utf-8",
    )
    checker_clean = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_clean, target_dir=clean_dir)
    assert not checker_clean.errors, f"Unexpected errors on clean idioms: {checker_clean.errors}"

    direct_bad_dir = tmp_path / "direct_bad"
    direct_bad_dir.mkdir()
    (direct_bad_dir / "direct_bad.py").write_text(
        "from typing import Any, cast\n\n"
        "def sink(**kwargs: Any) -> dict[str, Any]:\n"
        "    return kwargs\n\n"
        "def bad_direct(entity) -> None:\n"
        "    _ = entity.device_info['identifiers']\n"
        "    del entity.device_info['name']\n"
        "    _ = entity._attr_device_info.get('model')\n"
        "    _ = entity.device_info.values()\n"
        "    _ = entity.device_info.setdefault('model', 'X')\n"
        "    _ = entity.device_info.pop('model', None)\n"
        "    _ = entity.device_info.copy()\n"
        "    _ = 'identifiers' in entity.device_info\n"
        "    _ = 'model' not in entity._attr_device_info\n"
        "    _ = dict(entity.device_info)\n"
        "    _ = {**entity.device_info}\n"
        "    _ = sink(**entity._attr_device_info)\n"
        "    _ = cast(dict[str, Any], entity.device_info)['identifiers']\n"
        "    _ = getattr(entity, 'device_info')['identifiers']\n"
        "    _ = len(entity.device_info)\n"
        "    _ = list(entity.device_info)\n"
        "    for _k in entity.device_info:\n"
        "        pass\n"
        "    _ = [k for k in entity.device_info]\n"
        "    match entity.device_info:\n"
        "        case {'identifiers': _ids}:\n"
        "            pass\n",
        encoding="utf-8",
    )
    checker_direct = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_direct, target_dir=direct_bad_dir)
    assert len(checker_direct.errors) == 19, (
        f"Expected 19 direct violations, got {len(checker_direct.errors)}: {checker_direct.errors}"
    )
    assert all("RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING" in err for err in checker_direct.errors)

    aliased_bad_dir = tmp_path / "aliased_bad"
    aliased_bad_dir.mkdir()
    (aliased_bad_dir / "aliased_bad.py").write_text(
        "from typing import Any\n"
        "from homeassistant.helpers.device_registry import DeviceInfo\n\n"
        "def bad_aliased(entity, act3, zone1, source) -> None:\n"
        "    info = entity.device_info\n"
        "    _ = info['identifiers']\n"
        "    _ = info.get('model')\n"
        "    _ = info.keys()\n"
        "    _ = info.values()\n"
        "    _ = info.items()\n"
        "    _ = 'name' in info\n"
        "    _ = 'via_device_id' not in info\n"
        "    _ = dict(info)\n"
        "    _ = {**info}\n"
        "    ann_info: Any = entity._attr_device_info\n"
        "    _ = ann_info['identifiers']\n"
        "    typed_local: DeviceInfo | None = source()\n"
        "    _ = typed_local.get('name')\n"
        "    assert (walrus_info := entity.device_info) is not None\n"
        "    _ = walrus_info['identifiers']\n"
        "    first, second = act3.device_info, zone1.device_info\n"
        "    _ = (first['identifiers'], second.get('name'))\n"
        "    for item in (act3.device_info, zone1.device_info):\n"
        "        _ = item['identifiers']\n"
        "    _ = [comp_item['identifiers'] for comp_item in (act3.device_info, zone1.device_info)]\n\n"
        "def bad_typed_param(param_di: DeviceInfo | None) -> Any:\n"
        "    return param_di['identifiers'] if param_di else None\n",
        encoding="utf-8",
    )
    checker_aliased = StandardsChecker()
    check_no_deprecated_device_info_mapping(checker_aliased, target_dir=aliased_bad_dir)
    assert len(checker_aliased.errors) == 17, (
        f"Expected 17 aliased violations, got {len(checker_aliased.errors)}: {checker_aliased.errors}"
    )
    assert all("RULE_NO_DEPRECATED_DEVICE_INFO_MAPPING" in err for err in checker_aliased.errors)


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
                "homeassistant": "2026.8.0",
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
                "homeassistant": "2026.8.0",
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
                "homeassistant": "2026.8.0",
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

    # 8. Unpinned hacs/action, if/continue-on-error bypasses, HA version < 2026.8.0, category duplicates/unknowns/overlaps, brand PNG checks, and unparseable OUTPUT_ZIP
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
                "homeassistant": "2026.7.4",
            }
        ),
        encoding="utf-8",
    )
    checker_edge2 = StandardsChecker()
    check_marketplace_manifest_rule(checker_edge2, root_dir=tmp_path)
    assert any("must be 'custom_pkg.zip' to match scripts/package_release.py" in err for err in checker_edge2.errors)
    assert any("must be >= '2026.8.0'" in err for err in checker_edge2.errors)
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


_ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter_scanlines(raw: bytes, offset: int, width: int, height: int) -> tuple[bytearray, int]:
    """Decode one PNG pass of 8-bit RGBA scanlines starting at ``offset``."""
    stride = width * 4
    out = bytearray(height * stride)
    prev = bytearray(stride)
    for y in range(height):
        ftype = raw[offset]
        offset += 1
        row = bytearray(raw[offset : offset + stride])
        offset += stride
        for i in range(stride):
            left = row[i - 4] if i >= 4 else 0
            up = prev[i]
            up_left = prev[i - 4] if i >= 4 else 0
            if ftype == 1:
                row[i] = (row[i] + left) & 0xFF
            elif ftype == 2:
                row[i] = (row[i] + up) & 0xFF
            elif ftype == 3:
                row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
            elif ftype == 4:
                row[i] = (row[i] + _paeth_predictor(left, up, up_left)) & 0xFF
            elif ftype != 0:
                raise ValueError(f"Unsupported PNG filter type {ftype}")
        out[y * stride : (y + 1) * stride] = row
        prev = row
    return out, offset


def _parse_png(path: Path) -> tuple[tuple[int, int, int, int, int, int, int], dict[bytes, list[bytes]]]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks: dict[bytes, list[bytes]] = {}
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        ctype = data[pos + 4 : pos + 8]
        cdata = data[pos + 8 : pos + 8 + length]
        chunks.setdefault(ctype, []).append(cdata)
        pos += 12 + length
    ihdr = struct.unpack(">IIBBBBB", chunks[b"IHDR"][0])
    return ihdr, chunks


def _decode_png_rgba(path: Path) -> tuple[int, int, bytearray]:
    """Decode an 8-bit RGBA PNG (non-interlaced or Adam7 interlaced) using stdlib only."""
    ihdr, chunks = _parse_png(path)
    width, height, bit_depth, color_type, _comp, _filt, interlace = ihdr
    assert (bit_depth, color_type) == (8, 6)
    raw = zlib.decompress(b"".join(chunks[b"IDAT"]))
    pixels = bytearray(width * height * 4)
    if interlace == 0:
        decoded, _ = _unfilter_scanlines(raw, 0, width, height)
        return width, height, decoded

    assert interlace == 1
    offset = 0
    for x0, y0, dx, dy in _ADAM7_PASSES:
        pw = (width - x0 + dx - 1) // dx
        ph = (height - y0 + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        sub, offset = _unfilter_scanlines(raw, offset, pw, ph)
        for sy in range(ph):
            dy_idx = y0 + sy * dy
            for sx in range(pw):
                dx_idx = x0 + sx * dx
                s_idx = (sy * pw + sx) * 4
                d_idx = (dy_idx * width + dx_idx) * 4
                pixels[d_idx : d_idx + 4] = sub[s_idx : s_idx + 4]
    return width, height, pixels


def _contrast_ratio(rgb_a: tuple[int, int, int], rgb_b: tuple[int, int, int]) -> float:
    def rel_lum(rgb: tuple[int, int, int]) -> float:
        lin = [
            (c / 255.0) / 12.92 if (c / 255.0) <= 0.03928 else ((c / 255.0 + 0.055) / 1.055) ** 2.4
            for c in rgb
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    la, lb = rel_lum(rgb_a), rel_lum(rgb_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _make_solid_png(width: int, height: int, rgba: tuple[int, int, int, int] = (255, 255, 255, 255)) -> bytes:
    def chunk(ctype: bytes, cdata: bytes) -> bytes:
        crc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
        return struct.pack(">I", len(cdata)) + ctype + cdata + struct.pack(">I", crc)

    row = b"\x00" + bytes(rgba) * width
    compressed = zlib.compress(row * height)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def test_local_brand_assets_light_and_dark_compliance():
    """Verify all 8 local brand PNGs match HA specifications, transparency, encoding, and dark contrast."""
    brand_dir = CUSTOM_COMPONENTS_DIR / "brand"
    assert sorted(p.name for p in brand_dir.iterdir()) == sorted(ALLOWED_BRAND_IMAGES)

    expected_dims = {
        "icon.png": (256, 256),
        "icon@2x.png": (512, 512),
        "dark_icon.png": (256, 256),
        "dark_icon@2x.png": (512, 512),
        "logo.png": (453, 128),
        "logo@2x.png": (907, 256),
        "dark_logo.png": (453, 128),
        "dark_logo@2x.png": (907, 256),
    }

    decoded: dict[str, tuple[int, int, bytearray]] = {}
    for name, dims in expected_dims.items():
        path = brand_dir / name
        ihdr, chunks = _parse_png(path)
        width, height, bit_depth, color_type, comp, filt, interlace = ihdr
        assert (width, height) == dims
        assert (bit_depth, color_type, comp, filt) == (8, 6, 0, 0)
        if name.startswith("dark_"):
            assert interlace == 1, f"{name} should be Adam7 interlaced"
            assert chunks.get(b"sRGB") == [b"\x00"], f"{name} must include sRGB perceptual chunk"
            assert path.read_bytes() != (brand_dir / name.removeprefix("dark_")).read_bytes()

        w, h, px = _decode_png_rgba(path)
        for cx, cy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            assert px[(cy * w + cx) * 4 + 3] == 0, f"{name} corner ({cx},{cy}) must be transparent"
        decoded[name] = (w, h, px)

    # Dark icons: grey Legrand badge + (R) preserved verbatim; MyHOME typography recolored to #F2F2F2
    for light_name, dark_name, half_y, badge_x in (
        ("icon.png", "dark_icon.png", 128, 140),
        ("icon@2x.png", "dark_icon@2x.png", 256, 280),
    ):
        w, h, l_px = decoded[light_name]
        _, _, d_px = decoded[dark_name]
        opaque_typo = 0
        for y in range(h):
            for x in range(w):
                idx = (y * w + x) * 4
                dr, dg, db, da = d_px[idx : idx + 4]
                if y < half_y and x >= badge_x:
                    assert d_px[idx : idx + 4] == l_px[idx : idx + 4]
                elif da == 255:
                    assert (dr, dg, db) == (242, 242, 242)
                    opaque_typo += 1
        assert opaque_typo > 1000
        assert _contrast_ratio((242, 242, 242), (28, 28, 28)) >= 10.0

    # Dark logos: Legrand red & BTicino orange 'b' preserved verbatim; MyHOME & 'ticino' recolored to #F2F2F2
    for light_name, dark_name, row_y, red_bounds, orange_bounds, ticino_x in (
        ("logo.png", "dark_logo.png", 89, (168, 280), (310, 333), 334),
        ("logo@2x.png", "dark_logo@2x.png", 178, (335, 560), (620, 666), 667),
    ):
        w, h, l_px = decoded[light_name]
        _, _, d_px = decoded[dark_name]
        opaque_dark_typo = 0
        for y in range(h):
            for x in range(w):
                idx = (y * w + x) * 4
                dr, dg, db, da = d_px[idx : idx + 4]
                if y >= row_y and (
                    red_bounds[0] <= x <= red_bounds[1]
                    or orange_bounds[0] <= x <= orange_bounds[1]
                ):
                    assert d_px[idx : idx + 4] == l_px[idx : idx + 4]
                elif da == 255 and (y < row_y - 2 or x >= ticino_x):
                    assert (dr, dg, db) == (242, 242, 242)
                    opaque_dark_typo += 1
        assert opaque_dark_typo > 1000


def test_brand_dir_validation_catches_invalid_dark_and_size_variants(tmp_path):
    """Verify check_marketplace_manifest_rule catches unknown files, bad dimensions, and identical dark fallbacks."""
    (tmp_path / "hacs.json").write_text(
        json.dumps(
            {
                "name": "MyHOME",
                "zip_release": True,
                "filename": "myhome.zip",
                "homeassistant": "2026.8.0",
            }
        ),
        encoding="utf-8",
    )
    manifest_dir = tmp_path / "custom_components" / "myhome"
    manifest_dir.mkdir(parents=True)
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
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "validate.yml").write_text(
        "jobs:\n"
        "  validate:\n"
        "    steps:\n"
        "      - uses: hacs/action@d556e736723344f83838d08488c983a15381059a\n"
        "        with:\n"
        "          category: integration\n",
        encoding="utf-8",
    )

    brand_dir = manifest_dir / "brand"
    brand_dir.mkdir()
    (brand_dir / "nested").mkdir()
    (brand_dir / "extra.png").write_bytes(_make_solid_png(16, 16))
    (brand_dir / "icon.png").write_bytes(_make_solid_png(128, 128))
    (brand_dir / "logo.png").write_bytes(_make_solid_png(400, 64))
    icon_2x_bytes = _make_solid_png(512, 512, (0, 0, 0, 255))
    (brand_dir / "icon@2x.png").write_bytes(icon_2x_bytes)
    (brand_dir / "dark_icon@2x.png").write_bytes(icon_2x_bytes)
    (brand_dir / "logo@2x.png").write_bytes(_make_solid_png(907, 256))

    checker = StandardsChecker()
    check_marketplace_manifest_rule(checker, root_dir=tmp_path)
    assert any("Local brand entry 'brand/nested' is not a file" in err for err in checker.errors)
    assert any("Unexpected file 'brand/extra.png' in brand/" in err for err in checker.errors)
    assert any("Local brand asset 'icon.png' is 128x128, expected 256x256" in err for err in checker.errors)
    assert any("Local brand asset 'logo.png' is 400x64, shortest side must be 128..256 px" in err for err in checker.errors)
    assert any("Dark brand asset 'dark_icon@2x.png' is byte-identical to 'icon@2x.png'" in err for err in checker.errors)
    assert any("Required local brand asset 'dark_icon.png' missing from brand/" in err for err in checker.errors)
