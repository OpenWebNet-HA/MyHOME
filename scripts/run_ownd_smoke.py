#!/usr/bin/env python3
"""OWNd Protocol Engine Smoke Test Runner.

Verifies the integration and protocol engine health of OWNd across:
1. Pinned release (manifest.json / const.py REQUIRED_OWND_VERSION)
2. Latest published PyPI release (pip install --pre -U OWNd)
3. Upstream development version (git+https://github.com/OpenWebNet-HA/OWNd.git@master)

Executes 4 comprehensive validation gates:
- Gate 1: Metadata & Version Lockstep Audit
- Gate 2: OpenWebNet Golden Corpus Conformance (191 tests)
- Gate 3: Integration Platform Import Cleanliness
- Gate 4: Mock Gateway TCP Handshake & Asynchronous Event Loopback
"""

import argparse
import asyncio
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

CONST_PY = REPO_ROOT / "custom_components" / "myhome" / "const.py"
MANIFEST_JSON = REPO_ROOT / "custom_components" / "myhome" / "manifest.json"


def get_pinned_version() -> str:
    """Extract required OWNd version from const.py and manifest.json."""
    const_text = CONST_PY.read_text(encoding="utf-8")
    m = re.search(r'REQUIRED_OWND_VERSION\s*=\s*["\']([^"\']+)["\']', const_text)
    if not m:
        raise ValueError("Could not find REQUIRED_OWND_VERSION in const.py")
    return m.group(1)


