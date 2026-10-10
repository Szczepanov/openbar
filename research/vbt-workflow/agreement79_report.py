#!/usr/bin/env python3
"""#79 aggregate report: rehash locked inputs, check consumer reports, evaluate the frozen criterion.

Reads the locked private inventory and lock summary (agreement79_inventory.py), the two consumer
`velocity-agreement-v1` report runs per lift, the S1-SQ-1 hand-check and the session scale reports, then
writes ONE new aggregate-only Markdown report (agreement79_report_render.py) under docs/analysis/ or
target/ of this checkout. No parser, segmenter, pairing or statistic is reimplemented: every number comes
from the consumer's serialized JSON. The verdict is a mechanical evaluation of the frozen criterion; the
reviewed decision is recorded separately. Only a lockable inventory (collection complete or concluded) is
reported; statistics are never rendered for a collection in progress. The tool checkout's git state enters C4.
Exit 0 written, 1 invalid or inconsistent input (including a non-lockable inventory), 3 infrastructure (consumer
checkout not the pinned clean commit, git or node unavailable). Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import agreement79_inventory as inventory_tool
import agreement79_report_render as render
import agreement79_study as study
import analyze_lift as workflow
from agreement79_slot_evidence import InfrastructureError as EvidenceInfrastructureError
from agreement79_slot_evidence import dig, is_number

ROOT = workflow.ROOT
HANDCHECK_STATUSES = ("passed", "failed")
EXERCISES = tuple(lift["exercise"] for lift in study.LIFTS.values())
REPORT_FILES = (("run1_json", "run1.json"), ("run2_json", "run2.json"), ("run1_md", "run1.md"), ("run2_md", "run2.md"))
OUTPUT_DIRS = ("docs/analysis", "target")
COUNT_KEYS = ("wlTotal", "openBarTotal", "paired", "wlOnly", "openBarOnly", "openBarExcluded")
UNMATCHED_KEYS = ("wlOnly", "openBarOnly", "openBarExcluded")
TRACKING_STATES = ("tracked", "low_confidence", "lost")
STATISTICS = ("bias", "sampleSd", "lowerLoA", "upperLoA", "meanAbsoluteDifference", "slope", "intercept",
              "pearsonR", "geometricMeanRatio")
STAT_REASONS = frozenset({"insufficient_n", "constant_magnitudes", "zero_variance", "invalid_scale",
                          "non_finite_result"})
PAIRS_PER_LIFT = study.VIDEOS_PER_LIFT * study.PLANNED_REPS_PER_VIDEO
REDACTION_MIN_CHARS = 4
GitState = Callable[[Path], "tuple[str, bool]"]
NodeVersion = Callable[[], str]
ReportInputError = study.StudyInputError
INFRASTRUCTURE_ERRORS = (study.StudyInfrastructureError, EvidenceInfrastructureError, OSError)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportInputError(message)


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and study.SHA256_RE.fullmatch(value) is not None


# --- Injectable process boundaries ---------------------------------------------------------------

def consumer_git_state(app: Path) -> tuple[str, bool]:
    """HEAD of the consumer checkout holding ``app`` and whether its tracked files are clean."""
    try:
        return study.git_state(app.parent)
    except study.StudyInfrastructureError as error:
        raise study.StudyInfrastructureError(f"consumer checkout: {error}") from error


def node_version() -> str:
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, check=False)
    except OSError as error:
        raise study.StudyInfrastructureError(f"node could not be run: {error}") from error
    text = result.stdout.decode("utf-8", errors="replace").strip()
    if result.returncode != 0 or not text or "\n" in text:
        raise study.StudyInfrastructureError("node --version failed")
    return text


# --- Inputs --------------------------------------------------------------------------------------

def read_json(path: Path, label: str) -> tuple[Any, bytes]:
    """Strict JSON plus its exact bytes (hashes are always of the bytes, never re-serialized JSON)."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ReportInputError(f"{label} is unreadable: {error}") from error
    try:
        return workflow.schema_check.loads_strict(data.decode("utf-8")), data
    except (UnicodeDecodeError, ValueError) as error:
        raise ReportInputError(f"{label} is not strict UTF-8 JSON: {error}") from error


