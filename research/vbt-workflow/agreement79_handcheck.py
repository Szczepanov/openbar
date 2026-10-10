#!/usr/bin/env python3
"""#79 S1-SQ-1 hand-check: independent arithmetic on the consumer's own rep windows.

The slot is fixed by the preregistration (`S1-SQ-1`, all three reps). Windows come only from the pinned
consumer's parser (run through `agreement79_consumer_reps.mjs`); this tool never chooses windows, pairs
reps or computes agreement statistics. It recomputes every OpenBar `vy_mps` from consecutive FILTERED
positions and authoritative timestamps with the OpenBar backward-difference rule, then applies the
consumer's arithmetic (left-to-right mean, peak, endpoint ROM, `Math.round(x * k) / k`) to each paired
window, and compares the rounded values exactly to the parser and to the back_squat run-1 report.

Recorded inventory paths resolve against `--root` (the pinned data checkout); command-line paths are used
as given. `--report` may be omitted only when S1-SQ-1 was not paired (not analyzable, or no back_squat
pairs file); the result is then `failed` with `slot_not_analyzable`. The tool checkout's commit is
recorded and must be tracked-clean. The output is private and written once. Exit 0 written (passed or
failed), 1 invalid input (including a dirty tool checkout), 3 infrastructure (consumer checkout not the
clean pinned commit, git/node/bridge unavailable; nothing written).
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
from agreement79_slot_evidence import is_number

ROOT = workflow.ROOT
# Spec-frozen bound on |recomputed vy - analysis vy_mps|; both are the same IEEE arithmetic, so any real
# difference is a contract break rather than rounding.
VY_TOLERANCE_MPS = 1e-9
REVIEWS = ("confirmed", "failed", "not_done")
SOURCES = ("recomputed_vy", "analysis_vy")
BRIDGE = Path(__file__).resolve().with_name("agreement79_consumer_reps.mjs")
REPORT_CONTRACT = (("schemaVersion", study.REPORT_SCHEMA), ("segmentationRule", study.SEGMENTATION),
                   ("wlParserVersion", study.WL_PARSER), ("openBarParserVersion", study.OPENBAR_PARSER),
                   ("minOverlap", study.MIN_OVERLAP))

Bridge = Callable[[Path, Path], dict]
GitState = Callable[[Path], "tuple[str, bool]"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise study.StudyInputError(message)


# --- Injectable process boundaries ---------------------------------------------------------------

def consumer_reps(app: Path, analysis: Path) -> dict:
    """The pinned consumer parser's output for ``analysis`` via the node bridge (one JSON line)."""
    argv = ["node", "--experimental-strip-types", str(BRIDGE), str(app), str(analysis)]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except OSError as error:
        raise study.StudyInfrastructureError(f"node could not be run: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise study.StudyInfrastructureError(
            f"consumer parser bridge failed: {detail[-1] if detail else result.returncode}")
    lines = result.stdout.decode("utf-8", errors="replace").strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as error:
        raise study.StudyInfrastructureError("consumer parser bridge wrote no JSON line") from error


def verify_consumer(app: Path, git: GitState) -> str:
    """The consumer checkout (the app dir's parent) must be the clean pinned commit (else exit 3)."""
    commit, clean = git(Path(app) / "..")
    if commit != study.CONSUMER_COMMIT or not clean:
        raise study.StudyInfrastructureError(f"the consumer checkout must be the clean pinned commit "
                                             f"{study.CONSUMER_COMMIT}; found {commit} (tracked files clean: {clean})")
    return commit


def verify_tool(tool_root: Path, git: GitState) -> tuple[str, bool]:
    """The tool checkout running this hand-check must have no tracked changes (else exit 1)."""
    commit, clean = git(tool_root)
    require(clean, f"the tool checkout {tool_root} has tracked changes; run the hand-check from a clean commit")
    return commit, clean


def checked_bridge_output(value: Any) -> dict:
    """Shape of the bridge line; anything else means the bridge or consumer is not the pinned one."""
    def rep_ok(rep: Any) -> bool:
        return (isinstance(rep, dict)
                and all(type(rep.get(key)) is int for key in ("index", "startFrame", "endFrame", "frameCount"))
                and all(is_number(rep.get(key)) for key in ("startTimeS", "endTimeS", *study.METRICS)))
    ok = (isinstance(value, dict) and isinstance(value.get("parserVersion"), str)
          and isinstance(value.get("segmentationRule"), str) and type(value.get("frameCount")) is int
          and isinstance(value.get("breaks"), list) and isinstance(value.get("reps"), list)
          and all(rep_ok(rep) for rep in value["reps"]))
    if not ok:
        raise study.StudyInfrastructureError("consumer parser bridge output is malformed")
    return value


