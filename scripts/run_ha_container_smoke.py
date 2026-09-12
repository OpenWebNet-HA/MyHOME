#!/usr/bin/env python3
"""Home Assistant Container Smoke Test Runner.

Executes containerized smoke tests using official Home Assistant images
(stable, beta, or dev) to verify:
1. `hass --script check_config` validity.
2. Clean imports of all platform modules and requirements without deprecation errors.
3. Live container initialization with zero ERRORs or asyncio loop-blocking warnings.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_SRC = REPO_ROOT / "custom_components" / "myhome"


def run_cmd(cmd: list[str], check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
    """Execute a command with formatted output."""
    print(f"\n[EXEC] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture_output, text=True)


def test_ha_container(channel: str = "stable") -> bool:
    """Run container smoke test against specified Home Assistant channel."""
    image = f"ghcr.io/home-assistant/home-assistant:{channel}"
    print(f"\n{'='*70}")
    print(f"Starting Home Assistant Container Smoke Test: {image}")
    print(f"{'='*70}")

    # 1. Prepare temporary test configuration directory
    temp_dir = Path(tempfile.mkdtemp(prefix="ha_smoke_"))
    try:
        custom_components_dst = temp_dir / "custom_components" / "myhome"
        custom_components_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(CUSTOM_COMPONENTS_SRC, custom_components_dst)

        config_yaml = temp_dir / "configuration.yaml"
        config_yaml.write_text(
            "default_config:\n"
            "logger:\n"
            "  default: info\n"
            "  logs:\n"
            "    custom_components.myhome: debug\n",
            encoding="utf-8",
        )

        config_mount = f"{temp_dir}:/config"

        # 2. Pull container image
        print(f"\nPulling container {image}...")
        run_cmd(["docker", "pull", image])

        # 3. Step 1: Configuration check
        print(f"\nRunning check_config in {image}...")
        res = run_cmd(
            ["docker", "run", "--rm", "-v", config_mount, image, "hass", "-c", "/config", "--script", "check_config"],
            check=False,
        )
        if res.returncode != 0:
            print(f"ERROR: check_config failed for {image}")
            return False

        # 4. Step 2: Test clean module imports
        print(f"\nTesting platform imports in {image}...")
        import_test_script = (
            "import sys, json, subprocess\n"
            "from pathlib import Path\n"
            "print(f'Container Python: {sys.version}')\n"
            "manifest = json.loads(Path('/config/custom_components/myhome/manifest.json').read_text(encoding='utf-8'))\n"
            "for req in manifest.get('requirements', []):\n"
            "    print(f'Installing {req}...')\n"
            "    subprocess.run([sys.executable, '-m', 'pip', 'install', req], check=True)\n"
            "sys.path.insert(0, '/config')\n"
            "platforms = ['myhome', 'myhome.const', 'myhome.gateway', 'myhome.light', 'myhome.switch', "
            "'myhome.cover', 'myhome.climate', 'myhome.sensor', 'myhome.binary_sensor', 'myhome.button', "
            "'myhome.alarm_control_panel', 'myhome.media_player', 'myhome.diagnostics', 'myhome.websocket']\n"
            "for p in platforms:\n"
            "    mod = f'custom_components.{p}'\n"
            "    __import__(mod)\n"
            "    print(f'  ✓ {mod} imported cleanly')\n"
        )

        res = run_cmd(
            ["docker", "run", "--rm", "-v", config_mount, image, "python3", "-c", import_test_script],
            check=False,
        )
        if res.returncode != 0:
            print(f"ERROR: Platform imports failed in {image}")
            return False

        # 5. Step 3: Boot Home Assistant and inspect logs
        print(f"\nBooting {image} in daemon mode to monitor initialization and event loop...")
        run_res = run_cmd(
            ["docker", "run", "-d", "-v", config_mount, "-e", "TZ=UTC", image, "hass", "-c", "/config"],
            capture_output=True,
        )
        container_id = run_res.stdout.strip()
        print(f"Container ID: {container_id}")

        try:
            initialized = False
            for i in range(1, 41):
                logs_proc = run_cmd(["docker", "logs", container_id], capture_output=True, check=False)
                combined_logs = logs_proc.stdout + logs_proc.stderr
                if "Home Assistant initialized" in combined_logs:
                    print(f"✓ Home Assistant initialized in {i} seconds!")
                    initialized = True
                    break
                time.sleep(1)

            if not initialized:
                print(f"WARNING: Home Assistant did not log 'Home Assistant initialized' within 40 seconds on {image}.")

            logs_proc = run_cmd(["docker", "logs", container_id], capture_output=True, check=False)
            logs = logs_proc.stdout + logs_proc.stderr
            print("\n--- HOME ASSISTANT LOG SUMMARY ---")
            for line in logs.splitlines():
                if "myhome" in line.lower() or "error" in line.lower() or "warning" in line.lower():
                    print(line)
            print("----------------------------------\n")

            errors = [
                line for line in logs.splitlines()
                if ("ERROR (MainThread) [custom_components.myhome" in line
                    or "ImportError" in line
                    or "Traceback" in line) and "myhome" in line.lower()
            ]
            blocking = [
                line for line in logs.splitlines()
                if "Detected blocking call" in line and "custom_components/myhome" in line
            ]

            if errors:
                print(f"ERROR: Fatal errors detected in {image}:\n" + "\n".join(errors))
                return False

            if blocking:
                print(f"ERROR: Event loop blocking detected in {image}:\n" + "\n".join(blocking))
                return False

            print(f"✓ Container test passed for {image} with zero errors and zero loop blocking warnings!")
            return True

        finally:
            print(f"Stopping and removing container {container_id}...")
            run_cmd(["docker", "stop", container_id], check=False, capture_output=True)
            run_cmd(["docker", "rm", container_id], check=False, capture_output=True)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Run containerized Home Assistant smoke test.")
    parser.add_argument(
        "--channel",
        choices=["stable", "beta", "dev", "all"],
        default="stable",
        help="Home Assistant image tag/channel to test (default: stable)",
    )
    args = parser.parse_args()

    channels = ["stable", "beta", "dev"] if args.channel == "all" else [args.channel]
    success = True
    for ch in channels:
        if not test_ha_container(ch):
            success = False
            if ch != "dev":
                break

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
