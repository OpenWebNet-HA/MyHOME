#!/usr/bin/env python3
"""Strict 100% Test Coverage Enforcer for MyHOME.

Ensures that all modules within custom_components/myhome maintain
100.0% line coverage (zero missing lines). Any module that falls below
100% coverage will cause CI to fail immediately with detailed line numbers,
source snippets, and GitHub Actions inline annotations.
"""
import os
import sys
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MYHOME_DIR = os.path.join(REPO_ROOT, "custom_components", "myhome")
COVERAGE_XML = os.path.join(REPO_ROOT, "coverage.xml")

# Standalone interactive or trivial modules excluded from coverage enforcement
EXCLUDED_MODULES = set()


def collapse_line_ranges(line_numbers):
    """Convert a list of string line numbers into compact human-readable ranges.

    e.g. ['667', '668', '670'] -> '667-668, 670'
    """
    if not line_numbers:
        return ""
    nums = sorted(set(int(n) for n in line_numbers))
    ranges = []
    start = nums[0]
    prev = nums[0]

    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = n
            prev = n
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(ranges)


def get_source_snippet(file_path: str, line_no: int) -> str:
    """Retrieve source code line content for context."""
    full_path = os.path.join(REPO_ROOT, file_path) if not os.path.isabs(file_path) else file_path
    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            for idx, line in enumerate(f, 1):
                if idx == line_no:
                    return line.rstrip()
    except Exception:
        pass
    return ""


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


def verify_all_coverage(xml_path: str = COVERAGE_XML) -> int:
    """Validate that every custom_components/myhome module has 100% test coverage."""
    if not os.path.exists(xml_path):
        print(f"[ERROR] Coverage report '{xml_path}' not found. Run pytest with --cov-report=xml first.")
        return 1

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Map normalized relative filenames to (total_statements, line_rate, uncovered_lines)
    coverage_data = {}
    for pkg in root.findall(".//package"):
        for cls in pkg.findall(".//class"):
            raw_fn = cls.attrib.get("filename", "")
            fn = normalize_coverage_filename(raw_fn)
            lines = cls.findall(".//line")
            total_stmts = len(lines)
            rate = float(cls.attrib.get("line-rate", 0)) * 100.0
            uncovered = [line_elem.attrib.get("number") for line_elem in lines if line_elem.attrib.get("hits") == "0"]
            coverage_data[fn] = (total_stmts, rate, uncovered)

    # Discover all Python source files on disk in custom_components/myhome
    disk_files = []
    for root_dir, _, files in os.walk(MYHOME_DIR):
        for f in files:
            if f.endswith(".py"):
                full_path = os.path.join(root_dir, f)
                rel_path = os.path.relpath(full_path, REPO_ROOT).replace("\\", "/")
                if rel_path not in EXCLUDED_MODULES:
                    disk_files.append(rel_path)

    disk_files.sort()

    print(f"[CHECK] Verifying strict 100% test coverage across {len(disk_files)} module(s)...")
    errors = []
    failing_report_rows = []
    total_stmts_checked = 0
    total_uncovered_checked = 0

    for rel in disk_files:
        if rel not in coverage_data:
            errors.append((rel, 0, 0.0, [], f"[FAIL] {rel}: Missing from coverage report (untested or omitted)"))
            failing_report_rows.append((rel, 0, 0, "0.0%", "Untested / omitted"))
            continue

        stmts, rate, uncovered = coverage_data[rel]
        total_stmts_checked += stmts
        total_uncovered_checked += len(uncovered)

        if rate < 100.0 or uncovered:
            ranges = collapse_line_ranges(uncovered)
            errors.append((rel, stmts, rate, uncovered, f"[FAIL] {rel}: {rate:.1f}% coverage (Missing {len(uncovered)} line(s): {ranges})"))
            failing_report_rows.append((rel, stmts, len(uncovered), f"{rate:.1f}%", ranges))
        else:
            print(f"  [OK] {rel}: 100.0% coverage ({stmts}/{stmts} statements)")

    # Publish to GITHUB_STEP_SUMMARY if present
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")

    if errors:
        print("\n" + "=" * 78)
        print("[CRITICAL] STRICT 100% TEST COVERAGE ENFORCEMENT FAILED:")
        print("=" * 78)
        for rel, stmts, rate, uncovered, msg in errors:
            print(f"\n{msg}")
            # Emit GitHub Actions workflow command annotations for inline display
            for line_str in uncovered:
                line_no = int(line_str)
                snippet = get_source_snippet(rel, line_no)
                print(f"    Line {line_no:4d}: {snippet}")
                print(f"::error file={rel},line={line_no}::Statement not covered by tests (line {line_no})")

        print("\n" + "=" * 78)
        print("All modules in 'custom_components/myhome' must maintain strict 100.0% coverage.")
        print("Please add automated unit tests covering the missing line(s) before merging.")
        print("=" * 78)

        if summary_path:
            try:
                with open(summary_path, "a", encoding="utf-8") as f:
                    f.write("\n## ❌ Strict 100% Test Coverage Enforcement Failed\n\n")
                    f.write(f"**{len(errors)}** module(s) dropped below strict 100.0% line coverage:\n\n")
                    f.write("| Component / Module | Statements | Missing | Coverage | Missing Lines |\n")
                    f.write("|---|:---:|:---:|:---:|---|\n")
                    for rel, stmts, miss_cnt, cr, ranges in failing_report_rows:
                        f.write(f"| `{rel}` | {stmts} | {miss_cnt} | **{cr}** | `{ranges}` |\n")
                    f.write("\n> [!CAUTION]\n")
                    f.write("> Every statement in `custom_components/myhome` must be 100% exercised by tests.\n")
            except Exception:
                pass

        return 1

    covered_count = total_stmts_checked - total_uncovered_checked
    print(f"\n[SUCCESS] All {len(disk_files)} custom_components/myhome module(s) strictly meet 100.0% test coverage!")
    print(f"[SUCCESS] {covered_count:,} / {total_stmts_checked:,} statements covered across the entire codebase with 0 missing lines.")

    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as f:
                f.write("\n## 🎯 Strict 100% Test Coverage Enforced\n\n")
                f.write(f"All **{len(disk_files)}** component modules strictly achieved **100.0%** line coverage ")
                f.write(f"(**{covered_count:,} / {total_stmts_checked:,}** statements, **0** missing lines).\n")
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    coverage_file = sys.argv[1] if len(sys.argv) > 1 else COVERAGE_XML
    sys.exit(verify_all_coverage(coverage_file))
