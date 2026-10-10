#!/usr/bin/env python3
"""#79 S1-SQ-1 hand-check: independent arithmetic on the consumer's own rep windows.

The slot is fixed by the preregistration (`S1-SQ-1`, all three reps). Windows come only from the pinned
consumer's parser (run through `agreement79_consumer_reps.mjs`); this tool never chooses windows, pairs
reps or computes agreement statistics. It recomputes every OpenBar `vy_mps` from consecutive FILTERED
positions and authoritative timestamps with the OpenBar backward-difference rule, then applies the
consumer's arithmetic (left-to-right mean, peak, endpoint ROM, `Math.round(x * k) / k`) to each paired
window, and compares the rounded values exactly to the parser and to the back_squat run-1 report.

Recorded inventory paths resolve against `--root` (the pinned data checkout); command-line paths are used
as given. The output is private and written once. Exit 0 written (passed or failed), 1 invalid input,
3 infrastructure (consumer checkout not the clean pinned commit, node/bridge unavailable; nothing written).
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import agreement79_study as study
import analyze_lift as workflow
from agreement79_slot_evidence import InfrastructureError, is_number

ROOT = workflow.ROOT
SLOT = "S1-SQ-1"
FORMAT = "owner-vbt-agreement-79-handcheck"
FORMAT_VERSION = 1
INVENTORY_FORMAT = study.INVENTORY_FORMAT
# Spec-frozen bound on |recomputed vy - analysis vy_mps|; both are the same IEEE arithmetic, so any real
# difference is a contract break rather than rounding.
VY_TOLERANCE_MPS = 1e-9
REVIEWS = ("confirmed", "failed", "not_done")
METRICS = ("meanVelocityMps", "peakVelocityMps", "romCm")
SOURCES = ("recomputed_vy", "analysis_vy")
TARGETS = ("vs_parser", "vs_report")
BRIDGE = Path(__file__).resolve().with_name("agreement79_consumer_reps.mjs")
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
REPORT_CONTRACT = (("schemaVersion", study.REPORT_SCHEMA), ("segmentationRule", study.SEGMENTATION),
                   ("wlParserVersion", study.WL_PARSER), ("openBarParserVersion", study.OPENBAR_PARSER),
                   ("minOverlap", study.MIN_OVERLAP))

Bridge = Callable[[Path, Path], dict]
GitState = Callable[[Path], "tuple[str, str]"]


class HandcheckInputError(ValueError):
    """Invalid inventory, report, path or existing output (exit 1)."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise HandcheckInputError(message)


# --- Injectable process boundaries ---------------------------------------------------------------

def consumer_git_state(app: Path) -> tuple[str, str]:
    """HEAD of the consumer checkout (the app dir's parent) and its tracked-file porcelain status."""
    git = ["git", "-C", str(Path(app) / "..")]
    try:
        head = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, check=False)
        status = subprocess.run([*git, "status", "--porcelain", "--untracked-files=no"],
                                capture_output=True, check=False)
    except OSError as error:
        raise InfrastructureError(f"git could not be run: {error}") from error
    if head.returncode != 0 or status.returncode != 0:
        raise InfrastructureError(f"cannot read the consumer checkout's git state at {app}")
    return (head.stdout.decode("utf-8", errors="replace").strip(),
            status.stdout.decode("utf-8", errors="replace"))


