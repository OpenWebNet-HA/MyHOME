#!/usr/bin/env python3
"""Update README.md coverage table and coverage.svg from coverage.xml.

Maintains live, automated documentation of test coverage across all components.
Called locally and by the GitHub Actions test-coverage workflow.
"""
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERAGE_XML = os.path.join(REPO_ROOT, "coverage.xml")
README_MD = os.path.join(REPO_ROOT, "README.md")
COVERAGE_SVG = os.path.join(REPO_ROOT, "coverage.svg")
GOLDEN_CORPUS = os.path.join(REPO_ROOT, "tests", "golden", "corpus.json")

START_MARKER = "<!-- START_COVERAGE_TABLE -->"
END_MARKER = "<!-- END_COVERAGE_TABLE -->"

COMPONENT_NOTES = {
    "custom_components/myhome/cover_calibration_automatic.py": "Automatic open/close/open measurement within an owned panel calibration session",
    "custom_components/myhome/cover_calibration_batch.py": "Sequential multi-cover measurement with a reviewed atomic profile save",
    "custom_components/myhome/cover_calibration_check.py": "Measured-height verification runs using the active cover motion model",
    "custom_components/myhome/cover_calibration_fit.py": "Slat and roll geometry fitting from timed travel and tape measurements",
    "custom_components/myhome/cover_calibration_geometry.py": "Guided slat and roll measurement phases and geometry review",
    "custom_components/myhome/cover_calibration_recovery.py": "Live calibration recovery across panel tabs and connection changes",
    "custom_components/myhome/cover_calibration_reservation.py": "Gateway calibration ownership independent of client connection lifetime",
    "custom_components/myhome/cover_calibration_save.py": "Reviewed measurement persistence into local values or shared profiles",
    "custom_components/myhome/cover_geometry.py": "Validated profile geometry and nonlinear motion model resolution",
    "custom_components/myhome/cover_motion_runtime.py": "Motor-anchored nonlinear position tracking and remaining travel calculation",
    "custom_components/myhome/cover_profile_assignment.py": "Atomic multi-cover profile assignment with revision-bound previews",
    "custom_components/myhome/cover_profile_catalogue.py": "Gateway-scoped profile catalogue management without motor commands",
    "custom_components/myhome/cover_profile_export.py": "Versioned export of committed profiles and per-cover settings",
    "custom_components/myhome/cover_profile_mutations.py": "Per-cover overrides and confirmed shared-profile changes",
    "custom_components/myhome/cover_profile_provenance.py": "Backend-owned provenance and measurement evidence for cover settings",
    "custom_components/myhome/cover_profiles.py": "Gateway-scoped profile storage, atomic transactions and runtime snapshots",
    "custom_components/myhome/cover_settings.py": "Per-key resolution of native timings, profiles, overrides and geometry",
    "custom_components/myhome/cover_settings_api.py": "Registry-backed gateway overview for cover positioning settings",
    "custom_components/myhome/cover_translation.py": "Delayed movement fallback for command translations without a bus status",
    "custom_components/myhome/panel.py": "Native MyHOME administration panel registration, assets and sidebar settings",
    "custom_components/myhome/__init__.py": "Setup/unload lifecycle, static Lovelace card registration & cleanup, and migrations",
    "custom_components/myhome/access_policy.py": "Human-in-the-loop approval, pre-warning, and watchdog policy for impulse gates/doors",
    "custom_components/myhome/alarm_control_panel.py": "Read-only WHO=5 burglar alarm central unit status (3485/3486) and broadcast sync",
    "custom_components/myhome/binary_sensor.py": "WHO=1 PIR motion, WHO=9 AUX channels, and WHO=25 dry contacts with state inversion",
    "custom_components/myhome/bus_monitor.py": "In-band 500-frame circular ring buffer tap (0 extra gateway sockets)",
    "custom_components/myhome/button.py": "WHO=14 actuator lock/unlock, WHO=2 cover calibration, and WHO=16/22 FM tuner seek",
    "custom_components/myhome/climate.py": "WHO=4 zone thermostats, 4-pipe/fancoil controls, and Central Unit 3550/4695 sync",
    "custom_components/myhome/climate_discovery.py": "WHO=4 zone discovery: registry, myhome.yaml and bus frames, held actuator-only frames (#785), and the thermostat reads at start",
    "custom_components/myhome/config_flow.py": "Gateway onboarding, manual/SSDP setup, and subentry flow registration",
    "custom_components/myhome/config_flow_audio.py": "Audio matrix sources, FM tuner flags, environment defaults, and decoder slot options",
    "custom_components/myhome/config_flow_discovery.py": "SSDP discovery, connection probing, reauthentication, and reconfiguration steps",
    "custom_components/myhome/config_flow_expose.py": "Subentry flow for projecting Home Assistant entities onto the SCS bus",
    "custom_components/myhome/config_flow_options.py": "Gateway runtime options flow (worker sessions, fades, connection, ignored addresses)",
    "custom_components/myhome/config_flow_thermo_switch.py": "Subentry flow for WHO=4 probe-less thermoregulation / towel-warmer switches",
    "custom_components/myhome/config_flow_topology.py": "Shared-bus primary, secondary (delegated WHO), and warm-standby topology flows",
    "custom_components/myhome/const.py": "Protocol commands, dimensions, platform definitions, and integration constants",
    "custom_components/myhome/cover.py": "WHO=2 shutters/blinds and WHO=1 impulse gates/garage doors with state tracking",
    "custom_components/myhome/cover_calibration.py": "Automated on-bus stopwatch travel-time calibration engine for WHO=2 covers",
    "custom_components/myhome/cover_motion.py": "Pure travel-time position interpolation math and command echo suppression",
    "custom_components/myhome/cover_scope.py": "Stateless general, area, and group WHERE cover entities (WHO=2)",
    "custom_components/myhome/cover_strategies/__init__.py": "Strategy exports and registry for pluggable WHO=2 cover kinematics",
    "custom_components/myhome/cover_strategies/base.py": "Base protocol and shared timing state for WHO=2 cover kinematics strategies",
    "custom_components/myhome/cover_strategies/native.py": "Native percentage position strategy for advanced WHO=2 actuators (Dim 10/11)",
    "custom_components/myhome/cover_strategies/simple.py": "Open/close/stop strategy for uncalibrated standard WHO=2 actuators",
    "custom_components/myhome/cover_strategies/timed.py": "Interpolated virtual position strategy for travel-time calibrated WHO=2 shutters",
    "custom_components/myhome/cover_strategies/venetian.py": "Combined curtain travel and slat-tilt dead-reckoning strategy for Venetian blinds",
    "custom_components/myhome/data.py": "Typed per-entry runtime_data container and config-entry lookup helpers",
    "custom_components/myhome/decoder_allocation.py": "Deterministic decoder slot allocation, LRU eviction, and group topology bookkeeper",
    "custom_components/myhome/decoder_companion.py": "Cross-integration streaming companion discovery and loop-safe entity filtering",
    "custom_components/myhome/decoder_pool.py": "Dynamic streaming proxy audio pool coordinating matrix inputs and upstream players",
    "custom_components/myhome/device_health.py": "Hardware fault telemetry and self-clearing Home Assistant Repair issue lifecycle",
    "custom_components/myhome/device_trigger.py": "Stateless WHO=15 CEN and WHO=25 CEN+ pushbutton and rotary automation triggers",
    "custom_components/myhome/diagnostics.py": "Sanitized config-entry diagnostics export with credential, IP, and MAC redaction",
    "custom_components/myhome/discovery.py": "Shared entity restore, YAML overlay merge, dynamic bus discovery, and dispatcher",
    "custom_components/myhome/expose.py": "Virtual OpenWebNet responder projecting HA entities and system clock onto SCS",
    "custom_components/myhome/gateway.py": "Gateway coordinator, profile capability gates, startup sweeps, and bus lifecycle",
    "custom_components/myhome/gateway_confirm.py": "Point-to-point WHO=1 status read-back when a light's status does not follow an acknowledged command",
    "custom_components/myhome/gateway_events.py": "Inbound OpenWebNet frame classification, event bus emission, and doorbell routing",
    "custom_components/myhome/gateway_resync.py": "Debounced lighting status resync sweeps after group, area, and general broadcasts",
    "custom_components/myhome/gateway_sessions.py": "Persistent event session watchdog, priority command queue, and paced worker pool",
    "custom_components/myhome/identity.py": "Multi-source gateway model and firmware resolution (SSDP, WHO=13, WHO=1013)",
    "custom_components/myhome/ignored.py": "WHO/WHERE address parser and filter silencing discovery, entities, and fault alerts",
    "custom_components/myhome/legacy_yaml.py": "Backward-compatible myhome.yaml loader, schema validator, and platform normalizer",
    "custom_components/myhome/light.py": "WHO=1 relays, dimmers, DALI DT8 tunable white/HSV colour, and DIN bus timers",
    "custom_components/myhome/light_dali.py": "DALI DT8 feature-lock and Home Assistant colour-mode mutual exclusion helpers",
    "custom_components/myhome/light_fade.py": "Software-stepped dimmer brightness fade interpolation and cancellation engine",
    "custom_components/myhome/light_group.py": "Declared physical SCS lighting group entities (WHO=1, WHERE=#G) with member rollup",
    "custom_components/myhome/lock.py": "WHO=6 video door-entry electric strikes with constant-time PIN verification",
    "custom_components/myhome/media_player.py": "WHO=16 F441/F441M audio zones, WHO=16/22 FM RDS tuners, and dynamic streaming proxy",
    "custom_components/myhome/media_player_decoder.py": "Upstream streaming decoder mirroring, fixed pre-gain feed trim, and anti-hiss auto-off",
    "custom_components/myhome/media_player_group.py": "Multi-room audio zone join/unjoin, master hand-over, parking, and group wake",
    "custom_components/myhome/media_player_pool.py": "Gateway options-to-DecoderPool builder and live pool reconfiguration helpers",
    "custom_components/myhome/media_player_power.py": "Audio zone power-state machine, frame classification, and auto-off delay rules",
    "custom_components/myhome/media_player_routing.py": "WHO=16 environment/amplifier matrix routing address math",
    "custom_components/myhome/media_player_source.py": "Matrix input source naming, tuner binding, and per-environment default routing",
    "custom_components/myhome/media_player_zone.py": "Shared state, volume/mute primitives, and DecoderPool access for audio zone mixins",
    "custom_components/myhome/migrate.py": "Config-entry version upgrades and zero-friction entity/device registry migrations",
    "custom_components/myhome/myhome_device.py": "Base entity class, DeviceInfo registry linkage, and availability lifecycle",
    "custom_components/myhome/number.py": "Direct FM radio frequency number entities (87.50–108.00 MHz) for F500/F500N tuners",
    "custom_components/myhome/poll_health.py": "Adaptive suppression of non-answering bus addresses during background status polls",
    "custom_components/myhome/repairs.py": "Home Assistant Repair issue builders, interactive fix flows, and cleanup helpers",
    "custom_components/myhome/router.py": "O(1) WHO/WHERE frame router dispatching bus telegrams to registered entities",
    "custom_components/myhome/sensor.py": "WHO=18 active power & total/daily/monthly energy counters, WHO=4 probes, WHO=1 lux",
    "custom_components/myhome/services.py": "Domain service registrations, Voluptuous schemas, and gateway/entity action handlers",
    "custom_components/myhome/sound_source.py": "F500/F500N FM RDS tuner media_player entity, preset browser, and WHO=16/22 tuning",
    "custom_components/myhome/switch.py": "WHO=1 relay/outlet switches, WHO=4 thermo actuator switches, and WHO=9/25 invert switches",
    "custom_components/myhome/thermo_switch.py": "Runtime helpers and subentry sync for WHO=4 probe-less thermo actuator switches",
    "custom_components/myhome/topology.py": "Shared-bus role validation, WHO delegation rules, and warm-standby failover logic",
    "custom_components/myhome/tuner_decode.py": "Pure WHO=16 and WHO=22 FM frequency, preset, and RDS text frame decoders",
    "custom_components/myhome/typing_compat.py": "Cross-version Home Assistant typing compatibility shims for strict mypy",
    "custom_components/myhome/validate.py": "Voluptuous schemas for myhome.yaml devices, canonical WHERE rules, and access policies",
    "custom_components/myhome/websocket.py": "WebSocket API for real-time bus monitor streaming, frame history, and diagnostics",
    "custom_components/myhome/where_grammar.py": "Canonical OpenWebNet APL, F422 #4#bus, and WHO=4 zone/actuator WHERE grammar",
}


