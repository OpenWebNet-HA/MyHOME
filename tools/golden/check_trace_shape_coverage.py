#!/usr/bin/env python3
"""OpenWebNet Trace-to-Golden Corpus Structural Shape Coverage Gate.

Bridges physical bus trace fixtures (`tests/fixtures/**/*.json` and, only with
`--encyclopedia-evidence <dir>`, the `<dir>/*/frames.jsonl` files of an
OpenWebNet-Encyclopedia checkout) with the declarative Golden Corpus
(`tests/golden/corpus.json`).

Enforces three invariants in CI:
1. Zero new uncovered protocol shapes: every valid frame shape in any trace/plant
   fixture must either exist in `tests/golden/corpus.json`, match a documented
   self-cleaning allowlist, or belong to the legacy baseline
   `tests/golden/uncovered_trace_shapes_baseline.json`.
2. Monotonic ratchet shrinkage: if any shape in `uncovered_trace_shapes_baseline.json`
   becomes covered by `tests/golden/corpus.json` (or disappears from `tests/fixtures/`),
   CI fails until `--write-baseline` shrinks the baseline.
3. Self-cleaning allowlists: every entry in `KNOWN_INVALID_OR_ADVERSARIAL_FRAMES`
   and `UNIMPLEMENTED_PARSER_SHAPES` must exist in `tests/fixtures/` and must not
   silently linger once removed or implemented in `OWNd`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from OWNd.message import OWNMessage, OWNSignaling

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_CORPUS_JSON = REPO_ROOT / "tests" / "golden" / "corpus.json"
BASELINE_JSON = REPO_ROOT / "tests" / "golden" / "uncovered_trace_shapes_baseline.json"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
# Opt-in (`--encyclopedia-evidence`): CI has no Encyclopedia checkout, so the default
# result must not depend on a sibling folder.
DEFAULT_ENCYCLOPEDIA_EVIDENCE_DIR: Path | None = None

ShapeKey = tuple[
    str,  # kind
    int | None,  # who
    str | None,  # what_shape
    str | None,  # where_shape
    str | None,  # dim_shape
    int | None,  # dim_val_arity
]

# Session negotiation & authentication challenge/response frames that belong to
# transport handshakes rather than bus/application protocol semantics.
SESSION_AND_AUTH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\*99\*\d+##$"),
    re.compile(r"^\*98\*\d+##$"),
    re.compile(r"^\*#\d{4,}##$"),
    re.compile(r"^\*#12[89]\*"),
)

# Adversarial bare WHO 22 power stimuli (*22*0*WHERE## / *22*1*WHERE##) that crash
# scsserver on 8 gateway firmwares and are blocked outbound by MyHOME.
BARE_WHO22_CRASH_PATTERN = re.compile(r"^\*22\*[01]\*[^*]+##$")

# Exact invalid, out-of-range, or crash-reproduction frames present in tests/fixtures/.
# Self-cleaning: CI fails if any entry disappears from tests/fixtures/.
KNOWN_INVALID_OR_ADVERSARIAL_FRAMES: dict[str, str] = {
    "*22*2#1*6*2##": (
        "f500_tuner: non-standard 5-token WHO 22 tuner frame rejected by OpenWebNet grammar"
    ),
    "*#13**#0*22*46*45*0530##": (
        "f461_who13_clock_tz_live_2026-10-26: invalid timezone probe '0530' (OWNd#144)"
    ),
    "*#13**#0*22*46*56*124##": (
        "f461_who13_clock_tz_live_2026-10-26: out-of-range >=24h timezone probe '124' (OWNd#144)"
    ),
    "*#13**#22*22*47*06*0530*1*26*10*2026##": (
        "f461_who13_clock_tz_live_2026-10-26: invalid timezone probe '0530' on Dim 22 (OWNd#144)"
    ),
    "*#13**#22*22*47*17*124*1*26*10*2026##": (
        "f461_who13_clock_tz_live_2026-10-26: out-of-range >=24h timezone probe '124' on Dim 22 (OWNd#144)"
    ),
    "*#4*#0*22##": (
        "f461_live_2026-10-15: WHO 4 Dimension 22 firmware crash stimulus on CU #0"
    ),
    "*#4*2*22##": (
        "f461_live_2026-10-15: WHO 4 Dimension 22 firmware crash stimulus on Zone 2"
    ),
}

# Shapes in tests/fixtures/ for subsystems or MyHomeSuite register writes that
# OWNd intentionally leaves untyped (base OWNEvent / OWNCommand).
# Self-cleaning: CI fails if a shape disappears from tests/fixtures/ or enters corpus.json.
UNIMPLEMENTED_PARSER_SHAPES: dict[ShapeKey, str] = {
    ("status_request", 0, None, "general", None, None): (
        "WHO 0 dimensionless status request (*#0*0##): NACK on F461; no OWNScenarioCommand in OWNd"
    ),
    ("status_request", 1001, None, "area_or_point", None, None): (
        "WHO 1001 dimensionless status request (*#1001*74##): NACK on F461; WHO 1001 requires a dimension"
    ),
    ("dimension_write", 1001, None, "general", "30", 2): (
        "WHO 1001 #30 MyHomeSuite actuator memory-register configuration write (*#1001*0*#30*...##)"
    ),
    ("dimension_write", 1001, None, "general", "32#dp1", 2): (
        "WHO 1001 #32#1 MyHomeSuite slot-address configuration write (*#1001*0*#32#1*1*1001##)"
    ),
    ("dimension_write", 1001, None, "general", "35#dp2", 1): (
        "WHO 1001 #35 MyHomeSuite actuator memory-slot configuration write (*#1001*0*#35#SLOT#BANK*VAL##)"
    ),
    ("command_or_event", 1000, "5", "general", None, None): (
        "WHO 1000 MyHomeSuite programming-session frame (*1000*5*0##)"
    ),
    ("command_or_event", 1000, "6", "general", None, None): (
        "WHO 1000 MyHomeSuite programming-session frame (*1000*6*0##)"
    ),
    ("dimension_response", 130, None, "empty", "1", 5): (
        "WHO 130 undocumented MyHomeServer1 internal telemetry (*#130**1*1*0*3*1*8##)"
    ),
    ("dimension_response", 1022, None, "area_or_point", "8", 1): (
        "WHO 1022 sound diffusion diagnostic bitmask (*#1022*5*8*...##), not yet typed in OWNd"
    ),
}


def _who4_actuator_code_bucket(token: str) -> str:
    """Bucket continuous 1%..100% 0-10 V codes (101..200) while keeping discrete codes exact."""
    if token.isascii() and token.isdigit():
        val = int(token)
        if 101 <= val <= 200:
            return "level_101_200"
    return token


def message_shape_key(parsed: OWNMessage) -> ShapeKey:
    """Map a parsed `OWNMessage` to its canonical structural `ShapeKey`."""
    if isinstance(parsed, OWNSignaling):
        return ("signaling", None, str(parsed), None, None, None)

    if parsed.is_status_request:
        kind = "status_request"
    elif parsed.is_dimension_request:
        kind = "dimension_request"
    elif parsed.is_dimension_writing:
        kind = "dimension_write"
    elif parsed.is_dimension_reply:
        kind = "dimension_response"
    else:
        kind = "command_or_event"

    what_shape: str | None = None
    if parsed.what is not None:
        what_shape = str(parsed.what)
        if parsed.what_param:
            what_shape += f"#{len(parsed.what_param)}"

    iface, rem_wp = parsed._interface_and_where_params()
    w = parsed.where
    if w is None or w == "":
        where_shape = "empty"
    elif w == "#0":
        where_shape = "central"
    elif w.startswith("#"):
        where_shape = "group_or_hash"
    elif w == "0":
        where_shape = "general"
    else:
        where_shape = "area_or_point"
    if iface is not None:
        where_shape += "+if"
    if rem_wp:
        where_shape += f"#wp{len(rem_wp)}"

    dim_shape: str | None = None
    dim_val_arity: int | None = None
    if parsed.dimension is not None:
        dim_shape = str(parsed.dimension)
        if parsed.dimension_param:
            dim_shape += f"#dp{len(parsed.dimension_param)}"
        dv = parsed.dimension_value
        if parsed.who == 4 and dv:
            if parsed.dimension == 7:
                dim_shape += f":ctx={dv[0]}" + (f":st={dv[1]}" if len(dv) >= 2 else "")
            elif parsed.dimension == 19 and len(dv) >= 2:
                dim_shape += (
                    f":cv={_who4_actuator_code_bucket(dv[0])}"
                    f":hv={_who4_actuator_code_bucket(dv[1])}"
                )
            elif parsed.dimension == 20:
                dim_shape += f":st={_who4_actuator_code_bucket(dv[0])}"
        dim_val_arity = len(dv)

    return (kind, parsed.who, what_shape, where_shape, dim_shape, dim_val_arity)


def serialize_shape_key(key: ShapeKey) -> str:
    """Serialize a `ShapeKey` tuple to a deterministic pipe-delimited string."""
    kind, who, what_s, where_s, dim_s, arity = key
    return "|".join(
        [
            kind,
            "-" if who is None else str(who),
            "-" if what_s is None else what_s,
            "-" if where_s is None else where_s,
            "-" if dim_s is None else dim_s,
            "-" if arity is None else str(arity),
        ]
    )


def deserialize_shape_key(raw_key: str) -> ShapeKey:
    """Deserialize a pipe-delimited string back into a `ShapeKey` tuple."""
    parts = raw_key.split("|")
    if len(parts) != 6:
        raise ValueError(f"Invalid serialized ShapeKey: {raw_key!r}")
    kind, who_s, what_s, where_s, dim_s, arity_s = parts
    return (
        kind,
        None if who_s == "-" else int(who_s),
        None if what_s == "-" else what_s,
        None if where_s == "-" else where_s,
        None if dim_s == "-" else dim_s,
        None if arity_s == "-" else int(arity_s),
    )


def is_session_or_auth_frame(raw: str) -> bool:
    """Return True if `raw` is a transport session or nonce/HMAC handshake frame."""
    return any(pat.match(raw) for pat in SESSION_AND_AUTH_PATTERNS)


def extract_frames_from_json_file(
    json_path: Path,
    repo_root: Path = REPO_ROOT,
    *,
    directions: tuple[str, ...] = ("tx", "rx"),
) -> list[tuple[str, str]]:
    """Return `(source_label, raw_frame)` pairs from a structured trace or plant JSON file."""
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    try:
        rel_label = json_path.relative_to(repo_root).as_posix()
    except ValueError:
        rel_label = json_path.as_posix()

    out: list[tuple[str, str]] = []
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list):
        data_obj = payload.get("data")
        if isinstance(data_obj, dict):
            bm = data_obj.get("bus_monitor")
            if isinstance(bm, dict):
                raw_frames = bm.get("recent_frames")
    if isinstance(raw_frames, list):
        for entry in raw_frames:
            if isinstance(entry, dict):
                direction = entry.get("direction")
                raw = entry.get("raw")
                if raw and direction in directions:
                    out.append((rel_label, str(raw)))

    archive_rows = payload.get("rows")
    if isinstance(archive_rows, list):
        for row in archive_rows:
            if isinstance(row, dict):
                direction = row.get("dir")
                raw = row.get("frame")
                if raw and direction in directions:
                    out.append((rel_label, str(raw)))

    return out


def iter_all_trace_frames(
    fixtures_dir: Path = FIXTURES_DIR,
    encyclopedia_evidence_dir: Path | None = DEFAULT_ENCYCLOPEDIA_EVIDENCE_DIR,
    repo_root: Path = REPO_ROOT,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return `(local_fixture_frames, encyclopedia_frames)` as `(source, raw)` lists."""
    local_frames: list[tuple[str, str]] = []
    if fixtures_dir.is_dir():
        for json_path in sorted(fixtures_dir.rglob("*.json")):
            local_frames.extend(extract_frames_from_json_file(json_path, repo_root=repo_root))

    enc_frames: list[tuple[str, str]] = []
    if encyclopedia_evidence_dir is not None and encyclopedia_evidence_dir.is_dir():
        for sub in sorted(encyclopedia_evidence_dir.iterdir()):
            manifest_path = sub / "manifest.json"
            frames_path = sub / "frames.jsonl"
            if not manifest_path.is_file() or not frames_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            ev_id = f"OpenWebNet-Encyclopedia/evidence/{manifest.get('evidence_id', sub.name)}"
            for raw_line in frames_path.read_text(encoding="utf-8").splitlines():
                if not raw_line.strip():
                    continue
                rec = json.loads(raw_line)
                if rec.get("raw") and rec.get("dir") in ("tx", "rx"):
                    enc_frames.append((ev_id, str(rec["raw"])))

    return local_frames, enc_frames