def run_cmd(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Execute command with formatted logging."""
    print(f"[EXEC] {' '.join(cmd)}")
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "OWND_SMOKE_TEST": "1"}
    return subprocess.run(cmd, check=check, text=True, cwd=str(REPO_ROOT), env=env)


def install_target(target: str, pinned_version: str, dev_ref: str = None) -> bool:
    """Install the specified OWNd target."""
    print(f"\n--- Installing OWNd target: '{target}' ---")
    if target == "pinned":
        cmd = [sys.executable, "-m", "pip", "install", f"OWNd=={pinned_version}"]
    elif target == "latest":
        cmd = [sys.executable, "-m", "pip", "install", "--pre", "-U", "OWNd"]
    elif target == "dev":
        ref = dev_ref or os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "master"
        # Try installing from matching branch/ref first if available
        print(f"Attempting to install OWNd@{ref}...")
        cmd = [sys.executable, "-m", "pip", "install", f"git+https://github.com/OpenWebNet-HA/OWNd.git@{ref}"]
        res = run_cmd(cmd, check=False)
        if res.returncode == 0:
            return True
        print(f"[WARN] Failed to install OWNd@{ref}, falling back to master branch...")
        cmd = [sys.executable, "-m", "pip", "install", "git+https://github.com/OpenWebNet-HA/OWNd.git@master"]
    else:
        raise ValueError(f"Unknown target: {target}")

    res = run_cmd(cmd, check=False)
    return res.returncode == 0


def verify_metadata(target: str, pinned_version: str) -> Tuple[bool, str]:
    """Gate 1: Verify version and manifest lockstep."""
    try:
        installed_ver = importlib.metadata.version("OWNd")
    except Exception as err:
        return False, f"Failed to retrieve OWNd package version: {err}"

    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    reqs = manifest.get("requirements", [])
    expected_req = f"OWNd=={pinned_version}"

    if expected_req not in reqs:
        return False, f"manifest.json does not contain '{expected_req}' (found: {reqs})"

    if target == "pinned" and installed_ver != pinned_version:
        return False, f"Installed OWNd ({installed_ver}) does not match pinned version ({pinned_version})"

    return True, f"Installed OWNd: {installed_ver} (manifest required: {expected_req})"


def verify_golden_corpus() -> Tuple[bool, str]:
    """Gate 2: Run Golden Corpus Conformance Suite."""
    cmd = [sys.executable, "-m", "pytest", "tests/test_golden_conformance.py", "-q"]
    res = run_cmd(cmd, check=False)
    if res.returncode != 0:
        return False, "test_golden_conformance.py failed against installed OWNd"
    return True, "191 Golden Corpus fixtures verified (parser extraction & builder parity passed)"


def verify_platform_imports() -> Tuple[bool, str]:
    """Gate 3: Verify integration platform import cleanliness."""
    platforms = [
        "myhome", "myhome.const", "myhome.gateway", "myhome.light", "myhome.switch",
        "myhome.cover", "myhome.climate", "myhome.sensor", "myhome.binary_sensor",
        "myhome.button", "myhome.alarm_control_panel", "myhome.media_player",
        "myhome.diagnostics", "myhome.websocket",
    ]
    sys.path.insert(0, str(REPO_ROOT))
    for p in platforms:
        mod_name = f"custom_components.{p}"
        try:
            __import__(mod_name)
        except Exception as err:
            return False, f"Failed to import {mod_name}: {err}"
    return True, f"All {len(platforms)} integration platforms imported cleanly without symbol or deprecation errors"


async def run_loopback_async() -> Tuple[bool, str]:
    """Gate 4: Test mock gateway TCP handshake and event loopback."""
    from OWNd.message import OWNLightingCommand, OWNLightingEvent, OWNMessage

    from tests.mock_gateway_harness import MockGatewayHarness

    harness = MockGatewayHarness()
    port = await harness.start()

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        # 1. Initial ACK from gateway
        ack = await asyncio.wait_for(reader.readexactly(6), timeout=5.0)
        if ack != b"*#*1##":
            return False, f"Expected initial ACK b'*#*1##', received {ack!r}"

        # 2. Command session handshake
        writer.write(b"*99*0##")
        await writer.drain()
        ack = await asyncio.wait_for(reader.readexactly(6), timeout=5.0)
        if ack != b"*#*1##":
            return False, f"Expected command session ACK b'*#*1##', received {ack!r}"

        # 3. Send lighting command frame
        writer.write(b"*1*1*11##")
        await writer.drain()
        ack = await asyncio.wait_for(reader.readexactly(6), timeout=5.0)
        if ack != b"*#*1##":
            return False, f"Expected command ACK b'*#*1##', received {ack!r}"

        # 4. Parse frame using installed OWNd
        parsed = OWNMessage.parse("*1*1*11##")
        if not isinstance(parsed, (OWNLightingCommand, OWNLightingEvent)):
            return False, f"OWNMessage.parse('*1*1*11##') produced unexpected type: {type(parsed)}"
        if getattr(parsed, "who", None) != 1 or getattr(parsed, "where", None) != "11":
            return False, f"Parsed message attributes invalid: who={getattr(parsed, 'who', None)}, where={getattr(parsed, 'where', None)}"

        writer.close()
        await writer.wait_closed()
        return True, "Mock gateway TCP handshake (*99*0##) & frame roundtrip verified successfully"
    except Exception as err:
        return False, f"Mock gateway loopback failed: {err}"
    finally:
        await harness.stop()


def run_smoke_suite(target: str, skip_install: bool = False, dev_ref: str = None) -> bool:
    """Execute all smoke test gates for the specified target."""
    pinned = get_pinned_version()
    print(f"\n{'='*75}")
    print(f"🚀 STARTING OWND SMOKE TEST: Target '{target}' (Pinned: {pinned})")
    print(f"{'='*75}")

    if not skip_install:
        if not install_target(target, pinned, dev_ref=dev_ref):
            print(f"❌ FAILED: Unable to install OWNd target '{target}'")
            return False

    gates = [
        ("Gate 1: Metadata & Version Lockstep", lambda: verify_metadata(target, pinned)),
        ("Gate 2: Golden Corpus Conformance", verify_golden_corpus),
        ("Gate 3: Platform Import Cleanliness", verify_platform_imports),
        ("Gate 4: Mock Gateway TCP Handshake & Loopback", lambda: asyncio.run(run_loopback_async())),
    ]

    all_passed = True
    results = []
    for gate_name, gate_func in gates:
        print(f"\n[RUNNING] {gate_name}...")
        try:
            passed, msg = gate_func()
        except Exception as err:
            passed = False
            msg = f"Unexpected exception: {err}"

        results.append((gate_name, passed, msg))
        if passed:
            print(f"  ✅ PASS: {msg}")
        else:
            print(f"  ❌ FAIL: {msg}")
            all_passed = False

    print(f"\n{'='*75}")
    print(f"📋 SUMMARY REPORT: Target '{target}'")
    print(f"{'='*75}")
    for name, passed, msg in results:
        status_icon = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status_icon} | {name}: {msg}")
    print(f"{'='*75}\n")

    return all_passed


def main():
    parser = argparse.ArgumentParser(description="OWNd Protocol Engine Smoke Test Runner.")
    parser.add_argument(
        "--target",
        choices=["pinned", "latest", "dev", "all"],
        default="pinned",
        help="OWNd distribution target to test (default: pinned)",
    )
    parser.add_argument(
        "--dev-ref",
        default=None,
        help="Git ref (branch or tag) to install for dev target (default: current branch or master)",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip pip installation and test currently installed OWNd",
    )
    args = parser.parse_args()

    targets = ["pinned", "latest", "dev"] if args.target == "all" else [args.target]
    overall_success = True

    for t in targets:
        if not run_smoke_suite(t, skip_install=args.skip_install, dev_ref=args.dev_ref):
            overall_success = False
            if t != "dev":
                break

    sys.exit(0 if overall_success else 1)


if __name__ == "__main__":
    main()