JUNIT_XML = os.path.join(REPO_ROOT, "junit.xml")


def _is_fresh_junit(junit_xml: str, coverage_xml: str) -> bool:
    """Return True if junit.xml exists and is not stale relative to coverage.xml."""
    if not os.path.exists(junit_xml):
        return False
    if not os.path.exists(coverage_xml):
        return True
    try:
        return os.path.getmtime(junit_xml) >= os.path.getmtime(coverage_xml) - 120.0
    except OSError:
        return False


def get_test_count(
    junit_xml: str | None = None,
    coverage_xml: str | None = None,
) -> int:
    """Dynamically determine total test count from a fresh junit.xml or pytest collection."""
    junit_path = JUNIT_XML if junit_xml is None else junit_xml
    cov_path = COVERAGE_XML if coverage_xml is None else coverage_xml
    if _is_fresh_junit(junit_path, cov_path):
        try:
            tree = ET.parse(junit_path)
            root = tree.getroot()
            if "tests" in root.attrib:
                count = int(root.attrib["tests"])
                if count > 0:
                    return count
            suites = root.findall(".//testsuite")
            if suites:
                total = sum(int(ts.attrib.get("tests", 0)) for ts in suites)
                if total > 0:
                    return total
        except Exception:
            pass

    try:
        import pytest

        class TestCollector:
            def __init__(self):
                self.count = 0

            def pytest_collection_modifyitems(self, items):
                self.count = len(items)

        collector = TestCollector()
        pytest.main(
            ["--collect-only", "-q", "-o", "addopts=", os.path.join(REPO_ROOT, "tests")],
            plugins=[collector],
        )
        if collector.count > 0:
            return collector.count
    except Exception:
        pass

    for cmd in [
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts="],
        ["poetry", "run", "pytest", "--collect-only", "-q", "-o", "addopts="],
        ["pytest", "--collect-only", "-q", "-o", "addopts="],
    ]:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=45)
            m = re.search(r"(\d+)\s+tests?\s+collected", res.stdout)
            if m:
                return int(m.group(1))
        except Exception:
            pass

    return 701