def load_golden_shapes(corpus_records: list[dict[str, Any]] | None = None) -> set[ShapeKey]:
    """Compute the set of canonical `ShapeKey`s covered by `tests/golden/corpus.json`."""
    if corpus_records is None:
        corpus_records = json.loads(GOLDEN_CORPUS_JSON.read_text(encoding="utf-8"))
    shapes: set[ShapeKey] = set()
    for rec in corpus_records:
        parsed = OWNMessage.parse(rec["frame"])
        if parsed is not None:
            shapes.add(message_shape_key(parsed))
    return shapes


@dataclass
class CoverageAuditResult:
    """Structured result of evaluating trace shape coverage against the Golden Corpus."""

    golden_shape_count: int
    observed_local_shape_count: int
    uncovered_local_shapes: dict[ShapeKey, tuple[str, str]]
    new_uncovered_shapes: dict[ShapeKey, list[tuple[str, str]]]
    stale_baseline_shapes: list[ShapeKey]
    unexpected_invalid_frames: list[tuple[str, str, str]]
    stale_invalid_allowlist: list[str]
    stale_unimplemented_allowlist: list[ShapeKey]
    graduated_unimplemented_allowlist: list[ShapeKey]

    @property
    def ok(self) -> bool:
        return not (
            self.new_uncovered_shapes
            or self.stale_baseline_shapes
            or self.unexpected_invalid_frames
            or self.stale_invalid_allowlist
            or self.stale_unimplemented_allowlist
            or self.graduated_unimplemented_allowlist
        )