def check_inventory(document: Any) -> None:
    require(isinstance(document, dict) and document.get("format") == study.INVENTORY_FORMAT
            and document.get("format_version") == study.STUDY_DOCUMENT_VERSION
            and document.get("study_id") == study.STUDY_ID, f"inventory must be a v1 {study.STUDY_ID} inventory")
    require(document.get("freeze") == study.freeze_references(), "inventory freeze references are not the frozen ones")
    require(document.get("collection_status") in inventory_tool.COLLECTION_STATUSES, "inventory collection_status is invalid")
    require(document.get("lockable") is True and document["collection_status"] != "in_progress",
            "inventory is not lockable (collection in progress); only a locked, lockable inventory is reported")
    slots = document.get("slots")
    require(isinstance(slots, list) and [dig(slot, "slot") for slot in slots] == list(study.SLOTS),
            "inventory slots must list the 18 frozen slots in order")
    for slot in slots:
        lift = study.slot_lift(slot["slot"])
        require(slot.get("exercise") == lift["exercise"] and slot.get("load_kg") == lift["load_kg"]
                and slot.get("session") == study.slot_session(slot["slot"]), f"inventory {slot['slot']} lift is not frozen")
        require(all(type(slot.get(key)) is bool for key in ("analyzable", "protocol_conforming")),
                f"inventory {slot['slot']} analyzable/protocol_conforming must be booleans")
        require(isinstance(slot.get("failures"), list) and isinstance(slot.get("files"), dict),
                f"inventory {slot['slot']} failures/files are malformed")
    require(isinstance(document.get("lifts"), dict) and set(document["lifts"]) == set(EXERCISES),
            "inventory lifts must cover the three frozen lifts")
    require(isinstance(document.get("counts"), dict) and isinstance(document.get("sessions"), dict),
            "inventory counts/sessions are malformed")


def check_lock_summary(summary: Any, inventory_sha: str) -> None:
    require(isinstance(summary, dict) and summary.get("format") == study.LOCK_SUMMARY_FORMAT
            and summary.get("format_version") == study.STUDY_DOCUMENT_VERSION
            and summary.get("study_id") == study.STUDY_ID, "lock summary must be a v1 #79 lock summary")
    require(summary.get("inventory_sha256") == inventory_sha, "inventory sha256 is not the lock summary's")
    require(summary.get("lockable") is True, "lock summary is not lockable; only a locked, lockable inventory is reported")
    require(summary.get("freeze") == study.freeze_references(), "lock summary freeze references are not the frozen ones")


def check_stats(stats: Any, where: str) -> None:
    require(isinstance(stats, dict) and set(study.METRICS) <= set(stats),
            f"{where}: stats must cover {', '.join(study.METRICS)}")
    for metric in study.METRICS:
        item = stats[metric]
        require(isinstance(item, dict) and type(item.get("n")) is int and item["n"] >= 0
                and isinstance(item.get("reasons"), dict), f"{where}: {metric} stats are malformed")
        for key in STATISTICS:
            value, reason = item.get(key), item["reasons"].get(key)
            require(value is None or is_number(value), f"{where}: {metric}.{key} must be a finite number or null")
            require((value is None) == (reason in STAT_REASONS) and (value is None or reason is None),
                    f"{where}: {metric}.{key} needs a known null reason exactly when it is null")


def check_counts(counts: Any, where: str) -> None:
    require(isinstance(counts, dict) and all(type(counts.get(key)) is int and counts[key] >= 0 for key in COUNT_KEYS),
            f"{where}: counts must be non-negative integers")