# --- Inputs --------------------------------------------------------------------------------------

def load_document(path: Path, label: str) -> Any:
    try:
        return study.load_json(path)
    except (OSError, ValueError) as error:
        raise study.StudyInputError(f"cannot read {label} {path}: {error}") from error


def load_slot(root: Path, inventory: Any) -> dict[str, Any]:
    """The S1-SQ-1 inventory entry (analyzability, bound analysis) and whether back_squat has a pairs file."""
    slot = study.HANDCHECK_SLOT
    require(isinstance(inventory, dict) and inventory.get("format") == study.INVENTORY_FORMAT
            and inventory.get("format_version") == study.STUDY_DOCUMENT_VERSION
            and inventory.get("study_id") == study.STUDY_ID, f"not a v1 {study.STUDY_ID} inventory")
    slots, lifts = inventory.get("slots"), inventory.get("lifts")
    require(isinstance(slots, list), "inventory slots must be a list")
    squat = lifts.get("back_squat") if isinstance(lifts, dict) else None
    require(isinstance(squat, dict) and "pairs_file" in squat, "inventory has no back_squat lift record")
    entries = [entry for entry in slots if isinstance(entry, dict) and entry.get("slot") == slot]
    require(len(entries) == 1, f"inventory must hold exactly one {slot} entry")
    entry = entries[0]
    require(inventory.get("lockable") is True, "the inventory is not lockable; run the hand-check on the locked inventory")
    require(isinstance(entry.get("analyzable"), bool), f"inventory {slot} analyzable must be a boolean")
    pairs = squat["pairs_file"] is not None
    lock_tool = inventory.get("tool_commit")
    require(isinstance(lock_tool, str) and study.COMMIT_RE.fullmatch(lock_tool) is not None,
            "inventory tool_commit is missing or invalid")
    if not entry["analyzable"]:
        return {"analyzable": False, "pairs_file": pairs, "path": None, "recorded": None, "sha256": None,
                "lock_tool_commit": lock_tool}
    files = entry.get("files")
    binding = files.get("analysis") if isinstance(files, dict) else None
    require(isinstance(binding, dict), f"analyzable {slot} has no inventory analysis binding")
    path = study.recorded_path(root, binding.get("path"))
    sha = binding.get("sha256")
    require(path is not None, f"inventory {slot} analysis path is not a canonical path inside {root}")
    require(isinstance(sha, str) and study.SHA256_RE.fullmatch(sha) is not None,
            f"inventory {slot} analysis sha256 is invalid")
    return {"analyzable": True, "pairs_file": pairs, "path": path, "recorded": binding["path"], "sha256": sha,
            "lock_tool_commit": lock_tool}


def report_expected(slot: dict[str, Any]) -> bool:
    """A back_squat run-1 report exists for the hand-check only if S1-SQ-1 was paired."""
    return slot["analyzable"] and slot["pairs_file"]


def slot_videos(report: dict[str, Any]) -> list[Any]:
    return [video for video in report["videos"] if isinstance(video, dict) and video.get("label") == study.HANDCHECK_SLOT]


def report_rows(report: Any) -> list[dict[str, Any]] | None:
    """Paired rows of the S1-SQ-1 video (None when absent); malformed consumer output is invalid input."""
    slot = study.HANDCHECK_SLOT
    require(isinstance(report, dict) and isinstance(report.get("videos"), list), "report has no videos list")
    videos = slot_videos(report)
    require(len(videos) <= 1, f"report labels {slot} more than once")
    if not videos:
        return None
    rows = videos[0].get("paired")
    require(isinstance(rows, list), f"report {slot} paired must be a list")
    for row in rows:
        require(isinstance(row, dict) and type(row.get("openBarIndex")) is int and type(row.get("wlIndex")) is int
                and all(isinstance(row.get(metric), dict) and is_number(row[metric].get("openBar"))
                        for metric in study.METRICS), f"report {slot} has a malformed paired row")
    return rows


def snake_case(key: str) -> str:
    """Public reason codes are lowercase tokens; the consumer's camelCase keys become snake_case."""
    return re.sub(r"(?<=[a-z0-9])([A-Z])", lambda match: "_" + match.group(1).lower(), key)


def report_checks(report: dict[str, Any], analysis_sha: str) -> list[str]:
    reasons = [f"report_contract_mismatch:{snake_case(key)}" for key, expected in REPORT_CONTRACT
               if report.get(key) != expected or type(report.get(key)) is not type(expected)]
    videos = slot_videos(report)
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
    if (window[0]["timestamp_s"] != rep["startTimeS"] or window[-1]["timestamp_s"] != rep["endTimeS"]
            or rep["frameCount"] != len(window)):
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
                     for metric in study.METRICS}
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
                  "parser": None if rep is None else {metric: rep[metric] for metric in study.METRICS},
                  "report": {metric: row[metric]["openBar"] for metric in study.METRICS}}
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