def evaluate_trace_shape_coverage(
    *,
    corpus_records: list[dict[str, Any]] | None = None,
    baseline_path: Path = BASELINE_JSON,
    fixtures_dir: Path = FIXTURES_DIR,
    encyclopedia_evidence_dir: Path | None = DEFAULT_ENCYCLOPEDIA_EVIDENCE_DIR,
) -> CoverageAuditResult:
    """Evaluate all trace fixtures against Golden Corpus shapes, allowlists, and ratchet."""
    golden_shapes = load_golden_shapes(corpus_records)
    local_frames, enc_frames = iter_all_trace_frames(
        fixtures_dir=fixtures_dir,
        encyclopedia_evidence_dir=encyclopedia_evidence_dir,
    )

    local_raw_set = {raw for _src, raw in local_frames}
    stale_invalid = sorted(
        raw for raw in KNOWN_INVALID_OR_ADVERSARIAL_FRAMES if raw not in local_raw_set
    )

    baseline_keys: set[ShapeKey] = set()
    if baseline_path.is_file():
        baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        for k_str in baseline_payload.get("shapes", {}):
            baseline_keys.add(deserialize_shape_key(k_str))

    observed_local_shapes: set[ShapeKey] = set()
    uncovered_local: dict[ShapeKey, tuple[str, str]] = {}
    new_uncovered: dict[ShapeKey, list[tuple[str, str]]] = {}
    unexpected_invalid: list[tuple[str, str, str]] = []

    def _record_new_uncovered(sk: ShapeKey, src: str, raw: str) -> None:
        occ = new_uncovered.setdefault(sk, [])
        if all(existing_src != src for existing_src, _ in occ):
            occ.append((src, raw))

    def _inspect(entries: list[tuple[str, str]], *, is_local: bool) -> None:
        for src, raw in entries:
            if is_session_or_auth_frame(raw) or BARE_WHO22_CRASH_PATTERN.match(raw):
                continue
            if raw in KNOWN_INVALID_OR_ADVERSARIAL_FRAMES:
                continue
            try:
                parsed = OWNMessage.parse(raw)
            except Exception as exc:  # noqa: BLE001
                unexpected_invalid.append((src, raw, f"{type(exc).__name__}: {exc}"))
                continue
            if parsed is None:
                unexpected_invalid.append((src, raw, "OWNMessage.parse returned None"))
                continue

            sk = message_shape_key(parsed)
            if is_local:
                observed_local_shapes.add(sk)
            if sk in UNIMPLEMENTED_PARSER_SHAPES or sk in golden_shapes:
                continue
            if is_local:
                uncovered_local.setdefault(sk, (src, raw))
                if sk not in baseline_keys:
                    _record_new_uncovered(sk, src, raw)
            else:
                _record_new_uncovered(sk, src, raw)

    _inspect(local_frames, is_local=True)
    _inspect(enc_frames, is_local=False)

    stale_unimplemented = sorted(
        sk for sk in UNIMPLEMENTED_PARSER_SHAPES if sk not in observed_local_shapes
    )
    graduated_unimplemented = sorted(
        sk for sk in UNIMPLEMENTED_PARSER_SHAPES if sk in golden_shapes
    )
    stale_baseline = sorted(sk for sk in baseline_keys if sk not in uncovered_local)

    return CoverageAuditResult(
        golden_shape_count=len(golden_shapes),
        observed_local_shape_count=len(observed_local_shapes),
        uncovered_local_shapes=uncovered_local,
        new_uncovered_shapes=new_uncovered,
        stale_baseline_shapes=stale_baseline,
        unexpected_invalid_frames=unexpected_invalid,
        stale_invalid_allowlist=stale_invalid,
        stale_unimplemented_allowlist=stale_unimplemented,
        graduated_unimplemented_allowlist=graduated_unimplemented,
    )


