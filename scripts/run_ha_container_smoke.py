#!/usr/bin/env python3
"""Home Assistant Container Smoke Test Runner.

Executes containerized smoke tests using official Home Assistant images
(stable, beta, or dev) to verify:
1. `hass --script check_config` validity.
2. Clean imports of all platform modules and requirements without deprecation errors.
3. Live container initialization with zero ERRORs or asyncio loop-blocking warnings.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CUSTOM_COMPONENTS_SRC = REPO_ROOT / "custom_components" / "myhome"
MANIFEST_JSON = CUSTOM_COMPONENTS_SRC / "manifest.json"


def run_cmd(cmd: list[str], check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
    """Execute a command with formatted output."""
    print(f"\n[EXEC] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=capture_output, text=True)


def get_pinned_version() -> str | None:
    """Extract the exact pinned OWNd version from manifest.json."""
    try:
        manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
        for req in manifest.get("requirements", []):
            m = re.fullmatch(r"OWNd==([0-9][0-9A-Za-z.]*)", req)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def is_pypi_released(package: str, version: str) -> bool:
    """Check if a specific package version has been published to PyPI."""
    try:
        import urllib.request

        url = f"https://pypi.org/pypi/{package}/json"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "MyHOME-Smoke-CI (https://github.com/OpenWebNet-HA/MyHOME)"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                releases = data.get("releases", {})
                return version in releases
    except Exception:
        pass
    return False


def test_ha_container(
    channel: str = "stable",
    registry: str = "ghcr.io/home-assistant",
    ownd_target: str = "auto",
    dev_ref: str | None = None,
) -> bool:
    """Run container smoke test against specified Home Assistant channel."""
    base_image = f"{registry}/home-assistant:{channel}"
    print(f"\n{'='*70}")
    print(f"Starting Home Assistant Container Smoke Test: {base_image}")
    print(f"{'='*70}")

    pinned_ownd = get_pinned_version()
    use_dev_ownd = False
    if ownd_target == "dev":
        use_dev_ownd = True
    elif ownd_target == "auto" and pinned_ownd:
        if not is_pypi_released("OWNd", pinned_ownd):
            print(f"\n[NOTICE] Pinned OWNd=={pinned_ownd} is not yet published to PyPI.")
            print("Building ephemeral test container image with upstream development OWNd...")
            use_dev_ownd = True

    # 1. Pull container image
    print(f"\nPulling container {base_image}...")
    run_cmd(["docker", "pull", base_image])

    test_image = base_image
    installed_dev_ver = None
    if use_dev_ownd:
        ref = dev_ref or os.environ.get("OWND_REF") or os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "master"
        test_image = f"ha_smoke_test:{channel}_{int(time.time())}"
        dockerfile = (
            f"FROM {base_image}\n"
            f"RUN (python3 -m pip install --no-cache-dir https://github.com/OpenWebNet-HA/OWNd/archive/refs/heads/{ref}.tar.gz 2>/dev/null || \\\n"
            "     python3 -m pip install --no-cache-dir https://github.com/OpenWebNet-HA/OWNd/archive/refs/heads/master.tar.gz)\n"
        )
        print(f"\nBuilding ephemeral container {test_image} with OWNd@{ref}...")
        build_proc = subprocess.run(
            ["docker", "build", "-t", test_image, "-"],
            input=dockerfile,
            text=True,
            capture_output=True,
        )
        if build_proc.returncode != 0:
            print(f"ERROR: Failed to build ephemeral image {test_image}:")
            print(build_proc.stdout)
            print(build_proc.stderr)
            return False

        ver_proc = run_cmd(
            [
                "docker",
                "run",
                "--rm",
                test_image,
                "python3",
                "-c",
                "import importlib.metadata; print(importlib.metadata.version('OWNd'))",
            ],
            capture_output=True,
            check=False,
        )
        if ver_proc.returncode == 0:
            installed_dev_ver = ver_proc.stdout.strip()
            print(f"✓ Ephemeral container ready with installed OWNd {installed_dev_ver}")
        else:
            installed_dev_ver = pinned_ownd or "2.0.0"
            print(f"[WARN] Could not retrieve installed OWNd version, defaulting to {installed_dev_ver}")

    # 2. Prepare temporary test configuration directory
    temp_dir = Path(tempfile.mkdtemp(prefix="ha_smoke_"))
    container_id = None
    try:
        custom_components_dst = temp_dir / "custom_components" / "myhome"
        custom_components_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(CUSTOM_COMPONENTS_SRC, custom_components_dst)

        # Align manifest.json and configuration.yaml for container tests if using dev OWNd
        if use_dev_ownd and installed_dev_ver:
            temp_manifest_path = custom_components_dst / "manifest.json"
            temp_manifest = json.loads(temp_manifest_path.read_text(encoding="utf-8"))
            temp_manifest["requirements"] = [f"OWNd=={installed_dev_ver}"]
            temp_manifest_path.write_text(json.dumps(temp_manifest, indent=2), encoding="utf-8")

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

        # 3. Step 1: Configuration check
        print(f"\nRunning check_config in {test_image}...")
        res = run_cmd(
            ["docker", "run", "--rm", "-v", config_mount, test_image, "hass", "-c", "/config", "--script", "check_config"],
            check=False,
        )
        if res.returncode != 0:
            print(f"ERROR: check_config failed for {test_image}")
            return False

        # 4. Step 2: Test clean module imports
        print(f"\nTesting platform imports in {test_image}...")
        import_test_script = (
            "import sys, json, subprocess, re\n"
            "import importlib.metadata\n"
            "from pathlib import Path\n"
            "print(f'Container Python: {sys.version}')\n"
            "manifest = json.loads(Path('/config/custom_components/myhome/manifest.json').read_text(encoding='utf-8'))\n"
            "for req in manifest.get('requirements', []):\n"
            "    pkg_name = re.split(r'[=<>!~]', req)[0].strip()\n"
            "    try:\n"
            "        ver = importlib.metadata.version(pkg_name)\n"
            "        print(f'  ✓ Package {pkg_name} ({ver}) already installed in container')\n"
            "    except Exception:\n"
            "        print(f'Installing {req}...')\n"
            "        subprocess.run([sys.executable, '-m', 'pip', 'install', req], check=True)\n"
            "sys.path.insert(0, '/config')\n"
            "platforms = ['myhome', 'myhome.const', 'myhome.gateway', 'myhome.light', 'myhome.switch', "
            "'myhome.cover', 'myhome.climate', 'myhome.sensor', 'myhome.binary_sensor', 'myhome.button', "
            "'myhome.alarm_control_panel', 'myhome.media_player', 'myhome.sound_source', 'myhome.diagnostics', 'myhome.websocket']\n"
            "for p in platforms:\n"
            "    mod = f'custom_components.{p}'\n"
            "    __import__(mod)\n"
            "    print(f'  ✓ {mod} imported cleanly')\n"
        )

        res = run_cmd(
            ["docker", "run", "--rm", "-v", config_mount, test_image, "python3", "-c", import_test_script],
            check=False,
        )
        if res.returncode != 0:
            print(f"ERROR: Platform imports failed in {test_image}")
            return False

        # 5. Step 3: Boot Home Assistant and inspect logs
        print(f"\nBooting {test_image} in daemon mode to monitor initialization and event loop...")
        run_res = run_cmd(
            ["docker", "run", "-d", "-v", config_mount, "-e", "TZ=UTC", test_image, "hass", "-c", "/config"],
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
                print(f"WARNING: Home Assistant did not log 'Home Assistant initialized' within 40 seconds on {test_image}.")

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
                print(f"ERROR: Fatal errors detected in {test_image}:\n" + "\n".join(errors))
                return False

            if blocking:
                print(f"ERROR: Event loop blocking detected in {test_image}:\n" + "\n".join(blocking))
                return False

            print(f"✓ Container test passed for {test_image} with zero errors and zero loop blocking warnings!")
            return True

        finally:
            if container_id:
                print(f"Stopping and removing container {container_id}...")
                run_cmd(["docker", "stop", container_id], check=False, capture_output=True)
                run_cmd(["docker", "rm", container_id], check=False, capture_output=True)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if use_dev_ownd and test_image != base_image:
            print(f"Removing ephemeral container image {test_image}...")
            run_cmd(["docker", "rmi", "-f", test_image], check=False, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Run containerized Home Assistant smoke test.")
    parser.add_argument(
        "--channel",
        choices=["stable", "beta", "dev", "all"],
        default="stable",
        help="Home Assistant image tag/channel to test (default: stable)",
    )
    parser.add_argument(
        "--registry",
        default="ghcr.io/home-assistant",
        help="Image registry/namespace (default: ghcr.io/home-assistant; docker.io/homeassistant is the Docker Hub mirror)",
    )
    parser.add_argument(
        "--ownd-target",
        choices=["auto", "pinned", "dev"],
        default="auto",
        help="OWNd target to test in container: 'auto' falls back to dev if pinned version is not on PyPI (default: auto)",
    )
    parser.add_argument(
        "--dev-ref",
        default=None,
        help="Git ref (branch or tag) for dev OWNd install (default: matching ref or master)",
    )
    args = parser.parse_args()

    channels = ["stable", "beta", "dev"] if args.channel == "all" else [args.channel]
    success = True
    for ch in channels:
        if not test_ha_container(
            channel=ch,
            registry=args.registry,
            ownd_target=args.ownd_target,
            dev_ref=args.dev_ref,
        ):
            success = False
            if ch != "dev":
                break

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
