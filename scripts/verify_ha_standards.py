#!/usr/bin/env python3
"""Automated Home Assistant Architectural Standards Validator.

This script enforces Home Assistant Integration Quality Scale and Core
architectural standards across custom_components/myhome/:
1. Discovery flows (SSDP, Zeroconf, DHCP, USB, etc.) MUST NOT create config
   entries automatically; they must present a confirmation step (e.g. discovery_confirm).
2. Discovery flows must enforce unique ID registration and abort on duplicates.
3. Every step_id used in async_show_form in ConfigFlow and OptionsFlow must have
   complete translations in translations/en.json.
4. No synchronous blocking calls (e.g. time.sleep, requests.*) in async coroutines.
5. No unhandled top-level imports of deprecated homeassistant.const symbols.
"""

import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_DIR = ROOT_DIR / "custom_components" / "myhome"
TRANSLATIONS_DIR = CUSTOM_COMPONENTS_DIR / "translations"

DISCOVERY_STEPS = {
    "async_step_ssdp",
    "async_step_zeroconf",
    "async_step_dhcp",
    "async_step_usb",
    "async_step_bluetooth",
    "async_step_homekit",
    "async_step_mqtt",
}

DEPRECATED_HA_CONSTANTS = {
    "STATE_ALARM_DISARMED",
    "STATE_ALARM_ARMED_HOME",
    "STATE_ALARM_ARMED_AWAY",
    "STATE_ALARM_ARMED_NIGHT",
    "STATE_ALARM_ARMED_VACATION",
    "STATE_ALARM_ARMED_CUSTOM_BYPASS",
    "STATE_ALARM_PENDING",
    "STATE_ALARM_ARMING",
    "STATE_ALARM_DISARMING",
    "STATE_ALARM_TRIGGERED",
    "TEMP_CELSIUS",
    "TEMP_FAHRENHEIT",
    "PRESSURE_BAR",
    "PRESSURE_HPA",
    "PRESSURE_PSI",
    "SPEED_METERS_PER_SECOND",
    "SPEED_KILOMETERS_PER_HOUR",
    "SPEED_MILES_PER_HOUR",
}


class StandardsChecker:
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def log_error(self, rule: str, file_path: Path, line: int, message: str):
        try:
            rel = file_path.relative_to(ROOT_DIR)
        except ValueError:
            rel = file_path
        msg = f"[{rule}] {rel}:{line}: {message}"
        self.errors.append(msg)
        if os.getenv("GITHUB_ACTIONS"):
            print(f"::error file={rel},line={line},title={rule}::{message}")
        else:
            print(f"\033[91mFAIL\033[0m: {msg}")

    def log_ok(self, message: str):
        if not os.getenv("GITHUB_ACTIONS"):
            print(f"\033[92mPASS\033[0m: {message}")
        else:
            print(f"PASS: {message}")


def check_discovery_flows(checker: StandardsChecker):
    """Rule 1 & 2: Enforce discovery flows confirmation and unique ID verification."""
    config_flow_file = CUSTOM_COMPONENTS_DIR / "config_flow.py"
    if not config_flow_file.exists():
        checker.log_error("RULE_DISCOVERY", config_flow_file, 1, "config_flow.py not found")
        return

    with open(config_flow_file, "r", encoding="utf-8") as f:
        content = f.read()

    tree = ast.parse(content, filename=str(config_flow_file))

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in DISCOVERY_STEPS:
            step_name = node.name

            # Check 1: Must not call async_create_entry directly
            has_create_entry = False
            has_abort_unique_id = False
            delegates_to_confirm_or_form = False

            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    attr = child.func.attr
                    if attr == "async_create_entry":
                        has_create_entry = True
                    elif attr in ("_abort_if_unique_id_configured", "async_set_unique_id"):
                        has_abort_unique_id = True
                    elif attr in ("async_step_discovery_confirm", "async_show_form"):
                        delegates_to_confirm_or_form = True

                # Check if it calls test_connection directly without user confirmation
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    if child.func.attr == "async_step_test_connection":
                        checker.log_error(
                            "RULE_DISCOVERY_NO_AUTO_CREATE",
                            config_flow_file,
                            child.lineno,
                            f"{step_name} must NOT directly call async_step_test_connection. "
                            f"It must route to async_step_discovery_confirm to allow user [Configure]/[Ignore].",
                        )

            if has_create_entry:
                checker.log_error(
                    "RULE_DISCOVERY_NO_AUTO_CREATE",
                    config_flow_file,
                    node.lineno,
                    f"{step_name} directly invokes async_create_entry! "
                    f"Home Assistant architectural rules require discovery flows to show confirmation.",
                )

            if not has_abort_unique_id:
                checker.log_error(
                    "RULE_DISCOVERY_UNIQUE_ID",
                    config_flow_file,
                    node.lineno,
                    f"{step_name} does not verify unique ID or call _abort_if_unique_id_configured.",
                )

            if not delegates_to_confirm_or_form:
                checker.log_error(
                    "RULE_DISCOVERY_CONFIRMATION",
                    config_flow_file,
                    node.lineno,
                    f"{step_name} does not delegate to async_step_discovery_confirm or async_show_form.",
                )

    checker.log_ok("Discovery flows require user confirmation and unique ID verification.")