def write_baseline(baseline_path: Path = BASELINE_JSON) -> int:
    """Write the monotonically shrinking `uncovered_trace_shapes_baseline.json` file."""
    audit = evaluate_trace_shape_coverage(baseline_path=baseline_path)
    if audit.unexpected_invalid_frames:
        for src, raw, err in audit.unexpected_invalid_frames:
            print(f"[ERROR] Unexpected invalid frame in {src}: {raw} ({err})")
        return 1

    shapes_obj: dict[str, dict[str, str]] = {}
    for sk in sorted(audit.uncovered_local_shapes, key=serialize_shape_key):
        src, raw = audit.uncovered_local_shapes[sk]
        shapes_obj[serialize_shape_key(sk)] = {
            "example_frame": raw,
            "first_seen_in": src,
        }

    payload = {
        "description": (
            "Monotonically shrinking legacy trace-shape ratchet. "
            "New trace shapes MUST be added to tests/golden/frames/*.yaml instead of growing this file."
        ),
        "total_uncovered_shapes": len(shapes_obj),
        "shapes": shapes_obj,
    }
    baseline_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {len(shapes_obj)} legacy uncovered trace shapes to {baseline_path}")
    return 0


def suggest_for_trace(trace_path: Path) -> int:
    """Print YAML Golden Corpus candidate stubs for any uncovered shapes in `trace_path`."""
    if not trace_path.is_file():
        print(f"[ERROR] Trace file not found: {trace_path}")
        return 1

    golden_shapes = load_golden_shapes()
    pairs = extract_frames_from_json_file(trace_path)
    rx_pairs = extract_frames_from_json_file(trace_path, directions=("rx",))
    received = {raw for _src, raw in rx_pairs}
    rx_by_shape: dict[ShapeKey, str] = {}
    for _src, rx_raw in rx_pairs:
        if (
            is_session_or_auth_frame(rx_raw)
            or BARE_WHO22_CRASH_PATTERN.match(rx_raw)
            or rx_raw in KNOWN_INVALID_OR_ADVERSARIAL_FRAMES
        ):
            continue
        try:
            rx_parsed = OWNMessage.parse(rx_raw)
        except Exception:  # noqa: BLE001
            continue
        if rx_parsed is None or isinstance(rx_parsed, OWNSignaling):
            continue
        rx_by_shape.setdefault(message_shape_key(rx_parsed), rx_raw)
    suggestions: list[dict[str, Any]] = []
    seen_shapes: set[ShapeKey] = set()

    for _src, raw in pairs:
        if (
            is_session_or_auth_frame(raw)
            or BARE_WHO22_CRASH_PATTERN.match(raw)
            or raw in KNOWN_INVALID_OR_ADVERSARIAL_FRAMES
        ):
            continue
        try:
            parsed = OWNMessage.parse(raw)
        except Exception:  # noqa: BLE001
            continue
        if parsed is None or isinstance(parsed, OWNSignaling):
            continue
        sk = message_shape_key(parsed)
        if sk in golden_shapes or sk in UNIMPLEMENTED_PARSER_SHAPES or sk in seen_shapes:
            continue
        seen_shapes.add(sk)

        if sk in rx_by_shape and raw not in received:
            raw = rx_by_shape[sk]
            reparsed = OWNMessage.parse(raw)
            if reparsed is None or isinstance(reparsed, OWNSignaling):
                continue
            parsed = reparsed

        is_received = raw in received
        direction = sk[0]
        if direction == "command_or_event":
            direction = "event" if is_received else "command"
        if parsed.what is None:
            what_val: int | str | None = None
        elif parsed.what_param:
            what_val = f"{parsed.what}#{'#'.join(parsed.what_param)}"
        else:
            what_val = parsed.what
        rec: dict[str, Any] = {
            "id": f"who{parsed.who}.capture.todo_{len(suggestions) + 1}",
            "frame": raw,
            "direction": direction,
            "who": parsed.who,
            "what": what_val,
            "where": parsed.where if parsed.where is not None else "",
        }
        if getattr(parsed, "is_translation", False):
            rec["is_translation"] = True
        iface, rem_wp = parsed._interface_and_where_params()
        if iface is not None:
            rec["interface"] = iface
        if rem_wp:
            rec["where_param"] = rem_wp
        if parsed.dimension is not None:
            rec["dimension"] = parsed.dimension
            if parsed.dimension_param:
                rec["dimension_param"] = parsed.dimension_param
            if parsed.dimension_value:
                rec["dimension_values"] = parsed.dimension_value
        rec["source"] = "community-plant-capture"
        rec["mcp_valid"] = True
        rec["mcp_notes"] = (
            f"Captured in {trace_path.name}"
            if is_received
            else f"Probe only: sent in {trace_path.name}, not received there (add the reply: ACK or NACK)"
        )
        rec["roundtrip"] = True
        suggestions.append(rec)

    if not suggestions:
        print(
            f"0 uncovered shapes in {trace_path.as_posix()} — "
            "100% covered by tests/golden/corpus.json!"
        )
        return 0

    print(f"# {len(suggestions)} uncovered shape(s) in {trace_path.as_posix()}:")
    print(yaml.safe_dump(suggestions, sort_keys=False, allow_unicode=True))
    return 0