def evaluate(root: Path, slot: dict[str, Any], report: dict[str, Any] | None, app: Path,
             bridge: Bridge) -> dict[str, Any]:
    """Every check after the inputs are valid; the bridge runs only for a paired, hash-bound analysis."""
    result: dict[str, Any] = {"reasons": [], "reps": [], "velocity_check": None, "parser": None,
                              "analysis": None, "compared": 0, "matched": 0}
    if not report_expected(slot) or report is None:
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


def document(root: Path, args: argparse.Namespace, slot: dict[str, Any], commits: dict[str, Any],
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
        "format": study.HANDCHECK_FORMAT, "format_version": study.STUDY_DOCUMENT_VERSION, "study_id": study.STUDY_ID,
        "tool_version": study.TOOL_VERSION, "slot": study.HANDCHECK_SLOT, "status": status, "reasons": reasons,
        "reviews": {"pairing": args.pairing_review, "seed_reference": args.seed_reference_review},
        **commits, "freeze": study.freeze_references(), "vy_tolerance_mps": VY_TOLERANCE_MPS,
        "inputs": {"analysis": analysis, "report": None if args.report is None else hashed_input(root, args.report),
                   "inventory": hashed_input(root, args.inventory)},
        "parser": result["parser"], "velocity_check": check, "reps": result["reps"],
        "public": {"status": status, "reasons": reasons, "values_compared": result["compared"],
                   "values_matched": result["matched"], "max_abs_discrepancy_mps": worst},
    }


# --- Command -------------------------------------------------------------------------------------

def check_paths(root: Path, args: argparse.Namespace) -> None:
    study_dir = workflow.safe_resolve(root / study.STUDY_DIR)
    for flag, path in (("--inventory", args.inventory), ("--report", args.report)):
        if path is None:
            continue
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
    parser.add_argument("--report", type=Path,
                        help="back_squat run-1 velocity-agreement JSON (required when S1-SQ-1 was paired)")
    parser.add_argument("--pairing-review", choices=REVIEWS, required=True,
                        help="pairing of the three reps verified against the video")
    parser.add_argument("--seed-reference-review", choices=REVIEWS, required=True,
                        help="seed and reference (scale) review of the slot")
    parser.add_argument("--output", type=Path, required=True, help="new private hand-check JSON")
    return parser.parse_args(argv)


def load_report(args: argparse.Namespace, slot: dict[str, Any]) -> dict[str, Any] | None:
    """The back_squat run-1 report; it may be absent only when S1-SQ-1 was not paired."""
    if args.report is None:
        require(not report_expected(slot), f"--report is required: {study.HANDCHECK_SLOT} is analyzable and "
                                           "back_squat has a pairs file")
        return None
    report = load_document(args.report, "report")
    report_rows(report)
    return report


def main(argv: list[str] | None = None, *, root: Path = ROOT, tool_root: Path = ROOT,
         bridge: Bridge = consumer_reps, git: GitState = study.git_state) -> int:
    args = parse_args(argv, root)
    try:
        root = workflow.safe_resolve(args.root)
        args.inventory, args.output = workflow.safe_resolve(args.inventory), workflow.safe_resolve(args.output)
        args.report = None if args.report is None else workflow.safe_resolve(args.report)
        check_paths(root, args)
        slot = load_slot(root, load_document(args.inventory, "inventory"))
        report = load_report(args, slot)
        consumer_commit = verify_consumer(args.consumer_app, git)
        tool_commit, tool_clean = verify_tool(tool_root, git)
        require(tool_commit == slot["lock_tool_commit"],
                f"the hand-check must run from the tool commit recorded in the lock ({slot['lock_tool_commit']}); "
                f"this checkout is at {tool_commit}")
        result = evaluate(root, slot, report, args.consumer_app, bridge)
        commits = {"consumer_commit": consumer_commit, "tool_commit": tool_commit, "tool_tree_clean": tool_clean}
        output = document(root, args, slot, commits, result)
        study.write_new(args.output, study.serialize(output))
    except (study.StudyInputError, workflow.WorkflowError, FileExistsError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except (study.StudyInfrastructureError, OSError) as error:
        print(f"error: infrastructure abort ({error}); retry with unchanged inputs", file=sys.stderr)
        return 3
    public = output["public"]
    print(f"hand-check {study.HANDCHECK_SLOT}: {public['status']}; {public['values_matched']}/"
          f"{public['values_compared']} rounded values matched; max |vy| discrepancy {public['max_abs_discrepancy_mps']}; "
          f"reasons: {', '.join(public['reasons']) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