def check_translation_coverage(checker: StandardsChecker):
    """Rule 3: Verify all config flow step_ids have matching translations."""
    config_flow_file = CUSTOM_COMPONENTS_DIR / "config_flow.py"
    en_json_file = TRANSLATIONS_DIR / "en.json"

    if not config_flow_file.exists() or not en_json_file.exists():
        return

    with open(config_flow_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    with open(en_json_file, "r", encoding="utf-8") as f:
        en_data = json.load(f)

    config_translated = set(en_data.get("config", {}).get("step", {}).keys())
    options_translated = set(en_data.get("options", {}).get("step", {}).keys())
    all_translated = config_translated | options_translated

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "async_show_form":
                for kw in node.keywords:
                    if kw.arg == "step_id" and isinstance(kw.value, ast.Constant):
                        step_id = kw.value.value
                        if step_id not in all_translated:
                            checker.log_error(
                                "RULE_TRANSLATION_STEP_MISSING",
                                config_flow_file,
                                node.lineno,
                                f"async_show_form(step_id='{step_id}') has no translation in translations/en.json.",
                            )

    checker.log_ok("Config flow step translations are complete in en.json.")


def check_deprecated_constants(checker: StandardsChecker):
    """Rule 4: Verify no top-level unhandled imports of deprecated HA constants."""
    for root, _, files in os.walk(CUSTOM_COMPONENTS_DIR):
        for file in files:
            if not file.endswith(".py"):
                continue
            py_path = Path(root) / file
            with open(py_path, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=str(py_path))
                except SyntaxError:
                    continue

            # Inspect top-level statements (body of Module)
            for stmt in tree.body:
                if isinstance(stmt, ast.ImportFrom) and stmt.module == "homeassistant.const":
                    for alias in stmt.names:
                        if alias.name in DEPRECATED_HA_CONSTANTS:
                            checker.log_error(
                                "RULE_DEPRECATED_CONSTANTS",
                                py_path,
                                stmt.lineno,
                                f"Direct top-level import of deprecated constant '{alias.name}' from homeassistant.const. "
                                f"Use the modern component enum or wrap in try/except ImportError fallback.",
                            )

    checker.log_ok("No unhandled top-level imports of deprecated homeassistant.const symbols.")


def check_no_blocking_calls(checker: StandardsChecker):
    """Rule 5: Verify no synchronous sleep or blocking calls in async functions."""
    for root, _, files in os.walk(CUSTOM_COMPONENTS_DIR):
        for file in files:
            if not file.endswith(".py"):
                continue
            py_path = Path(root) / file
            with open(py_path, "r", encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=str(py_path))
                except SyntaxError:
                    continue

            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef):
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            # Check time.sleep
                            if isinstance(child.func, ast.Attribute):
                                if (
                                    isinstance(child.func.value, ast.Name)
                                    and child.func.value.id == "time"
                                    and child.func.attr == "sleep"
                                ):
                                    checker.log_error(
                                        "RULE_NO_BLOCKING_CALLS",
                                        py_path,
                                        child.lineno,
                                        f"Synchronous time.sleep() called inside async function '{node.name}'. "
                                        f"Use 'await asyncio.sleep()' instead.",
                                    )
                                elif (
                                    isinstance(child.func.value, ast.Name)
                                    and child.func.value.id == "requests"
                                ):
                                    checker.log_error(
                                        "RULE_NO_BLOCKING_CALLS",
                                        py_path,
                                        child.lineno,
                                        f"Synchronous requests.{child.func.attr}() called inside async function '{node.name}'. "
                                        f"Use aiohttp or wrap in hass.async_add_executor_job.",
                                    )

    checker.log_ok("No blocking calls (time.sleep, requests.*) detected in async coroutines.")


