#!/usr/bin/env python3
"""
Packaging script for MyHOME Home Assistant Integration.
Creates a HACS-compliant `myhome.zip` release asset containing the contents of
`custom_components/myhome/` at the root of the archive.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
import zipfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COMPONENT_DIR = os.path.join(REPO_ROOT, "custom_components", "myhome")
MANIFEST_PATH = os.path.join(COMPONENT_DIR, "manifest.json")
CONST_PATH = os.path.join(COMPONENT_DIR, "const.py")
OUTPUT_ZIP = os.path.join(REPO_ROOT, "myhome.zip")

EXCLUDE_PATTERNS = [
    r"__pycache__",
    r"\.pyc$",
    r"\.pyo$",
    r"\.pytest_cache",
    r"\.DS_Store",
    r"\.coverage",
]


def check_versions(target_tag=None):
    """Verify version consistency across manifest.json, const.py, and requirements."""
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest_version = manifest.get("version")

    with open(CONST_PATH, "r", encoding="utf-8") as f:
        const_content = f.read()
    match = re.search(r'INTEGRATION_VERSION\s*=\s*["\']([^"\']+)["\']', const_content)
    const_version = match.group(1) if match else None

    print(f"[CHECK] manifest.json version: {manifest_version}")
    print(f"[CHECK] const.py version:       {const_version}")

    if manifest_version != const_version:
        print(f"[ERROR] Version mismatch: manifest.json ({manifest_version}) != const.py ({const_version})", file=sys.stderr)
        sys.exit(1)

    requirements = manifest.get("requirements", [])
    ownd_match = re.search(r'REQUIRED_OWND_VERSION\s*=\s*["\']([^"\']+)["\']', const_content)
    required_ownd_version = ownd_match.group(1) if ownd_match else manifest_version
    expected_ownd_req = f"OWNd=={required_ownd_version}"
    print(f"[CHECK] manifest requirements: {requirements}")
    if expected_ownd_req not in requirements:
        print(
            f"[ERROR] Deployment Rule Violation: manifest.json 'requirements' must contain '{expected_ownd_req}'! "
            f"Found: {requirements}. Without this, Home Assistant will NOT fetch the updated engine from PyPI!",
            file=sys.stderr,
        )
        sys.exit(1)

    if target_tag:
        clean_tag = target_tag.lstrip("v")
        print(f"[CHECK] Target Git tag:        {target_tag} (normalized: {clean_tag})")
        if clean_tag != manifest_version:
            print(
                f"[ERROR] Deployment Rule Violation: Git tag ({target_tag}) does not match manifest.json version ({manifest_version})!",
                file=sys.stderr,
            )
            sys.exit(1)

    return manifest_version, required_ownd_version


def check_pypi_release(version):
    """Verify that the required OWNd engine version is published on PyPI."""
    url = "https://pypi.org/pypi/OWNd/json"
    req = urllib.request.Request(url, headers={"User-Agent": "MyHOME-ReleaseValidator/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            releases = data.get("releases", {})
            if version not in releases:
                print(
                    f"[ERROR] PyPI Verification Failed: OWNd=={version} is NOT published on PyPI! "
                    f"Latest PyPI releases: {list(releases.keys())[-5:]}",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"[CHECK] Verified OWNd=={version} is live on PyPI.")
    except Exception as err:
        print(f"[WARN] Could not verify PyPI (network or timeout): {err}")


def should_exclude(rel_path):
    """Check if file should be excluded from release package."""
    norm_path = rel_path.replace("\\", "/")
    for pattern in EXCLUDE_PATTERNS:
        if re.search(pattern, norm_path):
            return True
    return False


def build_zip(version):
    """Build the HACS-compliant myhome.zip package."""
    print(f"\n[BUILD] Packaging myhome.zip for version {version}...")
    if os.path.exists(OUTPUT_ZIP):
        os.remove(OUTPUT_ZIP)

    file_count = 0
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for root, dirs, files in os.walk(COMPONENT_DIR):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if not should_exclude(d)]
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, COMPONENT_DIR)
                if should_exclude(rel_path):
                    continue
                # Store using forward slashes for zip compatibility
                arcname = rel_path.replace("\\", "/")
                zf.write(abs_path, arcname)
                file_count += 1

    zip_size = os.path.getsize(OUTPUT_ZIP)
    print(f"[SUCCESS] Created {OUTPUT_ZIP}")
    print(f"          Total packaged files: {file_count}")
    print(f"          Archive size:         {zip_size:,} bytes ({zip_size / 1024:.1f} KB)")


def verify_zip():
    """Verify integrity and structure of generated zip."""
    with zipfile.ZipFile(OUTPUT_ZIP, "r") as zf:
        namelist = zf.namelist()
        if "manifest.json" not in namelist:
            print("[ERROR] manifest.json is not in the root of the zip archive!", file=sys.stderr)
            sys.exit(1)
        if "__init__.py" not in namelist:
            print("[ERROR] __init__.py is not in the root of the zip archive!", file=sys.stderr)
            sys.exit(1)
        print("[VERIFY] Archive structure verified successfully. HACS compliant.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package or verify MyHOME release.")
    parser.add_argument("--verify-only", action="store_true", help="Verify release rules without building zip.")
    parser.add_argument("--tag", type=str, default=None, help="Target git release tag to validate against.")
    args = parser.parse_args()

    # Use tag argument or check GITHUB_REF_NAME env var
    target_tag = args.tag or os.getenv("GITHUB_REF_NAME")

    ver, ownd_ver = check_versions(target_tag=target_tag)
    check_pypi_release(ownd_ver)

    if args.verify_only:
        print(f"[VERIFY] Release rules passed successfully for v{ver}.")
        sys.exit(0)

    build_zip(ver)
    verify_zip()