def check_video(video: Any, slot: dict[str, Any], where: str) -> None:
    label = slot["slot"]
    files = slot["files"]
    require(dig(video, "wl", "fileSha256") == dig(files, "wl_csv", "sha256"),
            f"{where}: {label} WL file sha256 is not the inventory's")
    require(dig(video, "openBar", "fileSha256") == dig(files, "analysis", "sha256"),
            f"{where}: {label} OpenBar analysis sha256 is not the inventory's")
    require(is_number(video.get("offsetS")) and video["offsetS"] == 0, f"{where}: {label} offsetS must be 0")
    require(is_number(video.get("loadKg")) and video["loadKg"] == slot["load_kg"],
            f"{where}: {label} loadKg must be {slot['load_kg']}")
    breaks = dig(video, "openBar", "breakCount")
    require(type(breaks) is int and breaks >= 0, f"{where}: {label} breakCount must be a non-negative integer")
    check_counts(video.get("counts"), f"{where}: {label}")
    check_stats(video.get("stats"), f"{where}: {label}")
    paired = video.get("paired")
    require(isinstance(paired, list) and len(paired) == video["counts"]["paired"]
            and all(isinstance(row, dict) and type(row.get("wlComplete")) is bool
                    and type(row.get("openBarComplete")) is bool for row in paired),
            f"{where}: {label} paired rows must match counts and carry completeness flags")
    for key in UNMATCHED_KEYS:
        require(isinstance(video.get(key), list) and len(video[key]) == video["counts"][key],
                f"{where}: {label} {key} rows must match counts")


def check_report(report: Any, exercise: str, labels: list[str], slots: dict[str, dict[str, Any]]) -> None:
    """Frozen consumer contract plus the binding of every video to the locked inventory."""
    where = f"{exercise} run1 report"
    require(isinstance(report, dict), f"{where} must be a JSON object")
    for key, expected in (("schemaVersion", study.REPORT_SCHEMA), ("segmentationRule", study.SEGMENTATION),
                          ("wlParserVersion", study.WL_PARSER), ("openBarParserVersion", study.OPENBAR_PARSER)):
        require(report.get(key) == expected, f"{where}: {key} must be {expected!r}")
    require(is_number(report.get("minOverlap")) and report["minOverlap"] == study.MIN_OVERLAP,
            f"{where}: minOverlap must be {study.MIN_OVERLAP}")
    require(is_sha256(report.get("openBarMethodConfigSha256")), f"{where}: openBarMethodConfigSha256 is malformed")
    videos = report.get("videos")
    require(isinstance(videos, list) and all(isinstance(video, dict) for video in videos), f"{where}: videos malformed")
    found = [video.get("label") for video in videos]
    require(len(set(found)) == len(found) and set(found) == set(labels),
            f"{where}: video labels must equal the pairs-file labels")
    pooled = report.get("pooled")
    require(isinstance(pooled, dict) and pooled.get("videoCount") == len(labels), f"{where}: pooled videoCount mismatch")
    check_counts(pooled.get("counts"), f"{where}: pooled")
    check_stats(pooled.get("stats"), f"{where}: pooled")
    for video in videos:
        check_video(video, slots[video["label"]], where)


def pairs_labels(root: Path, exercise: str, lift: dict[str, Any]) -> list[str]:
    path = study.recorded_path(root, dig(lift, "pairs_file", "path"))
    require(path is not None, f"{exercise} pairs file path is not a canonical repository path")
    document, _ = read_json(path, f"{exercise} pairs file")
    pairs = dig(document, "pairs")
    require(isinstance(pairs, list) and all(isinstance(item, dict) for item in pairs), f"{exercise} pairs file malformed")
    labels = [item.get("label") for item in pairs]
    require(labels == lift.get("analyzable_slots"), f"{exercise} pairs-file labels are not the analyzable slots")
    return labels


def load_lift(root: Path, runs_dir: Path, exercise: str, inventory: dict[str, Any]) -> dict[str, Any]:
    lift = inventory["lifts"][exercise]
    if lift.get("pairs_file") is None:
        return {"report": None, "reason": lift.get("reason") or "no_analyzable_slots"}
    labels = pairs_labels(root, exercise, lift)
    data = {}
    for key, suffix in REPORT_FILES:
        path = runs_dir / f"report-{exercise}-{suffix}"
        require(path.is_file(), f"required consumer report report-{exercise}-{suffix} is missing")
        try:
            data[key] = path.read_bytes()
        except OSError as error:
            raise ReportInputError(f"consumer report report-{exercise}-{suffix} is unreadable: {error}") from error
    report, _ = read_json(runs_dir / f"report-{exercise}-run1.json", f"{exercise} run1 report")
    slots = {slot["slot"]: slot for slot in inventory["slots"]}
    check_report(report, exercise, labels, slots)
    return {"report": report, "reason": None,
            "hashes": {key: hashlib.sha256(value).hexdigest() for key, value in data.items()},
            "json_identical": data["run1_json"] == data["run2_json"], "md_identical": data["run1_md"] == data["run2_md"]}