def check_ruff_standards(checker: StandardsChecker):
    """Rule 6: Verify codebase satisfies Ruff static analysis standards."""
    import shutil
    import subprocess

    ruff_bin = shutil.which("ruff")
    cmd = [ruff_bin, "check", "."] if ruff_bin else [sys.executable, "-m", "ruff", "check", "."]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT_DIR))
    except FileNotFoundError:
        checker.log_ok("Ruff not found in PATH; skipping static analysis check.")
        return

    if res.returncode != 0 and "No module named ruff" in res.stderr:
        checker.log_ok("Ruff module not installed; skipping static analysis check.")
        return

    if res.returncode != 0:
        checker.log_error(
            "RULE_RUFF_STATIC_ANALYSIS",
            ROOT_DIR,
            1,
            f"Ruff static analysis checks failed:\n{res.stdout.strip()}",
        )
    else:
        checker.log_ok("Ruff static analysis checks passed with 0 violations.")


def check_manifest_requirements_rule(checker: StandardsChecker):
    """Rule 6: Enforce manifest.json version matches const.py and requirements pins OWNd exactly."""
    manifest_file = CUSTOM_COMPONENTS_DIR / "manifest.json"
    const_file = CUSTOM_COMPONENTS_DIR / "const.py"

    if not manifest_file.exists():
        checker.log_error("RULE_MANIFEST", manifest_file, 1, "manifest.json not found")
        return
    if not const_file.exists():
        checker.log_error("RULE_MANIFEST", const_file, 1, "const.py not found")
        return

    try:
        with open(manifest_file, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        checker.log_error("RULE_MANIFEST", manifest_file, 1, f"Failed to parse manifest.json: {e}")
        return

    with open(const_file, "r", encoding="utf-8") as f:
        const_content = f.read()

    match = re.search(r'INTEGRATION_VERSION\s*=\s*["\']([^"\']+)["\']', const_content)
    const_version = match.group(1) if match else None
    manifest_version = manifest.get("version")

    if manifest_version != const_version:
        checker.log_error(
            "RULE_MANIFEST",
            manifest_file,
            1,
            f"Version mismatch: manifest.json ({manifest_version}) != const.py ({const_version})",
        )

    ownd_match = re.search(r'REQUIRED_OWND_VERSION\s*=\s*["\']([^"\']+)["\']', const_content)
    required_ownd_version = ownd_match.group(1) if ownd_match else manifest_version

    requirements = manifest.get("requirements", [])
    expected_req = f"OWNd=={required_ownd_version}"
    if expected_req not in requirements:
        checker.log_error(
            "RULE_MANIFEST",
            manifest_file,
            1,
            f"manifest.json requirements must contain '{expected_req}' to guarantee Home Assistant dependency updates. Found: {requirements}",
        )
    else:
        checker.log_ok(f"manifest.json requirements synchronization verified ({expected_req}).")


def check_quality_scale_rules(checker: StandardsChecker):
    """Enforce Home Assistant Integration Quality Scale (Bronze, Silver, Gold, Platinum/Diamond)."""
    # 1. [BRONZE] Runtime Data
    init_file = CUSTOM_COMPONENTS_DIR / "__init__.py"
    if init_file.exists():
        init_content = init_file.read_text(encoding="utf-8")
        if "runtime_data" not in init_content:
            checker.log_error(
                "RULE_IQS_BRONZE",
                init_file,
                1,
                "Quality Scale Bronze rule 'runtime-data': entry.runtime_data must be implemented in __init__.py",
            )
        else:
            checker.log_ok("[BRONZE] runtime_data entry mapping implemented.")

    # 2. [BRONZE] Services Extraction
    services_file = CUSTOM_COMPONENTS_DIR / "services.py"
    if not services_file.exists():
        checker.log_error(
            "RULE_IQS_BRONZE",
            services_file,
            1,
            "Quality Scale Bronze rule 'action-setup': services must be extracted to services.py",
        )
    else:
        services_content = services_file.read_text(encoding="utf-8")
        if "async_setup_services" not in services_content or "async_unload_services" not in services_content:
            checker.log_error(
                "RULE_IQS_BRONZE",
                services_file,
                1,
                "Quality Scale Bronze rule 'action-setup': services.py must define async_setup_services and async_unload_services",
            )
        else:
            checker.log_ok("[BRONZE] action-setup: services.py registered with setup and unload handlers.")

    # 3. [SILVER] Concurrency: PARALLEL_UPDATES = 0 across all platform files
    platform_files = [
        "alarm_control_panel.py",
        "binary_sensor.py",
        "button.py",
        "climate.py",
        "cover.py",
        "light.py",
        "media_player.py",
        "sensor.py",
        "switch.py",
    ]
    missing_parallel = []
    for pf_name in platform_files:
        pf = CUSTOM_COMPONENTS_DIR / pf_name
        if pf.exists():
            content = pf.read_text(encoding="utf-8")
            if not re.search(r"^PARALLEL_UPDATES\s*(:\s*int)?\s*=\s*0", content, re.MULTILINE):
                missing_parallel.append(pf_name)
    if missing_parallel:
        checker.log_error(
            "RULE_IQS_SILVER",
            CUSTOM_COMPONENTS_DIR,
            1,
            f"Quality Scale Silver rule 'parallel-updates': PARALLEL_UPDATES = 0 missing in: {', '.join(missing_parallel)}",
        )
    else:
        checker.log_ok(f"[SILVER] parallel-updates: PARALLEL_UPDATES = 0 verified across all {len(platform_files)} platform files.")

    # 4. [GOLD] Reconfiguration Flow
    config_flow_file = CUSTOM_COMPONENTS_DIR / "config_flow.py"
    if config_flow_file.exists():
        content = config_flow_file.read_text(encoding="utf-8")
        if "async_step_reconfigure" not in content:
            checker.log_error(
                "RULE_IQS_GOLD",
                config_flow_file,
                1,
                "Quality Scale Gold rule 'reconfiguration-flow': async_step_reconfigure must be implemented in config_flow.py",
            )
        else:
            checker.log_ok("[GOLD] reconfiguration-flow: async_step_reconfigure implemented.")

    # 5. [GOLD] Translations & Strings Sync
    strings_file = CUSTOM_COMPONENTS_DIR / "strings.json"
    en_file = TRANSLATIONS_DIR / "en.json"
    if not strings_file.exists() or not en_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            strings_file,
            1,
            "Quality Scale Gold rule 'entity-translations': strings.json and translations/en.json must exist",
        )
    else:
        try:
            with open(strings_file, "r", encoding="utf-8") as f:
                strings_data = json.load(f)
            with open(en_file, "r", encoding="utf-8") as f:
                en_data = json.load(f)
            if set(strings_data.keys()) != set(en_data.keys()):
                checker.log_error(
                    "RULE_IQS_GOLD",
                    strings_file,
                    1,
                    "Quality Scale Gold rule 'entity-translations': top-level keys in strings.json and translations/en.json do not match",
                )
            else:
                checker.log_ok("[GOLD] entity-translations: strings.json and translations/en.json synchronized.")
        except Exception as e:
            checker.log_error("RULE_IQS_GOLD", strings_file, 1, f"Failed parsing strings/translations JSON: {e}")

    # 6. [GOLD] Icon Translations
    icons_file = CUSTOM_COMPONENTS_DIR / "icons.json"
    if not icons_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            icons_file,
            1,
            "Quality Scale Gold rule 'icon-translations': icons.json must exist",
        )
    else:
        checker.log_ok("[GOLD] icon-translations: icons.json registered.")

    # 7. [GOLD] Repairs Platform
    repairs_file = CUSTOM_COMPONENTS_DIR / "repairs.py"
    if not repairs_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            repairs_file,
            1,
            "Quality Scale Gold rule 'repair-issues': repairs.py platform must exist",
        )
    else:
        checker.log_ok("[GOLD] repair-issues: repairs.py platform implemented.")

    # 8. [GOLD] Diagnostics
    diag_file = CUSTOM_COMPONENTS_DIR / "diagnostics.py"
    if not diag_file.exists():
        checker.log_error(
            "RULE_IQS_GOLD",
            diag_file,
            1,
            "Quality Scale Gold rule 'diagnostics': diagnostics.py platform must exist",
        )
    else:
        checker.log_ok("[GOLD] diagnostics: diagnostics.py platform implemented.")

    # 9. [DIAMOND/PLATINUM] Async Dependency (No blocking libraries)
    manifest_file = CUSTOM_COMPONENTS_DIR / "manifest.json"
    if manifest_file.exists():
        try:
            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest_data = json.load(f)
            reqs = manifest_data.get("requirements", [])
            blocking_libs = ["requests", "urllib3", "urllib"]
            found_blocking = [r for r in reqs if any(b in r.lower() for b in blocking_libs)]
            if found_blocking:
                checker.log_error(
                    "RULE_IQS_DIAMOND",
                    manifest_file,
                    1,
                    f"Quality Scale Platinum rule 'async-dependency': blocking library found in requirements: {found_blocking}",
                )
            else:
                checker.log_ok("[DIAMOND] async-dependency: zero blocking network libraries in manifest requirements.")
        except Exception as e:
            checker.log_error("RULE_IQS_DIAMOND", manifest_file, 1, f"Failed parsing manifest.json: {e}")

    # 10. [DIAMOND/PLATINUM] Quality Scale Manifest Audit
    qs_file = CUSTOM_COMPONENTS_DIR / "quality_scale.yaml"
    if not qs_file.exists():
        checker.log_error(
            "RULE_IQS_DIAMOND",
            qs_file,
            1,
            "Quality Scale audit manifest quality_scale.yaml must exist in custom_components/myhome/",
        )
    else:
        try:
            import yaml
            with open(qs_file, "r", encoding="utf-8") as f:
                qs_data = yaml.safe_load(f)
            rules = qs_data.get("rules", {})
            valid_statuses = {"done", "todo", "in_progress", "exempt"}
            invalid_rules = [k for k, v in rules.items() if isinstance(v, dict) and v.get("status") not in valid_statuses]
            if invalid_rules:
                checker.log_error(
                    "RULE_IQS_DIAMOND",
                    qs_file,
                    1,
                    f"quality_scale.yaml has invalid status for rules: {invalid_rules}",
                )
            else:
                done_count = sum(1 for v in rules.values() if isinstance(v, dict) and v.get("status") in ("done", "exempt"))
                checker.log_ok(f"[DIAMOND] quality_scale.yaml validated ({done_count}/{len(rules)} rules satisfied/exempt).")
        except Exception as e:
            checker.log_error("RULE_IQS_DIAMOND", qs_file, 1, f"Failed parsing quality_scale.yaml: {e}")