def consumer_reps(app: Path, analysis: Path) -> dict:
    """The pinned consumer parser's output for ``analysis`` via the node bridge (one JSON line)."""
    argv = ["node", "--experimental-strip-types", str(BRIDGE), str(app), str(analysis)]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except OSError as error:
        raise InfrastructureError(f"node could not be run: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise InfrastructureError(f"consumer parser bridge failed: {detail[-1] if detail else result.returncode}")
    lines = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise InfrastructureError("consumer parser bridge wrote no JSON line") from error


def verify_consumer(app: Path, git: GitState) -> str:
    commit, porcelain = git(app)
    if commit != study.CONSUMER_COMMIT or porcelain.strip():
        raise InfrastructureError(f"the consumer checkout must be the clean pinned commit {study.CONSUMER_COMMIT}; "
                                  f"found {commit} (tracked changes: {bool(porcelain.strip())})")
    return commit


def checked_bridge_output(value: Any) -> dict:
    """Shape of the bridge line; anything else means the bridge or consumer is not the pinned one."""
    def rep_ok(rep: Any) -> bool:
        return (isinstance(rep, dict) and all(type(rep.get(key)) is int for key in ("index", "startFrame", "endFrame"))
                and all(is_number(rep.get(key)) for key in ("startTimeS", "endTimeS", *METRICS)))
    ok = (isinstance(value, dict) and isinstance(value.get("parserVersion"), str)
          and isinstance(value.get("segmentationRule"), str) and type(value.get("frameCount")) is int
          and isinstance(value.get("breaks"), list) and isinstance(value.get("reps"), list)
          and all(rep_ok(rep) for rep in value["reps"]))
    if not ok:
        raise InfrastructureError("consumer parser bridge output is malformed")
    return value


# --- Inputs --------------------------------------------------------------------------------------

def load_document(path: Path, label: str) -> Any:
    try:
        return study.load_json(path)
    except (OSError, ValueError) as error:
        raise HandcheckInputError(f"cannot read {label} {path}: {error}") from error


def load_slot(root: Path, inventory: Any) -> dict[str, Any]:
    """The S1-SQ-1 inventory entry: analyzability and, when analyzable, the bound analysis file."""
    require(isinstance(inventory, dict) and inventory.get("format") == INVENTORY_FORMAT
            and inventory.get("format_version") == study.STUDY_DOCUMENT_VERSION
            and inventory.get("study_id") == study.STUDY_ID, f"not a v1 {study.STUDY_ID} inventory")
    slots = inventory.get("slots")
    require(isinstance(slots, list), "inventory slots must be a list")
    entries = [entry for entry in slots if isinstance(entry, dict) and entry.get("slot") == SLOT]
    require(len(entries) == 1, f"inventory must hold exactly one {SLOT} entry")
    entry = entries[0]
    require(isinstance(entry.get("analyzable"), bool), f"inventory {SLOT} analyzable must be a boolean")
    if not entry["analyzable"]:
        return {"analyzable": False, "path": None, "recorded": None, "sha256": None}
    files = entry.get("files")
    binding = files.get("analysis") if isinstance(files, dict) else None
    require(isinstance(binding, dict), f"analyzable {SLOT} has no inventory analysis binding")
    path = study.recorded_path(root, binding.get("path"))
    sha = binding.get("sha256")
    require(path is not None, f"inventory {SLOT} analysis path is not a canonical path inside {root}")
    require(isinstance(sha, str) and SHA256.fullmatch(sha) is not None, f"inventory {SLOT} analysis sha256 is invalid")
    return {"analyzable": True, "path": path, "recorded": binding["path"], "sha256": sha}


def report_rows(report: Any) -> list[dict[str, Any]] | None:
    """Paired rows of the S1-SQ-1 video (None when absent); malformed consumer output is invalid input."""
    require(isinstance(report, dict) and isinstance(report.get("videos"), list), "report has no videos list")
    videos = [video for video in report["videos"] if isinstance(video, dict) and video.get("label") == SLOT]
    require(len(videos) <= 1, f"report labels {SLOT} more than once")
    if not videos:
        return None
    rows = videos[0].get("paired")
    require(isinstance(rows, list), f"report {SLOT} paired must be a list")
    for row in rows:
        require(isinstance(row, dict) and type(row.get("openBarIndex")) is int and type(row.get("wlIndex")) is int
                and all(isinstance(row.get(metric), dict) and is_number(row[metric].get("openBar"))
                        for metric in METRICS), f"report {SLOT} has a malformed paired row")
    return rows


def report_checks(report: dict[str, Any], analysis_sha: str) -> list[str]:
    reasons = [f"report_contract_mismatch:{key}" for key, expected in REPORT_CONTRACT
               if report.get(key) != expected or type(report.get(key)) is not type(expected)]
    videos = [video for video in report["videos"] if isinstance(video, dict) and video.get("label") == SLOT]
    if not videos:
        return reasons + ["report_slot_missing"]
    open_bar = videos[0].get("openBar")
    if not isinstance(open_bar, dict) or open_bar.get("fileSha256") != analysis_sha:
        reasons.append("report_analysis_hash_mismatch")
    return reasons


def parse_analysis(analysis: Any) -> dict[str, Any] | None:
    """The fields the hand-check reads; None when the document does not have them (recorded failure)."""
    def samples(value: Any, velocity: bool) -> list[dict[str, Any]] | None:
        if not isinstance(value, list) or not value:
            return None
        rows = []
        for sample in value:
            if not isinstance(sample, dict) or not all(is_number(sample.get(key))
                                                        for key in ("timestamp_s", "y_m", "confidence")):
                return None
            vy = sample.get("vy_mps")
            if velocity and vy is not None and not is_number(vy):
                return None
            rows.append({"timestamp_s": sample["timestamp_s"], "y_m": sample["y_m"],
                         "confidence": sample["confidence"], "vy_mps": vy if velocity else None})
        times = [row["timestamp_s"] for row in rows]
        return rows if all(later > earlier for earlier, later in zip(times, times[1:])) else None

    derived = analysis.get("derived") if isinstance(analysis, dict) else None
    kinematics = derived.get("kinematics") if isinstance(derived, dict) else None
    filtered = derived.get("filtered") if isinstance(derived, dict) else None
    if not isinstance(kinematics, dict):
        return None
    method = kinematics.get("method")
    parameters = method.get("parameters") if isinstance(method, dict) else None
    kinematic_rows = samples(kinematics.get("samples"), True)
    if kinematic_rows is None or not isinstance(parameters, dict):
        return None
    max_gap_s, min_confidence = parameters.get("max_gap_s"), parameters.get("min_confidence")
    if not is_number(max_gap_s) or not is_number(min_confidence):
        return None
    filtered_rows = None
    if filtered is not None:  # absent filtered output is a recorded `filtered_sample_missing`, not invalid
        filtered_rows = samples(filtered.get("samples"), False) if isinstance(filtered, dict) else None
        if filtered_rows is None:
            return None
    return {"input": kinematics.get("input"), "kinematics": kinematic_rows, "filtered": filtered_rows,
            "max_gap_s": max_gap_s, "min_confidence": min_confidence}


# --- Arithmetic ----------------------------------------------------------------------------------

def recompute_velocity(filtered: list[dict[str, Any]], max_gap_s: float, min_confidence: float) -> list[float | None]:
    """OpenBar backward difference (kinematics.rs derive_velocity): null at 0, across a gap or low confidence."""
    velocities: list[float | None] = [None]
    for previous, current in zip(filtered, filtered[1:]):
        dt = current["timestamp_s"] - previous["timestamp_s"]
        supported = (dt <= max_gap_s and previous["confidence"] >= min_confidence
                     and current["confidence"] >= min_confidence)
        velocities.append((current["y_m"] - previous["y_m"]) / dt if supported else None)
    return velocities


def consumer_metrics(velocities: list[float], y_start_m: float, y_end_m: float, y0_m: float) -> dict[str, float]:
    """concentricSegmentation.ts arithmetic: left-to-right sum / count, max, endpoint ROM, Math.round."""
    total = 0.0
    for velocity in velocities:  # explicit loop: Python's sum() of floats is compensated, JS reduce is not
        total += velocity
    rom_cm = (y_end_m - y0_m) * 100 - (y_start_m - y0_m) * 100
    return {"meanVelocityMps": study.js_round(total / len(velocities), 1000),
            "peakVelocityMps": study.js_round(max(velocities), 1000), "romCm": study.js_round(rom_cm, 100)}


def velocity_check(series: dict[str, Any]) -> tuple[dict[str, Any], list[float | None] | None, list[str]]:
    """Recompute vy from filtered positions; compare null pattern exactly and values within tolerance."""
    kinematics = series["kinematics"]
    stats: dict[str, Any] = {"samples": len(kinematics), "null_count": sum(s["vy_mps"] is None for s in kinematics),
                             "compared": 0, "null_pattern_mismatches": 0, "max_abs_discrepancy_mps": None,
                             "passed": False}
    if series["input"] != "filtered":
        return stats, None, ["kinematics_input_not_filtered"]
    filtered = series["filtered"]
    if filtered is None:
        return stats, None, ["filtered_sample_missing"]
    position = {sample["timestamp_s"]: index for index, sample in enumerate(filtered)}
    twins = [position.get(sample["timestamp_s"]) for sample in kinematics]
    if any(twin is None for twin in twins):
        return stats, None, ["filtered_sample_missing"]
    reasons = []
    if any(filtered[twin]["y_m"] != sample["y_m"] for twin, sample in zip(twins, kinematics)):
        reasons.append("filtered_y_mismatch")
    every = recompute_velocity(filtered, series["max_gap_s"], series["min_confidence"])
    recomputed = [every[twin] for twin in twins]
    pairs = list(zip(recomputed, (sample["vy_mps"] for sample in kinematics)))
    mismatches = sum((mine is None) != (theirs is None) for mine, theirs in pairs)
    differences = [abs(mine - theirs) for mine, theirs in pairs if mine is not None and theirs is not None]
    worst = max(differences, default=0.0)
    if mismatches:
        reasons.append("velocity_null_pattern_mismatch")
    if worst > VY_TOLERANCE_MPS:
        reasons.append("velocity_recompute_mismatch")
    stats.update(compared=len(differences), null_pattern_mismatches=mismatches, max_abs_discrepancy_mps=worst,
                 passed=not reasons)
    return stats, recomputed, reasons


# --- Per-rep comparison --------------------------------------------------------------------------

def window_metrics(detail: dict[str, Any], rep: dict[str, Any], series: dict[str, Any],
                   recomputed: list[float | None] | None) -> list[str]:
    """Fill the consumer window (kinematics indices startFrame-1 .. endFrame-1) and both recomputations."""
    kinematics = series["kinematics"]
    start, end = rep["startFrame"] - 1, rep["endFrame"] - 1
    if not 0 <= start <= end < len(kinematics):
        return ["window_out_of_range"]
    window = kinematics[start:end + 1]
    detail["window"] = {"start_frame": rep["startFrame"], "end_frame": rep["endFrame"],
                        "start_time_s": window[0]["timestamp_s"], "end_time_s": window[-1]["timestamp_s"],
                        "sample_count": len(window)}
    reasons = []
    if window[0]["timestamp_s"] != rep["startTimeS"] or window[-1]["timestamp_s"] != rep["endTimeS"]:
        reasons.append("window_mismatch")
    values = {"recomputed_vy": None if recomputed is None else recomputed[start:end + 1],
              "analysis_vy": [sample["vy_mps"] for sample in window]}
    contains_null = any(source is not None and None in source for source in values.values())
    detail["window_contains_null"] = contains_null
    if contains_null:
        reasons.append("window_contains_null")
    y0_m = kinematics[0]["y_m"]
    for source, velocities in values.items():
        if velocities is not None and None not in velocities:
            detail["recomputed"][source] = consumer_metrics(velocities, window[0]["y_m"], window[-1]["y_m"], y0_m)
    return reasons


def compare_rep(detail: dict[str, Any]) -> tuple[int, int, bool]:
    """Exact (==) comparison of each recomputation to parser and report values; (compared, matched, mismatch)."""
    compared = matched = 0
    mismatch = False
    detail["matches"] = {}
    for source in SOURCES:
        computed = detail["recomputed"][source]
        detail["matches"][source] = {}
        for target, expected in (("vs_parser", detail["parser"]), ("vs_report", detail["report"])):
            flags = {metric: computed is not None and expected is not None and computed[metric] == expected[metric]
                     for metric in METRICS}
            detail["matches"][source][target] = flags
            compared += len(flags)
            matched += sum(flags.values())
            mismatch = mismatch or (computed is not None and expected is not None and not all(flags.values()))
    return compared, matched, mismatch


def rep_details(rows: list[dict[str, Any]], parser: dict[str, Any] | None, series: dict[str, Any] | None,
                recomputed: list[float | None] | None) -> tuple[list[dict[str, Any]], list[str], int, int]:
    by_index = {} if parser is None else {rep["index"]: rep for rep in parser["reps"]}
    details, reasons, compared, matched = [], [], 0, 0
    for row in rows:
        rep = by_index.get(row["openBarIndex"])
        detail = {"openBarIndex": row["openBarIndex"], "wlIndex": row["wlIndex"], "window": None,
                  "window_contains_null": None, "recomputed": {source: None for source in SOURCES},
                  "parser": None if rep is None else {metric: rep[metric] for metric in METRICS},
                  "report": {metric: row[metric]["openBar"] for metric in METRICS}}
        if rep is None:
            reasons.append("parser_rep_missing")
        elif series is not None:
            reasons += window_metrics(detail, rep, series, recomputed)
        rep_compared, rep_matched, mismatch = compare_rep(detail)
        compared, matched = compared + rep_compared, matched + rep_matched
        if mismatch:
            reasons.append("value_mismatch")
        details.append(detail)
    return details, reasons, compared, matched


# --- Evaluation ----------------------------------------------------------------------------------

def hashed_input(root: Path, path: Path) -> dict[str, str]:
    return {"path": study.repo_relative(path, root), "sha256": study.file_sha256(path)}


def evaluate(root: Path, slot: dict[str, Any], report: dict[str, Any], app: Path, bridge: Bridge) -> dict[str, Any]:
    """Every check after the inputs are valid; the bridge runs only for an analyzable, hash-bound analysis."""
    result: dict[str, Any] = {"reasons": [], "reps": [], "velocity_check": None, "parser": None,
                              "analysis": None, "compared": 0, "matched": 0}
    if not slot["analyzable"]:
        result["reasons"].append("slot_not_analyzable")
        return result
    reasons = result["reasons"]
    rows = report_rows(report)
    reasons += report_checks(report, slot["sha256"])
    rows = rows or []
    if len(rows) != study.PLANNED_REPS_PER_VIDEO:
        reasons.append("not_three_paired_reps")
    analysis_path = slot["path"]
    if not analysis_path.is_file():
        reasons.append("analysis_missing")
        return result
    actual_sha = study.file_sha256(analysis_path)
    result["analysis"] = {"path": slot["recorded"], "sha256": actual_sha}
    if actual_sha != slot["sha256"]:
        reasons.append("analysis_hash_mismatch")
        return result
    try:
        series = parse_analysis(study.load_json(analysis_path))
    except (OSError, ValueError):
        series = None
    if series is None:
        reasons.append("analysis_invalid")
        return result
    parser = checked_bridge_output(bridge(app, analysis_path))
    result["parser"] = {"parserVersion": parser["parserVersion"], "segmentationRule": parser["segmentationRule"],
                        "frameCount": parser["frameCount"], "breakCount": len(parser["breaks"]),
                        "repCount": len(parser["reps"])}
    if (parser["parserVersion"], parser["segmentationRule"]) != (study.OPENBAR_PARSER, study.SEGMENTATION):
        reasons.append("parser_contract_mismatch")
    stats, recomputed, velocity_reasons = velocity_check(series)
    result["velocity_check"] = stats
    reasons += velocity_reasons
    details, rep_reasons, compared, matched = rep_details(rows, parser, series, recomputed)
    result.update(reps=details, compared=compared, matched=matched)
    reasons += rep_reasons
    return result


def document(root: Path, args: argparse.Namespace, slot: dict[str, Any], consumer_commit: str,
             result: dict[str, Any]) -> dict[str, Any]:
    reasons = list(result["reasons"])
    reasons += [f"pairing_review_{args.pairing_review}"] if args.pairing_review != "confirmed" else []
    reasons += ([f"seed_reference_review_{args.seed_reference_review}"]
                if args.seed_reference_review != "confirmed" else [])
    reasons = sorted(set(reasons))
    status = "failed" if reasons else "passed"
    check = result["velocity_check"]
    worst = None if check is None else check["max_abs_discrepancy_mps"]
    analysis = result["analysis"] or {"path": slot["recorded"], "sha256": None}
    return {
        "format": FORMAT, "format_version": FORMAT_VERSION, "study_id": study.STUDY_ID,
        "tool_version": study.TOOL_VERSION, "slot": SLOT, "status": status, "reasons": reasons,
        "reviews": {"pairing": args.pairing_review, "seed_reference": args.seed_reference_review},
        "consumer_commit": consumer_commit, "freeze": study.freeze_references(),
        "vy_tolerance_mps": VY_TOLERANCE_MPS,
        "inputs": {"analysis": analysis, "report": hashed_input(root, args.report),
                   "inventory": hashed_input(root, args.inventory)},
        "parser": result["parser"], "velocity_check": check, "reps": result["reps"],
        "public": {"status": status, "reasons": reasons, "values_compared": result["compared"],
                   "values_matched": result["matched"], "max_abs_discrepancy_mps": worst},
    }


# --- Command -------------------------------------------------------------------------------------

def check_paths(root: Path, args: argparse.Namespace) -> None:
    study_dir = workflow.safe_resolve(root / study.STUDY_DIR)
    for flag, path in (("--inventory", args.inventory), ("--report", args.report)):
        require(workflow.is_within(path, root), f"{flag} {path} is outside the data checkout {root}")
        require(path.is_file(), f"{flag} {path} is not a file")
    require(workflow.is_within(args.output, study_dir) and args.output != study_dir,
            f"--output must be a new file under {study.STUDY_DIR.as_posix()}/ of the data checkout")
    require(not args.output.exists(), f"--output {args.output} already exists; retained outputs are never replaced")


def parse_args(argv: list[str] | None, root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=root,
                        help="pinned data checkout holding the study evidence (default: this checkout)")
    parser.add_argument("--inventory", type=Path, required=True, help="private #79 inventory.json")
    parser.add_argument("--consumer-app", type=Path, required=True, help="pinned consumer app/ directory")
    parser.add_argument("--report", type=Path, required=True, help="back_squat run-1 velocity-agreement JSON")
    parser.add_argument("--pairing-review", choices=REVIEWS, required=True,
                        help="pairing of the three reps verified against the video")
    parser.add_argument("--seed-reference-review", choices=REVIEWS, required=True,
                        help="seed and reference (scale) review of the slot")
    parser.add_argument("--output", type=Path, required=True, help="new private hand-check JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, root: Path = ROOT, bridge: Bridge = consumer_reps,
         git: GitState = consumer_git_state) -> int:
    args = parse_args(argv, root)
    try:
        root = workflow.safe_resolve(args.root)
        args.inventory, args.report = workflow.safe_resolve(args.inventory), workflow.safe_resolve(args.report)
        args.output = workflow.safe_resolve(args.output)
        check_paths(root, args)
        slot = load_slot(root, load_document(args.inventory, "inventory"))
        report = load_document(args.report, "report")
        report_rows(report)
        consumer_commit = verify_consumer(args.consumer_app, git)
        result = evaluate(root, slot, report, args.consumer_app, bridge)
        output = document(root, args, slot, consumer_commit, result)
        study.write_new(args.output, study.serialize(output))
    except (HandcheckInputError, workflow.WorkflowError, FileExistsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (InfrastructureError, OSError) as error:
        print(f"error: infrastructure abort ({error}); retry with unchanged inputs", file=sys.stderr)
        return 3
    public = output["public"]
    print(f"hand-check {SLOT}: {public['status']}; {public['values_matched']}/{public['values_compared']} rounded "
          f"values matched; max |vy| discrepancy {public['max_abs_discrepancy_mps']}; "
          f"reasons: {', '.join(public['reasons']) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
