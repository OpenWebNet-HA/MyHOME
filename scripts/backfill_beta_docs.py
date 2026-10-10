#!/usr/bin/env python3
"""Backfill and publish versioned documentation snapshots for historical beta releases.

Enables maintainers to deploy frozen, calibrated documentation snapshots for each
released beta tag into GitHub Pages using mike.

Usage:
  python scripts/backfill_beta_docs.py --dry-run
  python scripts/backfill_beta_docs.py --versions 2.0.0b11,2.0.0b13
  python scripts/backfill_beta_docs.py --all-betas --push
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Key milestone betas to maintain in version list (historical tags; append 2.0.0b14 once released)
DEFAULT_MILESTONE_BETAS = [
    "2.0.0b5",
    "2.0.0b6",
    "2.0.0b7",
    "2.0.0b8",
    "2.0.0b9",
    "2.0.0b10",
    "2.0.0b11",
    "2.0.0b13",
]


def run_cmd(cmd: list[str], dry_run: bool = False, cwd: Path = REPO_ROOT) -> int:
    """Execute shell command or print if dry-run."""
    cmd_str = " ".join(cmd)
    print(f"[{'DRY-RUN' if dry_run else 'EXEC'}] {cmd_str}")
    if dry_run:
        return 0
    env = dict(os.environ)
    py_dir = str(Path(sys.executable).parent)
    env["PATH"] = f"{py_dir}{os.pathsep}{env.get('PATH', '')}"
    res = subprocess.run(cmd, cwd=cwd, env=env)
    return res.returncode


def get_git_tags() -> list[str]:
    """Return all beta tags sorted."""
    res = subprocess.run(
        ["git", "tag", "-l", "2.0.0b*"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return []
    tags = [t.strip() for t in res.stdout.splitlines() if t.strip()]
    def natural_key(text: str) -> list[int | str]:
        return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]
    return sorted(tags, key=natural_key)


def backfill_version(version: str, is_latest: bool = False, push: bool = False, dry_run: bool = False) -> bool:
    """Build and deploy a specific beta version snapshot using mike."""
    clean_ver = version.lstrip("v")
    title = f"v{clean_ver} (beta)" if is_latest else f"v{clean_ver}"

    cmd = ["mike", "deploy", "--update-aliases", "-F", "mkdocs.yml"]
    if push:
        cmd.append("--push")
    cmd.append(clean_ver)
    if is_latest:
        cmd.extend(["beta", "dev"])
    cmd.extend(["-t", title])

    code = run_cmd(cmd, dry_run=dry_run)
    return code == 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for beta documentation backfill."""
    parser = argparse.ArgumentParser(
        description="Backfill versioned documentation snapshots for historical beta releases."
    )
    parser.add_argument(
        "--versions",
        type=str,
        help="Comma-separated list of versions to deploy (e.g. 2.0.0b11,2.0.0b13)",
    )
    parser.add_argument(
        "--all-betas",
        action="store_true",
        help="Deploy all discovered git beta tags (2.0.0b*)",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Push deployed documentation branch (gh-pages) to remote",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate deployment commands without executing",
    )

    args = parser.parse_args(argv)

    if args.versions:
        versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    elif args.all_betas:
        versions = get_git_tags()
    else:
        versions = DEFAULT_MILESTONE_BETAS

    print("=" * 70)
    print("MyHOME Beta Documentation Backfill & Multi-Version Publisher")
    print(f"Target Versions ({len(versions)}): {', '.join(versions)}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'ACTIVE'} | Push: {args.push}")
    print("=" * 70)

    if not versions:
        print("No versions selected for deployment.")
        return 1

    latest_ver = versions[-1]
    success_count = 0

    for ver in versions:
        is_latest = (ver == latest_ver)
        print(f"\nProcessing version: {ver} (Latest Beta: {is_latest})")
        ok = backfill_version(ver, is_latest=is_latest, push=args.push, dry_run=args.dry_run)
        if ok:
            success_count += 1
        else:
            print(f"ERROR: Failed deploying version {ver}", file=sys.stderr)

    print("\n" + "=" * 70)
    print(f"Completed: {success_count}/{len(versions)} versions deployed successfully.")
    print("=" * 70)
    return 0 if success_count == len(versions) else 1


if __name__ == "__main__":
    sys.exit(main())