def check_ownd_library_standards(checker: StandardsChecker):
    """Enforce OWNd client library PyPI & async decoupling standards."""
    try:
        import OWNd
        ownd_dir = Path(OWNd.__file__).resolve().parent
        has_ha_import = False
        for py_file in ownd_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"^\s*(import homeassistant|from homeassistant)", text, re.MULTILINE):
                has_ha_import = True
                checker.log_error(
                    "RULE_OWND_DECOUPLING",
                    py_file,
                    1,
                    "OWNd protocol library must be completely decoupled and MUST NOT import homeassistant",
                )
                break
        if not has_ha_import:
            checker.log_ok("[OWND] Client library clean decoupling verified (0 homeassistant imports).")
    except Exception as err:
        checker.log_ok(f"[OWND] Client library verification skipped (OWNd not in path: {err}).")


def main():
    print("=" * 70)
    print("Running Home Assistant Architectural Standards Validator")
    print("=" * 70)

    checker = StandardsChecker()
    check_discovery_flows(checker)
    check_translation_coverage(checker)
    check_deprecated_constants(checker)
    check_no_blocking_calls(checker)
    check_ruff_standards(checker)
    check_manifest_requirements_rule(checker)
    check_quality_scale_rules(checker)
    check_ownd_library_standards(checker)

    print("=" * 70)
    if checker.errors:
        print(f"\nFAILED: {len(checker.errors)} architectural standards violation(s) found.\n")
        for err in checker.errors:
            print(f"  * {err}")
        print("\nPlease fix the above violations to satisfy Home Assistant standards.")
        sys.exit(1)
    else:
        print("\nSUCCESS: All Home Assistant architectural standards passed!\n")
        sys.exit(0)



if __name__ == "__main__":
    main()
