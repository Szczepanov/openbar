#!/usr/bin/env python3
"""Per-slot derived evidence for the #79 inventory (owner-vbt-agreement-79-v1).

Read-only: bindings, the frozen method, the #111 assessment, novelty and timing of retained session
outputs. It never reads velocities or scale-ratio values and computes no outcome; missing evidence is a
recorded failure, never an invented value. Tools that cannot run raise
``study.StudyInfrastructureError`` (exit 3, retry); a bad exclusion list raises ``study.StudyInputError``.
"""
from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

import agreement79_study as study
import analyze_lift as workflow
import session_run

ASSESSMENT_SCHEMA = workflow.ROOT / "validation/schema/vbt-clip-assessment-v1.schema.json"
SCALE_REPORT_JSON = "scale-reference-v1.json"
ASSESSMENT_STATUSES = ("processing", "mechanical", "experiment_suitability", "accuracy")
ENVIRONMENT_KEYS = ("python", "opencv_version", "numpy_version", "ffmpeg", "ffprobe")
RUN_OUTPUTS = ("prediction", "analysis")
FREEZE_DATE = study.FREEZE_INSTANT.date()
BRIDGE = Path(__file__).resolve().with_name("agreement79_consumer_reps.mjs")
PREFLIGHT_SOURCES = ("openbar", "wl")
SCALE_BINDINGS = (("source_video_sha256", "video"), ("analysis_sha256", "analysis"),
                  ("click_csv_sha256", "scale_click_csv"))

# Compatibility alias for callers that still import it from here; the class is the shared one.
InfrastructureError = study.StudyInfrastructureError

StatusCheck = Callable[[Path, str], "tuple[bool, str | None]"]
Probe = Callable[[Path], "str | None"]
Preflight = Callable[[Path, Path, Path], "dict[str, Any]"]


# --- Injectable process boundaries ---------------------------------------------------------------

def last_line(data: bytes) -> str | None:
    lines = [line.strip() for line in data.decode("utf-8", errors="replace").splitlines() if line.strip()]
    return lines[-1] if lines else None


def session_status(root: Path, session_id: str) -> tuple[bool, str | None]:
    """The existing `vbt_session.py status` (#114) verdict and, when it failed, its last message line.

    Exit 0 means complete and hash-verified, exit 1 a verdict of not complete. A Python traceback or any
    other exit code means the status tool itself broke: infrastructure, never a slot failure.
    """
    argv = [sys.executable, "research/vbt-workflow/vbt_session.py", "status", "--session", session_id]
    try:
        result = subprocess.run(argv, cwd=root, capture_output=True, check=False)
    except OSError as error:
        raise study.StudyInfrastructureError(f"vbt_session.py status could not be run: {error}") from error
    if b"Traceback (most recent call last)" in result.stderr:
        raise study.StudyInfrastructureError(f"vbt_session.py status crashed for session {session_id}")
    if result.returncode not in (0, 1):
        raise study.StudyInfrastructureError(
            f"vbt_session.py status exited {result.returncode} for session {session_id}")
    if result.returncode == 0:
        return True, None
    return False, last_line(result.stderr) or last_line(result.stdout)


def valid_preflight(value: Any) -> bool:
    """Exactly two sources, each {accepted: bool, error: null when accepted, else non-empty text}."""
    if not isinstance(value, dict) or set(value) != set(PREFLIGHT_SOURCES):
        return False
    for item in value.values():
        if not isinstance(item, dict) or set(item) != {"accepted", "error"} or type(item["accepted"]) is not bool:
            return False
        if item["accepted"] != (item["error"] is None):
            return False
        if item["error"] is not None and not (isinstance(item["error"], str) and item["error"].strip()):
            return False
    return True


def consumer_preflight(app: Path, analysis: Path, wl_csv: Path) -> dict[str, Any]:
    """Whether the pinned consumer's own parsers accept the pair (bridge preflight mode); no values."""
    argv = ["node", "--experimental-strip-types", str(BRIDGE), "preflight", str(app), str(analysis), str(wl_csv)]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except OSError as error:
        raise study.StudyInfrastructureError(f"node could not be run: {error}") from error
    if result.returncode != 0:
        detail = last_line(result.stderr) or f"exit {result.returncode}"
        raise study.StudyInfrastructureError(f"consumer parser preflight bridge failed: {detail}")
    line = last_line(result.stdout)
    try:
        value = json.loads(line) if line is not None else None
    except json.JSONDecodeError as error:
        raise study.StudyInfrastructureError("consumer parser preflight bridge wrote no JSON line") from error
    if not valid_preflight(value):
        raise study.StudyInfrastructureError("consumer parser preflight bridge output is malformed")
    return value