def handcheck_consistent(document: dict[str, Any], inventory_sha: str, squat_report_sha: str | None) -> bool:
    """Bound to this inventory and squat report (null only without one), pinned consumer, frozen refs, coherent status."""
    recorded_report = dig(document, "inputs", "report")
    report_bound = (recorded_report is None if squat_report_sha is None
                    else dig(recorded_report, "sha256") == squat_report_sha)
    public = document["public"]
    coherent = public["status"] != "passed" or (not public["reasons"] and not document.get("reasons"))
    return (dig(document, "inputs", "inventory", "sha256") == inventory_sha and report_bound
            and document.get("consumer_commit") == study.CONSUMER_COMMIT
            and ("freeze" not in document or document["freeze"] == study.freeze_references()) and coherent)


def load_handcheck(path: Path, inventory_sha: str, squat_report_sha: str | None) -> dict[str, Any]:
    """The only reader of the hand-check document; uses its `public` sub-object and recorded input hashes.

    Reasons are reduced to public tool codes; any inconsistency adds `handcheck_inputs_mismatch` (status failed).
    """
    document, _ = read_json(path, "hand-check")
    require(isinstance(document, dict) and document.get("format") == study.HANDCHECK_FORMAT
            and document.get("format_version") == study.STUDY_DOCUMENT_VERSION
            and document.get("study_id") == study.STUDY_ID,
            f"hand-check must be a {study.STUDY_ID} {study.HANDCHECK_FORMAT} v1")
    require(document.get("slot") == study.HANDCHECK_SLOT, f"hand-check slot must be {study.HANDCHECK_SLOT}")
    public = document.get("public")
    require(isinstance(public, dict) and public.get("status") in HANDCHECK_STATUSES
            and public["status"] == document.get("status"), "hand-check public status is missing or inconsistent")
    require(isinstance(public.get("reasons"), list) and all(isinstance(item, str) for item in public["reasons"]),
            "hand-check public reasons must be a list of text")
    for key in ("values_compared", "values_matched"):
        require(type(public.get(key)) is int and public[key] >= 0, f"hand-check public {key} must be a count")
    discrepancy = public.get("max_abs_discrepancy_mps")
    require(discrepancy is None or is_number(discrepancy), "hand-check max_abs_discrepancy_mps must be a number or null")
    inputs_match = handcheck_consistent(document, inventory_sha, squat_report_sha)
    reasons = [render.public_code(item) for item in public["reasons"]]
    reasons += [] if inputs_match else ["handcheck_inputs_mismatch"]
    tool_commit = document.get("tool_commit")
    return {"status": public["status"] if inputs_match else "failed", "reasons": reasons,
            "values_compared": public["values_compared"], "values_matched": public["values_matched"],
            "max_abs_discrepancy_mps": discrepancy, "inputs_match": inputs_match,
            "tool_commit": tool_commit if isinstance(tool_commit, str) else None}


def tracking_counts(root: Path, binding: Any) -> dict[str, int] | None:
    """Counts of `lost` and `low_confidence` raw observations of a hash-bound analysis; None when unavailable."""
    path = study.recorded_path(root, dig(binding, "path"))
    if path is None or not path.is_file():
        return None
    try:
        data = path.read_bytes()
        observations = dig(workflow.schema_check.loads_strict(data.decode("utf-8")), "raw_observations")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if hashlib.sha256(data).hexdigest() != dig(binding, "sha256") or not isinstance(observations, list):
        return None
    states = [dig(item, "tracking_state") for item in observations]
    if not all(state in TRACKING_STATES for state in states):
        return None
    return {"lost": states.count("lost"), "low_confidence": states.count("low_confidence")}