def format_audit_failures(audit: CoverageAuditResult) -> str:
    """Format human-actionable CI error report when `audit.ok` is False."""
    lines: list[str] = []
    if audit.new_uncovered_shapes:
        lines.append(
            f"{len(audit.new_uncovered_shapes)} uncovered trace shape(s) missing from tests/golden/:"
        )
        for sk in sorted(audit.new_uncovered_shapes, key=serialize_shape_key):
            occurrences = audit.new_uncovered_shapes[sk]
            first_src, _first_raw = occurrences[0]
            shown = occurrences[:20]
            occ_summary = "; ".join(f"{raw} in {src}" for src, raw in shown)
            if len(occurrences) > 20:
                occ_summary += f" (+{len(occurrences) - 20} more)"
            lines.append(
                f"  - [shape={serialize_shape_key(sk)}] {occ_summary}\n"
                f"    Hint: python tools/golden/check_trace_shape_coverage.py --suggest {first_src}"
            )
    if audit.stale_baseline_shapes:
        lines.append(
            f"{len(audit.stale_baseline_shapes)} baseline shape(s) are now covered (shrink ratchet with --write-baseline):"
        )
        for sk in audit.stale_baseline_shapes:
            lines.append(f"  - {serialize_shape_key(sk)}")
    if audit.unexpected_invalid_frames:
        lines.append(
            f"{len(audit.unexpected_invalid_frames)} unparsed/invalid trace frame(s) not in KNOWN_INVALID_OR_ADVERSARIAL_FRAMES:"
        )
        for src, raw, err in audit.unexpected_invalid_frames:
            lines.append(f"  - {raw} in {src}: {err}")
    if audit.stale_invalid_allowlist:
        lines.append(
            f"Stale KNOWN_INVALID_OR_ADVERSARIAL_FRAMES entries no longer in tests/fixtures/: {audit.stale_invalid_allowlist}"
        )
    if audit.stale_unimplemented_allowlist:
        lines.append(
            "Stale UNIMPLEMENTED_PARSER_SHAPES entries no longer in tests/fixtures/: "
            + ", ".join(serialize_shape_key(sk) for sk in audit.stale_unimplemented_allowlist)
        )
    if audit.graduated_unimplemented_allowlist:
        lines.append(
            "Graduated UNIMPLEMENTED_PARSER_SHAPES entries now in Golden Corpus (remove from allowlist): "
            + ", ".join(serialize_shape_key(sk) for sk in audit.graduated_unimplemented_allowlist)
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify OpenWebNet trace-fixture structural shape coverage against tests/golden/."
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write/shrink tests/golden/uncovered_trace_shapes_baseline.json.",
    )
    parser.add_argument(
        "--suggest",
        type=Path,
        default=None,
        help="Print YAML Golden Corpus stubs for any uncovered shapes in a trace JSON file.",
    )
    parser.add_argument(
        "--encyclopedia-evidence",
        type=Path,
        default=None,
        help="Also check the frames.jsonl files of an OpenWebNet-Encyclopedia evidence/ folder.",
    )
    args = parser.parse_args(argv)

    if args.suggest is not None:
        return suggest_for_trace(args.suggest)
    if args.write_baseline:
        return write_baseline()

    audit = evaluate_trace_shape_coverage(encyclopedia_evidence_dir=args.encyclopedia_evidence)
    if not audit.ok:
        print(format_audit_failures(audit))
        return 1

    print(
        "Trace-to-Golden shape coverage OK: "
        f"{audit.golden_shape_count} golden shapes, "
        f"{audit.observed_local_shape_count} observed trace shapes, "
        f"{len(audit.uncovered_local_shapes)} ratcheted legacy shapes, "
        "0 new uncovered shapes."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