def ffprobe_creation_time(video: Path) -> str | None:
    """Container creation_time tag as text; None when ffprobe reports none for this file."""
    argv = ["ffprobe", "-v", "error", "-show_entries", "format_tags=creation_time", "-of", "json", str(video)]
    try:
        result = subprocess.run(argv, capture_output=True, check=False)
    except OSError as error:
        raise study.StudyInfrastructureError(f"ffprobe could not be run: {error}") from error
    if result.returncode != 0:
        return None
    try:
        value = dig(json.loads(result.stdout.decode("utf-8")), "format", "tags", "creation_time")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, str) else None


# --- Small helpers -------------------------------------------------------------------------------

def dig(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def is_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def hashed(root: Path, path: Path | None) -> dict[str, str] | None:
    if path is None or not path.is_file():
        return None
    try:
        return {"path": study.repo_relative(path, root), "sha256": study.file_sha256(path)}
    except OSError:
        return None


def load_document(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return study.load_json(path)
    except (OSError, ValueError):
        return None


def parse_creation_time(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    if candidate[-1] in "Zz":
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def valid_attempted_reps(values: list[Any]) -> bool:
    return (len(values) == study.PLANNED_REPS_PER_VIDEO
            and all(is_number(value) and value >= 0 for value in values)
            and all(later > earlier for earlier, later in zip(values, values[1:])))


@functools.lru_cache(maxsize=1)
def assessment_schema() -> dict[str, Any]:
    return workflow.schema_check.load_schema(ASSESSMENT_SCHEMA)


def load_exclusion_list(path: Path, expected_sha256: str) -> tuple[str, frozenset[str]]:
    """Verify the frozen novelty list by exact bytes, then read one SHA-256 per non-comment line."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise study.StudyInputError(f"novelty exclusion list unreadable: {error}") from error
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha256:
        raise study.StudyInputError(f"novelty exclusion list digest {digest} is not the frozen {expected_sha256}")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise study.StudyInputError("novelty exclusion list is not UTF-8") from error
    hashes = set()
    for number, line in enumerate(lines, start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        token = text.split()[0].lower()
        if study.SHA256_RE.fullmatch(token) is None:
            raise study.StudyInputError(f"novelty exclusion list line {number} does not start with a SHA-256")
        hashes.add(token)
    return digest, frozenset(hashes)


# --- Sessions ------------------------------------------------------------------------------------

def load_session(root: Path, session_id: str, status: StatusCheck) -> dict[str, Any]:
    directory = root / study.SESSIONS_DIR / session_id
    paths = session_run.session_paths(directory)
    complete, status_detail = status(root, session_id)
    record = load_document(paths["record"])
    record = record if isinstance(record, dict) else None
    scale_path = paths["scale_report"] / SCALE_REPORT_JSON
    rows = dig(load_document(scale_path), "rows")
    # Only each row's fixture id and its three binding hashes are read; never a ratio value.
    scale_rows = {row["fixture_id"]: {key: row.get(key) for key, _ in SCALE_BINDINGS}
                  for row in (rows if isinstance(rows, list) else [])
                  if isinstance(row, dict) and isinstance(row.get("fixture_id"), str)}
    sources = {"session_record": paths["record"], "scale_report_json": scale_path,
               "session_csv": study.recorded_path(root, dig(record, "inputs", "session_csv", "path")),
               "session_state": study.recorded_path(root, dig(record, "inputs", "session_state", "path")),
               "report_html": study.recorded_path(root, dig(record, "report", "path")),
               "manifest": study.recorded_path(root, dig(record, "inputs", "manifest", "path"))}
    files = {role: item for role, path in sources.items() if (item := hashed(root, path)) is not None}
    return {"id": session_id, "complete": complete and record is not None, "record": record,
            "scale_rows": scale_rows, "files": files, "status_detail": status_detail}


# --- Per-slot derivation -------------------------------------------------------------------------

class SlotEvidence:
    """Mutable accumulator for one slot's derived facts (internal; serialised by the inventory)."""

    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []
        self.files: dict[str, dict[str, str]] = {}
        self.processed = False
        self.assessment_statuses: dict[str, Any] | None = None
        self.seed_timestamp_s: float | None = None
        self.creation_time: str | None = None
        self.manifest_entry_sha256: str | None = None
        self.environment: dict[str, Any] | None = None
        self.video_sha256: str | None = None
        self.parser_preflight: dict[str, Any] | None = None

    def fail(self, stage: str, reason: str) -> None:
        self.failures.append((stage, reason))

    def bind(self, root: Path, role: str, path: Path | None) -> str | None:
        item = hashed(root, path)
        if item is not None:
            self.files[role] = item
        return None if item is None else item["sha256"]


def derive_slot(root: Path, entry: dict[str, Any], session: dict[str, Any], context: dict[str, Any]) -> SlotEvidence:
    """Steps 1-10 of the inventory spec for one enrolled slot with a fixture id."""
    evidence = SlotEvidence()
    reps_valid = valid_attempted_reps(entry["attempted_rep_start_s"])
    if date.fromisoformat(entry["session_date"]) < FREEZE_DATE:
        evidence.fail("novelty", "session_date_before_freeze")
    if not reps_valid:
        evidence.fail("protocol", "attempted_reps_not_three")
    wl_path = study.recorded_path(root, entry["wl_csv"])
    if evidence.bind(root, "wl_csv", wl_path) is None:
        evidence.fail("wl_export", "wl_csv_missing")
    if not session["complete"]:
        evidence.fail("processing", "session_status_failed")
        return evidence
    record = session["record"]
    clips = record.get("clips") if isinstance(record.get("clips"), list) else []
    clip = next((item for item in clips if dig(item, "fixture_id") == entry["fixture_id"]), None)
    if clip is None:
        skipped = record.get("skipped") if isinstance(record.get("skipped"), list) else []
        if entry["original_name"] in skipped:
            evidence.fail("confirmation", "skipped_on_session_page")
        else:
            evidence.fail("processing", "clip_not_in_session_record")
        return evidence
    clip_evidence(root, entry, session, clip, context, evidence, reps_valid)
    return evidence


def clip_evidence(root: Path, entry: dict[str, Any], session: dict[str, Any], clip: dict[str, Any],
                  context: dict[str, Any], evidence: SlotEvidence, reps_valid: bool) -> None:
    if clip.get("exercise") != study.slot_lift(entry["slot"])["exercise"]:
        evidence.fail("processing", "exercise_mismatch")
    configuration = session["record"].get("configuration")
    for field, mismatched in record_method(configuration, clip, context["plate_diameter_m"]):
        if mismatched:
            evidence.fail("processing", f"method_mismatch:{field}")
    video = study.recorded_path(root, dig(clip, "video", "path"))
    evidence.video_sha256 = evidence.bind(root, "video", video)
    seed = study.recorded_path(root, dig(clip, "seed", "path"))
    evidence.bind(root, "seed", seed)
    evidence.bind(root, "label_csv", study.recorded_path(root, dig(clip, "seed", "label_csv", "path")))
    evidence.bind(root, "scale_click_csv", study.recorded_path(root, dig(clip, "scale_click_csv", "path")))
    run_sha = run_evidence(root, entry, clip, context, evidence)
    assessment_evidence(root, entry["fixture_id"], run_sha, context, evidence)
    if evidence.video_sha256 is not None and evidence.video_sha256 in context["excluded"]:
        evidence.fail("novelty", "duplicate_of_excluded_clip")
    creation_evidence(video if evidence.video_sha256 is not None else None, context["probe"], evidence)
    timestamp = dig(load_document(seed), "seed", "timestamp_s")
    evidence.seed_timestamp_s = timestamp if is_number(timestamp) else None
    if reps_valid and (evidence.seed_timestamp_s is None
                       or not evidence.seed_timestamp_s < entry["attempted_rep_start_s"][0]):
        evidence.fail("protocol", "seed_not_before_first_rep")
    scale_evidence(entry["fixture_id"], session, evidence)


def scale_evidence(fixture_id: str, session: dict[str, Any], evidence: SlotEvidence) -> None:
    """The scale row must exist and bind this slot's exact video, analysis and click CSV bytes."""
    row = session["scale_rows"].get(fixture_id)
    if row is None:
        evidence.fail("reference", "scale_reference_missing")
        return
    for key, role in SCALE_BINDINGS:
        bound = evidence.files.get(role, {}).get("sha256")
        if not isinstance(row[key], str) or study.SHA256_RE.fullmatch(row[key]) is None or row[key] != bound:
            evidence.fail("reference", "scale_reference_unbound")
            return


def record_method(configuration: Any, clip: dict[str, Any], plate: float) -> list[tuple[str, bool]]:
    return [("tracker_policy", dig(configuration, "tracker_policy") != study.TRACKER_POLICY),
            ("preset", dig(configuration, "preset") != study.PRESET),
            ("analyze_options", dig(configuration, "analyze_options") != study.ANALYZE_OPTIONS),
            ("plate_diameter_m", not is_number(dig(configuration, "plate_diameter_m"))
             or dig(configuration, "plate_diameter_m") != plate),
            ("stick_length_m", not is_number(dig(configuration, "stick_length_m"))
             or dig(configuration, "stick_length_m") != study.STICK_LENGTH_M),
            ("clip_tracker", clip.get("tracker") != study.TRACKER)]


def run_method(run: Any, fixture_id: str, plate: float) -> list[tuple[str, bool]]:
    version = dig(run, "format_version")
    return [("run_record_format", dig(run, "format") != workflow.RUN_RECORD_FORMAT
             or type(version) is not int or version != workflow.RUN_RECORD_FORMAT_VERSION),
            ("run_fixture_id", dig(run, "fixture_id") != fixture_id),
            ("run_tracker", dig(run, "configuration", "tracker") != study.TRACKER),
            ("tracker_implementation",
             dig(run, "configuration", "tracker_implementation") != study.TRACKER_IMPLEMENTATION),
            ("run_preset", dig(run, "configuration", "preset") != study.PRESET),
            ("run_analyze_options", dig(run, "configuration", "analyze_options") != study.ANALYZE_OPTIONS),
            ("run_plate_diameter_m", not is_number(dig(run, "configuration", "plate_diameter_m"))
             or dig(run, "configuration", "plate_diameter_m") != plate),
            ("openbar_cli_sha256", dig(run, "environment", "openbar_cli", "sha256") != study.OPENBAR_CLI_SHA256),
            ("openbar_git_commit", dig(run, "openbar", "git_commit") != study.OPENBAR_BASELINE_COMMIT),
            ("openbar_tracked_changes", dig(run, "openbar", "tracked_changes") is not False)]


def run_evidence(root: Path, entry: dict[str, Any], clip: dict[str, Any], context: dict[str, Any],
                 evidence: SlotEvidence) -> str | None:
    """Run-record binding, frozen run method and its two outputs; returns the run record's file hash."""
    run_path = study.recorded_path(root, dig(clip, "analyze_lift", "run_record", "path"))
    run_sha = evidence.bind(root, "run_record", run_path)
    if run_sha is None or run_sha != dig(clip, "analyze_lift", "run_record", "sha256"):
        evidence.fail("processing", "method_mismatch:run_record_sha256")
    if run_sha is None:
        return None
    run = load_document(run_path)
    for field, mismatched in run_method(run, entry["fixture_id"], context["plate_diameter_m"]):
        if mismatched:
            evidence.fail("processing", f"method_mismatch:{field}")
    for name in RUN_OUTPUTS:
        file_name = dig(run, "outputs", name, "file")
        safe = isinstance(file_name, str) and file_name not in ("", ".", "..") and not re.search(r"[\\/\0]", file_name)
        digest = evidence.bind(root, name, run_path.parent / file_name if safe else None)
        if digest is None or digest != dig(run, "outputs", name, "sha256"):
            evidence.fail("processing", f"run_output_mismatch:{name}")
    evidence.processed = "analysis" in evidence.files
    manifest_entry = dig(run, "inputs", "manifest_entry", "sha256")
    evidence.manifest_entry_sha256 = manifest_entry if isinstance(manifest_entry, str) else None
    if isinstance(dig(run, "environment"), dict):
        evidence.environment = {key: dig(run, "environment", key) for key in ENVIRONMENT_KEYS}
    return run_sha


def assessment_evidence(root: Path, fixture_id: str, run_sha: str | None, context: dict[str, Any],
                        evidence: SlotEvidence) -> None:
    path = context["assessments_dir"] / f"{fixture_id}.assessment-v1.json"
    if evidence.bind(root, "assessment", path) is None:
        evidence.fail("assessment", "assessment_missing")
        return
    document = load_document(path)
    try:
        errors = workflow.schema_check.validate_document(document, assessment_schema())
    except (ValueError, TypeError, KeyError):
        errors = ["unvalidatable"]
    if document is None or errors:
        evidence.fail("assessment", "assessment_invalid")
        return
    if (document["fixture_id"] != fixture_id or run_sha is None
            or document["sources"]["run_record"]["sha256"] != run_sha
            or document["sources"]["validator"]["sha256"] != study.OPENBAR_CLI_SHA256):
        evidence.fail("assessment", "assessment_unbound")
        return
    evidence.assessment_statuses = {name: document[name]["status"] for name in ASSESSMENT_STATUSES}
    if document["processing"]["status"] != "complete":
        evidence.fail("assessment", f"processing_{document['processing']['status']}")
    if document["mechanical"]["status"] != "valid":
        evidence.fail("assessment", f"mechanical_{document['mechanical']['status']}")


def creation_evidence(video: Path | None, probe: Probe, evidence: SlotEvidence) -> None:
    text = probe(video) if video is not None else None
    parsed = parse_creation_time(text)
    if parsed is None:
        evidence.fail("novelty", "creation_time_unavailable")
        return
    evidence.creation_time = text
    if not parsed > study.FREEZE_INSTANT:
        evidence.fail("novelty", "recorded_before_freeze")