def tracking_diagnostics(root: Path, inventory: dict[str, Any]) -> dict[str, dict[str, int] | None]:
    """Per slot with an inventory analysis binding: tracking-state counts (counts only, never values)."""
    return {slot["slot"]: tracking_counts(root, slot["files"]["analysis"]) for slot in inventory["slots"]
            if isinstance(slot["files"].get("analysis"), dict)}


def scale_ratio(row: Any) -> dict[str, Any]:
    ratio = dig(row, "reference_to_plate_ratio")
    values = [dig(ratio, key) for key in ("value", "lower", "upper")]
    if not all(is_number(value) for value in values) or type(dig(ratio, "consistent_with_1")) is not bool:
        return {"status": "unavailable", "reason": "scale_row_invalid"}
    return {"status": "available", "value": values[0], "lower": values[1], "upper": values[2],
            "consistent_with_1": ratio["consistent_with_1"]}


def scale_diagnostics(root: Path, inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Diagnostic only: the session scale-reference-v1.json row per analyzable slot, bound by inventory hash."""
    sessions: dict[str, Any] = {}
    result = {}
    for slot in inventory["slots"]:
        if not slot["analyzable"]:
            continue
        session_id = slot.get("session_id")
        if session_id not in sessions:
            sessions[session_id] = load_scale_rows(root, dig(inventory, "sessions", session_id, "files", "scale_report_json"))
        rows = sessions[session_id]
        if isinstance(rows, str):
            result[slot["slot"]] = {"status": "unavailable", "reason": rows}
            continue
        row = next((item for item in rows if dig(item, "fixture_id") == slot.get("fixture_id")), None)
        result[slot["slot"]] = {"status": "unavailable", "reason": "scale_row_missing"} if row is None else scale_ratio(row)
    return result


def load_scale_rows(root: Path, item: Any) -> list[Any] | str:
    path = study.recorded_path(root, dig(item, "path"))
    if path is None or not path.is_file():
        return "scale_report_missing"
    try:
        data = path.read_bytes()
        rows = dig(workflow.schema_check.loads_strict(data.decode("utf-8")), "rows")
    except (OSError, UnicodeDecodeError, ValueError):
        return "scale_report_invalid"
    if hashlib.sha256(data).hexdigest() != dig(item, "sha256"):
        return "scale_report_changed"
    return rows if isinstance(rows, list) else "scale_report_invalid"


def private_tokens(root: Path, inventory: dict[str, Any]) -> list[str]:
    """Private strings that must never reach the Markdown; any free text containing one is redacted."""
    tokens: set[str] = set()

    def add_files(files: Any) -> None:
        for item in files.values() if isinstance(files, dict) else []:
            path = dig(item, "path")
            if isinstance(path, str):
                tokens.update({path, Path(path).name, Path(path).parent.as_posix()})
            if isinstance(dig(item, "sha256"), str):
                tokens.add(item["sha256"])

    for slot in inventory["slots"]:
        tokens.update(value for key in ("session_id", "session_date", "fixture_id", "creation_time")
                      if isinstance(value := slot.get(key), str))
        add_files(slot.get("files"))
    for session_id, session in inventory["sessions"].items():
        tokens.add(session_id)
        add_files(dig(session, "files"))
    add_files({"slots": inventory.get("slots_file"), "exclusion": inventory.get("exclusion_list"),
               **{name: dig(lift, "pairs_file") for name, lift in inventory["lifts"].items()}})
    if isinstance(inventory.get("assessments_dir"), str):
        tokens.add(inventory["assessments_dir"])
    slots_path = study.recorded_path(root, dig(inventory, "slots_file", "path"))
    try:
        owner = study.load_json(slots_path) if slots_path is not None else None
    except (OSError, ValueError):
        owner = None
    for entry in dig(owner, "slots") or []:
        if isinstance(dig(entry, "original_name"), str):
            tokens.add(entry["original_name"])
    return sorted((token for token in tokens if len(token) >= REDACTION_MIN_CHARS), key=lambda token: (-len(token), token))


# --- Criterion (pure) ----------------------------------------------------------------------------

def failure_text(failures: Any) -> str:
    return "; ".join(render.public_failure(item) for item in failures or [])


def condition(reasons: list[str]) -> dict[str, Any]:
    return {"passed": not reasons, "reasons": reasons}


def criterion_c1(slots: list[dict[str, Any]]) -> dict[str, Any]:
    reasons = [f"{slot['slot']}: {failure_text(slot.get('failures')) or 'not_protocol_conforming'}"
               for slot in slots if not slot.get("protocol_conforming")]
    for exercise in EXERCISES:
        conforming = [slot for slot in slots if slot.get("exercise") == exercise and slot.get("protocol_conforming")]
        sessions = {slot.get("session") for slot in conforming}
        if len(conforming) != study.VIDEOS_PER_LIFT or len(sessions) != len(study.SESSIONS):
            reasons.append(f"{exercise}: {len(conforming)}/{study.VIDEOS_PER_LIFT} conforming slots across "
                           f"{len(sessions)}/{len(study.SESSIONS)} sessions")
    return condition(reasons)


def video_reasons(slot: dict[str, Any], report: dict[str, Any] | None) -> list[str]:
    name = slot["slot"]
    if not slot.get("analyzable"):
        return [f"{name}: not_analyzable"]
    reasons = []
    reps = slot.get("attempted_rep_start_s")
    if not isinstance(reps, list) or len(reps) != study.PLANNED_REPS_PER_VIDEO:
        reasons.append(f"{name}: attempted_reps_not_{study.PLANNED_REPS_PER_VIDEO}")
    videos = dig(report, "videos") or []
    video = next((item for item in videos if dig(item, "label") == name), None)
    if video is None:
        return [*reasons, f"{name}: report_video_missing"]
    expected = {key: study.PLANNED_REPS_PER_VIDEO for key in ("wlTotal", "openBarTotal", "paired")}
    expected.update({key: 0 for key in UNMATCHED_KEYS})
    for key, value in expected.items():
        actual = dig(video, "counts", key)
        if actual != value:
            reasons.append(f"{name}: {key}={actual} (expected {value})")
    rows = dig(video, "paired") or []
    if not all(dig(row, "wlComplete") is True and dig(row, "openBarComplete") is True for row in rows):
        reasons.append(f"{name}: paired_rep_incomplete")
    return reasons


def criterion_c2(slots: list[dict[str, Any]], reports: dict[str, Any]) -> dict[str, Any]:
    reasons = [reason for slot in slots for reason in video_reasons(slot, reports.get(slot.get("exercise")))]
    for exercise in EXERCISES:
        paired = dig(reports.get(exercise), "pooled", "counts", "paired")
        if paired != PAIRS_PER_LIFT:
            shown = "unavailable" if paired is None else paired
            reasons.append(f"{exercise}: pooled paired={shown} (expected {PAIRS_PER_LIFT})")
    return condition(reasons)


def criterion_c3(reports: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    for exercise in EXERCISES:
        stats = dig(reports.get(exercise), "pooled", "stats", "meanVelocityMps")
        if reports.get(exercise) is None or not isinstance(stats, dict):
            reasons.append(f"{exercise}: statistics_unavailable")
            continue
        for key, inside in (("lowerLoA", lambda value: value >= -study.TOLERANCE_MPS),
                            ("upperLoA", lambda value: value <= study.TOLERANCE_MPS)):
            value = stats.get(key)
            if not is_number(value):
                reasons.append(f"{exercise}: {key} unavailable ({dig(stats, 'reasons', key) or 'missing'})")
            elif not inside(value):
                reasons.append(f"{exercise}: {key}={value!r} outside ±{study.TOLERANCE_MPS:.6f}")
    configs = {report.get("openBarMethodConfigSha256") for report in reports.values() if report is not None}
    if len(configs) > 1:
        reasons.append("method_config_mismatch_across_lifts")
    return condition(reasons)


def tool_reasons(tool: dict[str, Any]) -> list[str]:
    """The report's tool checkout must be clean and at the lock's tool commit, like the hand-check's."""
    lock = tool.get("lock_commit")
    reasons = [] if tool.get("tree_clean") is True else ["tool_tree_dirty"]
    if lock is None or tool.get("commit") != lock:
        reasons.append("tool_commit_differs_from_lock")
    if lock is None or tool.get("handcheck_commit") != lock:
        reasons.append("handcheck_tool_commit_differs")
    return reasons


def criterion_c4(reproducibility: dict[str, dict[str, bool]], inputs_unchanged: bool,
                 handcheck: dict[str, Any], tool: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    for exercise in EXERCISES:
        runs = reproducibility.get(exercise)
        if runs is None:
            continue
        for key, label in (("json_identical", "JSON"), ("md_identical", "Markdown")):
            if runs.get(key) is not True:
                reasons.append(f"{exercise}: run1/run2 {label} bytes differ")
    if inputs_unchanged is not True:
        reasons.append("inputs_changed_since_lock")
    if handcheck.get("status") != "passed":
        details = ", ".join(handcheck.get("reasons") or []) or "no reason recorded"
        reasons.append(f"handcheck_{handcheck.get('status')}: {details}")
    return condition(reasons + tool_reasons(tool))


def evaluate_criterion(facts: dict[str, Any]) -> dict[str, Any]:
    """Frozen #79 decision rule: PENDING during collection, PASS iff C1-C4 all pass, else FAIL.

    ``facts``: collection_status, slots (inventory slot documents), reports ({exercise: run1 report or
    None}), reproducibility ({exercise: {json_identical, md_identical}} for produced reports),
    inputs_unchanged, handcheck ({status, reasons}) and tool ({commit, tree_clean, lock_commit,
    handcheck_commit}). Secondary metrics and scale never enter. The CLI only evaluates lockable
    inventories, so it never renders PENDING; the branch stays for the pure rule.
    """
    conditions = {"C1": criterion_c1(facts["slots"]), "C2": criterion_c2(facts["slots"], facts["reports"]),
                  "C3": criterion_c3(facts["reports"]),
                  "C4": criterion_c4(facts["reproducibility"], facts["inputs_unchanged"], facts["handcheck"],
                                     facts["tool"])}
    provisional = facts["collection_status"] == "in_progress"
    passed = all(item["passed"] for item in conditions.values())
    verdict = "PENDING" if provisional else "PASS" if passed else "FAIL"
    return {"verdict": verdict, "provisional": provisional, "conditions": conditions}


# --- Command -------------------------------------------------------------------------------------

def check_paths(root: Path, tool_root: Path, args: argparse.Namespace) -> None:
    for flag, path in (("--inventory", args.inventory), ("--lock-summary", args.lock_summary),
                       ("--runs-dir", args.runs_dir), ("--handcheck", args.handcheck)):
        require(workflow.is_within(path, root), f"{flag} must be inside the data checkout {root}")
    require(args.runs_dir.is_dir(), "--runs-dir is not a folder")
    output = args.output
    require(output.suffix == ".md", "--output must be a .md file")
    require(any(workflow.is_within(output.parent, tool_root / folder) for folder in OUTPUT_DIRS),
            f"--output must be under {' or '.join(OUTPUT_DIRS)} of this checkout")
    require(not output.exists(), "--output already exists; reports are never replaced")


def check_consumer(app: Path, git: GitState) -> None:
    commit, clean = git(app)
    if commit != study.CONSUMER_COMMIT or not clean:
        raise study.StudyInfrastructureError(f"consumer checkout must be the clean pinned {study.CONSUMER_COMMIT}; "
                                  f"found {commit} (tracked files clean: {clean})")


def build(args: argparse.Namespace, root: Path, tool_root: Path, git: GitState, node: NodeVersion,
          tool_git: GitState = study.git_state) -> dict[str, Any]:
    """Every check and the rendered bytes; no writes."""
    check_paths(root, tool_root, args)
    verification = inventory_tool.verify(args.inventory, args.lock_summary, root)
    require(verification["inventory_matches_lock_summary"], "inventory sha256 is not the lock summary's")
    check_consumer(args.consumer_app, git)
    consumer = {"commit": study.CONSUMER_COMMIT, "node_version": node()}
    inventory, inventory_data = read_json(args.inventory, "inventory")
    check_inventory(inventory)
    inventory_sha = hashlib.sha256(inventory_data).hexdigest()
    summary, _ = read_json(args.lock_summary, "lock summary")
    check_lock_summary(summary, inventory_sha)
    lifts = {exercise: load_lift(root, args.runs_dir, exercise, inventory) for exercise in EXERCISES}
    squat = lifts["back_squat"]
    handcheck = load_handcheck(args.handcheck, inventory_sha, dig(squat, "hashes", "run1_json"))
    tool_commit, tool_clean = tool_git(tool_root)
    tool = {"commit": tool_commit, "tree_clean": tool_clean, "lock_commit": summary.get("tool_commit"),
            "handcheck_commit": handcheck["tool_commit"]}
    facts = {"collection_status": inventory["collection_status"], "slots": inventory["slots"],
             "reports": {exercise: lift["report"] for exercise, lift in lifts.items()},
             "reproducibility": {exercise: {key: lift[key] for key in ("json_identical", "md_identical")}
                                 for exercise, lift in lifts.items() if lift["report"] is not None},
             "inputs_unchanged": verification["inputs_unchanged"], "handcheck": handcheck, "tool": tool}
    criterion = evaluate_criterion(facts)
    context = {"inventory": inventory, "inventory_sha256": inventory_sha, "lock_summary": summary,
               "verification": verification, "lifts": lifts, "handcheck": handcheck, "criterion": criterion,
               "scale": scale_diagnostics(root, inventory), "tracking": tracking_diagnostics(root, inventory),
               "consumer": consumer, "tool": tool,
               "private_tokens": private_tokens(root, inventory)}
    return {"criterion": criterion, "markdown": render.render(context)}


def resolved(args: argparse.Namespace) -> argparse.Namespace:
    for name in ("inventory", "lock_summary", "runs_dir", "handcheck", "consumer_app", "output"):
        setattr(args, name, workflow.safe_resolve(getattr(args, name)))
    return args


def main(argv: list[str] | None = None, *, root: Path = ROOT, tool_root: Path = ROOT,
         git: GitState = consumer_git_state, node: NodeVersion = node_version,
         tool_git: GitState = study.git_state) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=root,
                        help="pinned data checkout holding the study evidence (default: this checkout)")
    parser.add_argument("--inventory", type=Path, required=True, help="locked private inventory.json")
    parser.add_argument("--lock-summary", type=Path, required=True, help="lock-summary.json of that inventory")
    parser.add_argument("--runs-dir", type=Path, required=True,
                        help="folder with report-<exercise>-run1/run2 .json and .md per lift with a pairs file")
    parser.add_argument("--handcheck", type=Path, required=True, help="private S1-SQ-1 hand-check JSON")
    parser.add_argument("--consumer-app", type=Path, required=True, help="pinned consumer app/ folder")
    parser.add_argument("--output", type=Path, required=True, help="new .md under docs/analysis/ or target/")
    args = parser.parse_args(argv)
    try:
        data_root = workflow.safe_resolve(args.root)
        result = build(resolved(args), data_root, workflow.safe_resolve(tool_root), git, node, tool_git)
        study.write_new(args.output, result["markdown"])
    except (study.StudyInputError, inventory_tool.InventoryInputError, FileExistsError, workflow.WorkflowError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except INFRASTRUCTURE_ERRORS as error:
        print(f"error: infrastructure abort ({error}); retry with unchanged inputs", file=sys.stderr)
        return 3
    conditions = " ".join(f"{name}={'pass' if item['passed'] else 'fail'}"
                          for name, item in result["criterion"]["conditions"].items())
    print(f"report written: verdict {result['criterion']['verdict']} ({conditions})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