def get_golden_corpus_count() -> int | None:
    """Number of frame fixtures in the Golden Corpus, or None if it cannot be read."""
    try:
        with open(GOLDEN_CORPUS, encoding="utf-8") as f:
            return len(json.load(f))
    except (OSError, ValueError, TypeError):
        return None


def normalize_coverage_filename(fn: str) -> str:
    """Normalize filenames from coverage.xml to custom_components/myhome/... paths."""
    fn = fn.replace("\\", "/")
    if os.path.isabs(fn):
        try:
            fn = os.path.relpath(fn, REPO_ROOT).replace("\\", "/")
        except ValueError:
            pass
    if not fn.startswith("custom_components/myhome/"):
        if fn.startswith("myhome/"):
            fn = f"custom_components/{fn}"
        else:
            fn = f"custom_components/myhome/{fn}"
    return fn


def update_readme_and_svg():
    if not os.path.exists(COVERAGE_XML):
        print(f"Error: {COVERAGE_XML} not found. Run pytest with --cov-report=xml first.")
        sys.exit(1)

    tree = ET.parse(COVERAGE_XML)
    root = tree.getroot()

    # ── 1. Collect per-component coverage & enforce strict 100% ─────────────
    file_rates = {}
    for p in root.findall(".//package"):
        for c in p.findall(".//class"):
            raw_fn = c.attrib.get("filename", "")
            fn = normalize_coverage_filename(raw_fn)
            # Filter out non-code or trivial init
            if fn in ("custom_components/myhome/ownd/__main__.py", "custom_components/myhome/ownd/__init__.py"):
                continue
            cr = float(c.attrib.get("line-rate", 0)) * 100
            file_rates[fn] = cr

    # Fail immediately if any module dropped below 100% coverage
    failing = [(fn, rate) for fn, rate in file_rates.items() if rate < 100.0]
    if failing:
        print("\n" + "=" * 78)
        print("[CRITICAL] COVERAGE ENFORCEMENT ERROR: Test coverage dropped below strict 100.0%!")
        print("=" * 78)
        for fn, rate in failing:
            print(f"  [MISS] {fn}: {rate:.1f}%")
        print("=" * 78)
        print("Refusing to commit degraded coverage table or badge to Git repository.")
        print("Failing CI step immediately.")
        sys.exit(1)

    total_rate = float(root.attrib.get("line-rate", 0)) * 100
    rate_round = round(total_rate)

    test_count = get_test_count()
    test_count_rounded = (test_count // 10) * 10
    print(f"Dynamically detected test count: {test_count} (rounded: {test_count_rounded})")

    # ── 2. Generate updated coverage.svg ─────────────────────────────────────
    color = "#4c1" if rate_round >= 80 else ("#dfb317" if rate_round >= 60 else "#e05d44")
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="98" height="20"><linearGradient id="b" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><mask id="a"><rect width="98" height="20" rx="3" fill="#fff"/></mask><g mask="url(#a)"><path fill="#555" d="M0 0h61v20H0z"/><path fill="{color}" d="M61 0h37v20H61z"/><path fill="url(#b)" d="M0 0h98v20H0z"/></g><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="30.5" y="15" fill="#010101" fill-opacity=".3">coverage</text><text x="30.5" y="14">coverage</text><text x="79.5" y="15" fill="#010101" fill-opacity=".3">{rate_round}%</text><text x="79.5" y="14">{rate_round}%</text></g></svg>'''
    with open(COVERAGE_SVG, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Updated {COVERAGE_SVG} -> {rate_round}% ({color})")

    # Sort files: 100% files first (by note order), then descending coverage
    def sort_key(item):
        fn, rate = item
        # Priority order: by coverage rate descending, then filename
        return (-round(rate, 1), fn)

    sorted_files = sorted(file_rates.items(), key=sort_key)

    # ── 3. Build Markdown table ──────────────────────────────────────────────
    table_lines = [
        "| Component / Module | Coverage | Notes |",
        "|---|:---:|---|",
    ]

    for fn, rate in sorted_files:
        display_name = fn.replace("custom_components/myhome/", "")
        notes = COMPONENT_NOTES.get(fn, "Core integration component")
        rate_str = f"**{round(rate)}%**" if round(rate) >= 90 else f"{round(rate)}%"
        table_lines.append(f"| [`{display_name}`]({fn}) | {rate_str} | {notes} |")

    new_table_block = "\n".join(table_lines)

    # ── 4. Update README.md ──────────────────────────────────────────────────
    if not os.path.exists(README_MD):
        print(f"Error: {README_MD} not found.")
        sys.exit(1)

    with open(README_MD, "r", encoding="utf-8") as f:
        content = f.read()

    # If markers exist, replace between them
    if START_MARKER in content and END_MARKER in content:
        pattern = re.compile(
            rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
            re.DOTALL,
        )
        replacement = f"{START_MARKER}\n\n{new_table_block}\n\n{END_MARKER}"
        content = pattern.sub(replacement, content)
    else:
        # Replace existing static table
        table_pattern = re.compile(
            r"\| Component / Module \| Coverage \| Notes \|.*?(?=\n\n> \*\*Live Test Execution\*\*)",
            re.DOTALL,
        )
        content = table_pattern.sub(
            f"{START_MARKER}\n\n{new_table_block}\n\n{END_MARKER}",
            content,
        )

    # Update summary test count mentions dynamically
    content = re.sub(
        r"(\*\*Comprehensive Test Suite\*\*:\s*(?:[Oo]ver\s+)?)\d+\s+automated unit tests\s*\(\d+%\s+line coverage\)",
        rf"\g<1>{test_count} automated unit tests ({rate_round}% line coverage)",
        content,
    )
    content = re.sub(
        r"(\*\*`test-coverage`\*\*:\s*)\d+\s+automated unit tests",
        rf"\g<1>{test_count} automated unit tests",
        content,
    )
    content = re.sub(
        r"(The integration maintains\s+(?:over\s+)?)\d+\s+automated unit tests\s*\(\d+%\s+line coverage(?: across all modules)?\)",
        rf"\g<1>{test_count} automated unit tests ({rate_round}% line coverage across all modules)",
        content,
    )
    content = re.sub(
        r"Synthetic mock TCP test harness with \d+\+? unit tests(?:\s*\(\d+%[+]? coverage\))?",
        f"Synthetic mock TCP test harness with {test_count}+ unit tests ({rate_round}% coverage)",
        content,
    )

    corpus_count = get_golden_corpus_count()
    if corpus_count is not None:
        content = re.sub(
            r"(Runs\s+)\d+(\s+OpenWebNet frame fixtures)",
            rf"\g<1>{corpus_count}\g<2>",
            content,
        )

    with open(README_MD, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Updated {README_MD} coverage table successfully ({len(sorted_files)} components, total {rate_round}%).")


if __name__ == "__main__":
    update_readme_and_svg()
